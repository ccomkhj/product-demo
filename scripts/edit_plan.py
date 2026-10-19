"""Pure edit planner for compose: demo spec + timeline + durations -> plan (stdlib only).

The plan lists every scene in spec order with its trim window in raw-video
seconds, its narration length, how it is fitted to that narration, and where it
lands in the final video. Nothing here runs ffmpeg: the runner measures the
decimated video lengths and feeds them back through ``Plan.with_video_lengths``.
"""

import json
import math
import re
from dataclasses import dataclass

MAX_SPEED = 1.5
FPS = 30  # the final video's frame rate; every scene is cut on a whole frame


class PlanError(ValueError):
    """Raised when the timeline, durations and demo spec don't line up."""


@dataclass(frozen=True)
class Fit:
    """How one scene's video is fitted to its narration.

    strategy: "hold" (freeze the last frame for ``hold_s``), "speedup" (play at
    ``speed``x so it lasts exactly as long as the audio) or "pad" (play at
    MAX_SPEED and pad the audio with ``pad_s`` of silence).
    """

    strategy: str
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
        return Fit("hold", 1.0, audio_s - video_s, 0.0, audio_s)
    if video_s <= MAX_SPEED * audio_s:
        return Fit("speedup", video_s / audio_s, 0.0, 0.0, audio_s)
    scene_s = video_s / MAX_SPEED
    return Fit("pad", MAX_SPEED, 0.0, scene_s - audio_s, scene_s)


@dataclass(frozen=True)
class ScenePlan:
    """One scene of the edit plan.

    trim_start/trim_end: the scene's window in raw-video seconds.
    audio_s: narration length. video_s: the video length the fit was worked out
    from (the trimmed length at first, the decimated length once measured).
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
        """Re-fit every scene with measured (decimated) video lengths, keyed by scene id."""
        drafts = [dict(_draft_fields(scene), video_s=float(video_lengths[scene.id]))
                  for scene in self.scenes]
        return _assemble(drafts)


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


def _ends_with_abbreviation(candidate):
    word = candidate.rstrip("\"')]").split(" ")[-1].lstrip("\"'([")
    stem = word[:-1] if word.endswith(".") else word
    if "." in stem:  # e.g. / i.e. / U.S.
        return True
    if len(stem) == 1 and stem.isalpha():  # an initial, as in "J. Smith"
        return True
    return stem.lower() in ABBREVIATIONS


def build_plan(spec, timeline, durations):
    """Build the edit plan from a validated demo spec, timeline events and durations.

    Each scene is first fitted from its trimmed length; call
    ``Plan.with_video_lengths`` once the decimated lengths are measured.
    """
    windows = _scene_windows(timeline, {scene["id"] for scene in spec["scenes"]})
    drafts = []
    for scene in spec["scenes"]:
        scene_id = scene["id"]
        if scene_id not in windows:
            raise PlanError(f"scene '{scene_id}' has no start marker in the timeline")
        audio_s = _duration(durations, scene_id)
        trim_start, trim_end = windows[scene_id]
        drafts.append({
            "id": scene_id,
            "title": scene.get("title") or scene_id,
            "narration": scene["narration"],
            "trim_start": trim_start,
            "trim_end": trim_end,
            "audio_s": audio_s,
            "video_s": trim_end - trim_start,
        })
    return _assemble(drafts)


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


def _scene_windows(timeline, spec_ids):
    """Return {scene id: (start, end)} in raw-video seconds, validating the timeline.

    Every timeline scene must be one of ``spec_ids``; that is checked before its
    markers are paired, so a stray scene is reported as such. Only the events after the last ``rec_start`` count: each re-recording appends
    a new one, so earlier takes in the file are ignored.
    """
    rec_starts = [i for i, e in enumerate(timeline) if e.get("event") == "rec_start"]
    if not rec_starts:
        raise PlanError("timeline has no rec_start marker; run `mark.py TIMELINE rec_start` "
                        "right after starting the screencast")
    origin_ms = _t_ms(timeline[rec_starts[-1]])

    marks = {}
    for event in timeline[rec_starts[-1] + 1:]:
        kind = event.get("event")
        if kind not in ("start", "end"):
            raise PlanError(f"timeline event {event!r} has unknown event type {kind!r}")
        scene_id = event.get("scene")
        if not scene_id:
            raise PlanError(f"timeline event {event!r} has no scene id")
        scene_marks = marks.setdefault(scene_id, {})
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
        windows[scene_id] = ((start_ms - origin_ms) / 1000, (end_ms - origin_ms) / 1000)
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


def _draft_fields(scene):
    return {
        "id": scene.id,
        "title": scene.title,
        "narration": scene.narration,
        "trim_start": scene.trim_start,
        "trim_end": scene.trim_end,
        "audio_s": scene.audio_s,
        "video_s": scene.video_s,
    }


def _assemble(drafts):
    """Fit each draft scene and lay the scenes end to end in order."""
    scenes = []
    offset = 0.0
    for draft in drafts:
        scene_fit = fit(draft["video_s"], draft["audio_s"])
        scene = ScenePlan(**draft, fit=scene_fit, offset_s=offset)
        scenes.append(scene)
        offset += scene.scene_s
    return Plan(tuple(scenes))
