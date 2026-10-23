#!/usr/bin/env python3
"""Generate one narration WAV per scene of a demo spec, plus durations.json.

Usage:
    uv run python scripts/tts.py demos/<slug>/demo.json [--backend say|kokoro|openai|elevenlabs|qwen3]
                           [--out-dir DIR]

Writes <out-dir>/<scene-id>.wav (48 kHz, mono, 16-bit PCM) for every scene and
<out-dir>/durations.json mapping scene id -> seconds, in spec order. The default
out-dir is the `audio/` folder next to the demo spec.

Backends: `say` (macOS), `kokoro` (the free, local Kokoro-82M model, needs uv),
`openai` (gpt-4o-mini-tts, needs OPENAI_API_KEY) and `elevenlabs` (needs
ELEVENLABS_API_KEY). `qwen3` clones a local reference voice on Apple Silicon via MLX.
`voice.voice` in the spec picks the voice: a `say -v` name, a
Kokoro voice (default af_heart), an OpenAI voice name (default alloy) or an ElevenLabs
voice ID. `voice.speed` (0.5-2, default 1) speeds every backend up or down, keeping pitch.
`voice.pronounce` respells whole words for the voice only; the spec text is unchanged.

Stdlib only (HTTP via urllib); needs ffmpeg/ffprobe on PATH (and macOS `say` for
the say backend, uv for the kokoro backend, which runs scripts/kokoro_tts.py).
"""

import argparse
import functools
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import proc
from demo_spec import BACKENDS, SpecError, choose_backend, load_spec
from narration import spoken
from voice_config import check_platform, probe_reference, resolve_voice_config

SAMPLE_RATE = 48000  # every narration WAV is 48 kHz mono 16-bit PCM; compose resamples to it


class TtsError(RuntimeError):
    """Raised when narration audio can't be produced."""


def synth_say(text, voice, out_base, _env, _transport):
    """Render ``text`` with macOS ``say`` and return the path of the raw audio file."""
    if voice:
        _check_say_voice(voice)
    out_path = out_base.with_suffix(".aiff")
    command = ["say", "-o", str(out_path)]
    if voice:
        command += ["-v", voice]
    command.append(text)
    _run(command)
    return out_path


def _check_say_voice(voice):
    listing = _run(["say", "-v", "?"])
    names = {line.split("  ")[0].strip() for line in listing.splitlines() if line.strip()}
    if voice not in names:
        raise TtsError(f"unknown say voice {voice!r} (list voices with: say -v '?')")


KOKORO_SCRIPT = Path(__file__).resolve().parent / "kokoro_tts.py"
KOKORO_DEFAULT_VOICE = "af_heart"


def synth_kokoro(text, voice, out_base, _env, _transport):
    """Render ``text`` with Kokoro-82M via ``uv run`` and return the raw WAV path."""
    out_path = out_base.with_suffix(".wav")
    _run(
        [
            "uv",
            "run",
            "--quiet",
            "--script",
            str(KOKORO_SCRIPT),
            "--voice",
            voice or KOKORO_DEFAULT_VOICE,
            "--out",
            str(out_path),
            text,
        ]
    )
    return out_path


OPENAI_URL = "https://api.openai.com/v1/audio/speech"
OPENAI_MODEL = "gpt-4o-mini-tts"
OPENAI_DEFAULT_VOICE = "alloy"


def synth_openai(text, voice, out_base, env, transport):
    """Render ``text`` with OpenAI ``gpt-4o-mini-tts`` and return the raw WAV path."""
    payload = {
        "model": OPENAI_MODEL,
        "voice": voice or OPENAI_DEFAULT_VOICE,
        "input": text,
        "response_format": "wav",
    }
    headers = {
        "Authorization": f"Bearer {env['OPENAI_API_KEY'].strip()}",
        "Content-Type": "application/json",
    }
    audio = _post("openai", transport, OPENAI_URL, headers, payload)
    out_path = out_base.with_suffix(".wav")
    out_path.write_bytes(audio)
    return out_path


ELEVENLABS_URL = (
    "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128"
)
ELEVENLABS_MODEL = "eleven_multilingual_v2"
ELEVENLABS_DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"  # "George", an ElevenLabs premade voice


def synth_elevenlabs(text, voice, out_base, env, transport):
    """Render ``text`` with ElevenLabs; ``voice`` is a voice ID. Return the raw MP3 path."""
    voice_id = urllib.parse.quote(voice or ELEVENLABS_DEFAULT_VOICE, safe="")
    payload = {"text": text, "model_id": ELEVENLABS_MODEL}
    headers = {
        "xi-api-key": env["ELEVENLABS_API_KEY"].strip(),
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    audio = _post(
        "elevenlabs", transport, ELEVENLABS_URL.format(voice_id=voice_id), headers, payload
    )
    out_path = out_base.with_suffix(".mp3")
    out_path.write_bytes(audio)
    return out_path


def _post(backend, transport, url, headers, payload):
    """Send a JSON POST through ``transport``; return the body or raise on a non-2xx status."""
    status, body = transport(url, headers, json.dumps(payload).encode())
    if not 200 <= status < 300:
        raise TtsError(f"{backend} request failed with HTTP {status}: {_error_detail(body)}")
    if not body:
        raise TtsError(f"{backend} returned no audio")
    return body


def _error_detail(body):
    """Pull a human-readable message out of an API error body (OpenAI or ElevenLabs shape)."""
    text = body.decode("utf-8", errors="replace").strip()
    try:
        data = json.loads(text)
    except ValueError:
        return text[:300] or "(empty response)"
    for key in ("error", "detail"):
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, dict):
            value = value.get("message") or value
        if value:
            return str(value)[:300]
    return text[:300]


