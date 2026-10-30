"""Edit planner for compose: demo spec + timeline + durations + raw video -> plan (stdlib only).

The plan lists every scene in spec order with its trim window in raw-video
seconds, its narration length, how it is fitted to that narration, and where it
lands in the final video. A scene is cut into segments at its beat markers; each
segment after the first starts when the narration reaches its action's ``at``
phrase, holding the previous frame until then. Nothing here runs ffmpeg: the raw
video is passed in, and asked only where its sync flash is and how much of each
segment is left once frozen frames are dropped (see ``build_plan``).
"""

import json
import math
from dataclasses import dataclass, replace
from enum import StrEnum

from narration import Narration

MAX_SPEED = 1.5
FPS = 30  # the final video's frame rate; every scene is cut on a whole frame
# An action this far past its phrase is late. Anchors are rounded to whole frames, so up
# to half a frame of difference is rounding, not lateness.
LATE_AFTER_S = 0.5 / FPS


class PlanError(ValueError):
    """Raised when the timeline, durations and demo spec don't line up."""


class Strategy(StrEnum):
    """How a scene's video is fitted to its narration (string-compatible: "hold" etc.)."""

    HOLD = "hold"  # freeze the last frame for ``hold_s``
    SPEEDUP = "speedup"  # play at ``speed``x so it lasts exactly as long as the audio
    PAD = "pad"  # play at MAX_SPEED and pad the audio with ``pad_s`` of silence


@dataclass(frozen=True)
class Fit:
    """How one scene's video is fitted to its narration (see Strategy)."""

    strategy: Strategy
    speed: float
    hold_s: float
    pad_s: float
    scene_s: float


def fit(video_s, audio_s):
    """Fit rule: a pure function of (decimated video length, audio length) in seconds."""
    if audio_s <= 0:
        raise ValueError(f"audio length must be positive, got {audio_s}")
    video_s = max(0.0, float(video_s))
    if video_s <= audio_s:
        return Fit(Strategy.HOLD, 1.0, audio_s - video_s, 0.0, audio_s)
    if video_s <= MAX_SPEED * audio_s:
        return Fit(Strategy.SPEEDUP, video_s / audio_s, 0.0, 0.0, audio_s)
    scene_s = video_s / MAX_SPEED
    return Fit(Strategy.PAD, MAX_SPEED, 0.0, scene_s - audio_s, scene_s)


@dataclass(frozen=True)
class Segment:
    """One part of a scene, from a beat marker (or the scene start) to the next.

    trim_start/trim_end: its window in raw-video seconds. anchor_s: when, from the
    scene start, the narration reaches its action's ``at`` phrase (0 for the first
    segment). frames: its length once frozen frames are dropped. lead_frames: how long
    its first frame is held before it plays, so that it starts on its anchor (or right
    after the previous segment, if that runs late). phrase: that ``at`` phrase ("" for
    the first segment).
    """

    trim_start: float
    trim_end: float
    anchor_s: float
    frames: int
    lead_frames: int = 0
    phrase: str = ""


@dataclass(frozen=True)
class PhonePage:
    """The take holds the whole window, so the phone frame needs these to crop out the phone page."""

    window: tuple  # (width, height) in CSS pixels before emulation, from the start mark
    page: tuple  # (width, height) in CSS pixels after emulation
    dpr: float  # the screen's device pixel ratio (see ADR-0011)


@dataclass(frozen=True)
class ScenePlan:
    """One scene of the edit plan.

    trim_start/trim_end: the scene's window in raw-video seconds.
    segments: the scene cut at its beat markers, synced to their ``at`` phrases.
    audio_s: narration length. video_s: the video length the fit was worked out
    from: the segments' frames plus their leads.
    offset_s: where the scene starts in the final video (cumulative scene lengths).
    The scene's length is the fit's length rounded to whole frames at FPS, which is
    exactly what compose cuts, so offsets and subtitle cues don't drift.
    """

    id: str
    title: str
    narration: str
    trim_start: float
    trim_end: float
    audio_s: float
    video_s: float
    fit: Fit
    offset_s: float
    segments: tuple = ()
    device: str = "desktop"
    phone_page: "PhonePage | None" = None  # phone scenes only

    @property
    def frames(self):
        return max(1, round(self.fit.scene_s * FPS))

    @property
    def scene_s(self):
        return self.frames / FPS

    @property
    def end_s(self):
        return self.offset_s + self.scene_s


