"""Post plan: post recipe + source length + cues + edit map -> plan (stdlib only, no ffmpeg).

The post recipe (``post.json`` in the demo folder) has one optional key per operation;
an absent key means the operation is off. ``build_post_plan`` validates it, builds the
time map (source time -> output time: trim and cuts, then speed, then the title card),
retimes the cues and lists the steps in the fixed step order (see ``STEPS``), each with
its run output line and, for a video step, its ffmpeg command builder.

Every time in the recipe is seconds in the source video, or a scene anchor
``{"scene": id, "at": phrase, "offset_s": s}`` (``at`` and ``offset_s`` optional)
resolved through the edit map.

Adding an operation: a validator in ``VALIDATORS`` (or ``EXPORT_VALIDATORS``) and a step
builder in its ``STEPS`` slot; a video step names its command builder (``Step.builder``),
any other step needs a runner in postprocess.py. The SVGs it draws (cards, callouts, watermark text, contact
sheet) live in post_draw.py; .srt reading and writing in subtitles.py.
"""

import math
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from avatar import CORNERS, Avatar, geometry
from edit_plan import FPS
from post_draw import callout_svg, watermark_svg
from subtitles import Cue

MIN_SPEED, MAX_SPEED = 0.5, 2.0
CARD_S, MIN_CARD_S, MAX_CARD_S = 3.0, 1.0, 10.0  # card length: default, range
CARD_FADE_S = 0.5  # fade at each side of a card and of the demo next to it
CARD_BACKGROUND, CARD_COLOR = "#111111", "#ffffff"
FADE_S = 0.03  # audio fade at each side of every cut, against clicks and pops
CONTACT_FRAMES, MAX_CONTACT_FRAMES, MAX_COLUMNS = 12, 60, 8
GIF_WIDTH, MIN_GIF_WIDTH, MAX_GIF_WIDTH = 640, 16, 1920
GIF_FPS, MAX_GIF_FPS = 10, 30
GIF_WARN_MB = 10
# Size cap: kept below the cap by SIZE_MARGIN for the container; the video bitrate may not
# drop below MIN_BPP bits per pixel per frame (readable UI text), else the resolution
# steps down the ladder (never up, never below its last rung).
SIZE_MARGIN, MIN_BPP, SIZE_AUDIO_KBPS = 0.95, 0.015, 64
SIZE_HEIGHTS = (1080, 720, 540, 360)
X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
AAC = ["-c:a", "aac", "-b:a", "192k"]
EDGES = ("bottom", "top")
BAR_COLOR, BAR_THICKNESS, MAX_THICKNESS = "#4da3ff", 6, 40
WATERMARK_SIZE, WATERMARK_OPACITY = 0.06, 0.8  # share of the video's height; 0-1
MIN_WATERMARK, MAX_WATERMARK = 0.02, 0.3
CALLOUT_TYPES = ("arrow", "text")
CALLOUT_FADE_S = 0.25
SUBTITLE_BAND = 0.2  # burned-in subtitles sit in the bottom 20% of the frame
AUDIO_FORMAT = "aformat=sample_rates=48000:channel_layouts=stereo"
CARD_KEYS = {"title": ("title", "subtitle", "logo"), "closing": ("text", "url")}
CLICK_SOUND = Path(__file__).resolve().parent.parent / "assets" / "click.wav"
CLICK_TYPES = ("click", "dblclick", "type")  # a type action clicks its field first
GLIDE_S = 0.6  # the overlay's cursor glide (assets/overlay.js GLIDE_MS): the click lands after it
CLICK_LEVEL = 0.5
MUSIC_LEVEL, MUSIC_DUCK, MAX_DUCK, MUSIC_FADE_S = 0.2, 8, 20, 2.0
YOUTUBE_MIN_CHAPTERS, YOUTUBE_MIN_CHAPTER_S = 3, 10.0
# ISO 639-1 -> ISO 639-2/T, the language tag mp4 subtitle tracks carry. A recipe may also
# give the three-letter code itself.
ISO639_2 = {
    "ar": "ara", "bg": "bul", "bn": "ben", "ca": "cat", "cs": "ces", "da": "dan",
    "de": "deu", "el": "ell", "en": "eng", "es": "spa", "et": "est", "fa": "fas",
    "fi": "fin", "fr": "fra", "he": "heb", "hi": "hin", "hr": "hrv", "hu": "hun",
    "id": "ind", "it": "ita", "ja": "jpn", "ko": "kor", "lt": "lit", "lv": "lav",
    "ms": "msa", "nl": "nld", "no": "nor", "pl": "pol", "pt": "por", "ro": "ron",
    "ru": "rus", "sk": "slk", "sl": "slv", "sr": "srp", "sv": "swe", "sw": "swa",
    "ta": "tam", "th": "tha", "tl": "tgl", "tr": "tur", "uk": "ukr", "ur": "urd",
    "vi": "vie", "zh": "zho",
}  # fmt: skip


class RecipeError(ValueError):
    """Raised when a post recipe is malformed or a time in it cannot be placed."""


@dataclass(frozen=True)
class Chapter:
    """One chapter of the output, in output seconds."""

    start_s: float
    end_s: float
    title: str


@dataclass(frozen=True)
class Step:
    """One operation of the plan.

    op: its name (a ``STEPS`` key). line: its line of the run output. params: what its
    command builder or runner needs. export: True when it writes an extra file from the
    finished video instead of changing the video. warnings: its warnings, added to the
    plan's. builder: a video step's ffmpeg command builder, ``(src, out, **params)``.
    """

    op: str
    line: str
    params: dict = field(default_factory=dict)
    export: bool = False
    warnings: tuple = ()
    builder: object = None

    def cmd(self, src, out, **more):
        """So the runner can add what only it knows (rendered images) to the planned params."""
        return self.builder(src, out, **{**self.params, **more})


@dataclass(frozen=True)
class Layer:
    """One image overlaid on the video (watermark, callout).

    image: the PNG; with ``svg`` set it is the file name the runner renders the SVG to
    (rsvg-convert, ADR-0010). x, y: ffmpeg overlay expressions. height: scale the image
    to this many pixels. start_s / end_s: its window (None: the whole video), with a
    ``fade_s`` fade in and out.
    """

    image: str
    svg: str | None = None
    x: str = "0"
    y: str = "0"
    height: int | None = None
    opacity: float = 1.0
    start_s: float | None = None
    end_s: float | None = None
    fade_s: float = 0.0


# --- time map ------------------------------------------------------------------------


