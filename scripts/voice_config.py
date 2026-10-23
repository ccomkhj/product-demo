"""Shared, dependency-free local voice configuration."""

import json
import math
from pathlib import Path
import platform

PROFILE_CONFIG = Path(__file__).resolve().parents[1] / ".voices" / "config.json"

QWEN_MODEL = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16"
QWEN_REVISION = "1eccf1cb2519b5a4e8a95b5f0544f3303568164f"
LANGUAGES = {"auto", "english", "chinese", "japanese", "korean", "german", "french",
             "russian", "portuguese", "spanish", "italian"}


def validate_voice(voice):
    if voice is None:
        return
    if not isinstance(voice, dict):
        raise ValueError("voice must be a JSON object")
    for field in ("backend", "voice", "profile", "model", "reference_audio", "reference_text", "language"):
        if field in voice and not isinstance(voice[field], str):
            raise ValueError(f"voice.{field} must be a string")
    speed = voice.get("speed", 1)
    # ffmpeg's atempo filter takes 0.5-2.0 in a single pass.
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not 0.5 <= speed <= 2:
        raise ValueError("voice.speed must be a number from 0.5 to 2")


def resolve_voice_config(voice, backend, spec_dir):
    validate_voice(voice)
    config = dict(voice or {})
    if backend != "qwen3":
        return config
    if config.get("profile"):
        if ("reference_audio" in config) != ("reference_text" in config):
            raise ValueError("Override reference_audio and reference_text together, or use the saved profile")
        if config["profile"] != "own_voice":
            raise ValueError("voice.profile must be own_voice")
        try:
            registry = json.loads(PROFILE_CONFIG.read_text())
            if not isinstance(registry, dict) or not isinstance(registry.get("own_voice"), dict):
                raise ValueError("own_voice is not registered")
            saved = dict(registry["own_voice"])
            validate_voice(saved)
            if saved.get("profile"):
                raise ValueError("saved voice profiles cannot refer to another profile")
            if saved.get("backend", "qwen3") != "qwen3":
                raise ValueError("own_voice must use qwen3")
            # Profile paths belong to the registry; per-demo overrides belong to the spec.
            for field in ("reference_audio", "model"):
                if saved.get(field) and not (field == "model" and saved[field] == QWEN_MODEL):
                    saved[field] = str((PROFILE_CONFIG.parent / Path(saved[field]).expanduser()).resolve())
            config = {**saved, **config}
        except (OSError, ValueError) as exc:
            raise ValueError(f"own_voice needs setup: {exc}; run scripts/own_voice.py") from exc
    for field in ("reference_audio", "reference_text"):
        if not config.get(field, "").strip():
            raise ValueError(f"voice.{field} is required for qwen3")
    if config.get("voice"):
        raise ValueError("qwen3 Base uses reference_audio, not a preset voice.voice")
    reference = Path(config["reference_audio"]).expanduser()
    reference = reference if reference.is_absolute() else Path(spec_dir) / reference
    with reference.open("rb") as handle:
        if not handle.read(1):
            raise ValueError("voice.reference_audio is empty")
    config["reference_audio"] = str(reference.resolve())
    model = config.get("model", QWEN_MODEL)
    if model != QWEN_MODEL:
        path = Path(model).expanduser()
        path = path if path.is_absolute() else Path(spec_dir) / path
        if not model.strip() or not path.is_dir():
            raise ValueError(f"voice.model must be {QWEN_MODEL} or a local snapshot directory")
        data = json.loads((path / "config.json").read_text())
        if (not isinstance(data, dict) or data.get("model_type") != "qwen3_tts"
                or data.get("tts_model_type") != "base"
                or not isinstance(data.get("talker_config"), dict)
                or data["talker_config"].get("hidden_size") != 1024):
            raise ValueError("voice.model must be a Qwen3-TTS 0.6B Base snapshot")
        model = str(path.resolve())
    config["model"] = model
    language = config.get("language", "English").lower()
    if language not in LANGUAGES:
        raise ValueError(f"unsupported voice.language {language!r}")
    config["language"] = language
    return config


def saved_photo():
    """The registered ``own_photo`` as an absolute path; ValueError when it is missing."""
    try:
        registry = json.loads(PROFILE_CONFIG.read_text())
        entry = registry.get("own_photo") if isinstance(registry, dict) else None
        name = entry.get("image") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name.strip():
            raise ValueError("own_photo is not registered")
        photo = (PROFILE_CONFIG.parent / Path(name).expanduser()).resolve()
        if not photo.is_file():
            raise ValueError(f"{photo} is missing")
        return photo
    except (OSError, ValueError) as exc:
        raise ValueError(f"own_photo needs setup: {exc}; run scripts/own_voice.py --photo <path>") from exc


def check_platform():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("qwen3 requires native Apple Silicon macOS and arm64 Python (not Rosetta)")


def probe_reference(path, run):
    output = run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                  "-show_entries", "stream=codec_type:format=duration", "-of", "json", str(path)])
    data = json.loads(output)
    duration = float(data.get("format", {}).get("duration", 0))
    if not data.get("streams") or not math.isfinite(duration) or duration <= 0:
        raise ValueError("voice.reference_audio must contain nonempty decodable audio")