@dataclass(frozen=True)
class Cue:
    """One subtitle cue: a narration sentence with absolute times in the final video."""

    scene_id: str
    start_s: float
    end_s: float
    text: str


@dataclass(frozen=True)
class ActionTime:
    """Where compose places one ``at`` action in the final video.

    index: its place among the scene's ``at`` actions, from 1. start_s: when it starts,
    after the holds and the scene's speed. late_s: how far past its phrase that is,
    when an earlier segment ran long (0 otherwise).
    """

    scene_id: str
    index: int
    start_s: float
    phrase: str
    late_s: float


@dataclass(frozen=True)
class Plan:
    """The scenes in spec order, laid end to end.

    flash_s: where the sync flash is in the raw video, or None when the timeline
    has no ``sync_ms`` and raw time 0 is the ``rec_start`` mark.
    """

    scenes: tuple
    flash_s: float | None = None

    @property
    def total_s(self):
        return self.scenes[-1].end_s if self.scenes else 0.0

    @property
    def cues(self):
        """Subtitle cues for every scene, one per narration sentence, in final-video time.

        A scene's *audio* length is shared out across its sentences by character
        count; each scene's cues start at its offset (cumulative *scene* lengths),
        so padding in one scene pushes every later cue back. A cue never runs
        past the end of its (frame-rounded) scene, where compose cuts the audio.
        """
        cues = []
        for scene in self.scenes:
            for start, end, sentence in Narration(scene.narration).cue_shares():
                cues.append(
                    Cue(
                        scene.id,
                        min(scene.offset_s + scene.audio_s * start, scene.end_s),
                        min(scene.offset_s + scene.audio_s * end, scene.end_s),
                        sentence,
                    )
                )
        return tuple(cues)

    @property
    def actions(self):
        """Every ``at`` action with its start in final-video time, in plan order."""
        actions = []
        for scene in self.scenes:
            frames = 0
            for index, segment in enumerate(scene.segments):
                start_s = (frames + segment.lead_frames) / FPS / scene.fit.speed
                frames += segment.lead_frames + segment.frames
                if not index:
                    continue  # the first segment starts the scene, not an action
                late_s = start_s - segment.anchor_s
                actions.append(
                    ActionTime(
                        scene.id,
                        index,
                        scene.offset_s + start_s,
                        segment.phrase,
                        late_s if late_s > LATE_AFTER_S else 0.0,
                    )
                )
        return tuple(actions)


def build_plan(spec, timeline, durations, video):
    """Build the edit plan from a validated demo spec, timeline events, durations and raw video.

    ``video`` is the raw recording, asked only for what the plan needs:

    - ``video.flash_s()``: where the sync flash first shows, in seconds, or None.
      Called only when the last ``rec_start`` has a ``sync_ms``.
    - ``video.frames(trim_start, trim_end)``: how many frames at FPS are left of
      that window once frozen frames are dropped. Called once per segment at least
      a frame long, in plan order, after the spec, timeline and durations have all
      been checked.

    Raises PlanError when they don't line up; errors raised by ``video`` pass through.
    """
    windows, flash_s = _scene_windows(timeline, {scene["id"] for scene in spec["scenes"]}, video)
    scenes = []
    for scene in spec["scenes"]:
        scene_id = scene["id"]
        if scene_id not in windows:
            raise PlanError(f"scene '{scene_id}' has no start marker in the timeline")
        audio_s = _duration(durations, scene_id)
        trim_start, trim_end, beats, start_event = windows[scene_id]
        device = scene.get("device", "desktop")
        page = _phone_page(scene_id, start_event) if device == "phone" else None
        phrases = [action["at"] for action in scene.get("actions", []) if "at" in action]
        anchors = [
            audio_s * share for share in Narration(scene["narration"]).phrase_shares(phrases)
        ]
        if len(beats) != len(anchors):
            raise PlanError(
                f"scene '{scene_id}' has {len(beats)} beat markers but "
                f"{len(anchors)} actions with an 'at' phrase; mark a beat right "
                f"before each such action"
            )
        bounds = [trim_start, *beats, trim_end]
        segments = tuple(
            Segment(start, end, anchor, _frames(end - start), phrase=phrase)
            for start, end, anchor, phrase in zip(
                bounds, bounds[1:], [0.0, *anchors], ["", *phrases]
            )
        )
        scenes.append(
            ScenePlan(
                id=scene_id,
                title=scene.get("title") or scene_id,
                narration=scene["narration"],
                trim_start=trim_start,
                trim_end=trim_end,
                audio_s=audio_s,
                video_s=0.0,
                fit=fit(0.0, audio_s),
                offset_s=0.0,
                segments=segments,
                device=device,
                phone_page=page,
            )
        )
    return _lay_out([_measure(scene, video) for scene in scenes], flash_s)


