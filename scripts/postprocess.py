"""Post-process a composed demo video from its post recipe (stdlib + ffmpeg).

Usage:
    uv run python scripts/postprocess.py demos/<slug>/post.json

The recipe's ``source`` defaults to <slug>.mp4 in the recipe's folder (the folder's name
is the slug); its .srt and .editmap.json are read from next to it and never modified.
Writes <slug>-post.mp4 and <slug>-post.srt next to the recipe, plus one
<slug>-post-<name> file per export. Prints one line per operation applied, then
``total <s>s <video>`` and ``subtitles <n> cues <srt>``; warnings go to stderr.

The plan (times, cues, warnings, ffmpeg arguments) comes from post_plan; this file only
runs it. Each step reads the previous step's video and writes the next.
"""

import argparse
import json
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import proc
from avatar import DEFAULT_CORNER, DEFAULT_SIZE
from compose import svg_to_png_cmd
from demo_spec import read_json_object
from post_draw import card_svg, contact_sheet_svg
from post_plan import (
    RecipeError,
    build_post_plan,
    card_defaults,
    cards_cmd,
    chapters_cmd,
    chapters_metadata,
    frame_cmd,
    gif_cmd,
    gif_warning,
    overlay_cmd,
    size_cap_cmds,
    size_cap_encoding,
    subtitle_tracks_cmd,
    validate_recipe,
    youtube_chapters,
)
from subtitles import parse_srt, srt_text

CONTACT_WIDTH = 480  # widest frame in a contact sheet


class PostError(RuntimeError):
    """Raised when an input is missing or an ffmpeg step fails."""


def run(cmd):
    return proc.run(cmd, error=PostError)


def probe(path):
    """(duration_s, width, height) of a video."""
    info = json.loads(run(proc.ffprobe_cmd(path, "stream=width,height:format=duration", "v")))
    stream = info["streams"][0]
    return float(info["format"]["duration"]), stream["width"], stream["height"]


# --- runners: one per step op ---------------------------------------------------------


def _run_video_step(step, video, out_prefix, work_dir):
    out = work_dir / f"{step.op}.mp4"
    run(step.cmd(video, out))
    return out


def _run_contact_sheet(step, video, out_prefix, work_dir):
    _, width, height = probe(video)
    frame_w = min(width, CONTACT_WIDTH) // 2 * 2
    frame_h = round(height * frame_w / width / 2) * 2
    frames = []
    for index, at_s in enumerate(step.params["times"]):
        png = work_dir / f"contact-{index:03d}.png"
        run(frame_cmd(video, at_s, frame_w, png))
        frames.append((png.name, at_s))
    out = Path(f"{out_prefix}-contact.png")
    _render_svg(
        contact_sheet_svg(frames, step.params["columns"], frame_w, frame_h),
        work_dir / "contact.svg",
        out,
        "the contact sheet needs rsvg-convert for its frame times",
    )
    return out


def _run_overlay_step(step, video, out_prefix, work_dir):
    """Overlay the step's layers: SVGs rendered to PNGs here, images from the demo folder."""
    layers = []
    for layer in step.params["layers"]:
        if layer.svg is None:
            image = Path(out_prefix).parent / layer.image  # checked by _plan
        else:
            image = work_dir / layer.image
            _render_svg(
                layer.svg,
                image.with_suffix(".svg"),
                image,
                f"the {step.op} need rsvg-convert to draw text",
            )
        layers.append(replace(layer, image=image))
    out = work_dir / f"{step.op}.mp4"
    run(overlay_cmd(video, out, layers))
    return out


def _run_cards(step, video, out_prefix, work_dir):
    _, width, height = probe(video)
    pngs = {}
    for name, card in step.params["cards"].items():
        logo = None
        if "logo" in card:  # rsvg-convert only loads images next to the SVG
            logo = f"logo-{name}{Path(card['logo']).suffix}"
            shutil.copyfile(Path(out_prefix).parent / card["logo"], work_dir / logo)
        pngs[name] = work_dir / f"card-{name}.png"
        _render_svg(
            card_svg(card, width, height, logo),
            work_dir / f"card-{name}.svg",
            pngs[name],
            "cards need rsvg-convert for their text",
        )
    out = work_dir / "cards.mp4"
    run(cards_cmd(video, out, pngs=pngs, **step.params))
    return out