@dataclass(frozen=True)
class TimeMap:
    """Source time -> output time: the kept ranges laid end to end, then the speed,
    then ``lead_s`` (the title card) in front and ``tail_s`` (the closing card) after."""

    kept: tuple  # ((start_s, end_s), ...) in source seconds, in order
    source_s: float  # the source's length
    speed: float = 1.0
    lead_s: float = 0.0
    tail_s: float = 0.0

    @property
    def demo_span(self):
        """(start, end) of the demo itself in the output: the cards excluded."""
        return self.lead_s, self.lead_s + sum(end - start for start, end in self.kept) / self.speed

    @property
    def total_s(self):
        return self.demo_span[1] + self.tail_s

    @property
    def removed(self):
        """The cut source ranges (trimmed start and end included), in order."""
        bounds = [0.0, *(t for kept in self.kept for t in kept), self.source_s]
        return tuple((a, b) for a, b in zip(bounds[::2], bounds[1::2]) if b - a > 1e-9)

    def output_s(self, source_s):
        """Where source time ``source_s`` lands in the output; RecipeError in a cut."""
        done = 0.0
        for start, end in self.kept:
            if start - 1e-9 <= source_s <= end + 1e-9:
                return self.lead_s + (done + min(max(source_s, start), end) - start) / self.speed
            done += end - start
        raise RecipeError(f"{source_s:.3f}s is inside a cut")

    def range(self, start_s, end_s):
        """The output range of source range [start_s, end_s] with its cut parts removed:
        (start, end, clipped), or None when it is all cut. ``clipped``: part of it was cut."""
        parts = [
            (max(start, start_s), min(end, end_s))
            for start, end in self.kept
            if min(end, end_s) > max(start, start_s)
        ]
        if not parts:
            return None
        kept_s = sum(end - start for start, end in parts)
        return (
            self.output_s(parts[0][0]),
            self.output_s(parts[-1][1]),
            kept_s < end_s - start_s - 1e-6,
        )


@dataclass(frozen=True)
class PostPlan:
    """The post plan: time map, retimed cues, warnings and steps in the fixed order."""

    time_map: TimeMap
    cues: tuple
    warnings: tuple
    steps: tuple
    edit_map: dict | None = None
    cards: dict = field(default_factory=dict)  # resolve_cards: "title" / "closing", if on
    translations: dict = field(default_factory=dict)  # language code -> retimed cues
    chapters: tuple = ()  # Chapter, in order, covering the whole output
    frame: tuple | None = None  # (width, height) of the source video
    bubble: tuple | None = None  # (corner, size) of the demo's presenter bubble

    @property
    def source_s(self):
        return self.time_map.source_s

    @property
    def total_s(self):
        return self.time_map.total_s

    @property
    def demo_span(self):
        return self.time_map.demo_span

    def output_s(self, value, where="time"):
        """Output seconds of a recipe time (source seconds or a scene anchor)."""
        seconds = source_time(value, self.source_s, self.edit_map, where)
        try:
            return self.time_map.output_s(seconds)
        except RecipeError as exc:
            raise RecipeError(f"{where}: {exc}") from None


def build_post_plan(
    recipe, source_s, cues, edit_map, demo=None, frame=None, bubble=None, translations=None
):
    """Validate ``recipe`` and build the plan; raise RecipeError before any ffmpeg work.

    source_s: the source video's length. cues: its subtitle cues (Cue). edit_map: the
    parsed ``<slug>.editmap.json``, or None (then scene anchors are an error). demo: the
    card defaults ``{"title", "url"}`` (see ``card_defaults``), or None. frame: the
    source's (width, height), needed by overlays. bubble: the presenter bubble's
    (corner, size) from the demo spec, or None without one. translations: {language
    code: its cues in source time} for the recipe's ``translations``.
    """
    validate_recipe(recipe)
    time_map = _time_map(recipe, source_s, edit_map)
    cards = resolve_cards(recipe.get("cards"), demo)
    time_map = replace(
        time_map,
        lead_s=cards["title"]["seconds"] if "title" in cards else 0.0,
        tail_s=cards["closing"]["seconds"] if "closing" in cards else 0.0,
    )
    retimed, warnings = _retime(cues, time_map)
    translated = _translations(recipe.get("translations", ()), cues, translations, time_map)
    if translated and subtitles_burned(edit_map):
        warnings.append(
            "translated subtitle tracks over burned-in subtitles: viewers see two at once "
            "(no edit map says otherwise); re-compose with --no-burn-subs for clean tracks"
        )
    chapter_list = _chapters(recipe, time_map, edit_map, "title" in cards)
    warnings += youtube_warnings(chapter_list)
    plan = PostPlan(
        time_map,
        retimed,
        tuple(warnings),
        (),
        edit_map,
        cards,
        translated,
        chapter_list,
        frame,
        bubble,
    )
    steps = tuple(step for build in STEPS.values() if (step := build(recipe, plan)))
    return replace(
        plan, steps=steps, warnings=(*plan.warnings, *(w for step in steps for w in step.warnings))
    )


def subtitles_burned(edit_map):
    """Whether the source has burned-in subtitles: yes unless its edit map says not."""
    return (edit_map or {}).get("subtitles_burned", True)


def _retime(cues, time_map):
    """(cues placed in the output, warnings for cues a cut splits); all-cut cues dropped."""
    warnings, retimed = [], []
    for number, cue in enumerate(cues, start=1):
        placed = time_map.range(cue.start_s, cue.end_s)
        if placed is None:
            continue
        start, end, clipped = placed
        if clipped:
            cuts = ", ".join(
                f"{a:.3f}s-{b:.3f}s"
                for a, b in time_map.removed
                if b > cue.start_s and a < cue.end_s
            )
            warnings.append(f'cut at {cuts} splits cue {number} "{cue.text}"')
        retimed.append(Cue(round(start, 3), round(end, 3), cue.text))
    return tuple(retimed), warnings


def _translations(languages, cues, translations, time_map):
    """{lang: retimed cues}; each translation must have the original's cues and timings."""
    retimed = {}
    for lang in languages:
        name = f"{lang}.srt"
        if lang not in (translations or {}):
            raise RecipeError(f"translations: {name} is missing; write it first")
        translated = translations[lang]
        if len(translated) != len(cues):
            raise RecipeError(
                f"translations: {name} has {len(translated)} cues, the original has "
                f"{len(cues)}; keep one translated cue per original cue"
            )
        for number, (ours, theirs) in enumerate(zip(translated, cues), start=1):
            if abs(ours.start_s - theirs.start_s) > 1e-3 or abs(ours.end_s - theirs.end_s) > 1e-3:
                raise RecipeError(
                    f"translations: {name} cue {number} is at {ours.start_s:.3f}s-"
                    f"{ours.end_s:.3f}s, the original at {theirs.start_s:.3f}s-"
                    f"{theirs.end_s:.3f}s; keep the original timings"
                )
        retimed[lang] = _retime(translated, time_map)[0]
    return retimed


def source_time(value, source_s, edit_map, where="time"):
    """Source seconds of a recipe time: seconds, or a scene anchor through the edit map."""
    if not isinstance(value, dict):
        seconds = float(value)
    else:
        seconds = _anchor_s(value, edit_map, where) + value.get("offset_s", 0)
    if seconds < 0:
        raise RecipeError(f"{where} is before the start of the source ({seconds:.3f}s)")
    if seconds > source_s + 1e-9:
        raise RecipeError(
            f"{where} is past the end of the source ({seconds:.3f}s of {source_s:.3f}s)"
        )
    return seconds


