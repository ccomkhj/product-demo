"""Shared, dependency-free local voice configuration."""

import json
import math
import os
import platform
import shutil
from pathlib import Path
from typing import NamedTuple

import demo_paths
from proc import ffprobe_cmd

# Where the registry lived before it moved to a per-user folder: an old skill install
# (`git clone` into ~/.claude/skills, `npx skills add`), then a plugin checkout. The first
# one holding a config.json is copied from, once.
LEGACY_DIRS = (
    Path("~/.claude/skills/product-demo/.voices"),
    Path("~/.agents/skills/product-demo/.voices"),
    Path(__file__).resolve().parents[1] / ".voices",
)


def registry_path():
    """The registry of own_voice, own_photo and defaults: ``config.json`` in
    ``$XDG_CONFIG_HOME/product-demo`` (fallback ``~/.config/product-demo``), outside the
    plugin folder so it survives updates. The first call copies an old ``.voices`` registry
    (``LEGACY_DIRS``) there; the old copy stays."""
    directory = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "product-demo"
    if not directory.exists():
        old = next(
            (d for d in map(Path.expanduser, LEGACY_DIRS) if (d / "config.json").is_file()), None
        )
        if old:
            shutil.copytree(old, directory)
    return directory / "config.json"


class QwenModel(NamedTuple):
    """A Hub id's pinned revision, and the hidden size that tells its local snapshots apart."""

    revision: str
    hidden_size: int


QWEN_MODEL = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16"
QWEN_MODELS = {
    QWEN_MODEL: QwenModel("1eccf1cb2519b5a4e8a95b5f0544f3303568164f", 1024),
    "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16": QwenModel(
        "a6eb4f68e4b056f1215157bb696209bc82a6db48", 2048
    ),
}
QWEN_HIDDEN_SIZES = {m.hidden_size for m in QWEN_MODELS.values()}
LANGUAGES = {
    "auto",
    "english",
    "chinese",
    "japanese",
    "korean",
    "german",
    "french",
    "russian",
    "portuguese",
    "spanish",
    "italian",
}


def validate_voice(voice):
    if voice is None:
        return
    if not isinstance(voice, dict):
        raise ValueError("voice must be a JSON object")
    for field in (
        "backend",
        "voice",
        "profile",
        "model",
        "reference_audio",
        "reference_text",
        "language",
    ):
        if field in voice and not isinstance(voice[field], str):
            raise ValueError(f"voice.{field} must be a string")
    speed = voice.get("speed", 1)
    # ffmpeg's atempo filter takes 0.5-2.0 in a single pass.
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not 0.5 <= speed <= 2:
        raise ValueError("voice.speed must be a number from 0.5 to 2")
    pronounce = voice.get("pronounce", {})
    if not isinstance(pronounce, dict) or not all(
        isinstance(text, str) and text.strip() for pair in pronounce.items() for text in pair
    ):
        raise ValueError("voice.pronounce must map nonempty words to nonempty spoken text")


def resolve_voice_config(voice, backend, spec_dir):
    validate_voice(voice)
    config = dict(voice or {})
    if backend != "qwen3":
        return config
    if config.get("profile"):
        if ("reference_audio" in config) != ("reference_text" in config):
            raise ValueError(
                "Override reference_audio and reference_text together, or use the saved profile"
            )
        if config["profile"] != "own_voice":
            raise ValueError("voice.profile must be own_voice")
        try:
            registry = json.loads(registry_path().read_text())
            if not isinstance(registry, dict) or not isinstance(registry.get("own_voice"), dict):
                raise ValueError("own_voice is not registered")
            saved = dict(registry["own_voice"])
            validate_voice(saved)
            if saved.get("profile"):
                raise ValueError("saved voice profiles cannot refer to another profile")
            if "pronounce" in saved:
                raise ValueError("voice.pronounce belongs in the demo spec, not the saved profile")
            if saved.get("backend", "qwen3") != "qwen3":
                raise ValueError("own_voice must use qwen3")
            # Profile paths belong to the registry; per-demo overrides belong to the spec.
            for field in ("reference_audio", "model"):
                if saved.get(field) and not (field == "model" and saved[field] in QWEN_MODELS):
                    saved[field] = str(
                        demo_paths.resolve(registry_path().parent, saved[field]).resolve()
                    )
            config = {**saved, **config}
        except (OSError, ValueError) as exc:
            raise ValueError(f"own_voice needs setup: {exc}; run scripts/own_voice.py") from exc
    for field in ("reference_audio", "reference_text"):
        if not config.get(field, "").strip():
            raise ValueError(f"voice.{field} is required for qwen3")
    if config.get("voice"):
        raise ValueError("qwen3 Base uses reference_audio, not a preset voice.voice")
    reference = demo_paths.resolve(spec_dir, config["reference_audio"])
    with reference.open("rb") as handle:
        if not handle.read(1):
            raise ValueError("voice.reference_audio is empty")
    config["reference_audio"] = str(reference.resolve())
    model = config.get("model", QWEN_MODEL)
    if model not in QWEN_MODELS:
        path = Path(model).expanduser()
        path = path if path.is_absolute() else Path(spec_dir) / path
        if not model.strip() or not path.is_dir():
            raise ValueError(
                f"voice.model must be one of {', '.join(QWEN_MODELS)} or a local snapshot directory"
            )
        data = json.loads((path / "config.json").read_text())
        if (
            not isinstance(data, dict)
            or data.get("model_type") != "qwen3_tts"
            or data.get("tts_model_type") != "base"
            or not isinstance(data.get("talker_config"), dict)
            or data["talker_config"].get("hidden_size") not in QWEN_HIDDEN_SIZES
        ):
            raise ValueError("voice.model must be a Qwen3-TTS 0.6B or 1.7B Base snapshot")
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
        registry = json.loads(registry_path().read_text())
        entry = registry.get("own_photo") if isinstance(registry, dict) else None
        name = entry.get("image") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name.strip():
            raise ValueError("own_photo is not registered")
        photo = (registry_path().parent / Path(name).expanduser()).resolve()
        if not photo.is_file():
            raise ValueError(f"{photo} is missing")
        return photo
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"own_photo needs setup: {exc}; run scripts/own_voice.py --photo <path>"
        ) from exc


def pinned_snapshot(repo, revision):
    """Local path of a pinned Hugging Face snapshot; downloads only when it isn't cached,
    so a warm run never checks the network. Imports huggingface_hub lazily: this module
    stays stdlib-only at import time."""
    from huggingface_hub import snapshot_download

    try:
        return snapshot_download(repo, revision=revision, local_files_only=True)
    except FileNotFoundError:
        return snapshot_download(repo, revision=revision)


def check_platform(caller="qwen3"):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError(
            f"{caller} requires native Apple Silicon macOS and arm64 Python (not Rosetta)"
        )


def probe_reference(path, run):
    output = run(ffprobe_cmd(path, "stream=codec_type:format=duration", "a"))
    data = json.loads(output)
    duration = float(data.get("format", {}).get("duration", 0))
    if not data.get("streams") or not math.isfinite(duration) or duration <= 0:
        raise ValueError("voice.reference_audio must contain nonempty decodable audio")