def load_timeline(path):
    """Read a timeline.jsonl file into a list of event dicts (blank lines skipped)."""
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise PlanError(f"cannot read timeline {path}: {exc.strerror}") from exc
    events = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PlanError(f"timeline {path} line {number} is not valid JSON: {exc}") from exc
        if not isinstance(event, dict):
            raise PlanError(f"timeline {path} line {number} must be a JSON object")
        events.append(event)
    return events


def _scene_windows(timeline, spec_ids, video):
    """Return ({scene id: (start, end, beats, start event)} in raw-video seconds, flash_s), validating.

    Every timeline scene must be one of ``spec_ids``; that is checked before its
    markers are paired, so a stray scene is reported as such. Only the events after the last ``rec_start`` count: each re-recording appends
    a new one, so earlier takes in the file are ignored. Raw-video time 0 is the
    sync flash's wall-clock time minus where ``video`` shows it when the
    ``rec_start`` has a ``sync_ms``, and the ``rec_start`` itself otherwise.
    """
    rec_starts = [i for i, e in enumerate(timeline) if e.get("event") == "rec_start"]
    if not rec_starts:
        raise PlanError(
            "timeline has no rec_start marker; run `mark.py TIMELINE rec_start` "
            "right after starting the screencast"
        )
    rec_start = timeline[rec_starts[-1]]
    origin_ms, flash_s = _t_ms(rec_start), None
    if rec_start.get("sync_ms") is not None:
        sync = rec_start["sync_ms"]
        if not isinstance(sync, (int, float)) or isinstance(sync, bool):
            raise PlanError(f"rec_start has a non-numeric sync_ms {sync!r}")
        flash_s = video.flash_s()
        if flash_s is None:
            raise PlanError(
                "no sync flash found in the raw video; run __demo.sync() right "
                "after screencast_start, then mark rec_start with --sync-ms"
            )
        origin_ms = sync - flash_s * 1000

    marks, beats, start_events = {}, {}, {}
    for event in timeline[rec_starts[-1] + 1 :]:
        kind = event.get("event")
        if kind not in ("start", "beat", "end"):
            raise PlanError(f"timeline event {event!r} has unknown event type {kind!r}")
        scene_id = event.get("scene")
        if not scene_id:
            raise PlanError(f"timeline event {event!r} has no scene id")
        scene_marks = marks.setdefault(scene_id, {})
        if kind == "beat":
            beats.setdefault(scene_id, []).append(_t_ms(event))
            continue
        if kind in scene_marks:
            raise PlanError(f"scene '{scene_id}' has more than one {kind} marker")
        scene_marks[kind] = _t_ms(event)
        if kind == "start":
            start_events[scene_id] = event

    for scene_id in marks:
        if scene_id not in spec_ids:
            raise PlanError(f"timeline scene '{scene_id}' is not in the demo spec")

    windows = {}
    for scene_id, scene_marks in marks.items():
        if "start" not in scene_marks:
            raise PlanError(f"scene '{scene_id}' has an end but no start marker")
        if "end" not in scene_marks:
            raise PlanError(f"scene '{scene_id}' has a start but no end marker")
        start_ms, end_ms = scene_marks["start"], scene_marks["end"]
        if start_ms < origin_ms:
            raise PlanError(f"scene '{scene_id}' starts before rec_start")
        if end_ms == start_ms:
            raise PlanError(
                f"scene '{scene_id}' has zero length: end equals start (t_ms={start_ms})"
            )
        if end_ms < start_ms:
            raise PlanError(
                f"scene '{scene_id}' ends (t_ms={end_ms}) before it starts (t_ms={start_ms})"
            )
        scene_beats = sorted(beats.get(scene_id, []))
        if any(not start_ms <= beat_ms <= end_ms for beat_ms in scene_beats):
            raise PlanError(f"scene '{scene_id}' has a beat marker outside its start and end")
        windows[scene_id] = (
            (start_ms - origin_ms) / 1000,
            (end_ms - origin_ms) / 1000,
            tuple((beat_ms - origin_ms) / 1000 for beat_ms in scene_beats),
            start_events[scene_id],
        )
    return windows, flash_s


