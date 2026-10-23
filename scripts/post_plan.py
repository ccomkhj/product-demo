"""Post plan: post recipe + source length + cues + edit map -> plan (stdlib only, no ffmpeg).

The post recipe (``post.json`` in the demo folder) has one optional key per operation;
an absent key means the operation is off. ``build_post_plan`` validates it, builds the
time map (source time -> output time: trim and cuts, then speed), retimes the cues and
lists the steps in the fixed step order (see ``STEPS``), each with its run output line
and its ffmpeg arguments.

Every time in the recipe is seconds in the source video, or a scene anchor
``{"scene": id, "at": phrase, "offset_s": s}`` (``at`` and ``offset_s`` optional)
resolved through the edit map.

Adding an operation: a validator in ``VALIDATORS`` (or ``EXPORT_VALIDATORS``), a step
builder in its ``STEPS`` slot, its command builder in ``COMMANDS`` (video steps), and a
runner in postprocess.py.
"""

import math
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from edit_plan import FPS

MIN_SPEED, MAX_SPEED = 0.5, 2.0
FADE_S = 0.03  # audio fade at each side of every cut, against clicks and pops
CONTACT_FRAMES, MAX_CONTACT_FRAMES, MAX_COLUMNS = 12, 60, 8
X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
AAC = ["-c:a", "aac", "-b:a", "192k"]


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


# --- time map ------------------------------------------------------------------------

@dataclass(frozen=True)
class TimeMap:
    """Source time -> output time: the kept ranges laid end to end, then the speed,
    then ``lead_s`` (room for a title card) in front."""

    kept: tuple  # ((start_s, end_s), ...) in source seconds, in order
    source_s: float  # the source's length
    speed: float = 1.0
    lead_s: float = 0.0

    @property
    def total_s(self):
        return self.lead_s + sum(end - start for start, end in self.kept) / self.speed

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
        parts = [(max(start, start_s), min(end, end_s)) for start, end in self.kept
                 if min(end, end_s) > max(start, start_s)]
        if not parts:
            return None
        kept_s = sum(end - start for start, end in parts)
        return (self.output_s(parts[0][0]), self.output_s(parts[-1][1]),
                kept_s < end_s - start_s - 1e-6)


@dataclass(frozen=True)
class PostPlan:
    """The post plan: time map, retimed cues, warnings and steps in the fixed order."""

    time_map: TimeMap
    cues: tuple
    warnings: tuple
    steps: tuple
    edit_map: dict | None = None

    @property
    def source_s(self):
        return self.time_map.source_s

    @property
    def total_s(self):
        return self.time_map.total_s

    def output_s(self, value, where="time"):
        """Output seconds of a recipe time (source seconds or a scene anchor)."""
        return self.time_map.output_s(source_time(value, self.source_s, self.edit_map, where))


def build_post_plan(recipe, source_s, cues, edit_map):
    """Validate ``recipe`` and build the plan; raise RecipeError before any ffmpeg work.

    source_s: the source video's length. cues: its subtitle cues (Cue). edit_map: the
    parsed ``<slug>.editmap.json``, or None (then scene anchors are an error).
    """
    validate_recipe(recipe)
    time_map = _time_map(recipe, source_s, edit_map)
    warnings, retimed = [], []
    for number, cue in enumerate(cues, start=1):
        placed = time_map.range(cue.start_s, cue.end_s)
        if placed is None:
            continue
        start, end, clipped = placed
        if clipped:
            cuts = ", ".join(f"{a:.3f}s-{b:.3f}s" for a, b in time_map.removed
                             if b > cue.start_s and a < cue.end_s)
            warnings.append(f'cut at {cuts} splits cue {number} "{cue.text}"')
        retimed.append(Cue(round(start, 3), round(end, 3), cue.text))
    plan = PostPlan(time_map, tuple(retimed), tuple(warnings), (), edit_map)
    steps = tuple(step for build in STEPS.values() if (step := build(recipe, plan)))
    return replace(plan, steps=steps,
                   warnings=(*plan.warnings, *(w for step in steps for w in step.warnings)))


def source_time(value, source_s, edit_map, where="time"):
    """Source seconds of a recipe time: seconds, or a scene anchor through the edit map."""
    if not isinstance(value, dict):
        seconds = float(value)
    else:
        seconds = _anchor_s(value, edit_map, where) + value.get("offset_s", 0)
    if seconds < 0:
        raise RecipeError(f"{where} is before the start of the source ({seconds:.3f}s)")
    if seconds > source_s + 1e-9:
        raise RecipeError(f"{where} is past the end of the source "
                          f"({seconds:.3f}s of {source_s:.3f}s)")
    return seconds


