import re
from pathlib import Path

import pytest

import edit_map
from edit_map import EditMap, EditMapError, MapAction, MapScene
from edit_plan import build_plan
from fake_video import FakeVideo
from post_plan import build_post_plan

# 20 + 20 characters share 8 s of audio, so "Then" (character 20) is reached at 4 s.
SPEC = {
    "scenes": [
        {
            "id": "home",
            "title": "Home",
            "url": "u",
            "narration": "First, we look here. Then we click there.",
            "actions": [
                {"type": "point", "target": "a"},
                {"type": "click", "target": "b", "at": "Then"},
                {"type": "type", "target": "c", "text": "hi"},
            ],
        },
        {
            "id": "end",
            "title": "End",
            "url": "u",
            "narration": "Thanks.",
            "actions": [{"type": "key", "key": "Enter"}],
        },
    ]
}
TIMELINE = [
    {"event": "rec_start", "t_ms": 0},
    {"scene": "home", "event": "start", "t_ms": 1_000},
    {"scene": "home", "event": "beat", "t_ms": 6_000},
    {"scene": "home", "event": "end", "t_ms": 9_000},
    {"scene": "end", "event": "start", "t_ms": 10_000},
    {"scene": "end", "event": "end", "t_ms": 12_000},
]
DURATIONS = {"home": 8.0, "end": 2.0}


def edit_plan():
    # the first segment plays 1 s, so the click is held back to its phrase at 4 s
    return build_plan(SPEC, TIMELINE, DURATIONS, FakeVideo({(1.0, 6.0): 1.0}))


def test_build_places_the_first_action_and_each_at_action_and_round_trips_through_the_file(
    tmp_path,
):
    built = edit_map.build(SPEC, edit_plan(), burned=True)
    video = tmp_path / "demo.mp4"

    edit_map.write(built, video)

    assert edit_map.read(video) == built
    home, end = built.scenes
    assert home.actions == (
        MapAction(0, "point", 0.0),
        MapAction(1, "click", 4.0, "Then"),
        MapAction(2, "type"),
    )
    assert (end.start_s, end.end_s, built.total_s) == (8.0, 10.0, 10.0)
    assert end.actions == (MapAction(0, "key", 8.0),)
    assert built.subtitles_burned is True


def test_each_scene_records_its_device_and_round_trips(tmp_path):
    spec = {
        "scenes": [
            {**SPEC["scenes"][0], "device": "desktop"},
            {**SPEC["scenes"][1], "device": "phone"},
        ]
    }
    timeline = [*TIMELINE]
    timeline[4] = {**timeline[4], "window": [500, 836], "page": [390, 836], "dpr": 1}
    plan = build_plan(spec, timeline, DURATIONS, FakeVideo({(1.0, 6.0): 1.0}))
    video = tmp_path / "demo.mp4"

    edit_map.write(edit_map.build(spec, plan), video)

    assert [scene.device for scene in edit_map.read(video).scenes] == ["desktop", "phone"]


def test_a_map_without_devices_reads_as_desktop(tmp_path):
    video = tmp_path / "demo.mp4"
    edit_map.write(edit_map.build(SPEC, edit_plan()), video)
    data = edit_map.to_dict(edit_map.read(video))
    for scene in data["scenes"]:
        del scene["device"]

    assert [scene.device for scene in edit_map.from_dict(data).scenes] == ["desktop", "desktop"]


def test_read_without_a_map_is_none(tmp_path):
    assert edit_map.read(tmp_path / "demo.mp4") is None


@pytest.mark.parametrize(
    "text",
    [
        "{not json",
        '{"total_s": 1.0, "scenes": [{"id": "a"}]}',
        '{"total_s": 1.0, "scenes": [{"id": "a", "title": "A", "start_s": 0, "end_s": 1, '
        '"actions": [{"index": 0, "type": "click", "at": "Go"}]}]}',
        "[]",
    ],
    ids=["bad json", "missing key", "at action without a start", "not an object"],
)
def test_a_malformed_map_is_an_error_naming_the_file(tmp_path, text):
    (tmp_path / "demo.editmap.json").write_text(text)

    with pytest.raises(EditMapError, match=r"demo\.editmap\.json.*re-run compose"):
        edit_map.read(tmp_path / "demo.mp4")