def urllib_transport(url, headers, body, timeout=120):
    """POST ``body`` to ``url``; return (status, response bytes). Never raises on HTTP errors."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except OSError as exc:  # URLError, timeouts, connection resets
        reason = getattr(exc, "reason", exc)
        raise TtsError(f"could not reach {urllib.parse.urlsplit(url).netloc}: {reason}") from exc


def synth_scenes(synthesize, scenes, config, directory, env, transport):
    for index, scene in enumerate(scenes):
        try:
            raw = synthesize(
                scene["narration"].strip(),
                config.get("voice"),
                directory / str(index),
                env,
                transport,
            )
        except TtsError as exc:
            raise TtsError(f"scene {scene['id']!r}: {exc}") from exc
        yield scene["id"], raw


def synth_qwen3(scenes, config, directory, _env, _transport):
    check_platform()
    probe_reference(config["reference_audio"], _run)
    reference = directory / "reference.wav"
    normalise_to_wav(config["reference_audio"], reference)
    config = {**config, "reference_audio": str(reference)}
    request = directory / "request.json"
    request.write_text(json.dumps({"voice": config, "scenes": scenes}))
    output = directory / "raw"
    _run(
        [
            "uv",
            "run",
            "--quiet",
            "--script",
            str(Path(__file__).with_name("qwen3_tts.py")),
            "--request",
            str(request),
            "--out-dir",
            str(output),
        ]
    )
    manifest = json.loads((output / "manifest.json").read_text())
    for index, scene in enumerate(scenes):
        name = manifest.get(scene["id"])
        if name != f"{index}.wav" or not (output / name).is_file():
            raise TtsError(f"scene {scene['id']!r}: invalid qwen3 output manifest")
        yield scene["id"], output / name


SYNTHESIZERS = {
    "say": functools.partial(synth_scenes, synth_say),
    "kokoro": functools.partial(synth_scenes, synth_kokoro),
    "openai": functools.partial(synth_scenes, synth_openai),
    "elevenlabs": functools.partial(synth_scenes, synth_elevenlabs),
    "qwen3": synth_qwen3,
}

# Backend name -> environment variable holding its API key, checked before any request.
API_KEYS = {
    "openai": "OPENAI_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
}


def generate(spec, backend, out_dir, env, transport=urllib_transport, spec_dir=None):
    """Write one normalised WAV per scene and durations.json; return the durations."""
    synthesize = SYNTHESIZERS[backend]
    key_name = API_KEYS.get(backend)
    if key_name and not (env.get(key_name) or "").strip():
        raise TtsError(
            f"{key_name} is not set; the {backend} backend needs it "
            f"(set it, or pick another backend with --backend)"
        )
    try:
        config = resolve_voice_config(spec.get("voice"), backend, spec_dir or Path.cwd())
    except (ValueError, OSError) as exc:
        raise TtsError(str(exc)) from exc
    # Speak from copies, so subtitles, transcript and `at` phrases keep the written spelling.
    config.pop("pronounce", None)  # applied here, not by the synthesizers
    scenes = [
        {**scene, "narration": spoken(scene["narration"], spec.get("voice"))}
        for scene in spec["scenes"]
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    durations = {}
    with tempfile.TemporaryDirectory(prefix="tts-") as tmp:
        for scene_id, raw in synthesize(scenes, config, Path(tmp), env, transport):
            wav = out_dir / f"{scene_id}.wav"
            normalise_to_wav(raw, wav, config.get("speed", 1))
            durations[scene_id] = round(measure_seconds(wav), 3)
    (out_dir / "durations.json").write_text(json.dumps(durations, indent=2) + "\n")
    return durations


def normalise_to_wav(source, target, speed=1):
    """Convert to 48 kHz mono PCM; a speed other than 1 changes the tempo but keeps the pitch."""
    tempo = ["-af", f"atempo={speed}"] if speed != 1 else []
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            *tempo,
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            str(target),
        ],
    )


def measure_seconds(path):
    output = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
    ).strip()
    try:
        return float(output)
    except ValueError as exc:
        raise TtsError(f"ffprobe could not measure {path}: {output!r}") from exc


def _run(command):
    return proc.run(command, error=TtsError)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("spec", type=Path, help="path to the demo spec (demo.json)")
    parser.add_argument(
        "--backend",
        choices=BACKENDS,
        help="force a TTS backend (default: voice.backend, then auto-detect)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        help="where to write WAVs and durations.json (default: <spec dir>/audio)",
    )
    args = parser.parse_args(argv)
    try:
        spec = load_spec(args.spec)
        backend = choose_backend(args.backend, spec, os.environ)
        out_dir = args.out_dir or args.spec.parent / "audio"
        durations = generate(
            spec, backend, out_dir, os.environ, spec_dir=args.spec.resolve().parent
        )
    except (SpecError, TtsError, ValueError, OSError) as exc:
        print(f"tts: error: {exc}", file=sys.stderr)
        return 1
    for scene_id, seconds in durations.items():
        print(f"{scene_id}\t{seconds:.3f}s")
    print(f"wrote {len(durations)} WAV(s) and durations.json to {out_dir} (backend: {backend})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
