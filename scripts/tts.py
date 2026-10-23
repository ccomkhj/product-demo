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
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import demo_paths
import proc
from demo_spec import SpecError, load_spec
from narration import spoken
from voice_config import check_platform, probe_reference, resolve_voice_config

SAMPLE_RATE = 48000  # every narration WAV is 48 kHz mono 16-bit PCM; compose resamples to it


class TtsError(RuntimeError):
    """Raised when narration audio can't be produced."""


def synth_say(text, voice, out_base, _env, _transport):
    """Render ``text`` with macOS ``say`` and return the path of the raw audio file."""
    out_path = out_base.with_suffix(".aiff")
    command = ["say", "-o", str(out_path)]
    if voice:
        command += ["-v", voice]
    command.append(text)
    _run(command)
    return out_path


def check_say_voice(config, run):
    """So an unknown voice fails once, up front, naming the command that lists them."""
    voice = config.get("voice")
    if not voice:
        return
    listing = run(["say", "-v", "?"])
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


def check_qwen3_voice(config, run):
    """So a bad machine or recording fails before the slow model download."""
    check_platform()
    probe_reference(config["reference_audio"], run)


def synth_qwen3(scenes, config, directory, _env, _transport):
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


@dataclass(frozen=True)
class Backend:
    """Everything one backend needs, in one place: synthesis, tts and preflight all read it."""

    # synthesize: (scenes, config, directory, env, transport) -> (scene id, raw audio) pairs.
    # tool: (executable, probe args). check_voice: (config, run) -> raises if it can't work.
    # note: preflight's warning for what it can't check (first-run downloads).

    synthesize: object
    api_key: str | None = None
    tool: tuple | None = None
    check_voice: object = None
    note: str | None = None

    def available(self, env):
        """So auto-detection only picks a backend that will run without more setup."""
        if self.api_key:
            return bool(env.get(self.api_key))
        path = env.get("PATH")
        return bool(self.tool and path and shutil.which(self.tool[0], path=path))


UV = ("uv", ("--version",))

BACKENDS = {
    "say": Backend(
        functools.partial(synth_scenes, synth_say),
        tool=("say", ("-v", "?")),
        check_voice=check_say_voice,
    ),
    "kokoro": Backend(
        functools.partial(synth_scenes, synth_kokoro),
        tool=UV,
        note="First synthesis may install dependencies and download models; "
        "preflight does neither.",
    ),
    "openai": Backend(functools.partial(synth_scenes, synth_openai), api_key="OPENAI_API_KEY"),
    "elevenlabs": Backend(
        functools.partial(synth_scenes, synth_elevenlabs), api_key="ELEVENLABS_API_KEY"
    ),
    "qwen3": Backend(
        synth_qwen3,
        tool=UV,
        check_voice=check_qwen3_voice,
        note="First synthesis may download dependencies and model weights; "
        "cache completeness is checked during synthesis.",
    ),
}

# Auto-detection order when neither the flag nor the spec names a backend; say is the fallback.
PREFERRED = ("elevenlabs", "openai", "kokoro")


def choose_backend(flag, spec, env):
    """Pick the TTS backend from (CLI flag, demo spec, environment mapping)."""
    for source, name in (
        ("--backend flag", flag),
        ("voice.backend", (spec.get("voice") or {}).get("backend")),
    ):
        if name:
            if name not in BACKENDS:
                raise SpecError(
                    f"unknown TTS backend {name!r} from {source} (allowed: {', '.join(BACKENDS)})"
                )
            return name
    if (spec.get("voice") or {}).get("profile") == "own_voice":
        return "qwen3"
    return next((name for name in PREFERRED if BACKENDS[name].available(env)), "say")


def generate(spec, backend, out_dir, env, transport=urllib_transport, spec_dir=None):
    """Write one normalised WAV per scene and durations.json; return the durations."""
    chosen = BACKENDS[backend]
    if chosen.api_key and not (env.get(chosen.api_key) or "").strip():
        raise TtsError(
            f"{chosen.api_key} is not set; the {backend} backend needs it "
            f"(set it, or pick another backend with --backend)"
        )
    try:
        config = resolve_voice_config(spec.get("voice"), backend, spec_dir or Path.cwd())
        if chosen.check_voice:
            chosen.check_voice(config, _run)
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
        for scene_id, raw in chosen.synthesize(scenes, config, Path(tmp), env, transport):
            wav = out_dir / f"{scene_id}.wav"
            normalise_to_wav(raw, wav, config.get("speed", 1))
            durations[scene_id] = round(measure_seconds(wav), 3)
    demo_paths.durations(out_dir).write_text(json.dumps(durations, indent=2) + "\n")
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
        proc.ffprobe_cmd(path, "format=duration", output="default=noprint_wrappers=1:nokey=1")
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
        out_dir = args.out_dir or demo_paths.audio_dir(args.spec.parent)
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
