"""Compose a fitted, narrated demo video from a raw screencast (stdlib + ffmpeg).

Usage:
    python3 scripts/compose.py demos/<slug>/demo.json [--raw RAW] [--timeline TIMELINE]
        [--audio-dir DIR] [--durations DURATIONS] [--out FINAL] [--burn-subs]

Defaults are relative to the demo spec's folder: raw.mp4, timeline.jsonl,
audio/ (with <scene-id>.wav), audio/durations.json and final.mp4.

Next to FINAL it also writes FINAL's name with .srt (one cue per narration
sentence) and transcript.md (scene titles + narration). --burn-subs also draws
the subtitles into the video: with libass when ffmpeg has the `subtitles`
filter, otherwise as cue images rendered by rsvg-convert and overlaid.

Each scene is cut from the raw recording, its frozen frames are dropped with
mpdecimate, and it is fitted to its narration (see edit_plan.fit). The scenes
are joined in spec order and muxed with the narration as 30 fps H.264 + AAC.
Every ffmpeg command is built as an argument list by a ``*_cmd`` function and
run separately by ``run``.
"""

import argparse
import shutil
import sys
import tempfile
import textwrap
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import proc
from demo_spec import SpecError, load_spec, read_json_object
from edit_plan import FPS, PlanError, build_plan, load_timeline
from tts import SAMPLE_RATE

X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]


class ComposeError(RuntimeError):
    """Raised when an input is missing or an ffmpeg step fails."""


# --- command builders (pure: arguments in, argument list out) ---------------------------

def decimate_cmd(raw, trim_start, trim_end, out):
    """Cut [trim_start, trim_end) from the raw video at 30 fps and drop frozen frames."""
    chain = (f"trim=start={trim_start:.3f}:end={trim_end:.3f},setpts=PTS-STARTPTS,"
             f"fps={FPS},mpdecimate,setpts=N/FRAME_RATE/TB")
    return ["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-vf", chain, "-an",
            "-r", str(FPS), *X264, str(out)]


def count_frames_cmd(path):
    return ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
            "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)]


def fit_cmd(src, scene_fit, frames, viewport, out):
    """Apply a fit to a decimated segment, scale to the viewport, emit exactly ``frames``."""
    width, height = _even(viewport["width"]), _even(viewport["height"])
    filters = []
    if scene_fit.speed != 1.0:
        filters.append(f"setpts=PTS/{scene_fit.speed:.6f}")
    filters += [
        f"fps={FPS}",
        f"scale={width}:{height}:force_original_aspect_ratio=decrease",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        "setsar=1",
        # Always clone the last frame a little past the target, then cut at
        # exactly ``frames``: covers the hold and any rounding short-fall.
        f"tpad=stop_mode=clone:stop_duration={scene_fit.hold_s + 1:.3f}",
    ]
    return ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", ",".join(filters), "-an",
            "-frames:v", str(frames), "-r", str(FPS), *X264, str(out)]


def concat_video_cmd(list_file, out):
    return ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c", "copy", str(out)]


def concat_audio_cmd(wavs_and_lengths, out):
    """Pad (apad) or trim each narration WAV to its scene length and join them."""
    args = ["ffmpeg", "-v", "error", "-y"]
    for wav, _ in wavs_and_lengths:
        args += ["-i", str(wav)]
    parts = []
    for index, (_, seconds) in enumerate(wavs_and_lengths):
        parts.append(f"[{index}:a]aresample={SAMPLE_RATE},aformat=channel_layouts=mono,"
                     f"apad=whole_dur={seconds:.6f},atrim=end={seconds:.6f},"
                     f"asetpts=PTS-STARTPTS[a{index}]")
    joined = "".join(f"[a{i}]" for i in range(len(wavs_and_lengths)))
    parts.append(f"{joined}concat=n={len(wavs_and_lengths)}:v=0:a=1[out]")
    return args + ["-filter_complex", ";".join(parts), "-map", "[out]",
                   "-c:a", "pcm_s16le", str(out)]


def subtitles_burn_cmd(video, srt_name, out):
    """Burn an .srt with libass (the ``subtitles`` filter); run with cwd = the srt's folder."""
    style = ("FontName=Helvetica,FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
             "BorderStyle=1,Outline=1.5,Shadow=0,MarginV=18")
    return ["ffmpeg", "-v", "error", "-y", "-i", str(video),
            "-vf", f"subtitles=filename={srt_name}:force_style='{style}'",
            "-an", "-r", str(FPS), *X264, str(out)]


