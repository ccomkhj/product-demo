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


@pytest.mark.parametrize("viewport", [{"width": 1920}, {"width": 0, "height": 1080},
                                      {"width": 19.2, "height": 1080}, "1920x1080"])
def test_malformed_viewport_is_rejected(viewport):
    with pytest.raises(SpecError, match="viewport"):
        validate_spec(make_spec(viewport=viewport))


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


def spec_with_actions(actions, narration="Point at the heading, then click Save to keep it."):
    return make_spec(scenes=[{"id": "home", "url": "https://example.com",
                              "actions": actions, "narration": narration}])


def test_at_phrases_from_the_narration_are_accepted_in_order_and_any_case():
    validate_spec(spec_with_actions([
        {"type": "point", "target": "the heading", "at": "the heading"},
        {"type": "wait", "ms": 200},
        {"type": "click", "target": "Save", "at": "click   SAVE"},
    ]))


def test_at_phrase_missing_from_the_narration_is_rejected_naming_the_action():
    with pytest.raises(SpecError, match=r"scene 1 'home', action 2: 'at' phrase 'Delete' is not in"):
        validate_spec(spec_with_actions([
            {"type": "point", "target": "the heading"},
            {"type": "click", "target": "Delete", "at": "Delete"},
        ]))


def test_at_phrases_out_of_narration_order_are_rejected():
    with pytest.raises(SpecError, match="action 2: .* after the previous phrase"):
        validate_spec(spec_with_actions([
            {"type": "click", "target": "Save", "at": "click Save"},
            {"type": "point", "target": "the heading", "at": "the heading"},
        ]))


@pytest.mark.parametrize("phrase", ["", "  ", 3, None])
def test_at_that_is_not_a_phrase_is_rejected(phrase):
    with pytest.raises(SpecError, match="action 1: 'at' must be a phrase"):
        validate_spec(spec_with_actions([{"type": "point", "target": "x", "at": phrase}]))


@pytest.mark.parametrize('purpose', ['feature-announcement', 'customer-tutorial', 'sales-walkthrough', 'custom'])
def test_purpose_metadata_and_scene_checks(purpose):
    spec = make_spec(purpose=purpose, outcome='Export a report', target_duration_s=30)
    spec['scenes'][0]['prerequisites'] = [
        {'type': 'visible_text', 'text': 'Reports'},
        {'type': 'visible_target', 'target': 'the enabled Export button'},
    ]
    spec['scenes'][0]['expected_outcomes'] = []
    result = validate_spec(spec)
    assert result['purpose'] == purpose
    assert result['scenes'][0]['prerequisites'] == spec['scenes'][0]['prerequisites']
    result['scenes'][0]['prerequisites'].clear()
    assert len(spec['scenes'][0]['prerequisites']) == 2


def test_old_spec_does_not_get_template_defaults():
    result = validate_spec(make_spec())
    assert not {'purpose', 'outcome', 'target_duration_s'} & result.keys()


@pytest.mark.parametrize('field,value', [
    ('purpose', 'unknown'), ('purpose', []), ('purpose', None),
    ('outcome', ''), ('outcome', '  '), ('outcome', 1),
    *[('target_duration_s', v) for v in (0, -1, True, False, '30', None, float('nan'), float('inf'))],
])
def test_invalid_demo_metadata(field, value):
    with pytest.raises(SpecError, match=field):
        validate_spec(make_spec(**{field: value}))


@pytest.mark.parametrize('field', ['prerequisites', 'expected_outcomes'])
@pytest.mark.parametrize('checks', [None, {}, 'Reports', [None], [{}],
    [{'type': 'script', 'text': 'Reports'}],
    [{'type': 'visible_text'}], [{'type': 'visible_text', 'text': ' '}],
    [{'type': 'visible_target', 'target': False}],
])
def test_invalid_scene_checks(field, checks):
    spec = make_spec()
    spec['scenes'][0][field] = checks
    with pytest.raises(SpecError, match=f"scene 1.*home.*{field}"):
        validate_spec(spec)


def test_avatar_gets_its_default_corner_and_size():
    result = validate_spec(make_spec(avatar={"image": "me.png"}))

    assert result["avatar"] == {"image": "me.png", "corner": "bottom-left", "size": 0.2}


def test_avatar_can_use_the_saved_photo():
    result = validate_spec(make_spec(avatar={"profile": "own_photo", "corner": "top-right"}))
    assert result["avatar"] == {"profile": "own_photo", "corner": "top-right", "size": 0.2}


def test_spec_without_avatar_has_none():
    assert "avatar" not in validate_spec(make_spec())


@pytest.mark.parametrize("avatar, message", [
    ("me.png", "avatar must be a JSON object"),
    ({}, "avatar.image must be"),
    ({"image": " "}, "avatar.image must be"),
    ({"profile": "own_voice"}, "avatar.profile must be own_photo"),
    ({"profile": "own_photo", "image": "me.png"}, "without an image"),
    ({"image": "me.png", "corner": "middle"}, "avatar.corner must be one of bottom-left"),
    ({"image": "me.png", "size": 0.6}, "avatar.size must be a number from 0.05 to 0.5"),
    ({"image": "me.png", "size": 0}, "avatar.size"),
    ({"image": "me.png", "size": True}, "avatar.size"),
])
def test_malformed_avatar_is_rejected(avatar, message):
    with pytest.raises(SpecError, match=message):
        validate_spec(make_spec(avatar=avatar))
