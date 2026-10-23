# /// script
# requires-python = ">=3.11"
# dependencies = ["mlx-audio==0.6.1"]
# ///
"""Synthesize a demo in one local MLX session; called by tts.py."""

import argparse
import json
from pathlib import Path
import sys
import wave

from voice_config import QWEN_MODEL, QWEN_REVISION, check_platform, pinned_snapshot


def load_local_model(model):
    from mlx_audio.tts.utils import load_model

    if model == QWEN_MODEL:
        model = pinned_snapshot(model, QWEN_REVISION)
    return load_model(model)


def write_audio(results, path):
    import numpy as np

    count = 0
    rate = None
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        for result in results:
            samples = np.asarray(result.audio).reshape(-1)
            if not samples.size or not np.isfinite(samples).all():
                raise ValueError("model returned empty or nonfinite audio")
            if rate is None:
                rate = result.sample_rate
                wav.setframerate(rate)
            elif rate != result.sample_rate:
                raise ValueError("model changed sample rate within a scene")
            wav.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
            count += samples.size
        if not count:
            wav.setframerate(24000)
            raise ValueError("model returned no audio")


def synthesize(request, out_dir, loader=load_local_model, writer=write_audio):
    config = request["voice"]
    model = loader(config["model"])
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for index, scene in enumerate(request["scenes"]):
        name = f"{index}.wav"
        try:
            results = model.generate(text=scene["narration"].strip(),
                                     ref_audio=config["reference_audio"],
                                     ref_text=config["reference_text"],
                                     lang_code=config["language"])
            writer(results, out_dir / name)
        except Exception as exc:
            raise RuntimeError(f"scene {scene['id']!r}: {exc}") from exc
        manifest[scene["id"]] = name
    (out_dir / "manifest.json").write_text(json.dumps(manifest))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        check_platform()
        synthesize(json.loads(args.request.read_text()), args.out_dir)
    except Exception as exc:
        print(f"qwen3: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
