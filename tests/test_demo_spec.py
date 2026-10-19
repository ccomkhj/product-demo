import pytest

from demo_spec import SpecError, validate_spec


def make_spec(**overrides):
    spec = {
        "title": "Acme tour",
        "product": "Acme",
        "audience": "new users",
        "scenes": [
            {
                "id": "home",
                "title": "Home page",
                "url": "https://example.com",
                "actions": [{"type": "point", "target": "the main heading"}],
                "narration": "This is the home page.",
            },
            {
                "id": "details",
                "title": "Details",
                "url": "https://example.com/details",
                "actions": [{"type": "click", "target": "More information link"}],
                "narration": "Here are the details.",
            },
        ],
    }
    spec.update(overrides)
    return spec


def test_valid_spec_is_accepted_with_default_viewport():
    result = validate_spec(make_spec())

    assert result["viewport"] == {"width": 1920, "height": 1080}
    assert [s["id"] for s in result["scenes"]] == ["home", "details"]


def test_explicit_viewport_is_kept():
    result = validate_spec(make_spec(viewport={"width": 1080, "height": 1920}))

    assert result["viewport"] == {"width": 1080, "height": 1920}


@pytest.mark.parametrize("scenes", [[], None, "home"])
def test_spec_without_scenes_is_rejected(scenes):
    with pytest.raises(SpecError, match="at least one scene"):
        validate_spec(make_spec(scenes=scenes))


def test_scene_without_id_is_rejected_naming_the_scene():
    spec = make_spec()
    del spec["scenes"][1]["id"]

    with pytest.raises(SpecError) as excinfo:
        validate_spec(spec)

    message = str(excinfo.value)
    assert "missing id" in message
    assert "scene 2" in message
    assert "Details" in message


def test_duplicate_scene_id_is_rejected_naming_the_id():
    spec = make_spec()
    spec["scenes"][1]["id"] = "home"

    with pytest.raises(SpecError) as excinfo:
        validate_spec(spec)

    message = str(excinfo.value)
    assert "duplicate id 'home'" in message
    assert "scene 2" in message


def test_unknown_action_type_is_rejected_naming_the_scene_and_type():
    spec = make_spec()
    spec["scenes"][0]["actions"].append({"type": "hover", "target": "logo"})

    with pytest.raises(SpecError) as excinfo:
        validate_spec(spec)

    message = str(excinfo.value)
    assert "unknown action type 'hover'" in message
    assert "'home'" in message


@pytest.mark.parametrize("narration", ["", "   \n", None])
def test_empty_narration_is_rejected_naming_the_scene(narration):
    spec = make_spec()
    spec["scenes"][1]["narration"] = narration

    with pytest.raises(SpecError) as excinfo:
        validate_spec(spec)

    message = str(excinfo.value)
    assert "empty narration" in message
    assert "'details'" in message


@pytest.mark.parametrize("url", [None, "", "   ", 42])
def test_scene_without_a_url_is_rejected_naming_the_scene(url):
    spec = make_spec()
    spec["scenes"][1]["url"] = url

    with pytest.raises(SpecError) as excinfo:
        validate_spec(spec)

    message = str(excinfo.value)
    assert "url" in message
    assert "'details'" in message


@pytest.mark.parametrize("scene", ["home", 3, None, ["home"]])
def test_scene_that_is_not_an_object_is_rejected_naming_the_scene(scene):
    spec = make_spec()
    spec["scenes"][1] = scene

    with pytest.raises(SpecError) as excinfo:
        validate_spec(spec)

    message = str(excinfo.value)
    assert "scene 2" in message
    assert "object" in message


@pytest.mark.parametrize("action", ["click", 7, None])
def test_action_that_is_not_an_object_is_rejected_naming_the_scene(action):
    spec = make_spec()
    spec["scenes"][0]["actions"].append(action)

    with pytest.raises(SpecError) as excinfo:
        validate_spec(spec)

    message = str(excinfo.value)
    assert "'home'" in message
    assert "action 2" in message
    assert "object" in message
