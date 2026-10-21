"""Demo spec loading and validation (stdlib only)."""

import copy
import json
import math
import shutil

from avatar import CORNERS, DEFAULT_CORNER, DEFAULT_SIZE, MAX_SIZE, MIN_SIZE
from narration import Narration, PhraseError

DEFAULT_VIEWPORT = {"width": 1920, "height": 1080}
BACKENDS = ("say", "kokoro", "openai", "elevenlabs")
ACTION_TYPES = ("point", "click", "dblclick", "key", "type", "scroll", "back", "wait")
PURPOSES = ("feature-announcement", "customer-tutorial", "sales-walkthrough", "custom")


class SpecError(ValueError):
    """Raised when a demo spec is malformed."""


def read_json_object(path, label, error, hint="", shape="a JSON object"):
    """Read a JSON file that must hold an object; raise ``error`` naming ``label`` otherwise.

    ``hint`` is appended to the cannot-read message (e.g. "; run tts.py first");
    ``shape`` describes the expected object in the wrong-type message.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise error(f"cannot read {label} {path}: {exc.strerror}{hint}") from exc
    except json.JSONDecodeError as exc:
        raise error(f"{label} {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise error(f"{label} {path} must be {shape}")
    return data


def load_spec(path):
    """Read a demo spec JSON file and return it validated, with defaults filled in."""
    return validate_spec(read_json_object(path, "demo spec", SpecError))


def validate_spec(spec):
    """Return a validated copy of ``spec`` with defaults filled in."""
    result = copy.deepcopy(spec)
    _validate_metadata(result)
    _validate_avatar(result)
    result.setdefault("viewport", dict(DEFAULT_VIEWPORT))
    scenes = result.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise SpecError("demo spec must have a 'scenes' list with at least one scene")
    seen_ids = {}
    for index, scene in enumerate(result["scenes"], start=1):
        if not isinstance(scene, dict):
            raise SpecError(f"scene {index}: must be a JSON object, got {scene!r}")
        where = _describe(index, scene)
        _validate_checks(scene, where)
        scene_id = scene.get("id")
        if not scene_id:
            raise SpecError(f"{where}: missing id")
        if scene_id in seen_ids:
            raise SpecError(
                f"{where}: duplicate id '{scene_id}' (already used by scene {seen_ids[scene_id]})"
            )
        seen_ids[scene_id] = index
        narration = scene.get("narration")
        if not isinstance(narration, str) or not narration.strip():
            raise SpecError(f"{where}: empty narration")
        url = scene.get("url")
        if not isinstance(url, str) or not url.strip():
            raise SpecError(f"{where}: missing url")
        for position, action in enumerate(scene.get("actions", []), start=1):
            if not isinstance(action, dict):
                raise SpecError(f"{where}, action {position}: must be a JSON object, "
                                f"got {action!r}")
            action_type = action.get("type")
            if action_type not in ACTION_TYPES:
                raise SpecError(
                    f"{where}, action {position}: unknown action type {action_type!r} "
                    f"(allowed: {', '.join(ACTION_TYPES)})"
                )
        anchored = [(number, action["at"])
                    for number, action in enumerate(scene.get("actions", []), start=1)
                    if "at" in action]
        try:
            Narration(narration).phrase_shares([phrase for _, phrase in anchored])
        except PhraseError as exc:
            raise SpecError(f"{where}, action {anchored[exc.index][0]}: 'at' {exc}") from exc
    return result


def positive_seconds(value):
    """Whether a JSON value is a finite positive duration (not a boolean)."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and value > 0 and (isinstance(value, int) or math.isfinite(value)))


def _validate_metadata(spec):
    if "purpose" in spec and spec["purpose"] not in PURPOSES:
        raise SpecError(f"purpose must be one of {', '.join(PURPOSES)}")
    if "outcome" in spec:
        value = spec["outcome"]
        if not isinstance(value, str) or not value.strip():
            raise SpecError("outcome must be a non-empty string")
    if "target_duration_s" in spec and not positive_seconds(spec["target_duration_s"]):
        raise SpecError("target_duration_s must be a finite positive number")


def _validate_avatar(spec):
    if "avatar" not in spec:
        return
    avatar = spec["avatar"]
    if not isinstance(avatar, dict):
        raise SpecError("avatar must be a JSON object with an 'image'")
    image = avatar.get("image")
    if not isinstance(image, str) or not image.strip():
        raise SpecError("avatar.image must be a non-empty path to a photo")
    corner = avatar.setdefault("corner", DEFAULT_CORNER)
    if corner not in CORNERS:
        raise SpecError(f"avatar.corner must be one of {', '.join(CORNERS)}")
    size = avatar.setdefault("size", DEFAULT_SIZE)
    if not positive_seconds(size) or not MIN_SIZE <= size <= MAX_SIZE:
        raise SpecError(f"avatar.size must be a number from {MIN_SIZE} to {MAX_SIZE} "
                        "(the photo's diameter as a share of the video's height)")


def _validate_checks(scene, where):
    for field in ("prerequisites", "expected_outcomes"):
        checks = scene.get(field, [])
        if not isinstance(checks, list):
            raise SpecError(f"{where}: {field} must be a list")
        for position, check in enumerate(checks, start=1):
            label = f"{where}, {field} check {position}"
            if not isinstance(check, dict):
                raise SpecError(f"{label}: must be a JSON object")
            kind = check.get("type")
            if kind not in ("visible_text", "visible_target"):
                raise SpecError(f"{label}: type must be visible_text or visible_target")
            payload = "text" if kind == "visible_text" else "target"
            value = check.get(payload)
            if not isinstance(value, str) or not value.strip():
                raise SpecError(f"{label}: {payload} must be a non-empty string")


def choose_backend(flag, spec, env):
    """Pick the TTS backend from (CLI flag, demo spec, environment mapping)."""
    for source, name in (("--backend flag", flag),
                         ("voice.backend", (spec.get("voice") or {}).get("backend"))):
        if name:
            if name not in BACKENDS:
                raise SpecError(
                    f"unknown TTS backend {name!r} from {source} "
                    f"(allowed: {', '.join(BACKENDS)})"
                )
            return name
    if env.get("ELEVENLABS_API_KEY"):
        return "elevenlabs"
    if env.get("OPENAI_API_KEY"):
        return "openai"
    if env.get("PATH") and shutil.which("uv", path=env["PATH"]):
        return "kokoro"
    return "say"


def _describe(index, scene):
    label = f"scene {index}"
    if scene.get("id"):
        label += f" '{scene['id']}'"
    if scene.get("title"):
        label += f" ({scene['title']})"
    return label
