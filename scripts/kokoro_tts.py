# /// script
# requires-python = ">=3.11"
# dependencies = ["kokoro-onnx==0.6.1"]
# ///
"""Render one text with the Kokoro-82M voice model into a 24 kHz mono WAV.

Usage:
    uv run --script scripts/kokoro_tts.py --voice af_heart --out OUT.wav TEXT

tts.py runs this for the `kokoro` backend; it is a separate script so that tts.py stays
stdlib only and uv installs kokoro-onnx into its own cached environment. The model files
(~340 MB, Apache-2.0) are downloaded once into $XDG_CACHE_HOME/product-demo/kokoro
(default ~/.cache/product-demo/kokoro) and checked against pinned SHA-256 digests.
"""

import argparse
import hashlib
import os
import sys
import urllib.request
import wave
from pathlib import Path

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
MODEL_FILES = {
    "kokoro-v1.0.onnx": "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}
# Voice-name prefix -> espeak language. Japanese (j) and Mandarin (z) voices need extra
# phonemizers that kokoro-onnx doesn't bundle, so they are left out.
LANGS = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi", "i": "it", "p": "pt-br"}


def cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "product-demo" / "kokoro"


def fetch_models(directory):
    """Download any missing model file into ``directory``; return the directory."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, digest in MODEL_FILES.items():
        target = directory / name
        if target.exists():
            continue
        print(f"kokoro: downloading {name} (once) into {directory}", file=sys.stderr)
        partial = target.with_suffix(target.suffix + ".part")
        sha = hashlib.sha256()
        with (
            urllib.request.urlopen(RELEASE + name, timeout=60) as response,
            partial.open("wb") as out,
        ):
            while chunk := response.read(1 << 20):
                sha.update(chunk)
                out.write(chunk)
        if sha.hexdigest() != digest:
            partial.unlink()
            sys.exit(f"kokoro: {name} failed its checksum; try again")
        partial.rename(target)
    return directory


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--voice", required=True, help="Kokoro voice, e.g. af_heart")
    parser.add_argument("--out", required=True, type=Path, help="WAV file to write")
    parser.add_argument("text")
    args = parser.parse_args(argv)

    lang = LANGS.get(args.voice[:1])
    if lang is None:
        sys.exit(f"kokoro: voice {args.voice!r} is not supported (use an a/b/e/f/h/i/p voice)")

    import numpy as np
    from kokoro_onnx import Kokoro

    models = fetch_models(cache_dir())
    kokoro = Kokoro(str(models / "kokoro-v1.0.onnx"), str(models / "voices-v1.0.bin"))
    if args.voice not in kokoro.get_voices():
        known = ", ".join(v for v in sorted(kokoro.get_voices()) if v[:1] in LANGS)
        sys.exit(f"kokoro: unknown voice {args.voice!r} (known: {known})")
    samples, rate = kokoro.create(args.text, voice=args.voice, lang=lang)

    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    with wave.open(str(args.out), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)


if __name__ == "__main__":
    main()