def _anchor_s(ref, edit_map, where):
    if edit_map is None:
        raise RecipeError(
            f"{where} is anchored to a scene, which needs the edit map; "
            f"re-run compose to write <slug>.editmap.json"
        )
    scenes = {scene["id"]: scene for scene in edit_map["scenes"]}
    if ref["scene"] not in scenes:
        raise RecipeError(f"{where}: unknown scene '{ref['scene']}' (scenes: {', '.join(scenes)})")
    scene = scenes[ref["scene"]]
    if "at" not in ref:
        return scene["start_s"]
    starts = {action["at"]: action["start_s"] for action in scene["actions"] if "at" in action}
    if ref["at"] not in starts:
        known = ", ".join(f"'{phrase}'" for phrase in starts) or "none"
        raise RecipeError(
            f"{where}: scene '{scene['id']}' has no action at '{ref['at']}' (at phrases: {known})"
        )
    return starts[ref["at"]]


def _time_map(recipe, source_s, edit_map):
    trim = recipe.get("trim", {})

    def at(key, default):
        if key not in trim:
            return default
        return _frame(source_time(trim[key], source_s, edit_map, f"trim.{key}"))

    start, end = at("start", 0.0), at("end", _frame(source_s))
    if end <= start:
        raise RecipeError("trim.end must be after trim.start")
    removed = [(0.0, start), (end, math.inf)]
    for index, cut in enumerate(trim.get("cuts", [])):
        where = f"trim.cuts[{index}]"
        cut_start = _frame(source_time(cut["from"], source_s, edit_map, f"{where}.from"))
        cut_end = _frame(source_time(cut["to"], source_s, edit_map, f"{where}.to"))
        if cut_end <= cut_start:
            raise RecipeError(f"{where} must end after it starts")
        removed.append((cut_start, cut_end))
    kept, cursor = [], 0.0
    for cut_start, cut_end in sorted(removed):
        if cut_start > cursor:
            kept.append((cursor, min(cut_start, source_s)))
        cursor = max(cursor, cut_end)
    kept = [(a, b) for a, b in kept if b > a]
    if not kept:
        raise RecipeError("the trim and cuts leave nothing left of the source")
    return TimeMap(tuple(kept), source_s, recipe.get("speed", 1.0))


def _frame(seconds):
    return round(seconds * FPS) / FPS


# --- click sounds and music (#20) ------------------------------------------------------


def _check_click_sounds(value, where):
    _check_keys(value, where, ("file", "level"))
    _check_file(value, where)
    _check_level(value, where, "level")


def _check_music(value, where):
    _check_keys(value, where, ("file", "level", "duck", "fade_out_s"))
    _check_file(value, where, required=True)
    _check_level(value, where, "level")
    if "duck" in value and (not _is_number(value["duck"]) or not 1 <= value["duck"] <= MAX_DUCK):
        raise RecipeError(
            f"{where}.duck must be a number from 1 (no ducking) to {MAX_DUCK}, "
            f"got {value['duck']!r}"
        )
    if "fade_out_s" in value and (not _is_number(value["fade_out_s"]) or value["fade_out_s"] < 0):
        raise RecipeError(f"{where}.fade_out_s must be a number of seconds (>= 0)")


def _check_file(value, where, required=False):
    name = value.get("file")
    if (required or "file" in value) and (not isinstance(name, str) or not name.strip()):
        raise RecipeError(f"{where}.file must be an audio file name next to the recipe")
    if isinstance(name, str) and re.match(r"[a-z][a-z0-9+.-]*://", name, re.IGNORECASE):
        raise RecipeError(
            f"{where}.file must be a local file: audio is never downloaded; "
            f"use a file you have the rights to"
        )


def _check_level(value, where, key):
    if key in value and (not _is_number(value[key]) or not 0 <= value[key] <= 1):
        raise RecipeError(f"{where}.{key} must be a number from 0 to 1, got {value[key]!r}")


def _click_sounds_step(recipe, plan):
    if "click_sounds" not in recipe:
        return None
    clicks = recipe["click_sounds"]
    if plan.edit_map is None:
        raise RecipeError(
            "click_sounds needs the click times in the edit map; "
            "re-run compose to write <slug>.editmap.json"
        )
    times, unplaced = [], []
    for scene in plan.edit_map["scenes"]:
        for action in scene["actions"]:
            if action["type"] not in CLICK_TYPES:
                continue
            what = f"scene '{scene['id']}' action {action['index']}"
            if "start_s" not in action:
                unplaced.append(
                    f"{what} ({action['type']}): start unknown "
                    f"(not the scene's first action and no at phrase)"
                )
                continue
            click_s = action["start_s"] + GLIDE_S
            try:
                times.append(round(plan.time_map.output_s(click_s), 3))
            except RecipeError:
                unplaced.append(f"{what} ({action['type']} at {click_s:.3f}s): inside a cut")
    line = f"click_sounds\t{len(times)} clicks"
    if unplaced:
        line += f"\t{len(unplaced)} unplaceable"
    return Step(
        "click_sounds",
        line,
        {
            "times": tuple(times),
            "sound": clicks.get("file", str(CLICK_SOUND)),
            "level": clicks.get("level", CLICK_LEVEL),
        },
        warnings=tuple(f"no click sound for {item}" for item in unplaced),
        builder=click_sounds_cmd,
    )


def click_sounds_cmd(src, out, times, sound, level):
    """Mix ``sound`` at ``level`` into the audio at each of ``times``; copy the picture."""
    if not times:
        return _filter_cmd(src, out, "[0:a]anull[a]", video="0:v")
    count = len(times)
    graph = [f"[1:a]volume={level:.3f},asplit={count}{''.join(f'[c{i}]' for i in range(count))}"]
    graph += [f"[c{i}]adelay={round(t * 1000)}:all=1[d{i}]" for i, t in enumerate(times)]
    graph.append(
        f"[0:a]{''.join(f'[d{i}]' for i in range(count))}"
        f"amix=inputs={count + 1}:duration=first:normalize=0[a]"
    )
    return _filter_cmd(src, out, ";".join(graph), ["-i", str(sound)], video="0:v")


def _music_step(recipe, plan):
    if "music" not in recipe:
        return None
    music = recipe["music"]
    params = {
        "file": music["file"],
        "level": music.get("level", MUSIC_LEVEL),
        "duck": music.get("duck", MUSIC_DUCK),
        "fade_out_s": float(music.get("fade_out_s", MUSIC_FADE_S)),
        "total_s": round(plan.total_s, 3),
    }
    if params["fade_out_s"] > params["total_s"]:
        raise RecipeError(
            f"music.fade_out_s {params['fade_out_s']:g} is longer than the "
            f"{params['total_s']:.3f}s video"
        )
    return Step(
        "music",
        f"music\t{Path(params['file']).name}\tlevel {params['level']:.2f}\t"
        f"duck {params['duck']:g}:1\tfade out {params['fade_out_s']:.2f}s",
        params,
        builder=music_cmd,
    )


