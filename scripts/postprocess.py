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
from compose import srt_text, svg_to_png_cmd
from demo_spec import read_json_object
from avatar import DEFAULT_CORNER, DEFAULT_SIZE
from post_plan import (RecipeError, build_post_plan, contact_sheet_svg, frame_cmd, overlay_cmd,
                       parse_srt, validate_recipe)

CONTACT_WIDTH = 480  # widest frame in a contact sheet


class PostError(RuntimeError):
    """Raised when an input is missing or an ffmpeg step fails."""


def run(cmd):
    return proc.run(cmd, error=PostError)


def probe_cmd(path):
    return ["ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path)]


def probe(path):
    """(duration_s, width, height) of a video."""
    info = json.loads(run(probe_cmd(path)))
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
    render_svg(contact_sheet_svg(frames, step.params["columns"], frame_w, frame_h),
               work_dir / "contact.svg", out, "the contact sheet's frame times")
    return out


def _run_overlay_step(step, video, out_prefix, work_dir):
    """Overlay the step's layers: SVGs rendered to PNGs here, images from the demo folder."""
    layers = []
    for layer in step.params["layers"]:
        if layer.svg is None:
            image = Path(out_prefix).parent / layer.image
            if not image.is_file():
                raise PostError(f"{step.op} image not found: {image}")
        else:
            image = work_dir / layer.image
            render_svg(layer.svg, image.with_suffix(".svg"), image, f"the {step.op}")
        layers.append(replace(layer, image=image))
    out = work_dir / f"{step.op}.mp4"
    run(overlay_cmd(video, out, layers))
    return out


def render_svg(svg, svg_path, png, what):
    """Write ``svg`` and convert it to ``png`` with rsvg-convert (ADR-0001)."""
    if not shutil.which("rsvg-convert"):
        raise PostError(f"{what} needs rsvg-convert to draw text (`brew install librsvg`)")
    svg_path.write_text(svg, encoding="utf-8")
    run(svg_to_png_cmd(svg_path, png))


# How each step op is run: (step, video, out_prefix, work_dir) -> the step's file.
RUNNERS = {
    "trim": _run_video_step,
    "speed": _run_video_step,
    "watermark": _run_overlay_step,
    "callouts": _run_overlay_step,
    "progress_bar": _run_video_step,

    # cards (#19)

    # subtitle_tracks, chapters (#21)

    # click_sounds, music (#20)

    # size_cap, gif, poster (#22)

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


def _plan(recipe, demo_dir, slug):
    """Validate the recipe, read the source's length, cues and edit map; build the plan.

    Returns (plan, source, warnings).
    """
    validate_recipe(recipe)
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
        warnings.append(f"no edit map ({edit_map_path.name}), so scene-anchored times are "
                        f"unavailable; re-run compose to write it")
    source_s, width, height = probe(source)
    plan = build_post_plan(recipe, source_s, parse_srt(srt.read_text(encoding="utf-8")),
                           edit_map, frame=(width, height), bubble=_bubble(demo_dir))
    return plan, source, [*warnings, *plan.warnings]


def _bubble(demo_dir):
    """(corner, size) of the presenter bubble in the demo spec, or None without one."""
    spec_path = demo_dir / "demo.json"
    if not spec_path.is_file():
        return None
    avatar = read_json_object(spec_path, "demo spec", PostError).get("avatar")
    if not isinstance(avatar, dict):
        return None
    return avatar.get("corner", DEFAULT_CORNER), avatar.get("size", DEFAULT_SIZE)


if __name__ == "__main__":
    sys.exit(main())