def _duration(durations, scene_id):
    """The scene's narration length in seconds; a positive number or a PlanError."""
    if scene_id not in durations:
        raise PlanError(f"scene '{scene_id}' is missing from the durations file")
    seconds = durations[scene_id]
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        raise PlanError(
            f"scene '{scene_id}' has a non-numeric duration {seconds!r} "
            f"in the durations file; re-run tts.py"
        )
    if not math.isfinite(seconds) or seconds <= 0:
        raise PlanError(
            f"scene '{scene_id}' has duration {seconds!r}; it must be positive (re-run tts.py)"
        )
    return float(seconds)


def _phone_page(scene_id, start_event):
    """Compose can't find the page in a phone scene's frames without the recorded sizes."""
    window, page, dpr = (start_event.get(key) for key in ("window", "page", "dpr"))
    if not (_size(window) and _size(page) and _positive(dpr)):
        raise PlanError(
            f"phone scene '{scene_id}' has no window, page and dpr on its start marker; "
            "mark it with `mark.py TIMELINE start SCENE --window WxH --page WxH --dpr N`"
        )
    return PhonePage(tuple(window), tuple(page), dpr)


def _size(value):
    return isinstance(value, list) and len(value) == 2 and all(map(_positive, value))


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _t_ms(event):
    t_ms = event.get("t_ms")
    if not isinstance(t_ms, (int, float)) or isinstance(t_ms, bool):
        raise PlanError(f"timeline event {event!r} has no numeric t_ms")
    return t_ms


def _frames(seconds):
    return max(0, round(seconds * FPS))


def _measure(scene, video):
    """The scene with each segment's frames measured in ``video``; PlanError if none are left."""
    segments = tuple(
        replace(segment, frames=video.frames(segment.trim_start, segment.trim_end))
        if segment.frames
        else segment  # shorter than a frame: nothing to cut
        for segment in scene.segments
    )
    if not any(segment.frames for segment in segments):
        raise PlanError(
            f"scene '{scene.id}' has no video frames in its trim window "
            f"{scene.trim_start:.3f}-{scene.trim_end:.3f}s; is it past the end of "
            f"the raw recording?"
        )
    return replace(scene, segments=segments)


def _sync(segments):
    """Give each segment the lead that starts it on its anchor; return (segments, frames).

    A segment never starts before the previous one ends, so a late one pushes the
    rest back. Empty segments get no lead: there is nothing to hold.
    """
    synced, end = [], 0
    for segment in segments:
        lead = max(0, round(segment.anchor_s * FPS) - end) if segment.frames else 0
        synced.append(replace(segment, lead_frames=lead))
        end += lead + segment.frames
    return tuple(synced), end


def _lay_out(scenes, flash_s):
    """Sync, then fit each scene to its video length and lay the scenes end to end in order."""
    laid_out, offset = [], 0.0
    for scene in scenes:
        segments, frames = _sync(scene.segments)
        video_s = frames / FPS
        scene = replace(
            scene,
            segments=segments,
            video_s=video_s,
            fit=fit(video_s, scene.audio_s),
            offset_s=offset,
        )
        laid_out.append(scene)
        offset += scene.scene_s
    return Plan(tuple(laid_out), flash_s)