def music_cmd(src, out, file, level, duck, fade_out_s, total_s):
    """Loop ``file`` to ``total_s`` at ``level``, fade it out, duck it by sidechain
    compression keyed on the narration (ratio ``duck``), and mix it under; copy the picture."""
    graph = (
        f"[1:a]{AUDIO_FORMAT},volume={level:.3f},atrim=duration={total_s:.3f},"
        f"afade=t=out:st={total_s - fade_out_s:.3f}:d={fade_out_s:.3f}[m];"
        f"[0:a]asplit=2[voice][key];"
        f"[m][key]sidechaincompress=threshold=0.02:ratio={duck:g}:attack=20:release=400[ducked];"
        f"[voice][ducked]amix=inputs=2:duration=first:normalize=0[a]"
    )
    return _filter_cmd(src, out, graph, ["-stream_loop", "-1", "-i", str(file)], video="0:v")


# --- validation ----------------------------------------------------------------------


def validate_recipe(recipe):
    """Check the recipe's shape and ranges (no times placed yet); raise RecipeError."""
    if not isinstance(recipe, dict):
        raise RecipeError("recipe must be a JSON object")
    for key, value in recipe.items():
        if key not in VALIDATORS:
            raise RecipeError(f"unknown recipe key '{key}' (known: {', '.join(VALIDATORS)})")
        VALIDATORS[key](value, key)


def _check_source(value, where):
    if not isinstance(value, str) or not value.strip():
        raise RecipeError(f"{where} must be a file name next to the recipe")


def _check_speed(value, where):
    if not _is_number(value) or not MIN_SPEED <= value <= MAX_SPEED:
        raise RecipeError(
            f"{where} must be a number from {MIN_SPEED} to {MAX_SPEED:g}, got {value!r}"
        )


def _check_trim(value, where):
    _check_keys(value, where, ("start", "end", "cuts"))
    for key in ("start", "end"):
        if key in value:
            check_time(value[key], f"{where}.{key}")
    cuts = value.get("cuts", [])
    if not isinstance(cuts, list):
        raise RecipeError(f'{where}.cuts must be a list of {{"from": t, "to": t}}')
    for index, cut in enumerate(cuts):
        here = f"{where}.cuts[{index}]"
        _check_keys(cut, here, ("from", "to"))
        if set(cut) != {"from", "to"}:
            raise RecipeError(f"{here} needs 'from' and 'to'")
        check_time(cut["from"], f"{here}.from")
        check_time(cut["to"], f"{here}.to")


def _check_exports(value, where):
    _check_keys(value, where, tuple(EXPORT_VALIDATORS))
    for key, export in value.items():
        EXPORT_VALIDATORS[key](export, f"{where}.{key}")


def _check_contact_sheet(value, where):
    _check_keys(value, where, ("columns", "interval_s"))
    if "columns" in value:
        _check_int(value["columns"], f"{where}.columns", 1, MAX_COLUMNS)
    if "interval_s" in value and (not _is_number(value["interval_s"]) or value["interval_s"] <= 0):
        raise RecipeError(f"{where}.interval_s must be a positive number of seconds")


def _check_progress_bar(value, where):
    _check_keys(value, where, ("edge", "color", "thickness"))
    if value.get("edge", "bottom") not in EDGES:
        raise RecipeError(f"{where}.edge must be one of {', '.join(EDGES)}")
    if "color" in value:
        _check_color(value["color"], f"{where}.color")
    if "thickness" in value:
        _check_int(value["thickness"], f"{where}.thickness", 1, MAX_THICKNESS)


def _check_watermark(value, where):
    _check_keys(value, where, ("image", "text", "corner", "size", "opacity"))
    if ("image" in value) == ("text" in value):
        raise RecipeError(f"{where} needs either an 'image' or a 'text'")
    content = value.get("image", value.get("text"))
    if not isinstance(content, str) or not content.strip():
        raise RecipeError(
            f"{where}.{'image' if 'image' in value else 'text'} must be a non-empty string"
        )
    if "corner" in value and value["corner"] not in CORNERS:
        raise RecipeError(f"{where}.corner must be one of {', '.join(CORNERS)}")
    size = value.get("size", WATERMARK_SIZE)
    if not _is_number(size) or not MIN_WATERMARK <= size <= MAX_WATERMARK:
        raise RecipeError(
            f"{where}.size must be a share of the video's height from "
            f"{MIN_WATERMARK} to {MAX_WATERMARK}"
        )
    opacity = value.get("opacity", WATERMARK_OPACITY)
    if not _is_number(opacity) or not 0.1 <= opacity <= 1:
        raise RecipeError(f"{where}.opacity must be a number from 0.1 to 1")


def _check_callouts(value, where):
    if not isinstance(value, list):
        raise RecipeError(f"{where} must be a list of arrow and text callouts")
    for index, callout in enumerate(value):
        here = f"{where}[{index}]"
        kind = callout.get("type") if isinstance(callout, dict) else None
        if kind not in CALLOUT_TYPES:
            raise RecipeError(f"{here}.type must be one of {', '.join(CALLOUT_TYPES)}")
        points = ("from", "to") if kind == "arrow" else ("position",)
        needed = (*points, "start", "end") + (("text",) if kind == "text" else ())
        _check_keys(callout, here, ("type", *needed, "color"))
        for key in needed:
            if key not in callout:
                raise RecipeError(f"{here} needs '{key}'")
        for key in points:
            _check_point(callout[key], f"{here}.{key}")
        if kind == "text" and (not isinstance(callout["text"], str) or not callout["text"].strip()):
            raise RecipeError(f"{here} needs a short 'text'")
        if "color" in callout:
            _check_color(callout["color"], f"{here}.color")
        check_time(callout["start"], f"{here}.start")
        check_time(callout["end"], f"{here}.end")


def _check_point(value, where):
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not all(_is_number(v) and v >= 0 for v in value)
    ):
        raise RecipeError(f"{where} must be [x, y] in video pixels")


def _check_color(value, where):
    """A hex colour: "#rrggbb" or "#rgb"."""
    if not isinstance(value, str) or not re.fullmatch(r"#(?:[0-9a-fA-F]{3}){1,2}", value):
        raise RecipeError(f'{where} must be a hex colour like "#4da3ff" or "#fff", got {value!r}')


