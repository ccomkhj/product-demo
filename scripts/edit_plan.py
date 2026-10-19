"""Pure edit planner for compose: demo spec + timeline + durations -> plan (stdlib only).

The plan lists every scene in spec order with its trim window in raw-video
seconds, its narration length, how it is fitted to that narration, and where it
lands in the final video. A scene is cut into segments at its beat markers; each
segment after the first starts when the narration reaches its action's ``at``
phrase, holding the previous frame until then. Nothing here runs ffmpeg: the
runner measures the decimated segment lengths and feeds them back through
``Plan.with_video_lengths``.
"""

import json
import math
import re
from dataclasses import dataclass, replace
from enum import StrEnum

from demo_spec import at_positions

MAX_SPEED = 1.5
FPS = 30  # the final video's frame rate; every scene is cut on a whole frame


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
    segment). frames: its length (trimmed at first, decimated once measured).
    lead_frames: how long its first frame is held before it plays, so that it
    starts on its anchor (or right after the previous segment, if that runs late).
    """

    trim_start: float
    trim_end: float
    anchor_s: float
    frames: int
    lead_frames: int = 0


@dataclass(frozen=True)
class ScenePlan:
    """One scene of the edit plan.

    trim_start/trim_end: the scene's window in raw-video seconds.
    segments: the scene cut at its beat markers, synced to their ``at`` phrases.
    audio_s: narration length. video_s: the video length the fit was worked out
    from: the segments' frames plus their leads (trimmed lengths at first,
    decimated lengths once measured).
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
class Plan:
    """The scenes in spec order, laid end to end."""

    scenes: tuple

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
            sentences = split_sentences(scene.narration)
            total_chars = sum(len(s) for s in sentences)
            done = 0
            for sentence in sentences:
                start = min(scene.offset_s + scene.audio_s * done / total_chars, scene.end_s)
                done += len(sentence)
                end = min(scene.offset_s + scene.audio_s * done / total_chars, scene.end_s)
                cues.append(Cue(scene.id, start, end, sentence))
        return tuple(cues)

    def with_video_lengths(self, video_lengths):
        """Re-fit every scene with measured (decimated) video lengths, keyed by scene id.

        A scene's value is the list of its segments' lengths in seconds, or a single
        number for a scene with one segment.
        """
        scenes = []
        for scene in self.scenes:
            lengths = video_lengths[scene.id]
            if isinstance(lengths, (int, float)):
                lengths = [lengths]
            if len(lengths) != len(scene.segments):
                raise PlanError(f"scene '{scene.id}' has {len(scene.segments)} segments but "
                                f"{len(lengths)} measured lengths")
            segments = tuple(replace(segment, frames=_frames(seconds))
                             for segment, seconds in zip(scene.segments, lengths))
            scenes.append(replace(scene, segments=segments))
        return _lay_out(scenes)


# Words that end in a full stop without ending the sentence (compared lower-case,
# without the stop). Dotted forms such as "e.g." and "U.S." and single-letter
# initials are recognised without being listed.
ABBREVIATIONS = frozenset({
    "dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st", "mt", "vs", "etc", "approx",
    "fig", "inc", "ltd", "co", "corp", "dept",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
})

_BOUNDARY = re.compile(r"[.!?]+[\"')\]]*\s+")


def split_sentences(text):
    """Split narration into sentences without breaking after abbreviations like "e.g." or "Dr."."""
    text = " ".join(text.split())
    sentences, start = [], 0
    for match in _BOUNDARY.finditer(text):
        candidate = text[start:match.end()].strip()
        if match.group().startswith(".") and _ends_with_abbreviation(candidate):
            continue
        sentences.append(candidate)
        start = match.end()
    if text[start:].strip():
        sentences.append(text[start:].strip())
    return sentences


def narration_time(narration, audio_s, position):
    """When the voice reaches character ``position`` of the whitespace-collapsed narration.

    Uses the same character share as the subtitle cues (sentences only, not the
    spaces between them), so an action anchored to a phrase starts with its cue.
    """
    text = " ".join(narration.split())
    sentences = split_sentences(narration)
    total = sum(len(s) for s in sentences)
    done, cursor = 0, 0
    for sentence in sentences:
        start = text.find(sentence, cursor)
        if position < start + len(sentence):
            done += max(0, position - start)
            break
        done += len(sentence)
        cursor = start + len(sentence)
    return audio_s * done / total


def _ends_with_abbreviation(candidate):
    word = candidate.rstrip("\"')]").split(" ")[-1].lstrip("\"'([")
    stem = word[:-1] if word.endswith(".") else word
    if "." in stem:  # e.g. / i.e. / U.S.
        return True
    if len(stem) == 1 and stem.isalpha():  # an initial, as in "J. Smith"
        return True
    return stem.lower() in ABBREVIATIONS


def sync_ms(timeline):
    """The ``sync_ms`` of the last ``rec_start``: when its sync flash was painted, or None.

    When it is set, compose must find the flash in the raw video and pass its time
    to ``build_plan`` as ``flash_s``.
    """
    rec_starts = [e for e in timeline if e.get("event") == "rec_start"]
    return rec_starts[-1].get("sync_ms") if rec_starts else None


