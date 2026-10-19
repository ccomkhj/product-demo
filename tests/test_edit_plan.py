import pytest

from edit_plan import PlanError, build_plan, load_timeline

SPEC = {
    "title": "Demo",
    "scenes": [
        {"id": "home", "title": "Home", "url": "https://example.com", "actions": [],
         "narration": "This is home."},
        {"id": "details", "url": "https://example.com/d", "actions": [],
         "narration": "These are the details."},
        {"id": "outro", "title": "Outro", "url": "https://example.com", "actions": [],
         "narration": "Thanks."},
    ],
}

# rec_start at 10 s wall-clock; scenes at raw-video 1-3 s, 4-10 s, 11-21 s.
TIMELINE = [
    {"event": "rec_start", "t_ms": 10_000},
    {"scene": "home", "event": "start", "t_ms": 11_000},
    {"scene": "home", "event": "end", "t_ms": 13_000},
    {"scene": "details", "event": "start", "t_ms": 14_000},
    {"scene": "details", "event": "end", "t_ms": 20_000},
    {"scene": "outro", "event": "start", "t_ms": 21_000},
    {"scene": "outro", "event": "end", "t_ms": 31_000},
]

DURATIONS = {"home": 5.0, "details": 4.0, "outro": 4.0}


def test_plan_has_trim_windows_relative_to_rec_start_in_spec_order():
    plan = build_plan(SPEC, TIMELINE, DURATIONS)

    assert [s.id for s in plan.scenes] == ["home", "details", "outro"]
    assert [(s.trim_start, s.trim_end) for s in plan.scenes] == [
        (1.0, 3.0), (4.0, 10.0), (11.0, 21.0),
    ]
    assert [s.audio_s for s in plan.scenes] == [5.0, 4.0, 4.0]


def test_scene_order_follows_the_spec_not_the_timeline():
    shuffled = [TIMELINE[0], *TIMELINE[3:5], *TIMELINE[1:3], *TIMELINE[5:]]

    plan = build_plan(SPEC, shuffled, DURATIONS)

    assert [s.id for s in plan.scenes] == ["home", "details", "outro"]
    assert plan.scenes[1].trim_start == 4.0


def test_initial_plan_fits_each_scene_from_its_trimmed_length():
    plan = build_plan(SPEC, TIMELINE, DURATIONS)

    home, details, outro = plan.scenes
    assert (home.fit.strategy, home.scene_s) == ("hold", 5.0)
    assert details.fit.strategy == "speedup"
    assert details.fit.speed == pytest.approx(1.5)
    assert details.scene_s == pytest.approx(4.0)
    assert outro.fit.strategy == "pad"
    assert outro.scene_s == pytest.approx(10 / 1.5)
    assert outro.fit.pad_s == pytest.approx(10 / 1.5 - 4.0)


def test_scene_offsets_accumulate_scene_lengths_including_padding():
    plan = build_plan(SPEC, TIMELINE, DURATIONS)

    assert [s.offset_s for s in plan.scenes] == pytest.approx([0.0, 5.0, 9.0])
    assert plan.total_s == pytest.approx(9.0 + 10 / 1.5)


def test_measured_decimated_lengths_refit_scenes_and_shift_offsets():
    plan = build_plan(SPEC, TIMELINE, DURATIONS)

    refitted = plan.with_video_lengths({"home": 0.0, "details": 9.0, "outro": 4.5})

    home, details, outro = refitted.scenes
    assert (home.video_s, home.fit.strategy, home.fit.hold_s) == (0.0, "hold", 5.0)
    assert details.fit.strategy == "pad"
    assert details.scene_s == pytest.approx(6.0)
    assert outro.fit.strategy == "speedup"
    assert outro.fit.speed == pytest.approx(4.5 / 4.0)
    assert [s.offset_s for s in refitted.scenes] == pytest.approx([0.0, 5.0, 11.0])
    assert refitted.total_s == pytest.approx(15.0)
    # trim windows and narration stay put
    assert [(s.trim_start, s.trim_end) for s in refitted.scenes] == [
        (1.0, 3.0), (4.0, 10.0), (11.0, 21.0),
    ]
    assert refitted.scenes[0].narration == "This is home."