def _check_int(value, where, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise RecipeError(f"{where} must be a whole number from {low} to {high}")


def _check_cards(value, where):
    _check_keys(value, where, tuple(CARD_KEYS))
    for name, card in value.items():
        here = f"{where}.{name}"
        _check_keys(card, here, (*CARD_KEYS[name], "seconds", "background", "color"))
        for key in CARD_KEYS[name]:
            if key in card and (not isinstance(card[key], str) or not card[key].strip()):
                raise RecipeError(f"{here}.{key} must be non-empty text")
        seconds = card.get("seconds", CARD_S)
        if not _is_number(seconds) or not MIN_CARD_S <= seconds <= MAX_CARD_S:
            raise RecipeError(
                f"{here}.seconds must be a number from {MIN_CARD_S:g} to "
                f"{MAX_CARD_S:g}, got {seconds!r}"
            )
        for key in ("background", "color"):
            if key in card:
                _check_color(card[key], f"{here}.{key}")


def _check_translations(value, where):
    if not isinstance(value, list) or not all(isinstance(lang, str) for lang in value):
        raise RecipeError(f'{where} must be a list of language codes, e.g. ["ko", "de"]')
    for lang in value:
        if lang not in ISO639_2 and lang not in ISO639_2.values():
            raise RecipeError(
                f"{where}: unknown language code '{lang}' (a two-letter ISO 639-1 code "
                f"like 'ko', or a three-letter ISO 639-2/T code like 'kor')"
            )
        if value.count(lang) > 1:
            raise RecipeError(f"{where} lists '{lang}' twice")


def _check_chapters(value, where):
    if not isinstance(value, bool):
        raise RecipeError(f"{where} must be true or false, got {value!r}")


def _check_size_cap(value, where):
    if not _is_number(value) or value <= 0:
        raise RecipeError(f"{where} must be a positive number of megabytes, got {value!r}")


def _check_gif(value, where):
    _check_keys(value, where, ("from", "to", "width", "fps"))
    for key in ("from", "to"):
        if key in value:
            check_time(value[key], f"{where}.{key}")
    for key, low, high in (("width", MIN_GIF_WIDTH, MAX_GIF_WIDTH), ("fps", 1, MAX_GIF_FPS)):
        if key in value:
            _check_int(value[key], f"{where}.{key}", low, high)


def _check_poster(value, where):
    _check_keys(value, where, ("time",))
    if "time" in value:
        check_time(value["time"], f"{where}.time")


def check_time(value, where):
    """A recipe time: seconds >= 0, or {"scene": id, "at"?: phrase, "offset_s"?: s}."""
    if isinstance(value, dict):
        _check_keys(value, where, ("scene", "at", "offset_s"))
        if not isinstance(value.get("scene"), str):
            raise RecipeError(f"{where} needs a scene id ('scene')")
        if "at" in value and not isinstance(value["at"], str):
            raise RecipeError(f"{where}.at must be an at phrase")
        if "offset_s" in value and not _is_number(value["offset_s"]):
            raise RecipeError(f"{where}.offset_s must be a number of seconds")
    elif not _is_number(value) or value < 0:
        raise RecipeError(
            f"{where} must be seconds (>= 0) or a scene anchor "
            f'{{"scene": id, "at": phrase, "offset_s": s}}'
        )


def _check_keys(value, where, known):
    if not isinstance(value, dict):
        raise RecipeError(f"{where} must be a JSON object")
    for key in value:
        if key not in known:
            raise RecipeError(f"unknown key '{key}' in {where} (known: {', '.join(known)})")


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


# One validator per recipe key, in step order. Later operations fill their slot.
VALIDATORS = {
    "source": _check_source,
    "trim": _check_trim,
    "speed": _check_speed,
    "watermark": _check_watermark,
    "callouts": _check_callouts,
    "progress_bar": _check_progress_bar,
    "cards": _check_cards,
    "translations": _check_translations,
    "chapters": _check_chapters,
    "click_sounds": _check_click_sounds,
    "music": _check_music,
    "exports": _check_exports,
}

EXPORT_VALIDATORS = {
    "size_cap_mb": _check_size_cap,
    "gif": _check_gif,
    "poster": _check_poster,
    "contact_sheet": _check_contact_sheet,
}


# --- steps (pure: recipe + plan in, Step or None out) ----------------------------------


def _trim_step(recipe, plan):
    if "trim" not in recipe:
        return None
    kept = plan.time_map.kept
    kept_s = sum(end - start for start, end in kept)
    ranges = len(plan.time_map.removed)
    return Step(
        "trim",
        f"trim\tremoved {plan.source_s - kept_s:.3f}s in {ranges} ranges\tkept {kept_s:.3f}s",
        {"kept": kept, "source_s": plan.source_s},
        builder=trim_cmd,
    )


def _speed_step(recipe, plan):
    if recipe.get("speed", 1.0) == 1.0:
        return None
    return Step(
        "speed", f"speed\t{recipe['speed']:.2f}x", {"speed": recipe["speed"]}, builder=speed_cmd
    )


def _watermark_step(recipe, plan):
    if "watermark" not in recipe:
        return None
    mark = recipe["watermark"]
    width, height = _frame_of(plan, "watermark")
    # The overlay draws keycaps bottom-right and the Back badge top-left; the bubble
    # defaults to bottom-left. Top-right is free unless the bubble was moved there.
    default = "bottom-left" if plan.bubble and plan.bubble[0] == "top-right" else "top-right"
    corner = mark.get("corner", default)
    size, opacity = mark.get("size", WATERMARK_SIZE), mark.get("opacity", WATERMARK_OPACITY)
    margin = round(height * 0.03)
    if "image" in mark:
        x = f"W-w-{margin}" if corner.endswith("right") else str(margin)
        y = f"H-h-{margin}" if corner.startswith("bottom") else str(margin)
        layer = Layer(mark["image"], x=x, y=y, height=round(size * height), opacity=opacity)
        line = f"watermark\timage {mark['image']}\t{corner}"
    else:
        svg = watermark_svg(
            mark["text"], corner, round(size * height), opacity, margin, width, height
        )
        layer = Layer("watermark.png", svg)
        line = f'watermark\ttext "{mark["text"]}"\t{corner}'
    return Step("watermark", line, {"layers": (layer,)}, builder=overlay_cmd)


def _callouts_step(recipe, plan):
    if not recipe.get("callouts"):
        return None
    width, height = _frame_of(plan, "callouts")
    lead = plan.time_map.lead_s  # overlays are drawn before the title card is added
    layers, parts, warnings = [], [], []
    for index, callout in enumerate(recipe["callouts"]):
        where = f"callouts[{index}]"
        start = plan.output_s(callout["start"], f"{where}.start")
        end = plan.output_s(callout["end"], f"{where}.end")
        if end <= start:
            raise RecipeError(f"{where} must end after it starts")
        for key in ("from", "to", "position"):
            if key in callout and (callout[key][0] > width or callout[key][1] > height):
                raise RecipeError(
                    f"{where}.{key} {callout[key]} is outside the {width}x{height} video"
                )
        svg, box = callout_svg(callout, width, height)
        name = f"{where} ({callout['type']})"
        band_y = round(height * (1 - SUBTITLE_BAND))
        if (
            subtitles_burned(plan.edit_map)
            and box[3] > band_y
            and any(cue.start_s < end and cue.end_s > start for cue in plan.cues)
        ):
            warnings.append(
                f"{name} overlaps the burned-in subtitle band at "
                f"{start:.3f}s-{end:.3f}s; move it above y={band_y}"
            )
        if plan.bubble and _overlap(box, _bubble_box(plan.bubble, width, height)):
            warnings.append(f"{name} overlaps the presenter bubble ({plan.bubble[0]})")
        layers.append(
            Layer(
                f"callout-{index}.png",
                svg,
                start_s=round(start - lead, 3),
                end_s=round(end - lead, 3),
                fade_s=min(CALLOUT_FADE_S, (end - start) / 2),
            )
        )
        parts.append(f"{callout['type']} {start:.3f}s-{end:.3f}s")
    return Step(
        "callouts",
        f"callouts\t{len(layers)}\t{', '.join(parts)}",
        {"layers": tuple(layers)},
        warnings=tuple(warnings),
        builder=overlay_cmd,
    )


def _bubble_box(bubble, width, height):
    geo = geometry(Avatar(None, *bubble), width, height)
    return geo.x, geo.y, geo.x + geo.canvas, geo.y + geo.canvas


def _overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _progress_bar_step(recipe, plan):
    if "progress_bar" not in recipe:
        return None
    bar = recipe["progress_bar"]
    edge, thickness = bar.get("edge", "bottom"), bar.get("thickness", BAR_THICKNESS)
    # Overlays run before the cards are added, so the bar spans the demo, never a card.
    start, end = plan.demo_span
    return Step(
        "progress_bar",
        f"progress_bar\t{edge} {thickness}px\t{start:.3f}s-{end:.3f}s",
        {
            "edge": edge,
            "color": bar.get("color", BAR_COLOR),
            "thickness": thickness,
            "width": _frame_of(plan, "progress_bar")[0],
            "demo_s": end - start,
        },
        builder=progress_bar_cmd,
    )


def _frame_of(plan, where):
    if plan.frame is None:
        raise RecipeError(f"{where} needs the video's size")
    return plan.frame


def _cards_step(recipe, plan):
    if not plan.cards:
        return None
    start, end = plan.demo_span
    line = "".join(f"\t{name} {card['seconds']:.3f}s" for name, card in plan.cards.items())
    return Step(
        "cards", f"cards{line}", {"cards": plan.cards, "demo_s": end - start}, builder=cards_cmd
    )


def _subtitle_tracks_step(recipe, plan):
    if not plan.translations:
        return None
    tracks = ((None, "und", plan.cues),) + tuple(
        (lang, ISO639_2.get(lang, lang), cues) for lang, cues in plan.translations.items()
    )
    names = ", ".join(lang or "original" for lang, _, _ in tracks)
    # export: muxed into the finished video, so no later re-encode drops the tracks
    return Step("subtitle_tracks", f"subtitle_tracks\t{names}", {"tracks": tracks}, export=True)


def _chapters_step(recipe, plan):
    if not plan.chapters:
        return None
    return Step(
        "chapters",
        f"chapters\t{len(plan.chapters)} chapters",
        {"chapters": plan.chapters},
        export=True,
    )


def _size_cap_step(recipe, plan):
    cap_mb = recipe.get("exports", {}).get("size_cap_mb")
    if cap_mb is None:
        return None
    return Step("size_cap", f"size_cap\t{cap_mb:g} MB", {"cap_mb": cap_mb}, export=True)


def _gif_step(recipe, plan):
    gif = recipe.get("exports", {}).get("gif")
    if gif is None:
        return None
    start, end = 0.0, plan.total_s
    if "from" in gif or "to" in gif:
        where = "exports.gif"
        placed = plan.time_map.range(
            source_time(gif.get("from", 0.0), plan.source_s, plan.edit_map, f"{where}.from"),
            source_time(gif.get("to", plan.source_s), plan.source_s, plan.edit_map, f"{where}.to"),
        )
        if placed is None or placed[1] - placed[0] <= 0:
            raise RecipeError(f"{where}: its range is empty or all cut")
        start, end = placed[0], placed[1]
    width, fps = gif.get("width", GIF_WIDTH), gif.get("fps", GIF_FPS)
    return Step(
        "gif",
        f"gif\t{start:.3f}s-{end:.3f}s\t{width}px {fps}fps",
        {"start_s": round(start, 3), "end_s": round(end, 3), "width": width, "fps": fps},
        export=True,
    )


def _poster_step(recipe, plan):
    poster = recipe.get("exports", {}).get("poster")
    if poster is None:
        return None
    if "time" in poster:
        at_s = plan.output_s(poster["time"], "exports.poster.time")
    else:
        scenes = [
            *(plan.edit_map or {}).get("scenes", ()),
            {"start_s": 0.0, "end_s": plan.source_s},
        ]
        placed = next(
            p for scene in scenes if (p := plan.time_map.range(scene["start_s"], scene["end_s"]))
        )
        at_s = (placed[0] + placed[1]) / 2  # the middle of the first scene left after the cards
    at_s = round(min(at_s, plan.total_s - 1 / FPS), 3)
    return Step("poster", f"poster\t{at_s:.3f}s", {"at_s": at_s}, export=True)


def _contact_sheet_step(recipe, plan):
    sheet = recipe.get("exports", {}).get("contact_sheet")
    if sheet is None:
        return None
    total = plan.total_s
    interval = sheet.get("interval_s", total / CONTACT_FRAMES)
    count = math.ceil(total / interval - 1e-9)
    if count > MAX_CONTACT_FRAMES:
        raise RecipeError(
            f"exports.contact_sheet.interval_s {interval:g} gives more than "
            f"{MAX_CONTACT_FRAMES} frames of a {total:.3f}s video"
        )
    times = tuple(round(index * interval, 3) for index in range(count))
    return Step(
        "contact_sheet",
        f"contact_sheet\t{len(times)} frames",
        {"columns": sheet.get("columns", 4), "times": times},
        export=True,
    )


# The fixed step order, whatever order the recipe lists its keys in (spec #14): trim and
# cuts -> speed -> watermark, callouts, progress bar -> cards -> subtitle tracks and
# chapters -> click sounds and music -> exports. One slot per operation.
STEPS = {
    "trim": _trim_step,
    "speed": _speed_step,
    "watermark": _watermark_step,
    "callouts": _callouts_step,
    "progress_bar": _progress_bar_step,
    "cards": _cards_step,
    "subtitle_tracks": _subtitle_tracks_step,
    "chapters": _chapters_step,
    "click_sounds": _click_sounds_step,
    "music": _music_step,
    "size_cap": _size_cap_step,
    "gif": _gif_step,
    "poster": _poster_step,
    "contact_sheet": _contact_sheet_step,
}


# --- cards ---------------------------------------------------------------------------


def card_defaults(spec, slug):
    """The cards' default text from the demo spec (demo.json, may be {}): its title (else
    the slug) and its last scene's URL (else none)."""
    scenes = spec.get("scenes") or [{}]
    return {"title": spec.get("title") or slug, "url": scenes[-1].get("url")}


def resolve_cards(cards, demo):
    """The recipe's cards with every default filled in, title card first; {} when off.

    Each card: {"seconds", "background", "color", "lines": ((text, role), ...)}, plus
    "logo" on a title card that has one. role: "title", "subtitle", "text" or "url".
    """
    resolved = {}
    for name in CARD_KEYS:
        if name not in (cards or {}):
            continue
        card = cards[name]
        texts = {**(demo or {}), **card}
        lines = tuple(
            (texts[key], key) for key in CARD_KEYS[name] if key != "logo" and texts.get(key)
        )
        if not lines:
            raise RecipeError(
                f"cards.{name} needs its text: there is no default to take it "
                f"from (demo title / last scene URL)"
            )
        resolved[name] = {
            "seconds": card.get("seconds", CARD_S),
            "background": card.get("background", CARD_BACKGROUND),
            "color": card.get("color", CARD_COLOR),
            "lines": lines,
            **({"logo": card["logo"]} if "logo" in card else {}),
        }
    return resolved


# --- subtitle tracks and chapters ----------------------------------------------------


def _chapters(recipe, time_map, edit_map, intro):
    """One Chapter per scene left after the cuts, named after its title; "Intro" first
    when there is a title card. Each ends where the next starts, the last at the end."""
    if not recipe.get("chapters"):
        return ()
    if edit_map is None:
        raise RecipeError(
            "chapters need the scene times from the edit map; "
            "re-run compose to write <slug>.editmap.json"
        )
    starts = [(0.0, "Intro")] if intro else []
    for scene in edit_map["scenes"]:
        if placed := time_map.range(scene["start_s"], scene["end_s"]):
            starts.append((placed[0], scene["title"]))
    ends = [start for start, _ in starts[1:]] + [time_map.total_s]
    return tuple(
        Chapter(round(start, 3), round(end, 3), title) for (start, title), end in zip(starts, ends)
    )


def youtube_warnings(chapters):
    """Warnings for each YouTube chapter rule the chapters break (YouTube ignores them)."""
    if not chapters:
        return []
    warnings = []
    if chapters[0].start_s > 0:
        warnings.append(
            f"the first chapter starts at {chapters[0].start_s:.3f}s; YouTube needs 0:00"
        )
    if len(chapters) < YOUTUBE_MIN_CHAPTERS:
        warnings.append(
            f"YouTube needs at least {YOUTUBE_MIN_CHAPTERS} chapters (got {len(chapters)})"
        )
    for chapter in chapters:
        if chapter.end_s - chapter.start_s < YOUTUBE_MIN_CHAPTER_S:
            warnings.append(
                f'chapter "{chapter.title}" is {chapter.end_s - chapter.start_s:.3f}s; '
                f"YouTube needs at least {YOUTUBE_MIN_CHAPTER_S:g} s"
            )
    return [f"chapters: {warning}, so YouTube ignores them" for warning in warnings]


def youtube_chapters(chapters):
    """The chapter list to paste into a YouTube description: one ``m:ss Title`` per line."""
    long = chapters[-1].end_s >= 3600

    def stamp(seconds):
        hours, rest = divmod(int(seconds + 1e-6), 3600)
        minutes, secs = divmod(rest, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}" if long else f"{minutes}:{secs:02d}"

    return "".join(f"{stamp(c.start_s)} {c.title}\n" for c in chapters)


def chapters_metadata(chapters):
    """The chapters as an ffmpeg metadata file (``-f ffmetadata``), in milliseconds."""
    lines = [";FFMETADATA1"]
    for chapter in chapters:
        title = re.sub(r"([=;#\\\n])", r"\\\1", chapter.title)
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={round(chapter.start_s * 1000)}",
            f"END={round(chapter.end_s * 1000)}",
            f"title={title}",
        ]
    return "\n".join(lines) + "\n"


