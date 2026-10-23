# /// script
# requires-python = ">=3.11"
# dependencies = ["mlx-whisper==0.4.3"]
# ///
"""Transcribe each scene's narration WAV and compare it with what the voice should say.

Usage:
    uv run --script scripts/check_audio.py demos/<slug>/demo.json

Reads audio/<scene-id>.wav next to the spec, as written by tts.py. Prints one line per
scene: id, ok|check, word-match ratio, and the differences (`+ "heard but not in the
narration"`, `- "in the narration but not heard"`, `"expected" -> "heard"`). Exits 1 when any scene is `check`. Runs whisper locally on
Apple Silicon via MLX; the first run downloads the pinned model.
"""

import argparse
import difflib
from pathlib import Path
import re
import sys

from demo_spec import load_spec
from narration import spoken
from voice_config import check_platform, pinned_snapshot, resolve_voice_config

WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"
WHISPER_REVISION = "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb"
# Word-match ratio below which a scene needs a listen; leaves room for about one word in 20,
# whisper's own error rate on clean speech.
THRESHOLD = 0.95

ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
        "fourteen fifteen sixteen seventeen eighteen nineteen").split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
SCALES = ((10**9, "billion"), (10**6, "million"), (1000, "thousand"), (100, "hundred"))


def number_words(n):
    """``n`` (a whole number) in English words, without "and": 1200 -> "one thousand two hundred"."""
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + (f" {ONES[n % 10]}" if n % 10 else "")
    for size, name in SCALES:
        if n >= size:
            head, rest = divmod(n, size)
            return f"{number_words(head)} {name}" + (f" {number_words(rest)}" if rest else "")


def words(text):
    """Lower-case words without punctuation; hyphens split words, digits become number words."""
    text = text.lower().replace("%", " percent ")
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)  # 1,200 -> 1200
    text = re.sub(r"(\d+)\.(\d+)", lambda m: f"{m[1]} point {' '.join(m[2])}", text)
    text = re.sub(r"\d+", lambda m: f" {number_words(int(m[0]))} ", text)
    text = re.sub(r"['’]", "", text)  # it's -> its
    return re.sub(r"[^\w\s]|_", " ", text).split()


def compare(expected, heard):
    """("ok" or "check", word-match ratio, the differences) of ``heard`` against ``expected``."""
    want, got = words(expected), words(heard)
    matcher = difflib.SequenceMatcher(None, want, got, autojunk=False)
    diff = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        missing, extra = " ".join(want[i1:i2]), " ".join(got[j1:j2])
        if op == "insert":
            diff.append(f'+ "{extra}"')
        elif op == "delete":
            diff.append(f'- "{missing}"')
        elif op == "replace":
            diff.append(f'"{missing}" -> "{extra}"')
    ratio = matcher.ratio()
    return ("ok" if ratio >= THRESHOLD else "check"), ratio, diff


def check(spec, audio_dir, language):
    """Print each scene's verdict; return True when every scene is ok."""
    import mlx_whisper

    model = pinned_snapshot(WHISPER_MODEL, WHISPER_REVISION)
    all_ok = True
    for scene in spec["scenes"]:
        wav = audio_dir / f"{scene['id']}.wav"
        if not wav.is_file():
            raise FileNotFoundError(f"{wav} is missing; run tts.py first")
        heard = mlx_whisper.transcribe(str(wav), path_or_hf_repo=model, language=language)["text"]
        verdict, ratio, diff = compare(spoken(scene["narration"], spec.get("voice")), heard)
        print(f"{scene['id']}\t{verdict}\t{ratio:.2f}\t{'; '.join(diff)}")
        all_ok = all_ok and verdict == "ok"
    return all_ok


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("spec", type=Path, help="path to the demo spec (demo.json)")
    args = parser.parse_args()
    try:
        check_platform("check_audio")
        spec = load_spec(args.spec)
        voice = resolve_voice_config(spec.get("voice"), "qwen3", args.spec.resolve().parent)
        language = None if voice["language"] == "auto" else voice["language"]
        ok = check(spec, args.spec.parent / "audio", language)
    except Exception as exc:
        print(f"check_audio: {exc}", file=sys.stderr)
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