def _render_svg(svg, svg_path, png, need):
    """Write ``svg`` to ``svg_path`` and convert it to ``png``; ``need``: why rsvg-convert."""
    if not shutil.which("rsvg-convert"):
        raise PostError(f"{need} (`brew install librsvg`)")
    svg_path.write_text(svg, encoding="utf-8")
    run(svg_to_png_cmd(svg_path, png))


def _run_subtitle_tracks(step, video, out_prefix, work_dir):
    """Write a sidecar .srt per track and mux them all into the finished video."""
    srts, tags = [], []
    for lang, tag, cues in step.params["tracks"]:
        srts.append(Path(f"{out_prefix}.{lang}.srt" if lang else f"{out_prefix}.srt"))
        srts[-1].write_text(srt_text(cues), encoding="utf-8")
        tags.append(tag)
    _remux(video, work_dir, lambda out: subtitle_tracks_cmd(video, out, srts, tags))
    return None


def _run_chapters(step, video, out_prefix, work_dir):
    """Add the chapters to the finished video; write the YouTube chapter list."""
    metadata = work_dir / "chapters.ffmetadata"
    metadata.write_text(chapters_metadata(step.params["chapters"]), encoding="utf-8")
    _remux(video, work_dir, lambda out: chapters_cmd(video, out, metadata))
    out = Path(f"{out_prefix}-chapters.txt")
    out.write_text(youtube_chapters(step.params["chapters"]), encoding="utf-8")
    return out


def _remux(video, work_dir, cmd):
    """Run ``cmd(out)`` on the finished ``video`` and put the result in its place."""
    out = work_dir / "remux.mp4"
    run(cmd(out))
    shutil.copyfile(out, video)


def _run_size_cap(step, video, out_prefix, work_dir):
    cap_mb = step.params["cap_mb"]
    length_s, width, height = probe(video)
    w, h, kbps = size_cap_encoding(cap_mb, length_s, width, height)
    out = Path(f"{out_prefix}-{cap_mb:g}mb.mp4")
    encoded = work_dir / out.name  # moved into place only once it is under the cap
    for cmd in size_cap_cmds(
        video, encoded, w, h, kbps, work_dir / "size-cap", scale=(w, h) != (width, height)
    ):
        run(cmd)
    if encoded.stat().st_size > cap_mb * 1e6:
        raise PostError(
            f"{out.name} would be {encoded.stat().st_size / 1e6:.2f} MB, over its "
            f"{cap_mb:g} MB cap; lower size_cap_mb a little and re-run"
        )
    shutil.copyfile(encoded, out)
    return out


def _run_gif(step, video, out_prefix, work_dir):
    out = Path(f"{out_prefix}-gif.gif")
    run(gif_cmd(video, out, **step.params))
    if warning := gif_warning(out.stat().st_size):
        print(f"postprocess: warning: {warning}", file=sys.stderr)
    return out


def _run_poster(step, video, out_prefix, work_dir):
    out = Path(f"{out_prefix}-poster.png")
    run(frame_cmd(video, step.params["at_s"], None, out))
    return out


# How each step op is run: (step, video, out_prefix, work_dir) -> the step's file.
RUNNERS = {
    "trim": _run_video_step,
    "speed": _run_video_step,
    "watermark": _run_overlay_step,
    "callouts": _run_overlay_step,
    "progress_bar": _run_video_step,
    "cards": _run_cards,
    "subtitle_tracks": _run_subtitle_tracks,
    "chapters": _run_chapters,
    "click_sounds": _run_video_step,
    "music": _run_video_step,
    "size_cap": _run_size_cap,
    "gif": _run_gif,
    "poster": _run_poster,
    "contact_sheet": _run_contact_sheet,
}


def postprocess(plan, source, out_prefix, work_dir):
    """Run the plan's steps on ``source``; write ``<out_prefix>.mp4``, ``.srt`` and exports.

    Returns [(step, export file or None)] in step order.
    """
    video, done = Path(source), []
    for step in (s for s in plan.steps if not s.export):
        video = RUNNERS[step.op](step, video, out_prefix, work_dir)
        done.append((step, None))
    final = Path(f"{out_prefix}.mp4")
    shutil.copyfile(video, final)
    Path(f"{out_prefix}.srt").write_text(srt_text(plan.cues), encoding="utf-8")
    for step in (s for s in plan.steps if s.export):
        done.append((step, RUNNERS[step.op](step, final, out_prefix, work_dir)))
    return done


