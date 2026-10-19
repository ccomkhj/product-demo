#!/usr/bin/env python3
"""Generate one narration WAV per scene of a demo spec, plus durations.json.

Usage:
    python3 scripts/tts.py demos/<slug>/demo.json [--backend say|openai|elevenlabs]
                           [--out-dir DIR]

Writes <out-dir>/<scene-id>.wav (48 kHz, mono, 16-bit PCM) for every scene and
<out-dir>/durations.json mapping scene id -> seconds, in spec order. The default
out-dir is the `audio/` folder next to the demo spec.

Stdlib only; needs ffmpeg/ffprobe on PATH (and macOS `say` for the say backend).
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from demo_spec import SpecError, choose_backend, load_spec

SAMPLE_RATE = 48000


class TtsError(RuntimeError):
    """Raised when narration audio can't be produced."""


def synth_say(text, voice, out_base, env):
    """Render ``text`` with macOS ``say`` and return the path of the raw audio file."""
    if voice:
        _check_say_voice(voice)
    out_path = out_base.with_suffix(".aiff")
    command = ["say", "-o", str(out_path)]
    if voice:
        command += ["-v", voice]
    command.append(text)
    _run(command, "say")
    return out_path


def _check_say_voice(voice):
    listing = _run(["say", "-v", "?"], "say").stdout
    names = {line.split("  ")[0].strip() for line in listing.splitlines() if line.strip()}
    if voice not in names:
        raise TtsError(f"unknown say voice {voice!r} (list voices with: say -v '?')")


# Backend name -> synthesize(text, voice, out_base: Path, env) -> Path of raw audio.
SYNTHESIZERS = {
    "say": synth_say,
}


def generate(spec, backend, out_dir, env):
    """Write one normalised WAV per scene and durations.json; return the durations."""
    synthesize = SYNTHESIZERS.get(backend)
    if synthesize is None:
        raise TtsError(f"TTS backend {backend!r} is not implemented yet")
    voice = (spec.get("voice") or {}).get("voice")
    out_dir.mkdir(parents=True, exist_ok=True)
    durations = {}
    with tempfile.TemporaryDirectory(prefix="tts-") as tmp:
        for scene in spec["scenes"]:
            scene_id = scene["id"]
            raw = synthesize(scene["narration"].strip(), voice, Path(tmp) / scene_id, env)
            wav = out_dir / f"{scene_id}.wav"
            normalise_to_wav(raw, wav)
            durations[scene_id] = round(measure_seconds(wav), 3)
    (out_dir / "durations.json").write_text(json.dumps(durations, indent=2) + "\n")
    return durations


def normalise_to_wav(source, target):
    _run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
         "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(target)],
        "ffmpeg",
    )


def measure_seconds(path):
    output = _run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        "ffprobe",
    ).stdout.strip()
    try:
        return float(output)
    except ValueError as exc:
        raise TtsError(f"ffprobe could not measure {path}: {output!r}") from exc


def _run(command, tool):
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise TtsError(f"{tool} not found on PATH") from exc
    if result.returncode != 0:
        raise TtsError(f"{tool} failed: {result.stderr.strip() or result.returncode}")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("spec", type=Path, help="path to the demo spec (demo.json)")
    parser.add_argument("--backend", choices=sorted(("say", "openai", "elevenlabs")),
                        help="force a TTS backend (default: voice.backend, then auto-detect)")
    parser.add_argument("--out-dir", type=Path,
                        help="where to write WAVs and durations.json (default: <spec dir>/audio)")
    args = parser.parse_args(argv)
    try:
        spec = load_spec(args.spec)
        backend = choose_backend(args.backend, spec, os.environ)
        out_dir = args.out_dir or args.spec.parent / "audio"
        durations = generate(spec, backend, out_dir, os.environ)
    except (SpecError, TtsError) as exc:
        print(f"tts: error: {exc}", file=sys.stderr)
        return 1
    for scene_id, seconds in durations.items():
        print(f"{scene_id}\t{seconds:.3f}s")
    print(f"wrote {len(durations)} WAV(s) and durations.json to {out_dir} (backend: {backend})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