def overlay_burn_cmd(video, overlays, out):
    """Burn pre-rendered cue images: ``overlays`` is [(png, start_s, end_s, y)], one per cue."""
    args = ["ffmpeg", "-v", "error", "-y", "-i", str(video)]
    for png, *_ in overlays:
        args += ["-i", str(png)]
    chain, last = [], "0:v"
    for index, (_, start, end, y) in enumerate(overlays, start=1):
        chain.append(f"[{last}][{index}:v]overlay=x=0:y={y}:"
                     f"enable='between(t,{start:.3f},{end:.3f})'[v{index}]")
        last = f"v{index}"
    return args + ["-filter_complex", ";".join(chain), "-map", f"[{last}]",
                   "-an", "-r", str(FPS), *X264, str(out)]


def svg_to_png_cmd(svg, png):
    return ["rsvg-convert", "-o", str(png), str(svg)]


def mux_cmd(video, audio, out):
    return ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(audio),
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(out)]


# --- text outputs (pure) -------------------------------------------------------------

def srt_text(cues):
    """Render subtitle cues as a SubRip (.srt) document."""
    blocks = [f"{n}\n{_srt_time(cue.start_s)} --> {_srt_time(cue.end_s)}\n{cue.text}\n"
              for n, cue in enumerate(cues, start=1)]
    return "\n".join(blocks)


def transcript_text(title, plan):
    """Render the narration as Markdown: the demo title, then each scene's title and narration."""
    lines = [f"# {title}", ""]
    for scene in plan.scenes:
        lines += [f"## {scene.title}", "", scene.narration.strip(), ""]
    return "\n".join(lines)


def cue_svg(text, width, height):
    """Render one cue as a transparent ``width``-wide SVG strip of centred, outlined text.

    Returns (svg, strip_height). Used to burn subtitles when ffmpeg lacks libass.
    """
    font = max(14, round(height * 0.045))
    line_h = round(font * 1.3)
    per_line = max(10, int(width * 0.85 / (font * 0.55)))
    lines = textwrap.wrap(text, per_line) or [""]
    strip_h = line_h * len(lines) + font
    tspans = "".join(
        f'<tspan x="{width / 2:.0f}" y="{font // 2 + line_h * (i + 1) - (line_h - font)}">'
        f"{xml_escape(line)}</tspan>"
        for i, line in enumerate(lines)
    )
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{strip_h}">'
           f'<text font-family="Helvetica, Arial, sans-serif" font-size="{font}" '
           f'font-weight="600" text-anchor="middle" fill="white" stroke="black" '
           f'stroke-width="{max(2, font // 6)}" stroke-linejoin="round" paint-order="stroke">'
           f"{tspans}</text></svg>")
    return svg, strip_h


def _srt_time(seconds):
    ms = max(0, round(seconds * 1000))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


# --- runner ---------------------------------------------------------------------------

def run(cmd, cwd=None):
    """Run one command; raise ComposeError with ffmpeg's message if it fails."""
    return proc.run(cmd, cwd=cwd, error=ComposeError)


def burn_method():
    """How subtitles can be burned in here: "subtitles" (libass), "overlay" (rsvg-convert) or None."""
    try:
        filters = run(["ffmpeg", "-hide_banner", "-filters"])
    except ComposeError:
        filters = ""
    if any(line.split()[1:2] == ["subtitles"] for line in filters.splitlines()):
        return "subtitles"
    if shutil.which("rsvg-convert"):
        return "overlay"
    return None


def burn_subtitles(video, cues, viewport, method, out, work_dir):
    """Burn ``cues`` into ``video`` with libass, or as pre-rendered images overlaid by ffmpeg."""
    if method == "subtitles":
        (work_dir / "subs.srt").write_text(srt_text(cues), encoding="utf-8")
        run(subtitles_burn_cmd(Path(video).resolve(), "subs.srt", Path(out).resolve()),
            cwd=work_dir)
        return
    width, height = _even(viewport["width"]), _even(viewport["height"])
    overlays = []
    for index, cue in enumerate(cues):
        svg, strip_h = cue_svg(cue.text, width, height)
        svg_path, png = work_dir / f"cue-{index:03d}.svg", work_dir / f"cue-{index:03d}.png"
        svg_path.write_text(svg, encoding="utf-8")
        run(svg_to_png_cmd(svg_path, png))
        y = max(0, height - strip_h - round(height * 0.04))
        overlays.append((png, cue.start_s, cue.end_s, y))
    if not overlays:
        shutil.copyfile(video, out)
        return
    run(overlay_burn_cmd(video, overlays, out))