def subtitle_tracks_cmd(src, out, srts, languages):
    """Copy ``src``'s picture and sound and add each .srt as a text track tagged with its
    language (``languages``: ISO 639-2 codes, same order)."""
    inputs = [arg for srt in srts for arg in ("-i", str(srt))]
    maps = [arg for index in range(1, len(srts) + 1) for arg in ("-map", str(index))]
    tags = [
        arg
        for index, lang in enumerate(languages)
        for arg in (f"-metadata:s:s:{index}", f"language={lang}")
    ]
    return [
        "ffmpeg", "-v", "error", "-y", "-i", str(src), *inputs,
        "-map", "0:v", "-map", "0:a", *maps,
        "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text", *tags,
        "-movflags", "+faststart", str(out),
    ]  # fmt: skip


def chapters_cmd(src, out, metadata):
    """Copy every stream of ``src`` and add the chapters of ``metadata`` (an ffmetadata file)."""
    return [
        "ffmpeg", "-v", "error", "-y", "-i", str(src), "-f", "ffmetadata", "-i", str(metadata),
        "-map", "0", "-c", "copy", "-map_chapters", "1", "-movflags", "+faststart", str(out),
    ]  # fmt: skip


# --- command builders (pure: arguments in, argument list out) ------------------------


def trim_cmd(src, out, kept, source_s):
    """Keep the ``kept`` source ranges, joined, with a short audio fade at every cut."""
    chain, joined = [], ""
    for index, (start, end) in enumerate(kept):
        fades = []
        if start > 0:
            fades.append(f"afade=t=in:d={_fade(start, end):.3f}")
        if end < source_s - 1e-9:
            fades.append(
                f"afade=t=out:st={end - start - _fade(start, end):.3f}:d={_fade(start, end):.3f}"
            )
        chain.append(f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{index}]")
        chain.append(
            f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS"
            f"{''.join(',' + fade for fade in fades)}[a{index}]"
        )
        joined += f"[v{index}][a{index}]"
    chain.append(f"{joined}concat=n={len(kept)}:v=1:a=1[v][a]")
    return _filter_cmd(src, out, ";".join(chain))