def _anchor_s(ref, edit_map, where):
    if edit_map is None:
        raise RecipeError(f"{where} is anchored to a scene, which needs the edit map; "
                          f"re-run compose to write <slug>.editmap.json")
    scenes = {scene["id"]: scene for scene in edit_map["scenes"]}
    if ref["scene"] not in scenes:
        raise RecipeError(f"{where}: unknown scene '{ref['scene']}' "
                          f"(scenes: {', '.join(scenes)})")
    scene = scenes[ref["scene"]]
    if "at" not in ref:
        return scene["start_s"]
    starts = {action["at"]: action["start_s"] for action in scene["actions"] if "at" in action}
    if ref["at"] not in starts:
        known = ", ".join(f"'{phrase}'" for phrase in starts) or "none"
        raise RecipeError(f"{where}: scene '{scene['id']}' has no action at '{ref['at']}' "
                          f"(at phrases: {known})")
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
MIX_FORMAT = "aformat=sample_rates=48000:channel_layouts=stereo"


def _check_click_sounds(value, where):
    _check_keys(value, where, ("file", "level"))
    _check_file(value, where)
    _check_level(value, where, "level")


def _check_music(value, where):
    _check_keys(value, where, ("file", "level", "duck", "fade_out_s"))
    _check_file(value, where, required=True)
    _check_level(value, where, "level")
    if "duck" in value and (not _is_number(value["duck"]) or not 1 <= value["duck"] <= MAX_DUCK):
        raise RecipeError(f"{where}.duck must be a number from 1 (no ducking) to {MAX_DUCK}, "
                          f"got {value['duck']!r}")
    if "fade_out_s" in value and (not _is_number(value["fade_out_s"]) or value["fade_out_s"] < 0):
        raise RecipeError(f"{where}.fade_out_s must be a number of seconds (>= 0)")


def _check_file(value, where, required=False):
    name = value.get("file")
    if (required or "file" in value) and (not isinstance(name, str) or not name.strip()):
        raise RecipeError(f"{where}.file must be an audio file name next to the recipe")
    if isinstance(name, str) and re.match(r"[a-z][a-z0-9+.-]*://", name, re.IGNORECASE):
        raise RecipeError(f"{where}.file must be a local file: audio is never downloaded; "
                          f"use a file you have the rights to")


def _check_level(value, where, key):
    if key in value and (not _is_number(value[key]) or not 0 <= value[key] <= 1):
        raise RecipeError(f"{where}.{key} must be a number from 0 to 1, got {value[key]!r}")


def _click_sounds_step(recipe, plan):
    if "click_sounds" not in recipe:
        return None
    clicks = recipe["click_sounds"]
    if plan.edit_map is None:
        raise RecipeError("click_sounds needs the click times in the edit map; "
                          "re-run compose to write <slug>.editmap.json")
    times, unplaced = [], []
    for scene in plan.edit_map["scenes"]:
        for action in scene["actions"]:
            if action["type"] not in CLICK_TYPES:
                continue
            what = f"scene '{scene['id']}' action {action['index']}"
            if "start_s" not in action:
                unplaced.append(f"{what} ({action['type']}): start unknown "
                                f"(not the scene's first action and no at phrase)")
                continue
            click_s = action["start_s"] + GLIDE_S
            try:
                times.append(round(plan.time_map.output_s(click_s), 3))
            except RecipeError:
                unplaced.append(f"{what} ({action['type']} at {click_s:.3f}s): inside a cut")
    line = f"click_sounds\t{len(times)} clicks"
    if unplaced:
        line += f"\t{len(unplaced)} unplaceable"
    return Step("click_sounds", line,
                {"times": tuple(times), "sound": clicks.get("file", str(CLICK_SOUND)),
                 "level": clicks.get("level", CLICK_LEVEL)},
                warnings=tuple(f"no click sound for {item}" for item in unplaced))


def click_sounds_cmd(src, out, times, sound, level):
    """Mix ``sound`` at ``level`` into the audio at each of ``times``; copy the picture."""
    if not times:
        return _mix_cmd(src, [], "[0:a]anull[a]", out)
    count = len(times)
    graph = [f"[1:a]volume={level:.3f},asplit={count}{''.join(f'[c{i}]' for i in range(count))}"]
    graph += [f"[c{i}]adelay={round(t * 1000)}:all=1[d{i}]" for i, t in enumerate(times)]
    graph.append(f"[0:a]{''.join(f'[d{i}]' for i in range(count))}"
                 f"amix=inputs={count + 1}:duration=first:normalize=0[a]")
    return _mix_cmd(src, ["-i", str(sound)], ";".join(graph), out)


