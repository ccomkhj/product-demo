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
from xml.sax.saxutils import escape as xml_escape

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
    extra file from the finished video instead of changing the video.
    """

    op: str
    line: str
    params: dict = field(default_factory=dict)
    export: bool = False

    def cmd(self, src, out):
        """The ffmpeg arguments of a video step: read ``src``, write ``out``."""
        return COMMANDS[self.op](src, out, **self.params)


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
        return self.time_map.output_s(source_time(value, self.source_s, self.edit_map, where))


def build_post_plan(recipe, source_s, cues, edit_map, demo=None):
    """Validate ``recipe`` and build the plan; raise RecipeError before any ffmpeg work.

    source_s: the source video's length. cues: its subtitle cues (Cue). edit_map: the
    parsed ``<slug>.editmap.json``, or None (then scene anchors are an error). demo: the
    card defaults ``{"title", "url"}`` (see ``card_defaults``), or None.
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
    plan = PostPlan(time_map, tuple(retimed), tuple(warnings), (), edit_map, cards)
    return replace(
        plan, steps=tuple(step for build in STEPS.values() if (step := build(recipe, plan)))
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
    # watermark, callouts, progress_bar (#18)
    "cards": _check_cards,
    # translations, chapters (#21)
    # click_sounds, music (#20)
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
    # watermark, callouts, progress_bar (#18)
    "cards": _cards_step,
    # subtitle_tracks, chapters (#21)
    # click_sounds, music (#20)
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
                f'<text font-family="Helvetica, Arial, sans-serif" font-size="{font}"'
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
            f'font-family="Helvetica, Arial, sans-serif" font-size="{label_h * 0.8:.0f}" '
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


def _fade(start, end):
    return min(FADE_S, (end - start) / 2)


# The ffmpeg command builder of each video step, by op.
COMMANDS = {
    "trim": trim_cmd,
    "speed": speed_cmd,
    "cards": cards_cmd,
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
