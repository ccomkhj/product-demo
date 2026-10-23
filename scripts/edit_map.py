"""The edit map, <slug>.editmap.json: post-processing's link to the demo's structure (stdlib).

Compose builds and writes it; post-processing reads it and asks it where scenes, ``at``
phrases and clicks land in the composed video. Times are seconds in that video, rounded
to the millisecond like compose's printed lines. An action's ``index`` is its 0-based place
in the scene's spec actions; only the scene's first action and each ``at`` action have a
known start.
"""

import json
from dataclasses import dataclass

import demo_paths

NEEDS_COMPOSE = "re-run compose to write <slug>.editmap.json"
CLICK_TYPES = ("click", "dblclick", "type")  # a type action clicks its field first
GLIDE_S = 0.6  # the overlay's cursor glide (assets/overlay.js GLIDE_MS): the click lands after it


class EditMapError(ValueError):
    """Raised when an edit map is malformed or a query names something it does not have."""


@dataclass(frozen=True)
class MapAction:
    """One spec action; its start is known only for a scene's first action and ``at`` actions."""

    index: int
    type: str
    start_s: float | None = None
    at: str | None = None


@dataclass(frozen=True)
class MapScene:
    """One scene's span in the composed video, so recipes can anchor to it."""

    id: str
    title: str
    start_s: float
    end_s: float
    actions: tuple = ()


@dataclass(frozen=True)
class EditMap:
    """What post-processing may know of the demo's structure; the rest stays in compose."""

    total_s: float
    subtitles_burned: bool
    scenes: tuple = ()

    def scene(self, scene_id):
        """Recipes name scenes by id; a typo should list the ids that exist."""
        for scene in self.scenes:
            if scene.id == scene_id:
                return scene
        known = ", ".join(scene.id for scene in self.scenes)
        raise EditMapError(f"unknown scene '{scene_id}' (scenes: {known})")

    def anchor_s(self, scene_id, at=None):
        """Recipes anchor times to scenes and phrases, which move whenever compose re-fits."""
        scene = self.scene(scene_id)
        if at is None:
            return scene.start_s
        starts = {action.at: action.start_s for action in scene.actions if action.at is not None}
        if at not in starts:
            known = ", ".join(f"'{phrase}'" for phrase in starts) or "none"
            raise EditMapError(f"scene '{scene.id}' has no action at '{at}' (at phrases: {known})")
        return starts[at]

    def clicks(self):
        """Click sounds need each click's moment: (scene id, action, click_s or None if unknown)."""
        return [
            (scene.id, action, None if action.start_s is None else action.start_s + GLIDE_S)
            for scene in self.scenes
            for action in scene.actions
            if action.type in CLICK_TYPES
        ]


def subtitles_burned(edit_map):
    """Overlays and subtitle tracks must assume burned-in subtitles unless the map says not."""
    return edit_map is None or edit_map.subtitles_burned


def build(spec, plan, burned=False):
    """Post-processing must not depend on compose's edit plan, only on what it hands over."""
    at_starts = {(action.scene_id, action.index): action.start_s for action in plan.actions}
    scenes = []
    for spec_scene, scene in zip(spec["scenes"], plan.scenes):
        actions, at_count = [], 0
        for index, action in enumerate(spec_scene.get("actions", [])):
            if "at" in action:
                at_count += 1
                start_s = _ms(at_starts[(scene.id, at_count)])
            elif index == 0:
                start_s = _ms(scene.offset_s)
            else:
                start_s = None
            actions.append(MapAction(index, action["type"], start_s, action.get("at")))
        scenes.append(
            MapScene(scene.id, scene.title, _ms(scene.offset_s), _ms(scene.end_s), tuple(actions))
        )
    return EditMap(_ms(plan.total_s), burned, tuple(scenes))


def write(edit_map, video):
    """Writes next to ``video`` so post-processing finds it from the video alone."""
    path = demo_paths.edit_map(video)
    path.write_text(json.dumps(to_dict(edit_map), indent=2) + "\n", encoding="utf-8")
    return path


def read(video):
    """None, not an error: demos composed before edit maps existed still post-process."""
    path = demo_paths.edit_map(video)
    if not path.is_file():
        return None
    try:
        return from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise EditMapError(f"edit map {path} is unreadable: {exc}; {NEEDS_COMPOSE}") from None


def to_dict(edit_map):
    """The on-disk format, kept stable so old and new demos read the same."""
    return {
        "total_s": edit_map.total_s,
        "subtitles_burned": edit_map.subtitles_burned,
        "scenes": [
            {
                "id": scene.id,
                "title": scene.title,
                "start_s": scene.start_s,
                "end_s": scene.end_s,
                "actions": [_action_dict(action) for action in scene.actions],
            }
            for scene in edit_map.scenes
        ],
    }


def from_dict(data):
    """Parses the on-disk format once, so a bad map fails here and not mid-plan."""
    try:
        return EditMap(
            data["total_s"],
            data.get("subtitles_burned", True),  # maps from before the key had them burned
            tuple(
                MapScene(
                    scene["id"],
                    scene["title"],
                    scene["start_s"],
                    scene["end_s"],
                    tuple(_action(action) for action in scene["actions"]),
                )
                for scene in data["scenes"]
            ),
        )
    except KeyError as exc:
        raise EditMapError(f"missing key {exc}") from None
    except (TypeError, AttributeError):
        raise EditMapError("not shaped like an edit map") from None


def _action(data):
    # an at action always has a start: anchors resolve to it
    start_s = data["start_s"] if "at" in data else data.get("start_s")
    return MapAction(data["index"], data["type"], start_s, data.get("at"))


def _action_dict(action):
    entry = {"index": action.index, "type": action.type}
    if action.start_s is not None:
        entry["start_s"] = action.start_s
    if action.at is not None:
        entry["at"] = action.at
    return entry


def _ms(seconds):
    return None if seconds is None else round(seconds, 3)
