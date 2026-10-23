import pytest

from edit_plan import PlanError, build_plan, load_timeline
from fake_video import FakeVideo

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
    plan = build_plan(SPEC, TIMELINE, DURATIONS, FakeVideo())

    assert [s.id for s in plan.scenes] == ["home", "details", "outro"]
    assert [(s.trim_start, s.trim_end) for s in plan.scenes] == [
        (1.0, 3.0), (4.0, 10.0), (11.0, 21.0),
    ]
    assert [s.audio_s for s in plan.scenes] == [5.0, 4.0, 4.0]


def test_scene_order_follows_the_spec_not_the_timeline():
    shuffled = [TIMELINE[0], *TIMELINE[3:5], *TIMELINE[1:3], *TIMELINE[5:]]

    plan = build_plan(SPEC, shuffled, DURATIONS, FakeVideo())

    assert [s.id for s in plan.scenes] == ["home", "details", "outro"]
    assert plan.scenes[1].trim_start == 4.0


def test_a_scene_without_frozen_frames_is_fitted_from_its_trimmed_length():
    plan = build_plan(SPEC, TIMELINE, DURATIONS, FakeVideo())

    home, details, outro = plan.scenes
    assert (home.fit.strategy, home.scene_s) == ("hold", 5.0)
    assert details.fit.strategy == "speedup"
    assert details.fit.speed == pytest.approx(1.5)
    assert details.scene_s == pytest.approx(4.0)
    assert outro.fit.strategy == "pad"
    assert outro.scene_s == pytest.approx(10 / 1.5)
    assert outro.fit.pad_s == pytest.approx(10 / 1.5 - 4.0)


def test_scene_offsets_accumulate_scene_lengths_including_padding():
    plan = build_plan(SPEC, TIMELINE, DURATIONS, FakeVideo())

    assert [s.offset_s for s in plan.scenes] == pytest.approx([0.0, 5.0, 9.0])
    assert plan.total_s == pytest.approx(9.0 + 10 / 1.5)


def test_each_scene_is_fitted_from_its_decimated_length_and_offsets_follow():
    video = FakeVideo({(1.0, 3.0): 0.5, (4.0, 10.0): 9.0, (11.0, 21.0): 4.5})

    refitted = build_plan(SPEC, TIMELINE, DURATIONS, video)

    home, details, outro = refitted.scenes
    assert (home.video_s, home.fit.strategy, home.fit.hold_s) == (0.5, "hold", 4.5)
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

    plan = build_plan(SPEC, [*first_take, *TIMELINE], DURATIONS, FakeVideo())

    assert [(s.trim_start, s.trim_end) for s in plan.scenes] == [
        (1.0, 3.0), (4.0, 10.0), (11.0, 21.0),
    ]


def test_scene_carries_title_falling_back_to_id():
    plan = build_plan(SPEC, TIMELINE, DURATIONS, FakeVideo())

    assert [s.title for s in plan.scenes] == ["Home", "details", "Outro"]


def errors_for(timeline, durations=DURATIONS):
    with pytest.raises(PlanError) as excinfo:
        build_plan(SPEC, timeline, durations, FakeVideo())
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


# --- beats: segments synced to the actions' `at` phrases -------------------------------

# 20 + 20 characters share 8 s of audio, so "Then" (character 20) is reached at 4 s.
SYNCED_NARRATION = "First, we look here. Then we click there."


def synced_spec(actions):
    return {"scenes": [{"id": "home", "url": "u", "narration": SYNCED_NARRATION,
                        "actions": actions}]}


def synced_timeline(*beats_s):
    events = [{"event": "rec_start", "t_ms": 0},
              {"scene": "home", "event": "start", "t_ms": 1_000}]
    events += [{"scene": "home", "event": "beat", "t_ms": int(b * 1000)} for b in beats_s]
    return events + [{"scene": "home", "event": "end", "t_ms": 9_000}]


ACTIONS = [{"type": "point", "target": "a"}, {"type": "click", "target": "b", "at": "Then"}]


