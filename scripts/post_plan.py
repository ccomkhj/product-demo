"""Post plan: post recipe + source length + cues + edit map -> plan (stdlib only, no ffmpeg).

The post recipe (``post.json`` in the demo folder) has one optional key per operation;
an absent key means the operation is off. ``build_post_plan`` validates it, builds the
time map (source time -> output time: trim and cuts, then speed, then the title card),
retimes the cues and lists the steps in the fixed step order (see ``STEPS``), each with
its run output line and its ffmpeg arguments.

Every time in the recipe is seconds in the source video, or a scene anchor
``{"scene": id, "at": phrase, "offset_s": s}`` (``at`` and ``offset_s`` optional)
resolved through the edit map.

Adding an operation: a validator in ``VALIDATORS`` (or ``EXPORT_VALIDATORS``), a step
builder in its ``STEPS`` slot, its command builder in ``COMMANDS`` (video steps), and a
runner in postprocess.py.
"""

import math
import re
import textwrap
from dataclasses import dataclass, field, replace
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from avatar import CORNERS, Avatar, geometry
from edit_plan import FPS

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
BAR_COLOR, BAR_THICKNESS = "#4da3ff", 6
WATERMARK_SIZE, WATERMARK_OPACITY = 0.06, 0.8  # share of the video's height; 0-1
MIN_WATERMARK, MAX_WATERMARK = 0.02, 0.3
CALLOUT_TYPES = ("arrow", "text")
CALLOUT_COLOR = "#ff3b30"
CALLOUT_FADE_S = 0.25
FONT = "Helvetica, Arial, sans-serif"
SUBTITLE_BAND = 0.2  # burned-in subtitles sit in the bottom 20% of the frame
AUDIO_FORMAT = "aformat=sample_rates=48000:channel_layouts=stereo"


class RecipeError(ValueError):
    """Raised when a post recipe is malformed or a time in it cannot be placed."""


@dataclass(frozen=True)
class Cue:
    """One subtitle cue, in seconds."""

    start_s: float
    end_s: float
    text: str


@dataclass(frozen=True)
class Step:
    """One operation of the plan.

    op: its name (a ``STEPS`` key). line: its line of the run output. params: what its
    command builder (``COMMANDS[op]``) or runner needs. export: True when it writes an
    extra file from the finished video instead of changing the video. warnings: its
    warnings, added to the plan's.
    """

    op: str
    line: str
    params: dict = field(default_factory=dict)
    export: bool = False
    warnings: tuple = ()

    def cmd(self, src, out):
        """The ffmpeg arguments of a video step: read ``src``, write ``out``."""
        return COMMANDS[self.op](src, out, **self.params)


