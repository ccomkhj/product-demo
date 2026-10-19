"""Compose a fitted, narrated demo video from a raw screencast (stdlib + ffmpeg).

Usage:
    python3 scripts/compose.py demos/<slug>/demo.json [--raw RAW] [--timeline TIMELINE]
        [--audio-dir DIR] [--durations DURATIONS] [--out FINAL]

Defaults are relative to the demo spec's folder: raw.mp4, timeline.jsonl,
audio/ (with <scene-id>.wav), audio/durations.json and final.mp4.

Each scene is cut from the raw recording, its frozen frames are dropped with
mpdecimate, and it is fitted to its narration (see edit_plan.fit). The scenes
are joined in spec order and muxed with the narration as 30 fps H.264 + AAC.
Every ffmpeg command is built as an argument list by a ``*_cmd`` function and
run separately by ``run``.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from demo_spec import SpecError, load_spec
from edit_plan import PlanError, build_plan, load_timeline

FPS = 30
SAMPLE_RATE = 48000
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


def mux_cmd(video, audio, out):
    return ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(audio),
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(out)]


# --- runner ---------------------------------------------------------------------------

def run(cmd):
    """Run one command; raise ComposeError with ffmpeg's message if it fails."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise ComposeError(f"{cmd[0]} not found on PATH") from exc
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-5:])
        raise ComposeError(f"{cmd[0]} failed (exit {result.returncode}): {tail}")
    return result.stdout


def compose(spec, plan, raw, audio_dir, out, work_dir):
    """Run the ffmpeg pipeline for ``plan``; return the plan re-fitted to measured lengths."""
    raw, audio_dir, work_dir = Path(raw), Path(audio_dir), Path(work_dir)
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
        frames = max(1, round(scene.scene_s * FPS))
        fitted = work_dir / f"{index:03d}-fitted.mp4"
        run(fit_cmd(decimated[scene.id], scene.fit, frames, spec["viewport"], fitted))
        list_lines.append(f"file '{fitted.as_posix()}'")
        audio_parts.append((wavs[scene.id], frames / FPS))
    list_file = work_dir / "scenes.txt"
    list_file.write_text("\n".join(list_lines) + "\n")

    video, audio = work_dir / "video.mp4", work_dir / "audio.wav"
    run(concat_video_cmd(list_file, video))
    run(concat_audio_cmd(audio_parts, audio))
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
    args = parser.parse_args(argv)

    demo_dir = args.spec.parent
    audio_dir = args.audio_dir or demo_dir / "audio"
    try:
        spec = load_spec(args.spec)
        timeline = load_timeline(args.timeline or demo_dir / "timeline.jsonl")
        durations = _load_durations(args.durations or audio_dir / "durations.json")
        plan = build_plan(spec, timeline, durations)
        with tempfile.TemporaryDirectory(prefix="compose-") as work_dir:
            plan = compose(spec, plan, args.raw or demo_dir / "raw.mp4", audio_dir,
                           args.out or demo_dir / "final.mp4", work_dir)
    except (SpecError, PlanError, ComposeError) as exc:
        print(f"compose: error: {exc}", file=sys.stderr)
        return 1

    for scene in plan.scenes:
        print(f"{scene.id}\t{scene.fit.strategy}\t{scene.fit.speed:.2f}x\t"
              f"video {scene.video_s:.3f}s\taudio {scene.audio_s:.3f}s\tscene {scene.scene_s:.3f}s")
    print(f"total\t{plan.total_s:.3f}s\t{args.out or demo_dir / 'final.mp4'}")
    return 0


def _load_durations(path):
    try:
        with open(path, encoding="utf-8") as handle:
            durations = json.load(handle)
    except OSError as exc:
        raise ComposeError(f"cannot read durations {path}: {exc.strerror}; run tts.py first") from exc
    except json.JSONDecodeError as exc:
        raise ComposeError(f"durations {path} is not valid JSON: {exc}") from exc
    if not isinstance(durations, dict):
        raise ComposeError(f"durations {path} must be a JSON object of scene id -> seconds")
    return durations


def _even(value):
    return int(value) - int(value) % 2


if __name__ == "__main__":
    sys.exit(main())
