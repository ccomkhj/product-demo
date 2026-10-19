"""Demo spec loading and validation (stdlib only)."""

import copy
import json

DEFAULT_VIEWPORT = {"width": 1920, "height": 1080}
BACKENDS = ("say", "openai", "elevenlabs")
ACTION_TYPES = ("point", "click", "dblclick", "key", "type", "scroll", "back", "wait")


class SpecError(ValueError):
    """Raised when a demo spec is malformed."""


def load_spec(path):
    """Read a demo spec JSON file and return it validated, with defaults filled in."""
    try:
        with open(path, encoding="utf-8") as handle:
            spec = json.load(handle)
    except OSError as exc:
        raise SpecError(f"cannot read demo spec {path}: {exc.strerror}") from exc
    except json.JSONDecodeError as exc:
        raise SpecError(f"demo spec {path} is not valid JSON: {exc}") from exc
    if not isinstance(spec, dict):
        raise SpecError(f"demo spec {path} must be a JSON object")
    return validate_spec(spec)


def validate_spec(spec):
    """Return a validated copy of ``spec`` with defaults filled in."""
    result = copy.deepcopy(spec)
    result.setdefault("viewport", dict(DEFAULT_VIEWPORT))
    scenes = result.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise SpecError("demo spec must have a 'scenes' list with at least one scene")
    seen_ids = {}
    for index, scene in enumerate(result["scenes"], start=1):
        where = _describe(index, scene)
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
        for position, action in enumerate(scene.get("actions", []), start=1):
            action_type = action.get("type")
            if action_type not in ACTION_TYPES:
                raise SpecError(
                    f"{where}, action {position}: unknown action type {action_type!r} "
                    f"(allowed: {', '.join(ACTION_TYPES)})"
                )
    return result


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
    return "say"


def _describe(index, scene):
    label = f"scene {index}"
    if scene.get("id"):
        label += f" '{scene['id']}'"
    if scene.get("title"):
        label += f" ({scene['title']})"
    return label