def main(argv=None):
    parser = argparse.ArgumentParser(prog="postprocess", description=__doc__.splitlines()[0])
    parser.add_argument("recipe", type=Path, help="demos/<slug>/post.json")
    args = parser.parse_args(argv)

    demo_dir = args.recipe.parent
    slug = demo_dir.resolve().name
    out_prefix = demo_dir / f"{slug}-post"
    try:
        recipe = read_json_object(args.recipe, "post recipe", RecipeError)
        plan, source, warnings = _plan(recipe, demo_dir, slug)
        for warning in warnings:
            print(f"postprocess: warning: {warning}", file=sys.stderr)
        with tempfile.TemporaryDirectory(prefix="postprocess-") as work_dir:
            done = postprocess(plan, source, out_prefix, Path(work_dir))
    except (RecipeError, PostError) as exc:
        print(f"postprocess: error: {exc}", file=sys.stderr)
        return 1

    for step, path in done:
        print(step.line + (f"\t{path}" if path else ""))
    print(f"total\t{plan.total_s:.3f}s\t{out_prefix}.mp4")
    print(f"subtitles\t{len(plan.cues)} cues\t{out_prefix}.srt")
    return 0


def _with_audio_files(recipe, demo_dir):
    """The recipe with its audio files (music, a custom click sound) as paths from the
    recipe's folder; PostError when one is missing."""
    recipe = dict(recipe)
    for key in ("click_sounds", "music"):
        if "file" in recipe.get(key, {}):
            path = demo_dir / recipe[key]["file"]
            if not path.is_file():
                raise PostError(
                    f"{key} file not found: {path}; audio is never downloaded, "
                    f"so give a file you have the rights to"
                )
            recipe[key] = {**recipe[key], "file": str(path)}
    return recipe


def _plan(recipe, demo_dir, slug):
    """Validate the recipe, read the source's length, cues and edit map; build the plan.

    Returns (plan, source, warnings).
    """
    validate_recipe(recipe)
    recipe = _with_audio_files(recipe, demo_dir)
    source = demo_dir / recipe.get("source", f"{slug}.mp4")
    if not source.is_file():
        raise PostError(f"source video not found: {source}; run compose first")
    if source.resolve() == Path(f"{demo_dir / slug}-post.mp4").resolve():
        raise PostError("the source must not be the post-processed video itself")
    srt = source.with_suffix(".srt")
    if not srt.is_file():
        raise PostError(f"subtitles not found: {srt}; re-run compose to write them")
    warnings = []
    edit_map_path = source.with_suffix(".editmap.json")
    if edit_map_path.is_file():
        edit_map = read_json_object(edit_map_path, "edit map", PostError)
    else:
        edit_map = None
        warnings.append(
            f"no edit map ({edit_map_path.name}), so scene-anchored times are "
            f"unavailable; re-run compose to write it"
        )
    images = {
        "cards.title.logo": recipe.get("cards", {}).get("title", {}).get("logo"),
        "watermark.image": recipe.get("watermark", {}).get("image"),
    }
    for where, image in images.items():
        if image and not (demo_dir / image).is_file():
            raise PostError(f"{where} not found: {demo_dir / image}")
    spec_path = demo_dir / "demo.json"
    spec = read_json_object(spec_path, "demo spec", PostError) if spec_path.is_file() else {}
    translations = {}
    for lang in recipe.get("translations", ()):
        path = source.with_suffix(f".{lang}.srt")
        if not path.is_file():
            raise PostError(
                f"translation not found: {path}; write it first "
                f"(the cues of {srt.name}, translated, same count and timings)"
            )
        translations[lang] = parse_srt(path.read_text(encoding="utf-8"))
    source_s, width, height = probe(source)
    plan = build_post_plan(
        recipe,
        source_s,
        parse_srt(srt.read_text(encoding="utf-8")),
        edit_map,
        card_defaults(spec, slug),
        translations=translations,
        frame=(width, height),
        bubble=_bubble(spec),
    )
    return plan, source, [*warnings, *plan.warnings]


def _bubble(spec):
    """(corner, size) of the presenter bubble in the demo spec, or None without one."""
    avatar = spec.get("avatar")
    if not isinstance(avatar, dict):
        return None
    return avatar.get("corner", DEFAULT_CORNER), avatar.get("size", DEFAULT_SIZE)


if __name__ == "__main__":
    sys.exit(main())