def test_a_re_recording_uses_only_the_events_after_the_last_rec_start():
    # an aborted first take (rec_start at 0 s, home only, a stray 'bonus' scene), then the real one
    first_take = [
        {"event": "rec_start", "t_ms": 0},
        {"scene": "home", "event": "start", "t_ms": 500},
        {"scene": "home", "event": "end", "t_ms": 9_000},
        {"scene": "bonus", "event": "start", "t_ms": 9_100},
    ]

    plan = build_plan(SPEC, [*first_take, *TIMELINE], DURATIONS)

    assert [(s.trim_start, s.trim_end) for s in plan.scenes] == [
        (1.0, 3.0), (4.0, 10.0), (11.0, 21.0),
    ]


def test_scene_carries_title_falling_back_to_id():
    plan = build_plan(SPEC, TIMELINE, DURATIONS)

    assert [s.title for s in plan.scenes] == ["Home", "details", "Outro"]


def errors_for(timeline, durations=DURATIONS):
    with pytest.raises(PlanError) as excinfo:
        build_plan(SPEC, timeline, durations)
    return str(excinfo.value)


def test_missing_rec_start_is_an_error():
    message = errors_for(TIMELINE[1:])

    assert "rec_start" in message


def test_scene_with_no_end_is_an_error():
    timeline = [e for e in TIMELINE if not (e.get("scene") == "details" and e["event"] == "end")]

    message = errors_for(timeline)

    assert "'details'" in message
    assert "no end" in message


def test_scene_ending_before_it_starts_is_an_error():
    timeline = [dict(e) for e in TIMELINE]
    timeline[4]["t_ms"] = 13_500  # details ends at 13.5 s but started at 14 s

    message = errors_for(timeline)

    assert "'details'" in message
    assert "before it starts" in message


def test_scene_in_timeline_but_not_in_spec_is_an_error():
    timeline = [*TIMELINE,
                {"scene": "bonus", "event": "start", "t_ms": 32_000},
                {"scene": "bonus", "event": "end", "t_ms": 33_000}]

    message = errors_for(timeline)

    assert "'bonus'" in message
    assert "not in the demo spec" in message


@pytest.mark.parametrize("kind", ["start", "end"])
def test_unknown_timeline_scene_with_one_marker_reports_not_in_the_spec(kind):
    message = errors_for([*TIMELINE, {"scene": "bonus", "event": kind, "t_ms": 32_000}])

    assert "'bonus'" in message
    assert "not in the demo spec" in message


def test_scene_ending_when_it_starts_has_its_own_error():
    timeline = [dict(e) for e in TIMELINE]
    timeline[4]["t_ms"] = 14_000  # details ends at 14 s, the moment it started

    message = errors_for(timeline)

    assert "scene 'details' has zero length: end equals start" in message


def test_spec_scene_missing_from_timeline_is_an_error():
    timeline = [e for e in TIMELINE if e.get("scene") != "outro"]

    message = errors_for(timeline)

    assert "'outro'" in message
    assert "no start" in message


def test_scene_starting_before_rec_start_is_an_error():
    timeline = [dict(e) for e in TIMELINE]
    timeline[1]["t_ms"] = 9_000

    message = errors_for(timeline)

    assert "'home'" in message
    assert "before rec_start" in message


def test_repeated_scene_marker_is_an_error():
    message = errors_for([*TIMELINE, {"scene": "home", "event": "start", "t_ms": 32_000}])

    assert "'home'" in message
    assert "more than one start" in message


def test_scene_missing_from_durations_is_an_error():
    message = errors_for(TIMELINE, durations={"home": 5.0, "details": 4.0})

    assert "'outro'" in message
    assert "durations" in message


def test_load_timeline_reads_jsonl_and_reports_bad_lines(tmp_path):
    good = tmp_path / "timeline.jsonl"
    good.write_text('{"event": "rec_start", "t_ms": 1}\n\n{"scene": "a", "event": "start", "t_ms": 2}\n')
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"event": "rec_start", "t_ms": 1}\nnot json\n')

    assert load_timeline(good) == [
        {"event": "rec_start", "t_ms": 1}, {"scene": "a", "event": "start", "t_ms": 2},
    ]
    with pytest.raises(PlanError, match="line 2"):
        load_timeline(bad)


@pytest.mark.parametrize("seconds", [0, 0.0, -1.5, float("nan"), "4.0s", None, True, [4.0]])
def test_unusable_scene_duration_is_an_error_naming_the_scene(seconds):
    message = errors_for(TIMELINE, durations={**DURATIONS, "details": seconds})

    assert "'details'" in message
    assert "duration" in message