def test_beats_cut_a_scene_into_segments_anchored_to_their_phrases():
    plan = build_plan(synced_spec(ACTIONS), synced_timeline(6.0), {"home": 8.0}, FakeVideo())

    first, second = plan.scenes[0].segments
    assert (first.trim_start, first.trim_end, first.anchor_s) == (1.0, 6.0, 0.0)
    assert (second.trim_start, second.trim_end) == (6.0, 9.0)
    assert second.anchor_s == pytest.approx(4.0)


def test_a_segment_is_held_back_until_the_narration_reaches_its_phrase():
    video = FakeVideo({(1.0, 6.0): 1.0, (6.0, 9.0): 2.0})

    scene = build_plan(synced_spec(ACTIONS), synced_timeline(6.0), {"home": 8.0}, video).scenes[0]

    assert [s.lead_frames for s in scene.segments] == [0, 90]  # 1 s played, held to 4 s
    assert scene.video_s == pytest.approx(6.0)
    assert (scene.fit.strategy, scene.fit.hold_s) == ("hold", pytest.approx(2.0))


def test_a_segment_that_runs_past_the_next_anchor_pushes_it_back_and_the_scene_fits_as_a_whole():
    video = FakeVideo({(1.0, 6.0): 5.0, (6.0, 9.0): 5.0})

    scene = build_plan(synced_spec(ACTIONS), synced_timeline(6.0), {"home": 8.0}, video).scenes[0]

    assert [s.lead_frames for s in scene.segments] == [0, 0]
    assert scene.video_s == pytest.approx(10.0)
    assert scene.fit.strategy == "speedup"


def test_an_empty_first_segment_leaves_the_next_one_to_hold_its_first_frame():
    video = FakeVideo({(1.0, 9.0): 2.0})

    scene = build_plan(synced_spec(ACTIONS), synced_timeline(1.0), {"home": 8.0}, video).scenes[0]

    assert video.measured == [(1.0, 9.0)]  # the empty first segment has nothing to measure
    assert [s.lead_frames for s in scene.segments] == [0, 120]
    assert scene.video_s == pytest.approx(6.0)


def test_scene_without_beats_is_one_segment_from_start_to_end():
    plan = build_plan(SPEC, TIMELINE, DURATIONS, FakeVideo())

    assert [len(s.segments) for s in plan.scenes] == [1, 1, 1]
    assert plan.scenes[1].segments[0].trim_start == 4.0


def test_beat_count_must_match_the_actions_with_an_at_phrase():
    with pytest.raises(PlanError, match="'home' has 0 beat markers but 1 actions with an 'at'"):
        build_plan(synced_spec(ACTIONS), synced_timeline(), {"home": 8.0}, FakeVideo())


def test_beat_outside_its_scene_is_an_error():
    with pytest.raises(PlanError, match="'home' has a beat marker outside its start and end"):
        build_plan(synced_spec(ACTIONS), synced_timeline(9.5), {"home": 8.0}, FakeVideo())


def test_anchor_matches_the_start_of_the_subtitle_cue_holding_the_phrase():
    plan = build_plan(synced_spec(ACTIONS), synced_timeline(6.0), {"home": 8.0}, FakeVideo())

    assert plan.cues[1].text.startswith("Then")
    assert plan.scenes[0].segments[1].anchor_s == pytest.approx(plan.cues[1].start_s)


# --- action times: where compose places each `at` action in the final video ---------

CLICK = [{"type": "point", "target": "a"}, {"type": "click", "target": "b", "at": "click"}]


def test_a_mid_sentence_phrase_is_placed_after_its_cue_starts():
    # "click" is character 28 of 40 spoken characters: 0.7 of 8 s = 5.6 s, while its
    # cue ("Then we click there.") starts at 4 s.
    video = FakeVideo({(1.0, 6.0): 1.0, (6.0, 9.0): 2.0})

    plan = build_plan(synced_spec(CLICK), synced_timeline(6.0), {"home": 8.0}, video)

    (action,) = plan.actions
    assert (action.scene_id, action.n, action.phrase) == ("home", 1, "click")
    assert action.start_s == pytest.approx(5.6, abs=1 / 30)
    assert action.start_s > plan.cues[1].start_s + 1
    assert action.late_s == 0