def speed_cmd(src, out, speed):
    """Retime the video by its timestamps and the audio by a tempo change that keeps pitch."""
    return _filter_cmd(
        src, out, f"[0:v]setpts=PTS/{speed:.6f},fps={FPS}[v];[0:a]atempo={speed:.6f}[a]"
    )


def overlay_cmd(src, out, layers):
    """Overlay each Layer's image on the video, in order, faded in and out of its window."""
    chain, last = [], "0:v"
    for index, layer in enumerate(layers, start=1):
        filters, enable = ["format=rgba"], ""
        if layer.height:
            filters.append(f"scale=-1:{layer.height}")
        if layer.opacity < 1:
            filters.append(f"colorchannelmixer=aa={layer.opacity:.2f}")
        if layer.start_s is not None:
            start, end, fade = layer.start_s, layer.end_s, layer.fade_s
            filters += [
                f"fade=t=in:st={start:.3f}:d={fade:.3f}:alpha=1",
                f"fade=t=out:st={end - fade:.3f}:d={fade:.3f}:alpha=1",
            ]
            enable = f":enable='between(t,{start:.3f},{end:.3f})'"
        label = "v" if index == len(layers) else f"o{index}"
        chain += [
            f"[{index}:v]{','.join(filters)}[l{index}]",
            f"[{last}][l{index}]overlay=x={layer.x}:y={layer.y}:shortest=1{enable}[{label}]",
        ]
        last = label
    return _filter_cmd(
        src, out, ";".join(chain), _looped(layer.image for layer in layers), audio="0:a"
    )