def test_a_map_from_before_subtitles_burned_reads_as_burned():
    old = edit_map.from_dict({"total_s": 1.0, "scenes": []})

    assert old.subtitles_burned is True
    assert edit_map.subtitles_burned(old) is True
    assert edit_map.subtitles_burned(None) is True
    assert edit_map.subtitles_burned(EditMap(1.0, False)) is False


MAP = EditMap(
    10.0,
    True,
    (
        MapScene(
            "home",
            "Home",
            0.0,
            8.0,
            (
                MapAction(0, "point", 0.0),
                MapAction(1, "click", 4.0, "Then"),
                MapAction(2, "type"),
                MapAction(3, "dblclick", 6.0, "Twice"),
            ),
        ),
        MapScene("end", "End", 8.0, 10.0, (MapAction(0, "key", 8.0),)),
    ),
)


def test_anchor_s_is_the_scene_start_or_its_at_phrase_start():
    assert MAP.anchor_s("end") == 8.0
    assert MAP.anchor_s("home", "Then") == 4.0


def test_an_unknown_scene_or_phrase_lists_the_known_ones():
    with pytest.raises(EditMapError, match=r"unknown scene 'nope' \(scenes: home, end\)"):
        MAP.anchor_s("nope")
    with pytest.raises(
        EditMapError, match=r"scene 'home' has no action at 'Now' \(at phrases: 'Then', 'Twice'\)"
    ):
        MAP.anchor_s("home", "Now")
    with pytest.raises(EditMapError, match=r"\(at phrases: none\)"):
        MAP.anchor_s("end", "Now")


def test_clicks_land_one_glide_after_their_action_starts():
    home = MAP.scene("home")

    assert MAP.clicks() == [
        ("home", home.actions[1], pytest.approx(4.6)),
        ("home", home.actions[2], None),  # start unknown
        ("home", home.actions[3], pytest.approx(6.6)),
    ]


def test_an_upload_taps_its_control_so_it_counts_as_a_click():
    upload = MapAction(1, "upload", 2.0, "Pick")
    scene = MapScene("pick", "Pick", 0.0, 5.0, (MapAction(0, "point", 0.0), upload))
    saved = edit_map.from_dict(edit_map.to_dict(EditMap(5.0, True, (scene,))))

    assert saved.clicks() == [("pick", upload, pytest.approx(2.6))]


def test_the_glide_matches_the_overlay():
    overlay = (Path(__file__).resolve().parent.parent / "assets" / "overlay.js").read_text()
    glide_ms = int(re.search(r"const GLIDE_MS = (\d+);", overlay).group(1))

    assert edit_map.GLIDE_S * 1000 == glide_ms


def test_post_processing_places_anchors_clicks_and_chapters_where_compose_put_them(tmp_path):
    compose_plan = edit_plan()
    video = tmp_path / "demo.mp4"
    edit_map.write(edit_map.build(SPEC, compose_plan), video)
    recipe = {
        "trim": {"start": {"scene": "home", "at": "Then"}},
        "click_sounds": {},
        "chapters": True,
    }

    post = build_post_plan(recipe, compose_plan.total_s, (), edit_map.read(video))

    [then] = compose_plan.actions
    home, end = compose_plan.scenes
    assert post.time_map.output_s(then.start_s) == 0.0
    [click_step] = [step for step in post.steps if step.op == "click_sounds"]
    assert click_step.params["times"] == (pytest.approx(edit_map.GLIDE_S),)
    assert [(c.title, c.start_s) for c in post.chapters] == [
        (home.title, 0.0),
        (end.title, pytest.approx(end.offset_s - then.start_s)),
    ]
    assert (
        "no click sound for scene 'home' action 2 (type): start unknown "
        "(not the scene's first action and no at phrase)"
    ) in post.warnings
