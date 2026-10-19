import pytest

from edit_plan import build_plan

# rec_start at 10 s; trimmed windows of 2 s, 6 s and 10 s.
TIMELINE = [
    {"event": "rec_start", "t_ms": 10_000},
    {"scene": "intro", "event": "start", "t_ms": 11_000},
    {"scene": "intro", "event": "end", "t_ms": 13_000},
    {"scene": "filters", "event": "start", "t_ms": 14_000},
    {"scene": "filters", "event": "end", "t_ms": 20_000},
    {"scene": "outro", "event": "start", "t_ms": 21_000},
    {"scene": "outro", "event": "end", "t_ms": 31_000},
]

# 10 + 30 characters: the 4 s of audio splits 1 s / 3 s.
INTRO = "Aaaa bbbb. Cccc dddd eeee ffff gggg hhhh."
# 26 + 18 characters.
FILTERS = "Use filters, e.g. by date. Dr. Smith said so."
OUTRO = "Thanks for watching."


def spec(intro=INTRO, filters=FILTERS, outro=OUTRO):
    return {
        "title": "Demo",
        "scenes": [
            {"id": "intro", "title": "Intro", "url": "u", "actions": [], "narration": intro},
            {"id": "filters", "title": "Filters", "url": "u", "actions": [], "narration": filters},
            {"id": "outro", "title": "Outro", "url": "u", "actions": [], "narration": outro},
        ],
    }


DURATIONS = {"intro": 4.0, "filters": 4.4, "outro": 2.0}


def cues_for(plan, scene_id):
    return [(c.start_s, c.end_s, c.text) for c in plan.cues if c.scene_id == scene_id]


def test_multi_sentence_scene_shares_its_audio_by_character_count():
    plan = build_plan(spec(), TIMELINE, DURATIONS)

    assert cues_for(plan, "intro") == [
        pytest.approx((0.0, 1.0, "Aaaa bbbb.")),
        pytest.approx((1.0, 4.0, "Cccc dddd eeee ffff gggg hhhh.")),
    ]


def test_abbreviations_do_not_split_sentences():
    plan = build_plan(spec(), TIMELINE, DURATIONS)

    # intro holds for its 4 s of audio, so filters starts at 4 s.
    assert cues_for(plan, "filters") == [
        pytest.approx((4.0, 6.6, "Use filters, e.g. by date.")),
        pytest.approx((6.6, 8.4, "Dr. Smith said so.")),
    ]


def test_single_sentence_scene_gets_one_cue_for_its_audio_not_its_padding():
    plan = build_plan(spec(), TIMELINE, DURATIONS)

    # outro: 10 s of video vs 2 s of audio is padded to 10/1.5 s, but the cue
    # only covers the spoken 2 s, starting after intro (4 s) + filters (4.4 s).
    assert plan.scenes[2].fit.strategy == "pad"
    assert cues_for(plan, "outro") == [pytest.approx((8.4, 10.4, "Thanks for watching."))]


def test_padding_an_earlier_scene_pushes_later_cues_back_by_the_padded_amount():
    plan = build_plan(spec(), TIMELINE, DURATIONS)
    unpadded = plan.with_video_lengths({"intro": 2.0, "filters": 6.0, "outro": 10.0})
    # 9 s of intro video against 4 s of audio: 1.5x leaves 6 s, so 2 s of silence.
    padded = plan.with_video_lengths({"intro": 9.0, "filters": 6.0, "outro": 10.0})

    assert padded.scenes[0].fit.pad_s == pytest.approx(2.0)
    assert cues_for(padded, "intro") == cues_for(unpadded, "intro")
    later = [c for c in unpadded.cues if c.scene_id != "intro"]
    later_padded = [c for c in padded.cues if c.scene_id != "intro"]
    assert [(c.start_s, c.end_s) for c in later_padded] == pytest.approx(
        [(c.start_s + 2.0, c.end_s + 2.0) for c in later]
    )
    assert [c.text for c in later_padded] == [c.text for c in later]


def test_cues_cover_every_scene_in_spec_order():
    plan = build_plan(spec(), TIMELINE, DURATIONS)

    assert [c.scene_id for c in plan.cues] == ["intro", "intro", "filters", "filters", "outro"]
    starts = [c.start_s for c in plan.cues]
    assert starts == sorted(starts)