def _music_step(recipe, plan):
    if "music" not in recipe:
        return None
    music = recipe["music"]
    params = {"file": music["file"], "level": music.get("level", MUSIC_LEVEL),
              "duck": music.get("duck", MUSIC_DUCK),
              "fade_out_s": float(music.get("fade_out_s", MUSIC_FADE_S)),
              "total_s": round(plan.total_s, 3)}
    if params["fade_out_s"] > params["total_s"]:
        raise RecipeError(f"music.fade_out_s {params['fade_out_s']:g} is longer than the "
                          f"{params['total_s']:.3f}s video")
    return Step("music", f"music\t{Path(params['file']).name}\tlevel {params['level']:.2f}\t"
                         f"duck {params['duck']:g}:1\tfade out {params['fade_out_s']:.2f}s",
                params)


def music_cmd(src, out, file, level, duck, fade_out_s, total_s):
    """Loop ``file`` to ``total_s`` at ``level``, fade it out, duck it by sidechain
    compression keyed on the narration (ratio ``duck``), and mix it under; copy the picture."""
    graph = (f"[1:a]{MIX_FORMAT},volume={level:.3f},atrim=duration={total_s:.3f},"
             f"afade=t=out:st={total_s - fade_out_s:.3f}:d={fade_out_s:.3f}[m];"
             f"[0:a]asplit=2[voice][key];"
             f"[m][key]sidechaincompress=threshold=0.02:ratio={duck:g}:attack=20:release=400[ducked];"
             f"[voice][ducked]amix=inputs=2:duration=first:normalize=0[a]")
    return _mix_cmd(src, ["-stream_loop", "-1", "-i", str(file)], graph, out)


def _mix_cmd(src, inputs, graph, out):
    """An audio-only step: ``graph`` makes ``[a]`` from ``src`` and ``inputs``; the
    picture is copied as it is."""
    return ["ffmpeg", "-v", "error", "-y", "-i", str(src), *inputs, "-filter_complex", graph,
            "-map", "0:v", "-map", "[a]", "-c:v", "copy", *AAC,
            "-map_metadata", "-1", "-movflags", "+faststart", str(out)]


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
        raise RecipeError(f"{where} must be a number from {MIN_SPEED} to {MAX_SPEED:g}, "
                          f"got {value!r}")


def _check_trim(value, where):
    _check_keys(value, where, ("start", "end", "cuts"))
    for key in ("start", "end"):
        if key in value:
            check_time(value[key], f"{where}.{key}")
    cuts = value.get("cuts", [])
    if not isinstance(cuts, list):
        raise RecipeError(f"{where}.cuts must be a list of {{\"from\": t, \"to\": t}}")
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
        raise RecipeError(f"{where} must be seconds (>= 0) or a scene anchor "
                          f"{{\"scene\": id, \"at\": phrase, \"offset_s\": s}}")


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
    # watermark, callouts, progress_bar (#18)

    # cards (#19)

    # translations, chapters (#21)

    "click_sounds": _check_click_sounds,
    "music": _check_music,

    "exports": _check_exports,
}

EXPORT_VALIDATORS = {
    # size_cap_mb, gif, poster (#22)

    "contact_sheet": _check_contact_sheet,
}


# --- steps (pure: recipe + plan in, Step or None out) ----------------------------------

def _trim_step(recipe, plan):
    if "trim" not in recipe:
        return None
    kept = plan.time_map.kept
    kept_s = sum(end - start for start, end in kept)
    ranges = len(plan.time_map.removed)
    return Step("trim", f"trim\tremoved {plan.source_s - kept_s:.3f}s in {ranges} ranges\t"
                        f"kept {kept_s:.3f}s",
                {"kept": kept, "source_s": plan.source_s})


def _speed_step(recipe, plan):
    if recipe.get("speed", 1.0) == 1.0:
        return None
    return Step("speed", f"speed\t{recipe['speed']:.2f}x", {"speed": recipe["speed"]})