def progress_bar_cmd(src, out, edge, color, thickness, width, demo_s):
    """A ``thickness``-pixel bar along ``edge`` that fills from left to right over ``demo_s``."""
    y = "0" if edge == "top" else "H-h"
    digits = color[1:] if len(color) == 7 else "".join(c * 2 for c in color[1:])  # ffmpeg: no #rgb
    graph = (
        f"color=c=0x{digits}:s={width}x{thickness}:r={FPS}:d={demo_s:.3f}[bar];"
        f"[0:v][bar]overlay=x='-w+w*t/{demo_s:.3f}':y={y}[v]"
    )
    return _filter_cmd(src, out, graph, audio="0:a")


def cards_cmd(src, out, cards, demo_s, pngs):
    """The title card, the demo, the closing card, joined: each card a still (``pngs``:
    card name -> image) with silence, faded in and out, and the demo faded where it meets
    a card. Audio is 48 kHz stereo so the parts join."""
    inputs, chain, parts = [], [], []
    for name in ("title", "demo", "closing"):
        if name == "demo":
            video = _faded(
                f"[0:v]fps={FPS},format=yuv420p,setsar=1",
                demo_s,
                "title" in cards,
                "closing" in cards,
            )
            chain += [f"{video}[demov]", f"[0:a]{AUDIO_FORMAT}[demoa]"]
        elif name in cards:
            seconds = cards[name]["seconds"]
            inputs += [
                "-loop",
                "1",
                "-framerate",
                str(FPS),
                "-t",
                f"{seconds:.3f}",
                "-i",
                str(pngs[name]),
            ]
            video = _faded(f"[{len(inputs) // 8}:v]format=yuv420p,setsar=1", seconds, True, True)
            chain += [
                f"{video}[{name}v]",
                f"anullsrc=r=48000:cl=stereo,atrim=duration={seconds:.3f}[{name}a]",
            ]
        else:
            continue
        parts.append(f"[{name}v][{name}a]")
    chain.append(f"{''.join(parts)}concat=n={len(parts)}:v=1:a=1[v][a]")
    return _filter_cmd(src, out, ";".join(chain), inputs)


def _faded(chain, length, fade_in, fade_out):
    fade_s = min(CARD_FADE_S, length / 4)
    if fade_in:
        chain += f",fade=t=in:st=0:d={fade_s:.3f}"
    if fade_out:
        chain += f",fade=t=out:st={length - fade_s:.3f}:d={fade_s:.3f}"
    return chain


def frame_cmd(src, at_s, width, out):
    """One frame of ``src`` at ``at_s`` seconds, scaled to ``width`` (None: as is), as a PNG."""
    scale = ["-vf", f"scale={width}:-2"] if width else []
    return [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-ss",
        f"{at_s:.3f}",
        "-i",
        str(src),
        "-frames:v",
        "1",
        *scale,
        str(out),
    ]


def size_cap_encoding(cap_mb, length_s, width, height):
    """(width, height, video kb/s) that fits a ``length_s`` video in ``cap_mb`` megabytes:
    the bitrate drops first, the resolution only below the MIN_BPP floor; RecipeError
    naming the smallest reachable size when even the ladder's last rung is too big."""
    video_kbps = cap_mb * 8000 / length_s * SIZE_MARGIN - SIZE_AUDIO_KBPS
    sizes = [(width, height)] + [
        (round(width * h / height / 2) * 2, h) for h in SIZE_HEIGHTS if h < height
    ]
    for w, h in sizes:
        if video_kbps >= _floor_kbps(w, h):
            return w, h, round(video_kbps)
    w, h = sizes[-1]
    smallest = (_floor_kbps(w, h) + SIZE_AUDIO_KBPS) * length_s / SIZE_MARGIN / 8000
    raise RecipeError(
        f"exports.size_cap_mb: {cap_mb:g} MB is out of reach for a "
        f"{length_s:.1f}s video even at {w}x{h}; the smallest reachable size "
        f"is {math.ceil(smallest * 100) / 100:.2f} MB (cut the video shorter)"
    )


def _floor_kbps(width, height):
    return MIN_BPP * width * height * FPS / 1000


def size_cap_cmds(src, out, width, height, video_kbps, passlog, scale):
    """The two x264 passes at ``video_kbps`` (scaled to width x height when ``scale``); the
    second keeps every subtitle track (with its language tag) and the chapters."""
    vf = ["-vf", f"scale={width}:{height}"] if scale else []
    common = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        str(src),
        *vf,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-b:v",
        f"{video_kbps}k",
        "-pix_fmt",
        "yuv420p",
        "-passlogfile",
        str(passlog),
    ]
    return [
        [*common, "-pass", "1", "-an", "-f", "null", "-"],
        [
            *common,
            "-pass",
            "2",
            "-map",
            "0",
            "-map_chapters",
            "0",
            "-c:a",
            "aac",
            "-b:a",
            f"{SIZE_AUDIO_KBPS}k",
            "-c:s",
            "copy",
            "-map_metadata",
            "0",
            "-movflags",
            "+faststart",
            str(out),
        ],
    ]


def gif_cmd(src, out, start_s, end_s, width, fps):
    """A looping GIF of [start_s, end_s] at ``width`` and ``fps``, with its own palette."""
    graph = (
        f"[0:v]fps={fps},scale={width}:-1:flags=lanczos,split[a][b];"
        f"[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5"
    )
    return [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-ss",
        f"{start_s:.3f}",
        "-t",
        f"{end_s - start_s:.3f}",
        "-i",
        str(src),
        "-filter_complex",
        graph,
        "-loop",
        "0",
        str(out),
    ]


def gif_warning(size_bytes):
    """A warning when a GIF is over GIF_WARN_MB, else None."""
    if size_bytes <= GIF_WARN_MB * 1e6:
        return None
    return (
        f"the GIF is {size_bytes / 1e6:.1f} MB (over {GIF_WARN_MB} MB); shorten its range "
        f"or lower its width or fps"
    )


def _filter_cmd(src, out, graph, inputs=(), video="[v]", audio="[a]"):
    """``src`` (input 0), then the extra ``inputs`` arguments, through ``graph`` to ``out``.
    video / audio: the stream each comes from; ``0:v`` / ``0:a`` (``src``'s own) is copied
    as it is, a ``graph`` label is encoded (x264 at FPS, AAC)."""
    video_codec = ["-c:v", "copy"] if video == "0:v" else ["-r", str(FPS), *X264]
    audio_codec = ["-c:a", "copy"] if audio == "0:a" else AAC
    return [
        "ffmpeg", "-v", "error", "-y", "-i", str(src), *inputs,
        "-filter_complex", graph, "-map", video, "-map", audio, *video_codec, *audio_codec,
        "-map_metadata", "-1", "-movflags", "+faststart", str(out),
    ]  # fmt: skip


def _looped(images):
    """Input arguments that loop each still image as a FPS video."""
    return [
        arg for image in images for arg in ("-loop", "1", "-framerate", str(FPS), "-i", str(image))
    ]


def _fade(start, end):
    return min(FADE_S, (end - start) / 2)