@dataclass(frozen=True)
class Layer:
    """One image overlaid on the video (watermark, callout).

    image: the PNG; with ``svg`` set it is the file name the runner renders the SVG to
    (rsvg-convert, ADR-0001). x, y: ffmpeg overlay expressions. height: scale the image
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


def build_post_plan(recipe, source_s, cues, edit_map, demo=None, frame=None, bubble=None):
    """Validate ``recipe`` and build the plan; raise RecipeError before any ffmpeg work.

    source_s: the source video's length. cues: its subtitle cues (Cue). edit_map: the
    parsed ``<slug>.editmap.json``, or None (then scene anchors are an error). demo: the
    card defaults ``{"title", "url"}`` (see ``card_defaults``), or None. frame: the
    source's (width, height), needed by overlays. bubble: the presenter bubble's
    (corner, size) from the demo spec, or None without one.
    """
    validate_recipe(recipe)
    time_map = _time_map(recipe, source_s, edit_map)
    cards = resolve_cards(recipe.get("cards"), demo)
    time_map = replace(
        time_map,
        lead_s=cards["title"]["seconds"] if "title" in cards else 0.0,
        tail_s=cards["closing"]["seconds"] if "closing" in cards else 0.0,
    )
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
    plan = PostPlan(time_map, tuple(retimed), tuple(warnings), (), edit_map, cards, frame, bubble)
    steps = tuple(step for build in STEPS.values() if (step := build(recipe, plan)))
    return replace(
        plan, steps=steps, warnings=(*plan.warnings, *(w for step in steps for w in step.warnings))
    )


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

CLICK_SOUND = Path(__file__).resolve().parent.parent / "assets" / "click.wav"
CLICK_TYPES = ("click", "dblclick", "type")  # a type action clicks its field first
GLIDE_S = 0.6  # the overlay's cursor glide (assets/overlay.js GLIDE_MS): the click lands after it


CLICK_LEVEL = 0.5
MUSIC_LEVEL, MUSIC_DUCK, MAX_DUCK, MUSIC_FADE_S = 0.2, 8, 20, 2.0


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
    )


def click_sounds_cmd(src, out, times, sound, level):
    """Mix ``sound`` at ``level`` into the audio at each of ``times``; copy the picture."""
    if not times:
        return _mix_cmd(src, [], "[0:a]anull[a]", out)
    count = len(times)
    graph = [f"[1:a]volume={level:.3f},asplit={count}{''.join(f'[c{i}]' for i in range(count))}"]
    graph += [f"[c{i}]adelay={round(t * 1000)}:all=1[d{i}]" for i, t in enumerate(times)]
    graph.append(
        f"[0:a]{''.join(f'[d{i}]' for i in range(count))}"
        f"amix=inputs={count + 1}:duration=first:normalize=0[a]"
    )
    return _mix_cmd(src, ["-i", str(sound)], ";".join(graph), out)


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
    return _mix_cmd(src, ["-stream_loop", "-1", "-i", str(file)], graph, out)


def _mix_cmd(src, inputs, graph, out):
    """An audio-only step: ``graph`` makes ``[a]`` from ``src`` and ``inputs``; the
    picture is copied as it is."""
    return [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        str(src),
        *inputs,
        "-filter_complex",
        graph,
        "-map",
        "0:v",
        "-map",
        "[a]",
        "-c:v",
        "copy",
        *AAC,
        "-map_metadata",
        "-1",
        "-movflags",
        "+faststart",
        str(out),
    ]


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
    columns = value.get("columns", 4)
    if isinstance(columns, bool) or not isinstance(columns, int) or not 1 <= columns <= MAX_COLUMNS:
        raise RecipeError(f"{where}.columns must be a whole number from 1 to {MAX_COLUMNS}")
    if "interval_s" in value and (not _is_number(value["interval_s"]) or value["interval_s"] <= 0):
        raise RecipeError(f"{where}.interval_s must be a positive number of seconds")


def _check_progress_bar(value, where):
    _check_keys(value, where, ("edge", "color", "thickness"))
    if value.get("edge", "bottom") not in EDGES:
        raise RecipeError(f"{where}.edge must be one of {', '.join(EDGES)}")
    if "color" in value:
        _check_color(value["color"], f"{where}.color")
    thickness = value.get("thickness", BAR_THICKNESS)
    if isinstance(thickness, bool) or not isinstance(thickness, int) or not 1 <= thickness <= 40:
        raise RecipeError(f"{where}.thickness must be a whole number of pixels from 1 to 40")


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
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise RecipeError(f'{where} must be a colour like "#4da3ff", got {value!r}')


CARD_KEYS = {"title": ("title", "subtitle", "logo"), "closing": ("text", "url")}


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
            if key in card and not (
                isinstance(card[key], str) and re.fullmatch(r"#(?:[0-9a-fA-F]{3}){1,2}", card[key])
            ):
                raise RecipeError(
                    f'{here}.{key} must be a hex colour like "#1a2b3c", got {card[key]!r}'
                )


def _check_size_cap(value, where):
    if not _is_number(value) or value <= 0:
        raise RecipeError(f"{where} must be a positive number of megabytes, got {value!r}")


def _check_gif(value, where):
    _check_keys(value, where, ("from", "to", "width", "fps"))
    for key in ("from", "to"):
        if key in value:
            check_time(value[key], f"{where}.{key}")
    for key, low, high in (("width", MIN_GIF_WIDTH, MAX_GIF_WIDTH), ("fps", 1, MAX_GIF_FPS)):
        number = value.get(key, low)
        if isinstance(number, bool) or not isinstance(number, int) or not low <= number <= high:
            raise RecipeError(f"{where}.{key} must be a whole number from {low} to {high}")


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
    # translations, chapters (#21)
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
    )


def _speed_step(recipe, plan):
    if recipe.get("speed", 1.0) == 1.0:
        return None
    return Step("speed", f"speed\t{recipe['speed']:.2f}x", {"speed": recipe["speed"]})


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
        font = round(size * height)
        right = corner.endswith("right")
        x = width - margin if right else margin
        y = height - margin - round(font * 0.25) if corner.startswith("bottom") else margin + font
        layer = Layer(
            "watermark.png",
            _svg(
                f'<text x="{x}" y="{y}" text-anchor="{"end" if right else "start"}" '
                f'font-family="{FONT}" font-size="{font}" font-weight="700" fill="white" '
                f'stroke="black" stroke-width="{max(1, font // 16)}" opacity="{opacity:g}">'
                f"{xml_escape(mark['text'])}</text>",
                width,
                height,
            ),
        )
        line = f'watermark\ttext "{mark["text"]}"\t{corner}'
    return Step("watermark", line, {"layers": (layer,)})


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
        if box[3] > band_y and any(cue.start_s < end and cue.end_s > start for cue in plan.cues):
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
    return Step("cards", f"cards{line}", {"cards": plan.cards, "demo_s": end - start})


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
    # subtitle_tracks, chapters (#21)
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


def card_svg(card, width, height, logo_href=None):
    """One card as an SVG: its background and its lines centred, the logo above them."""
    sizes = {"title": 0.08, "subtitle": 0.045, "text": 0.065, "url": 0.045}
    blocks = []  # (height, svg drawn from y=0)
    if logo_href:
        logo_h = round(height * 0.2)
        blocks.append(
            (
                logo_h,
                f'<image x="{width * 0.2:.0f}" y="0" width="{width * 0.6:.0f}" '
                f'height="{logo_h}" href="{xml_escape(logo_href)}" '
                f'preserveAspectRatio="xMidYMid meet"/>',
            )
        )
    for text, role in card["lines"]:
        font = max(12, round(height * sizes[role]))
        rows = textwrap.wrap(text, max(10, int(width * 0.85 / (font * 0.55)))) or [""]
        weight = ' font-weight="700"' if role in ("title", "text") else ""
        tspans = "".join(
            f'<tspan x="{width / 2:.0f}" y="{font + row * font * 1.25:.0f}">'
            f"{xml_escape(line)}</tspan>"
            for row, line in enumerate(rows)
        )
        blocks.append(
            (
                round(font * (1.25 * len(rows) + 0.25)),
                f'<text font-family="{FONT}" font-size="{font}"'
                f'{weight} text-anchor="middle" fill="{card["color"]}">{tspans}</text>',
            )
        )
    gap = round(height * 0.03)
    y = (height - sum(h for h, _ in blocks) - gap * (len(blocks) - 1)) / 2
    body = []
    for block_h, block in blocks:
        body.append(f'<g transform="translate(0 {y:.0f})">{block}</g>')
        y += block_h + gap
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
        f'<rect width="100%" height="100%" fill="{card["background"]}"/>{"".join(body)}</svg>'
    )


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
    return _encode(src, ";".join(chain), out)


def speed_cmd(src, out, speed):
    """Retime the video by its timestamps and the audio by a tempo change that keeps pitch."""
    return _encode(src, f"[0:v]setpts=PTS/{speed:.6f},fps={FPS}[v];[0:a]atempo={speed:.6f}[a]", out)


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
    return _overlay_encode(src, [layer.image for layer in layers], ";".join(chain), out)


def callout_svg(callout, width, height):
    """A callout drawn on a transparent ``width`` x ``height`` SVG: an arrow from ``from``
    to ``to``, or ``text`` in a box at ``position`` (its top-left corner).

    Returns (svg, box): box is (x0, y0, x1, y1), the pixels it covers.
    """
    color = callout.get("color", CALLOUT_COLOR)
    if callout["type"] == "arrow":
        (x0, y0), (x1, y1) = callout["from"], callout["to"]
        stroke = max(4, round(height * 0.008))
        head = stroke * 5
        length = math.hypot(x1 - x0, y1 - y0) or 1
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        bx, by = x1 - ux * head, y1 - uy * head  # base of the head
        wing = [
            (bx - uy * head * 0.6, by + ux * head * 0.6),
            (bx + uy * head * 0.6, by - ux * head * 0.6),
        ]
        points = " ".join(f"{x:.1f},{y:.1f}" for x, y in [(x1, y1), *wing])
        body = (
            f'<g stroke-linecap="round" stroke-linejoin="round">'
            f'<line x1="{x0}" y1="{y0}" x2="{bx:.1f}" y2="{by:.1f}" stroke="white" '
            f'stroke-width="{stroke + 4}"/><polygon points="{points}" fill="white" '
            f'stroke="white" stroke-width="4"/>'
            f'<line x1="{x0}" y1="{y0}" x2="{bx:.1f}" y2="{by:.1f}" stroke="{color}" '
            f'stroke-width="{stroke}"/><polygon points="{points}" fill="{color}"/></g>'
        )
        box = (min(x0, x1) - head, min(y0, y1) - head, max(x0, x1) + head, max(y0, y1) + head)
    else:
        x, y = callout["position"]
        font = max(14, round(height * 0.035))
        pad = font // 2
        box_w, box_h = (
            round(len(callout["text"]) * font * 0.6) + 2 * pad,
            round(font * 1.3) + 2 * pad,
        )
        body = (
            f'<rect x="{x}" y="{y}" width="{box_w}" height="{box_h}" rx="6" '
            f'fill="#141418" fill-opacity="0.88" stroke="{color}" stroke-width="3"/>'
            f'<text x="{x + pad}" y="{y + pad + font}" font-family="{FONT}" '
            f'font-size="{font}" font-weight="600" fill="white">'
            f"{xml_escape(callout['text'])}</text>"
        )
        box = (x, y, x + box_w, y + box_h)
    return _svg(body, width, height), box


def _svg(body, width, height):
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">{body}</svg>'


def progress_bar_cmd(src, out, edge, color, thickness, width, demo_s):
    """A ``thickness``-pixel bar along ``edge`` that fills from left to right over ``demo_s``."""
    y = "0" if edge == "top" else "H-h"
    graph = (
        f"color=c=0x{color[1:]}:s={width}x{thickness}:r={FPS}:d={demo_s:.3f}[bar];"
        f"[0:v][bar]overlay=x='-w+w*t/{demo_s:.3f}':y={y}[v]"
    )
    return _overlay_encode(src, [], graph, out)


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
    return _encode(src, ";".join(chain), out, inputs)


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
    """The two x264 passes at ``video_kbps`` (scaled to width x height when ``scale``)."""
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
            "-c:a",
            "aac",
            "-b:a",
            f"{SIZE_AUDIO_KBPS}k",
            "-map_metadata",
            "-1",
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


def contact_sheet_svg(frames, columns, width, height):
    """A grid of frames, each labelled with its time: ``frames`` is [(href, time_s)]."""
    label_h = max(18, height // 8)
    pad = 8
    rows = math.ceil(len(frames) / columns)
    cell_w, cell_h = width + pad, height + label_h + pad
    parts = []
    for index, (href, time_s) in enumerate(frames):
        x, y = pad + (index % columns) * cell_w, pad + (index // columns) * cell_h
        parts.append(
            f'<image x="{x}" y="{y}" width="{width}" height="{height}" href="{href}"/>'
            f'<text x="{x + 4}" y="{y + height + label_h * 0.8:.0f}" '
            f'font-family="{FONT}" font-size="{label_h * 0.8:.0f}" '
            f'fill="white">{time_s:.2f}s</text>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{pad + columns * cell_w}" '
        f'height="{pad + rows * cell_h}"><rect width="100%" height="100%" fill="#202020"/>'
        f"{''.join(parts)}</svg>"
    )


def _encode(src, graph, out, inputs=()):
    """``src`` (input 0), then the extra ``inputs`` arguments, through ``graph`` to ``out``."""
    return [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        str(src),
        *inputs,
        "-filter_complex",
        graph,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-r",
        str(FPS),
        *X264,
        *AAC,
        "-map_metadata",
        "-1",
        "-movflags",
        "+faststart",
        str(out),
    ]


def _overlay_encode(src, images, graph, out):
    """Encode ``graph`` ([0:v] + one looped input per image -> [v]); copy the audio."""
    inputs = [
        arg for image in images for arg in ("-loop", "1", "-framerate", str(FPS), "-i", str(image))
    ]
    return [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        str(src),
        *inputs,
        "-filter_complex",
        graph,
        "-map",
        "[v]",
        "-map",
        "0:a",
        "-r",
        str(FPS),
        *X264,
        "-c:a",
        "copy",
        "-map_metadata",
        "-1",
        "-movflags",
        "+faststart",
        str(out),
    ]


def _fade(start, end):
    return min(FADE_S, (end - start) / 2)


# The ffmpeg command builder of each video step, by op.
COMMANDS = {
    "trim": trim_cmd,
    "speed": speed_cmd,
    "watermark": overlay_cmd,
    "callouts": overlay_cmd,
    "progress_bar": progress_bar_cmd,
    "cards": cards_cmd,
    "click_sounds": click_sounds_cmd,
    "music": music_cmd,
}


# --- subtitles -----------------------------------------------------------------------

_SRT_TIME = r"(\d+):(\d\d):(\d\d)[,.](\d{3})"


def parse_srt(text):
    """The cues of a SubRip (.srt) document."""
    cues = []
    for block in re.split(r"\n\s*\n", text.strip().replace("\r\n", "\n")):
        lines = block.splitlines()
        for index, line in enumerate(lines):
            match = re.match(rf"\s*{_SRT_TIME}\s*-->\s*{_SRT_TIME}", line)
            if match:
                h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, match.groups())
                cues.append(
                    Cue(
                        (h1 * 3600 + m1 * 60 + s1) + ms1 / 1000,
                        (h2 * 3600 + m2 * 60 + s2) + ms2 / 1000,
                        "\n".join(lines[index + 1 :]),
                    )
                )
                break
    return tuple(cues)