def build_plan(spec, timeline, durations, flash_s=None):
    """Build the edit plan from a validated demo spec, timeline events and durations.

    ``flash_s`` is where the sync flash appears in the raw video, needed when the
    last ``rec_start`` has a ``sync_ms``. Each scene is first fitted from its
    trimmed segment lengths; call ``Plan.with_video_lengths`` once the decimated
    lengths are measured.
    """
    windows = _scene_windows(timeline, {scene["id"] for scene in spec["scenes"]}, flash_s)
    scenes = []
    for scene in spec["scenes"]:
        scene_id = scene["id"]
        if scene_id not in windows:
            raise PlanError(f"scene '{scene_id}' has no start marker in the timeline")
        audio_s = _duration(durations, scene_id)
        trim_start, trim_end, beats = windows[scene_id]
        anchors = [narration_time(scene["narration"], audio_s, position)
                   for position in at_positions(scene["narration"], scene.get("actions", []))
                   if position is not None]
        if len(beats) != len(anchors):
            raise PlanError(f"scene '{scene_id}' has {len(beats)} beat markers but "
                            f"{len(anchors)} actions with an 'at' phrase; mark a beat right "
                            f"before each such action")
        bounds = [trim_start, *beats, trim_end]
        segments = tuple(Segment(start, end, anchor, _frames(end - start))
                         for start, end, anchor in zip(bounds, bounds[1:], [0.0, *anchors]))
        scenes.append(ScenePlan(
            id=scene_id, title=scene.get("title") or scene_id, narration=scene["narration"],
            trim_start=trim_start, trim_end=trim_end, audio_s=audio_s, video_s=0.0,
            fit=fit(0.0, audio_s), offset_s=0.0, segments=segments,
        ))
    return _lay_out(scenes)


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


def _scene_windows(timeline, spec_ids, flash_s=None):
    """Return {scene id: (start, end, beats)} in raw-video seconds, validating the timeline.

    Every timeline scene must be one of ``spec_ids``; that is checked before its
    markers are paired, so a stray scene is reported as such. Only the events after the last ``rec_start`` count: each re-recording appends
    a new one, so earlier takes in the file are ignored. Raw-video time 0 is the
    sync flash's wall-clock time minus ``flash_s`` when the ``rec_start`` has a
    ``sync_ms``, and the ``rec_start`` itself otherwise.
    """
    rec_starts = [i for i, e in enumerate(timeline) if e.get("event") == "rec_start"]
    if not rec_starts:
        raise PlanError("timeline has no rec_start marker; run `mark.py TIMELINE rec_start` "
                        "right after starting the screencast")
    rec_start = timeline[rec_starts[-1]]
    origin_ms = _t_ms(rec_start)
    if rec_start.get("sync_ms") is not None:
        if flash_s is None:
            raise PlanError("rec_start has a sync_ms but the sync flash's time in the raw "
                            "video was not given")
        sync = rec_start["sync_ms"]
        if not isinstance(sync, (int, float)) or isinstance(sync, bool):
            raise PlanError(f"rec_start has a non-numeric sync_ms {sync!r}")
        origin_ms = sync - flash_s * 1000

    marks, beats = {}, {}
    for event in timeline[rec_starts[-1] + 1:]:
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
            raise PlanError(f"scene '{scene_id}' has zero length: end equals start "
                            f"(t_ms={start_ms})")
        if end_ms < start_ms:
            raise PlanError(
                f"scene '{scene_id}' ends (t_ms={end_ms}) before it starts (t_ms={start_ms})"
            )
        scene_beats = sorted(beats.get(scene_id, []))
        if any(not start_ms <= beat_ms <= end_ms for beat_ms in scene_beats):
            raise PlanError(f"scene '{scene_id}' has a beat marker outside its start and end")
        windows[scene_id] = ((start_ms - origin_ms) / 1000, (end_ms - origin_ms) / 1000,
                             tuple((beat_ms - origin_ms) / 1000 for beat_ms in scene_beats))
    return windows


def _duration(durations, scene_id):
    """The scene's narration length in seconds; a positive number or a PlanError."""
    if scene_id not in durations:
        raise PlanError(f"scene '{scene_id}' is missing from the durations file")
    seconds = durations[scene_id]
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        raise PlanError(f"scene '{scene_id}' has a non-numeric duration {seconds!r} "
                        f"in the durations file; re-run tts.py")
    if not math.isfinite(seconds) or seconds <= 0:
        raise PlanError(f"scene '{scene_id}' has duration {seconds!r}; it must be positive "
                        f"(re-run tts.py)")
    return float(seconds)


def _t_ms(event):
    t_ms = event.get("t_ms")
    if not isinstance(t_ms, (int, float)) or isinstance(t_ms, bool):
        raise PlanError(f"timeline event {event!r} has no numeric t_ms")
    return t_ms


def _frames(seconds):
    return max(0, round(seconds * FPS))


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


def _lay_out(scenes):
    """Sync, then fit each scene to its video length and lay the scenes end to end in order."""
    laid_out, offset = [], 0.0
    for scene in scenes:
        segments, frames = _sync(scene.segments)
        video_s = frames / FPS
        scene = replace(scene, segments=segments, video_s=video_s,
                        fit=fit(video_s, scene.audio_s), offset_s=offset)
        laid_out.append(scene)
        offset += scene.scene_s
    return Plan(tuple(laid_out))