def compose(spec, plan, raw, audio_dir, out, work_dir, burn_subs=False):
    """Run the ffmpeg pipeline for ``plan``; return the plan re-fitted to measured lengths.

    With ``burn_subs`` the plan's subtitle cues are drawn into the video.
    """
    raw, audio_dir, work_dir = Path(raw), Path(audio_dir), Path(work_dir)
    method = burn_method() if burn_subs else None
    if burn_subs and method is None:
        raise ComposeError("--burn-subs needs an ffmpeg with libass (the `subtitles` filter) "
                           "or rsvg-convert (`brew install librsvg`); drop the flag to get "
                           "the subtitles only as a separate .srt")
    if not raw.is_file():
        raise ComposeError(f"raw recording not found: {raw}")
    wavs = {scene.id: audio_dir / f"{scene.id}.wav" for scene in plan.scenes}
    for scene_id, wav in wavs.items():
        if not wav.is_file():
            raise ComposeError(f"narration audio for scene '{scene_id}' not found: {wav}")

    decimated, lengths = {}, {}
    for index, scene in enumerate(plan.scenes):
        path = work_dir / f"{index:03d}-decimated.mp4"
        run(decimate_cmd(raw, scene.trim_start, scene.trim_end, path))
        frames = int(run(count_frames_cmd(path)).strip() or 0)
        if frames == 0:
            raise ComposeError(
                f"scene '{scene.id}' has no video frames in its trim window "
                f"{scene.trim_start:.3f}-{scene.trim_end:.3f}s; is it past the end of {raw}?"
            )
        decimated[scene.id] = path
        lengths[scene.id] = frames / FPS
    plan = plan.with_video_lengths(lengths)

    list_lines, audio_parts = [], []
    for index, scene in enumerate(plan.scenes):
        fitted = work_dir / f"{index:03d}-fitted.mp4"
        run(fit_cmd(decimated[scene.id], scene.fit, scene.frames, spec["viewport"], fitted))
        list_lines.append(f"file '{fitted.as_posix()}'")
        audio_parts.append((wavs[scene.id], scene.scene_s))
    list_file = work_dir / "scenes.txt"
    list_file.write_text("\n".join(list_lines) + "\n")

    video, audio = work_dir / "video.mp4", work_dir / "audio.wav"
    run(concat_video_cmd(list_file, video))
    run(concat_audio_cmd(audio_parts, audio))
    if burn_subs:
        burned = work_dir / "burned.mp4"
        burn_subtitles(video, plan.cues, spec["viewport"], method, burned, work_dir)
        video = burned
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    run(mux_cmd(video, audio, out))
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(prog="compose", description=__doc__.splitlines()[0])
    parser.add_argument("spec", type=Path, help="demos/<slug>/demo.json")
    parser.add_argument("--raw", type=Path, help="raw screencast (default: <demo>/raw.mp4)")
    parser.add_argument("--timeline", type=Path, help="default: <demo>/timeline.jsonl")
    parser.add_argument("--audio-dir", type=Path, help="default: <demo>/audio")
    parser.add_argument("--durations", type=Path, help="default: <audio-dir>/durations.json")
    parser.add_argument("--out", type=Path, help="default: <demo>/final.mp4")
    parser.add_argument("--burn-subs", action="store_true",
                        help="also burn the subtitles into the video (the .srt is always written)")
    args = parser.parse_args(argv)

    demo_dir = args.spec.parent
    audio_dir = args.audio_dir or demo_dir / "audio"
    out = args.out or demo_dir / "final.mp4"
    srt_path, transcript_path = out.with_suffix(".srt"), out.parent / "transcript.md"
    try:
        spec = load_spec(args.spec)
        timeline = load_timeline(args.timeline or demo_dir / "timeline.jsonl")
        durations = read_json_object(args.durations or audio_dir / "durations.json",
                                     "durations", ComposeError, hint="; run tts.py first",
                                     shape="a JSON object of scene id -> seconds")
        plan = build_plan(spec, timeline, durations)
        with tempfile.TemporaryDirectory(prefix="compose-") as work_dir:
            plan = compose(spec, plan, args.raw or demo_dir / "raw.mp4", audio_dir, out, work_dir,
                           burn_subs=args.burn_subs)
        srt_path.write_text(srt_text(plan.cues), encoding="utf-8")
        transcript_path.write_text(transcript_text(spec.get("title") or demo_dir.name, plan),
                                   encoding="utf-8")
    except (SpecError, PlanError, ComposeError) as exc:
        print(f"compose: error: {exc}", file=sys.stderr)
        return 1

    for scene in plan.scenes:
        print(f"{scene.id}\t{scene.fit.strategy}\t{scene.fit.speed:.2f}x\t"
              f"video {scene.video_s:.3f}s\taudio {scene.audio_s:.3f}s\tscene {scene.scene_s:.3f}s")
    print(f"total\t{plan.total_s:.3f}s\t{out}")
    print(f"subtitles\t{len(plan.cues)} cues\t{srt_path}")
    print(f"transcript\t{transcript_path}")
    return 0


def _even(value):
    return int(value) - int(value) % 2


if __name__ == "__main__":
    sys.exit(main())