def _contact_sheet_step(recipe, plan):
    sheet = recipe.get("exports", {}).get("contact_sheet")
    if sheet is None:
        return None
    total = plan.total_s
    interval = sheet.get("interval_s", total / CONTACT_FRAMES)
    count = math.ceil(total / interval - 1e-9)
    if count > MAX_CONTACT_FRAMES:
        raise RecipeError(f"exports.contact_sheet.interval_s {interval:g} gives more than "
                          f"{MAX_CONTACT_FRAMES} frames of a {total:.3f}s video")
    times = tuple(round(index * interval, 3) for index in range(count))
    return Step("contact_sheet", f"contact_sheet\t{len(times)} frames",
                {"columns": sheet.get("columns", 4), "times": times}, export=True)


# The fixed step order, whatever order the recipe lists its keys in (spec #14): trim and
# cuts -> speed -> watermark, callouts, progress bar -> cards -> subtitle tracks and
# chapters -> click sounds and music -> exports. One slot per operation.
STEPS = {
    "trim": _trim_step,
    "speed": _speed_step,
    # watermark, callouts, progress_bar (#18)

    # cards (#19)

    # subtitle_tracks, chapters (#21)

    "click_sounds": _click_sounds_step,
    "music": _music_step,

    # size_cap, gif, poster (#22)

    "contact_sheet": _contact_sheet_step,
}


# --- command builders (pure: arguments in, argument list out) ------------------------

def trim_cmd(src, out, kept, source_s):
    """Keep the ``kept`` source ranges, joined, with a short audio fade at every cut."""
    chain, joined = [], ""
    for index, (start, end) in enumerate(kept):
        fades = []
        if start > 0:
            fades.append(f"afade=t=in:d={_fade(start, end):.3f}")
        if end < source_s - 1e-9:
            fades.append(f"afade=t=out:st={end - start - _fade(start, end):.3f}:"
                         f"d={_fade(start, end):.3f}")
        chain.append(f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{index}]")
        chain.append(f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS"
                     f"{''.join(',' + fade for fade in fades)}[a{index}]")
        joined += f"[v{index}][a{index}]"
    chain.append(f"{joined}concat=n={len(kept)}:v=1:a=1[v][a]")
    return _encode(src, ";".join(chain), out)


def speed_cmd(src, out, speed):
    """Retime the video by its timestamps and the audio by a tempo change that keeps pitch."""
    return _encode(src, f"[0:v]setpts=PTS/{speed:.6f},fps={FPS}[v];[0:a]atempo={speed:.6f}[a]", out)


def frame_cmd(src, at_s, width, out):
    """One frame of ``src`` at ``at_s`` seconds, scaled to ``width``, as a PNG."""
    return ["ffmpeg", "-v", "error", "-y", "-ss", f"{at_s:.3f}", "-i", str(src),
            "-frames:v", "1", "-vf", f"scale={width}:-2", str(out)]


def contact_sheet_svg(frames, columns, width, height):
    """A grid of frames, each labelled with its time: ``frames`` is [(href, time_s)]."""
    label_h = max(18, height // 8)
    pad = 8
    rows = math.ceil(len(frames) / columns)
    cell_w, cell_h = width + pad, height + label_h + pad
    parts = []
    for index, (href, time_s) in enumerate(frames):
        x, y = pad + (index % columns) * cell_w, pad + (index // columns) * cell_h
        parts.append(f'<image x="{x}" y="{y}" width="{width}" height="{height}" href="{href}"/>'
                     f'<text x="{x + 4}" y="{y + height + label_h * 0.8:.0f}" '
                     f'font-family="Helvetica, Arial, sans-serif" font-size="{label_h * 0.8:.0f}" '
                     f'fill="white">{time_s:.2f}s</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{pad + columns * cell_w}" '
            f'height="{pad + rows * cell_h}"><rect width="100%" height="100%" fill="#202020"/>'
            f'{"".join(parts)}</svg>')


def _encode(src, graph, out):
    return ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex", graph,
            "-map", "[v]", "-map", "[a]", "-r", str(FPS), *X264, *AAC,
            "-map_metadata", "-1", "-movflags", "+faststart", str(out)]


def _fade(start, end):
    return min(FADE_S, (end - start) / 2)


# The ffmpeg command builder of each video step, by op.
COMMANDS = {
    "trim": trim_cmd,
    "speed": speed_cmd,
    # click_sounds, music (#20)
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
                cues.append(Cue((h1 * 3600 + m1 * 60 + s1) + ms1 / 1000,
                                (h2 * 3600 + m2 * 60 + s2) + ms2 / 1000,
                                "\n".join(lines[index + 1:])))
                break
    return tuple(cues)