def test_a_sped_up_scene_places_its_actions_earlier_by_its_speed():
    # held to "Then" at 4 s, then 6 s of motion: 10 s of video for 8 s of audio, 1.25x
    video = FakeVideo({(1.0, 6.0): 1.0, (6.0, 9.0): 6.0})

    plan = build_plan(synced_spec(ACTIONS), synced_timeline(6.0), {"home": 8.0}, video)

    assert plan.scenes[0].fit.speed == pytest.approx(1.25)
    (action,) = plan.actions
    assert action.start_s == pytest.approx(3.2)
    assert action.late_s == 0


def test_an_action_pushed_past_its_phrase_is_late_by_the_overrun():
    # an intro scene first (2 s), then home: its first segment plays 5 s, past "Then" at 4 s
    spec = synced_spec(ACTIONS)
    spec["scenes"].insert(0, {"id": "intro", "url": "u", "narration": "Hi.", "actions": []})
    timeline = [*synced_timeline(6.0),
                {"scene": "intro", "event": "start", "t_ms": 9_500},
                {"scene": "intro", "event": "end", "t_ms": 10_500}]
    video = FakeVideo({(1.0, 6.0): 5.0, (6.0, 9.0): 1.0})

    plan = build_plan(spec, timeline, {"intro": 2.0, "home": 8.0}, video)

    (action,) = plan.actions
    assert (action.scene_id, action.phrase) == ("home", "Then")
    assert action.start_s == pytest.approx(7.0)
    assert action.late_s == pytest.approx(1.0)


# --- sync flash: raw-video time 0 from the flash, not from when rec_start was marked ---

def test_a_sync_flash_sets_raw_time_zero_however_late_rec_start_was_marked():
    # The flash was painted at wall 9.5 s and shows 2.0 s into the raw video, so the
    # recording began at 7.5 s; rec_start itself was only marked at 10 s.
    synced = [{**TIMELINE[0], "sync_ms": 9_500}, *TIMELINE[1:]]

    plan = build_plan(SPEC, synced, DURATIONS, FakeVideo(flash_s=2.0))

    assert [(s.trim_start, s.trim_end) for s in plan.scenes] == [
        (3.5, 5.5), (6.5, 12.5), (13.5, 23.5),
    ]
    assert plan.flash_s == 2.0


def test_without_a_sync_ms_the_video_is_not_scanned_for_a_flash():
    video = FakeVideo(flash_s=2.0)

    plan = build_plan(SPEC, TIMELINE, DURATIONS, video)

    assert not video.scanned
    assert plan.flash_s is None


def test_a_sync_ms_with_no_flash_in_the_video_is_an_error():
    synced = [{**TIMELINE[0], "sync_ms": 9_500}, *TIMELINE[1:]]

    with pytest.raises(PlanError, match=r"no sync flash found.*__demo\.sync\(\)"):
        build_plan(SPEC, synced, DURATIONS, FakeVideo(flash_s=None))


# --- measuring: the raw video is only decimated once everything else checks out ---

def test_segments_are_measured_once_each_in_plan_order():
    video = FakeVideo()

    build_plan(SPEC, TIMELINE, DURATIONS, video)

    assert video.measured == [(1.0, 3.0), (4.0, 10.0), (11.0, 21.0)]


def test_nothing_is_measured_when_a_later_scene_is_wrong():
    video = FakeVideo()

    with pytest.raises(PlanError, match="'outro'"):
        build_plan(SPEC, TIMELINE, {"home": 5.0, "details": 4.0}, video)

    assert video.measured == []


def test_a_scene_with_no_frames_left_is_an_error():
    with pytest.raises(PlanError, match="'details' has no video frames in its trim window "
                                        "4.000-10.000s"):
        build_plan(SPEC, TIMELINE, DURATIONS, FakeVideo({(4.0, 10.0): 0.0}))
