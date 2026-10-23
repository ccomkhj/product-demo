from pathlib import Path

import pytest

from post_plan import (
    Cue,
    RecipeError,
    build_post_plan,
    card_defaults,
    card_svg,
    cards_cmd,
    chapters_metadata,
    gif_cmd,
    gif_warning,
    size_cap_encoding,
    subtitle_tracks_cmd,
    youtube_chapters,
)

SOURCE_S = 12.0
EDIT_MAP = {
    "total_s": 12.0,
    "scenes": [
        {
            "id": "intro",
            "title": "Intro",
            "start_s": 0.0,
            "end_s": 4.0,
            "actions": [{"index": 0, "type": "point", "start_s": 0.0}],
        },
        {
            "id": "report",
            "title": "Report",
            "start_s": 4.0,
            "end_s": 12.0,
            "actions": [
                {"index": 0, "type": "click", "start_s": 4.0},
                {"index": 2, "type": "click", "start_s": 7.5, "at": "Export"},
            ],
        },
    ],
}
CUES = (
    Cue(0.0, 2.0, "Welcome."),
    Cue(2.0, 4.0, "This is the intro."),
    Cue(4.0, 7.0, "Open the report."),
    Cue(7.0, 12.0, "Export it."),
)


def plan(recipe, source_s=SOURCE_S, cues=CUES, edit_map=EDIT_MAP, demo=None, translations=None):
    return build_post_plan(recipe, source_s, cues, edit_map, demo, translations)


def scene(name, at=None, offset_s=None):
    ref = {"scene": name}
    if at is not None:
        ref["at"] = at
    if offset_s is not None:
        ref["offset_s"] = offset_s
    return ref


# --- time map --------------------------------------------------------------------------


def test_an_empty_recipe_keeps_the_source_as_it_is():
    result = plan({})

    assert result.total_s == SOURCE_S
    assert result.output_s(5.0) == 5.0
    assert result.cues == CUES
    assert result.steps == ()


def test_trim_start_end_and_cuts_shift_later_times_back():
    result = plan({"trim": {"start": 1.0, "end": 11.0, "cuts": [{"from": 5.0, "to": 6.0}]}})

    assert result.total_s == pytest.approx(9.0)
    assert result.output_s(1.0) == pytest.approx(0.0)
    assert result.output_s(5.0) == pytest.approx(4.0)
    assert result.output_s(6.0) == pytest.approx(4.0)
    assert result.output_s(8.0) == pytest.approx(6.0)
    assert result.output_s(11.0) == pytest.approx(9.0)


def test_speed_divides_output_times_after_the_cuts():
    result = plan({"speed": 1.25, "trim": {"cuts": [{"from": 2.0, "to": 4.5}]}})

    assert result.total_s == pytest.approx(9.5 / 1.25)
    assert result.output_s(6.0) == pytest.approx(3.5 / 1.25)


def test_overlapping_cuts_are_merged():
    result = plan(
        {"trim": {"start": 2.0, "cuts": [{"from": 1.0, "to": 3.0}, {"from": 2.5, "to": 4.0}]}}
    )

    assert result.total_s == pytest.approx(8.0)
    assert result.output_s(4.0) == 0.0


def test_cut_times_snap_to_whole_frames():
    result = plan({"trim": {"start": 1.01}})

    assert result.total_s == pytest.approx(11.0)


@pytest.mark.parametrize(
    "time, message",
    [
        (5.5, "inside a cut"),
        (0.5, "inside a cut"),
        (12.5, "past the end"),
    ],
)
def test_a_time_inside_a_cut_or_past_the_end_is_an_error(time, message):
    result = plan({"trim": {"start": 1.0, "cuts": [{"from": 5.0, "to": 6.0}]}})

    with pytest.raises(RecipeError, match=message):
        result.output_s(time)


# --- anchors ---------------------------------------------------------------------------


def test_scene_and_at_anchored_times_resolve_through_the_edit_map():
    result = plan({"trim": {"cuts": [{"from": 1.0, "to": 2.0}]}})

    assert result.output_s(scene("report")) == pytest.approx(3.0)
    assert result.output_s(scene("report", at="Export")) == pytest.approx(6.5)
    assert result.output_s(scene("report", at="Export", offset_s=-0.5)) == pytest.approx(6.0)


def test_trim_accepts_anchored_times():
    result = plan(
        {"trim": {"start": scene("report"), "end": scene("report", at="Export", offset_s=1)}}
    )

    assert result.total_s == pytest.approx(4.5)


@pytest.mark.parametrize(
    "ref, message",
    [
        (scene("missing"), "unknown scene 'missing'.*intro, report"),
        (scene("report", at="Nope"), "scene 'report' has no action at 'Nope'.*Export"),
        (scene("intro", offset_s=-1), "before the start"),
    ],
)
def test_bad_anchors_are_clear_errors(ref, message):
    with pytest.raises(RecipeError, match=message):
        plan({"trim": {"start": ref}})


def test_anchored_times_without_an_edit_map_ask_to_re_run_compose():
    with pytest.raises(RecipeError, match="re-run compose"):
        plan({"trim": {"start": scene("report")}}, edit_map=None)


def test_plain_seconds_work_without_an_edit_map():
    assert plan({"trim": {"start": 1.0}}, edit_map=None).total_s == pytest.approx(11.0)


# --- cues ------------------------------------------------------------------------------


def test_cues_inside_a_cut_are_dropped_straddling_ones_clipped_and_all_retimed():
    result = plan({"trim": {"cuts": [{"from": 1.0, "to": 4.0}]}, "speed": 2.0})

    assert result.cues == (
        Cue(0.0, 0.5, "Welcome."),
        Cue(0.5, 2.0, "Open the report."),
        Cue(2.0, 4.5, "Export it."),
    )


def test_a_cut_that_splits_a_cue_warns():
    result = plan({"trim": {"cuts": [{"from": 1.0, "to": 4.0}]}})

    assert result.warnings == ('cut at 1.000s-4.000s splits cue 1 "Welcome."',)


def test_cuts_on_cue_boundaries_do_not_warn():
    assert plan({"trim": {"start": 2.0, "cuts": [{"from": 4.0, "to": 7.0}]}}).warnings == ()


# --- steps -----------------------------------------------------------------------------


def test_steps_run_in_a_fixed_order_whatever_the_recipe_order():
    exports = {"contact_sheet": {}}
    trim = {"start": 1.0}
    one = plan({"exports": exports, "speed": 1.5, "trim": trim})
    other = plan({"trim": trim, "exports": exports, "speed": 1.5})

    assert [step.op for step in one.steps] == ["trim", "speed", "contact_sheet"]
    assert one.steps == other.steps


def test_each_step_has_its_run_output_line():
    result = plan({"trim": {"start": 1.0, "cuts": [{"from": 5.0, "to": 6.0}]}, "speed": 1.25})

    assert [step.line for step in result.steps] == [
        "trim\tremoved 2.000s in 2 ranges\tkept 10.000s",
        "speed\t1.25x",
    ]


def test_trim_step_cuts_video_and_audio_with_fades_at_every_cut():
    [step] = plan({"trim": {"start": 1.0, "cuts": [{"from": 5.0, "to": 6.0}]}}).steps

    args = step.cmd("in.mp4", "out.mp4")
    graph = args[args.index("-filter_complex") + 1]
    assert "[0:v]trim=start=1.000:end=5.000,setpts=PTS-STARTPTS[v0]" in graph
    assert "[0:v]trim=start=6.000:end=12.000,setpts=PTS-STARTPTS[v1]" in graph
    # fades at both ends of the first range, only at the start of the last (source end)
    assert (
        "[0:a]atrim=start=1.000:end=5.000,asetpts=PTS-STARTPTS,"
        "afade=t=in:d=0.030,afade=t=out:st=3.970:d=0.030[a0]"
    ) in graph
    assert "[0:a]atrim=start=6.000:end=12.000,asetpts=PTS-STARTPTS,afade=t=in:d=0.030[a1]" in graph
    assert "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]" in graph
    assert args[-1] == "out.mp4"


def test_speed_step_retimes_video_by_timestamps_and_audio_by_tempo():
    [step] = plan({"speed": 1.25}).steps

    args = step.cmd("in.mp4", "out.mp4")
    graph = args[args.index("-filter_complex") + 1]
    assert graph == "[0:v]setpts=PTS/1.250000,fps=30[v];[0:a]atempo=1.250000[a]"


def test_contact_sheet_samples_the_output_at_its_interval():
    [step] = plan(
        {"speed": 2.0, "exports": {"contact_sheet": {"columns": 3, "interval_s": 2}}}
    ).steps[1:]

    assert step.params == {"columns": 3, "times": (0.0, 2.0, 4.0)}
    assert step.line == "contact_sheet\t3 frames"


def test_contact_sheet_defaults_to_twelve_frames_in_four_columns():
    [step] = plan({"exports": {"contact_sheet": {}}}).steps

    assert step.params["columns"] == 4
    assert step.params["times"] == tuple(float(t) for t in range(12))


# --- exports: size cap, gif, poster (#22) ------------------------------------------------


def test_poster_defaults_to_a_frame_from_the_first_scene():
    [step] = plan({"exports": {"poster": {}}}).steps

    assert step.export
    assert step.params == {"at_s": 2.0}  # middle of intro, 0-4 s
    assert step.line == "poster\t2.000s"


def test_poster_default_skips_a_scene_that_was_cut_away():
    [_, step] = plan({"trim": {"start": 4.0}, "exports": {"poster": {}}}).steps

    assert step.params == {"at_s": 4.0}  # report 4-12 s is now 0-8 s


def test_poster_default_without_an_edit_map_is_the_middle_of_the_video():
    [step] = plan({"exports": {"poster": {}}}, edit_map=None).steps

    assert step.params == {"at_s": 6.0}


def test_a_custom_poster_time_resolves_through_the_time_map():
    result = plan(
        {
            "trim": {"cuts": [{"from": 1.0, "to": 2.0}]},
            "speed": 2.0,
            "exports": {"poster": {"time": scene("report", at="Export")}},
        }
    )

    assert result.steps[-1].params == {"at_s": 3.25}  # (7.5 - 1) / 2


def test_gif_defaults_to_the_whole_video():
    [step] = plan({"exports": {"gif": {}}}).steps

    assert step.export
    assert step.params == {"start_s": 0.0, "end_s": 12.0, "width": 640, "fps": 10}
    assert step.line == "gif\t0.000s-12.000s\t640px 10fps"


def test_a_gif_range_resolves_through_the_time_map():
    result = plan(
        {
            "trim": {"cuts": [{"from": 1.0, "to": 2.0}]},
            "speed": 2.0,
            "exports": {"gif": {"from": scene("report"), "to": 10.0, "width": 320, "fps": 15}},
        }
    )

    assert result.steps[-1].params == {"start_s": 1.5, "end_s": 4.5, "width": 320, "fps": 15}


def test_gif_command_generates_and_applies_a_palette_at_width_and_fps():
    [step] = plan({"exports": {"gif": {"from": 1.0, "to": 3.0, "width": 320, "fps": 12}}}).steps

    args = gif_cmd("in.mp4", "out.gif", **step.params)
    assert args[args.index("-ss") + 1] == "1.000" and args[args.index("-t") + 1] == "2.000"
    graph = args[args.index("-filter_complex") + 1]
    assert "fps=12,scale=320:-1" in graph
    assert "palettegen" in graph and "paletteuse" in graph
    assert args[-1] == "out.gif"


def test_a_gif_over_ten_megabytes_warns():
    assert gif_warning(9_999_999) is None
    assert "11.0 MB" in gif_warning(11_000_000)


def test_size_cap_step_carries_the_cap():
    [step] = plan({"exports": {"size_cap_mb": 10}}).steps

    assert step.export and step.params == {"cap_mb": 10}
    assert step.line == "size_cap\t10 MB"


def test_size_cap_lowers_the_bitrate_and_keeps_the_resolution_when_it_can():
    # 20 MB over 60 s: 20 * 8000 / 60 * 0.95 = 2533 kb/s, minus 64 kb/s audio
    assert size_cap_encoding(20, 60.0, 1920, 1080) == (1920, 1080, 2469)
    # 8 MB: 949 kb/s video, still above 1080p's floor (0.015 bpp * 1920x1080x30 = 933)
    assert size_cap_encoding(8, 60.0, 1920, 1080) == (1920, 1080, 949)


def test_size_cap_steps_the_resolution_down_only_below_the_floor():
    # 7 MB: 823 kb/s < 933 (1080p floor) -> 720p (floor 415)
    assert size_cap_encoding(7, 60.0, 1920, 1080) == (1280, 720, 823)
    assert size_cap_encoding(7, 60.0, 1440, 900) == (1440, 900, 823)  # floor 583
    # 189 kb/s: below 1200p, 1080p, 720p and 540p (864x540: 210) floors
    assert size_cap_encoding(2, 60.0, 1920, 1200) == (576, 360, 189)


def test_an_unreachable_size_cap_names_the_smallest_reachable_size():
    # 640x360 floor 103.7 kb/s + 64 audio, over 60 s, with the margin: 1.33 MB
    with pytest.raises(
        RecipeError, match=r"1 MB.*60\.0s video.*640x360.*smallest reachable size is 1.33 MB"
    ):
        size_cap_encoding(1, 60.0, 1920, 1080)


# --- validation ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "recipe, message",
    [
        ([], "recipe must be a JSON object"),
        ({"spead": 1.2}, "unknown recipe key 'spead'"),
        ({"speed": 2.5}, r"speed must be a number from 0.5 to 2"),
        ({"speed": 0.4}, r"speed must be a number from 0.5 to 2"),
        ({"speed": True}, r"speed must be a number"),
        ({"source": ""}, "source must be a file name"),
        ({"trim": {"begin": 1}}, r"unknown key 'begin' in trim"),
        ({"trim": {"start": -1}}, r"trim.start must be seconds"),
        ({"trim": {"start": "1s"}}, r"trim.start must be seconds"),
        ({"trim": {"cuts": {"from": 1, "to": 2}}}, r"trim.cuts must be a list"),
        ({"trim": {"cuts": [{"from": 3, "to": 2}]}}, r"trim.cuts\[0\] must end after it starts"),
        ({"trim": {"cuts": [{"from": 1}]}}, r"trim.cuts\[0\] needs 'from' and 'to'"),
        (
            {"trim": {"start": {"scene": "intro", "offset": 1}}},
            r"unknown key 'offset' in trim.start",
        ),
        ({"trim": {"start": 6, "end": 5}}, "trim.end must be after trim.start"),
        ({"trim": {"end": 13}}, "past the end"),
        ({"trim": {"cuts": [{"from": 0, "to": 12}]}}, "nothing left"),
        ({"exports": {"webm": {}}}, "unknown key 'webm' in exports"),
        ({"exports": {"size_cap_mb": 0}}, "size_cap_mb must be a positive number of megabytes"),
        ({"exports": {"size_cap_mb": "10"}}, "size_cap_mb must be a positive number"),
        ({"exports": {"gif": {"width": 8}}}, r"gif.width must be a whole number from 16 to 1920"),
        ({"exports": {"gif": {"fps": 60}}}, r"gif.fps must be a whole number from 1 to 30"),
        ({"exports": {"gif": {"range": [1, 2]}}}, "unknown key 'range' in exports.gif"),
        ({"exports": {"gif": {"from": 3, "to": 2}}}, "exports.gif: its range is empty or all cut"),
        ({"exports": {"gif": {"from": -1}}}, r"exports.gif.from must be seconds"),
        ({"exports": {"poster": {"time": "1s"}}}, r"exports.poster.time must be seconds"),
        ({"trim": {"start": 2}, "exports": {"poster": {"time": 1}}}, "inside a cut"),
        (
            {"exports": {"contact_sheet": {"columns": 0}}},
            "columns must be a whole number from 1 to 8",
        ),
        ({"exports": {"contact_sheet": {"interval_s": 0}}}, "interval_s must be a positive number"),
        ({"exports": {"contact_sheet": {"interval_s": 0.01}}}, "more than 60 frames"),
    ],
)
def test_invalid_recipes_fail_with_clear_messages(recipe, message):
    with pytest.raises(RecipeError, match=message):
        plan(recipe)


# --- cards -----------------------------------------------------------------------------

DEMO = {"title": "Weekly report", "url": "https://example.com/reports"}


def test_a_title_card_shifts_the_demo_and_its_cues_by_its_length():
    result = plan({"cards": {"title": {"seconds": 2.5}}}, demo=DEMO)

    assert result.total_s == pytest.approx(SOURCE_S + 2.5)
    assert result.output_s(5.0) == pytest.approx(7.5)
    assert result.cues[0] == Cue(2.5, 4.5, "Welcome.")
    assert result.demo_span == pytest.approx((2.5, 14.5))


def test_a_closing_card_adds_to_the_total_but_moves_nothing():
    result = plan({"cards": {"closing": {"seconds": 4}}}, demo=DEMO)

    assert result.total_s == pytest.approx(SOURCE_S + 4)
    assert result.output_s(5.0) == pytest.approx(5.0)
    assert result.demo_span == pytest.approx((0.0, SOURCE_S))


def test_cards_with_no_text_default_to_the_demo_title_and_last_scene_url():
    [step] = plan({"cards": {"closing": {}, "title": {}}}, demo=DEMO).steps

    assert step.line == "cards\ttitle 3.000s\tclosing 3.000s"
    title, closing = step.params["cards"]["title"], step.params["cards"]["closing"]
    assert title["lines"] == (("Weekly report", "title"),)
    assert closing["lines"] == (("https://example.com/reports", "url"),)
    assert (title["background"], title["color"]) == ("#111111", "#ffffff")
    assert step.params["demo_s"] == pytest.approx(SOURCE_S)


def test_card_text_and_style_override_the_defaults():
    [step] = plan(
        {
            "cards": {
                "title": {
                    "title": "Reports",
                    "subtitle": "in 30 seconds",
                    "logo": "logo.png",
                    "background": "#fff",
                    "color": "#000000",
                    "seconds": 2,
                },
                "closing": {"text": "Try it today"},
            }
        },
        demo=DEMO,
    ).steps

    title, closing = step.params["cards"]["title"], step.params["cards"]["closing"]
    assert title == {
        "seconds": 2,
        "background": "#fff",
        "color": "#000000",
        "logo": "logo.png",
        "lines": (("Reports", "title"), ("in 30 seconds", "subtitle")),
    }
    assert closing["lines"] == (("Try it today", "text"), ("https://example.com/reports", "url"))


def test_card_defaults_come_from_the_demo_spec_or_the_slug():

    spec = {"title": "Weekly report", "scenes": [{"url": "https://a"}, {"url": "https://b"}]}
    assert card_defaults(spec, "weekly") == {"title": "Weekly report", "url": "https://b"}
    assert card_defaults({}, "weekly") == {"title": "weekly", "url": None}


def test_a_closing_card_without_text_or_default_url_is_an_error():
    with pytest.raises(RecipeError, match="cards.closing needs its text"):
        plan({"cards": {"closing": {}}}, demo={"title": "x", "url": None})


def test_cards_step_joins_cards_and_demo_with_fades():
    [step] = plan({"cards": {"title": {"seconds": 2}, "closing": {}}}, demo=DEMO).steps

    args = cards_cmd(
        "in.mp4", "out.mp4", pngs={"title": "t.png", "closing": "c.png"}, **step.params
    )
    assert args[args.index("t.png") - 2] == "2.000" and args[args.index("c.png") - 2] == "3.000"
    graph = args[args.index("-filter_complex") + 1]
    assert (
        "[1:v]format=yuv420p,setsar=1,fade=t=in:st=0:d=0.500,fade=t=out:st=1.500:d=0.500[titlev]"
        in graph
    )
    assert (
        "[0:v]fps=30,format=yuv420p,setsar=1,fade=t=in:st=0:d=0.500,"
        "fade=t=out:st=11.500:d=0.500[demov]"
    ) in graph
    assert "anullsrc=r=48000:cl=stereo,atrim=duration=3.000[closinga]" in graph
    assert "[titlev][titlea][demov][demoa][closingv][closinga]concat=n=3:v=1:a=1[v][a]" in graph


def test_the_default_poster_and_the_contact_sheet_account_for_the_cards():
    steps = plan(
        {
            "cards": {"title": {"seconds": 2}, "closing": {}},
            "exports": {"poster": {}, "contact_sheet": {"interval_s": 4}},
        },
        demo=DEMO,
    ).steps

    assert [step.op for step in steps] == ["cards", "poster", "contact_sheet"]
    assert steps[1].params["at_s"] == pytest.approx(4.0)  # middle of scene intro, after 2 s
    assert steps[2].params["times"] == (0.0, 4.0, 8.0, 12.0, 16.0)  # 17 s in all


def test_card_svg_draws_its_colours_and_escaped_text():

    svg = card_svg(
        {"background": "#123456", "color": "#abcdef", "lines": (("Q&A <live>", "title"),)},
        640,
        360,
        logo_href="logo.png",
    )

    assert 'width="640" height="360"' in svg
    assert 'fill="#123456"' in svg and 'fill="#abcdef"' in svg
    assert "Q&amp;A &lt;live&gt;" in svg
    assert 'href="logo.png"' in svg


@pytest.mark.parametrize(
    "cards, message",
    [
        ({"intro": {}}, "unknown key 'intro' in cards"),
        ({"title": {"url": "x"}}, "unknown key 'url' in cards.title"),
        ({"title": {"title": ""}}, "cards.title.title must be non-empty text"),
        ({"closing": {"seconds": 0.5}}, "cards.closing.seconds must be a number from 1 to 10"),
        ({"title": {"background": "red"}}, "cards.title.background must be a hex colour"),
    ],
)
def test_invalid_cards_fail_with_clear_messages(cards, message):
    with pytest.raises(RecipeError, match=message):
        plan({"cards": cards}, demo=DEMO)


# --- chapters --------------------------------------------------------------------------

LONG_S = 40.0
LONG_MAP = {
    "total_s": LONG_S,
    "subtitles_burned": False,
    "scenes": [
        {"id": s, "title": s.title(), "start_s": a, "end_s": b, "actions": []}
        for s, a, b in (("login", 0.0, 12.0), ("search", 12.0, 25.0), ("export", 25.0, 40.0))
    ],
}


def chapters(p):
    return [(c.title, c.start_s, c.end_s) for c in p.chapters]


def test_one_chapter_per_scene_named_after_its_title_through_the_time_map():
    p = plan({"chapters": True, "trim": {"start": 1.0}, "speed": 2.0})

    assert chapters(p) == [("Intro", 0.0, 1.5), ("Report", 1.5, 5.5)]


def test_a_title_card_becomes_the_intro_chapter_and_the_first_scene_starts_after_it():
    p = plan(
        {"chapters": True, "cards": {"title": {"title": "Demo"}, "closing": {"text": "Bye"}}},
        LONG_S,
        edit_map=LONG_MAP,
    )

    assert chapters(p) == [
        ("Intro", 0.0, 3.0),
        ("Login", 3.0, 15.0),
        ("Search", 15.0, 28.0),
        ("Export", 28.0, 46.0),  # the closing card ends the last chapter
    ]


def test_a_scene_that_is_cut_entirely_gets_no_chapter():
    recipe = {"chapters": True, "trim": {"cuts": [{"from": 12.0, "to": 25.0}]}}

    p = plan(recipe, LONG_S, edit_map=LONG_MAP)

    assert chapters(p) == [("Login", 0.0, 12.0), ("Export", 12.0, 27.0)]


def test_chapters_that_youtube_accepts_give_no_warning():
    assert plan({"chapters": True}, LONG_S, edit_map=LONG_MAP).warnings == ()


def test_chapters_breaking_youtube_rules_warn():
    warnings = plan({"chapters": True}).warnings

    assert any("YouTube needs at least 3 chapters (got 2)" in w for w in warnings)
    assert any('chapter "Intro" is 4.000s; YouTube needs at least 10 s' in w for w in warnings)


def test_chapters_need_the_edit_map():
    with pytest.raises(RecipeError, match="chapters need the scene times.*re-run compose"):
        plan({"chapters": True}, edit_map=None)


def test_chapters_step_writes_a_youtube_list_and_mp4_chapter_metadata():
    p = plan({"chapters": True}, LONG_S, edit_map=LONG_MAP)
    [step] = p.steps

    assert step.line == "chapters\t3 chapters"
    assert youtube_chapters(p.chapters) == "0:00 Login\n0:12 Search\n0:25 Export\n"
    assert chapters_metadata(p.chapters) == (
        ";FFMETADATA1\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=12000\ntitle=Login\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=12000\nEND=25000\ntitle=Search\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=25000\nEND=40000\ntitle=Export\n"
    )


def test_chapters_off_is_no_step():
    assert plan({"chapters": False}).steps == ()


@pytest.mark.parametrize(
    ("recipe", "message"),
    [
        ({"chapters": "yes"}, "chapters must be true or false"),
        ({"translations": "ko"}, "translations must be a list of language codes"),
        ({"translations": ["xx"]}, "translations: unknown language code 'xx'"),
        ({"translations": ["ko", "ko"]}, "translations lists 'ko' twice"),
    ],
)
def test_invalid_chapters_and_translations_fail_with_clear_messages(recipe, message):
    with pytest.raises(RecipeError, match=message):
        plan(recipe, translations={"ko": CUES})


# --- translations ----------------------------------------------------------------------

KO = tuple(Cue(c.start_s, c.end_s, f"[ko] {c.text}") for c in CUES)


def test_translated_cues_are_retimed_like_the_original():
    recipe = {"translations": ["ko"], "trim": {"start": 2.0}, "speed": 2.0}
    p = plan(recipe, translations={"ko": KO}, demo=DEMO)
    titled = plan({**recipe, "cards": {"title": {}}}, translations={"ko": KO}, demo=DEMO)

    assert p.translations["ko"] == (
        Cue(0.0, 1.0, "[ko] This is the intro."),
        Cue(1.0, 2.5, "[ko] Open the report."),
        Cue(2.5, 5.0, "[ko] Export it."),
    )
    assert [c.start_s for c in titled.translations["ko"]] == [3.0, 4.0, 5.5]


@pytest.mark.parametrize(
    ("ko", "message"),
    [
        (KO[:3], "ko.srt has 3 cues, the original has 4"),
        ((*KO[:3], Cue(7.5, 12.0, "[ko] Export it.")), "ko.srt cue 4 is at 7.500s-12.000s"),
    ],
)
def test_a_translation_must_keep_the_cue_count_and_timings(ko, message):
    with pytest.raises(RecipeError, match=message):
        plan({"translations": ["ko"]}, translations={"ko": ko})


def test_subtitle_tracks_step_lists_the_original_and_each_language():
    p = plan({"translations": ["ko", "deu"]}, translations={"ko": KO, "deu": CUES})
    [step] = p.steps

    assert step.line == "subtitle_tracks\toriginal, ko, deu"
    assert [(lang, tag) for lang, tag, _ in step.params["tracks"]] == [
        (None, "und"),
        ("ko", "kor"),
        ("deu", "deu"),
    ]


def test_subtitle_tracks_cmd_muxes_each_srt_as_a_tagged_text_track():
    cmd = subtitle_tracks_cmd("in.mp4", "out.mp4", ["a.srt", "a.ko.srt"], ["und", "kor"])

    assert cmd[cmd.index("-i") + 1 :][:5] == ["in.mp4", "-i", "a.srt", "-i", "a.ko.srt"]
    assert "mov_text" in cmd and cmd[-1] == "out.mp4"
    assert cmd[cmd.index("language=kor") - 1] == "-metadata:s:s:1"


def test_translations_over_burned_in_subtitles_warn():
    burned = {**EDIT_MAP, "subtitles_burned": True}
    clean = {**EDIT_MAP, "subtitles_burned": False}
    recipe = {"translations": ["ko"]}

    for edit_map in (burned, None):
        [warning] = plan(recipe, edit_map=edit_map, translations={"ko": KO}).warnings
        assert "burned-in subtitles" in warning and "--no-burn-subs" in warning
    assert plan(recipe, edit_map=clean, translations={"ko": KO}).warnings == ()


# --- click sounds (#20) ----------------------------------------------------------------


def click_step(result):
    [step] = [step for step in result.steps if step.op == "click_sounds"]
    return step


def test_a_click_sound_plays_one_cursor_glide_after_each_known_click():
    step = click_step(plan({"click_sounds": {}}))

    # report's clicks start at 4.0 and 7.5; intro's point is not a click
    assert step.params["times"] == (4.6, 8.1)
    assert step.line == "click_sounds\t2 clicks"


def test_click_times_go_through_the_time_map_and_clicks_in_a_cut_are_dropped_and_listed():
    result = plan(
        {
            "click_sounds": {},
            "trim": {"start": 2.0, "cuts": [{"from": 4.0, "to": 7.0}]},
            "speed": 2.0,
        }
    )

    # 4.6 is cut; 8.1 -> (8.1 - 2 - 3) / 2
    assert click_step(result).params["times"] == (1.55,)
    assert click_step(result).line == "click_sounds\t1 clicks\t1 unplaceable"
    assert result.warnings == (
        "no click sound for scene 'report' action 0 (click at 4.600s): inside a cut",
    )


def test_clicks_without_a_known_start_are_listed_as_unplaceable():
    edit_map = {
        **EDIT_MAP,
        "scenes": [
            {
                **EDIT_MAP["scenes"][1],
                "actions": [
                    *EDIT_MAP["scenes"][1]["actions"],
                    {"index": 1, "type": "type"},
                    {"index": 3, "type": "key"},
                ],
            }
        ],
    }

    result = plan({"click_sounds": {}}, edit_map=edit_map)

    assert click_step(result).params["times"] == (4.6, 8.1)
    assert result.warnings == (
        "no click sound for scene 'report' action 1 (type): start unknown "
        "(not the scene's first action and no at phrase)",
    )


def test_click_sounds_default_to_the_bundled_sound_at_half_level():
    step = click_step(plan({"click_sounds": {}}))

    assert step.params["sound"].endswith("assets/click.wav")
    assert Path(step.params["sound"]).is_file()
    assert step.params["level"] == 0.5


def test_click_sounds_mix_a_custom_sound_at_each_click_time_over_the_narration():
    step = click_step(plan({"click_sounds": {"file": "/demo/tick.wav", "level": 0.3}}))

    args = step.cmd("in.mp4", "out.mp4")
    assert args[args.index("-i", args.index("in.mp4")) + 1] == "/demo/tick.wav"
    graph = args[args.index("-filter_complex") + 1]
    assert graph == (
        "[1:a]volume=0.300,asplit=2[c0][c1];"
        "[c0]adelay=4600:all=1[d0];[c1]adelay=8100:all=1[d1];"
        "[0:a][d0][d1]amix=inputs=3:duration=first:normalize=0[a]"
    )
    # the picture is copied, not re-encoded
    assert args[args.index("-c:v") + 1] == "copy"
    assert args[-1] == "out.mp4"


def test_music_is_looped_to_the_output_length_ducked_under_the_narration_and_faded_out():
    result = plan(
        {
            "music": {"file": "/demo/song.mp3", "level": 0.3, "duck": 10, "fade_out_s": 1.5},
            "speed": 2.0,
        },
        edit_map=None,
    )
    [step] = [step for step in result.steps if step.op == "music"]

    assert step.line == "music\tsong.mp3\tlevel 0.30\tduck 10:1\tfade out 1.50s"
    args = step.cmd("in.mp4", "out.mp4")
    # looped forever, cut to the 6 s output
    assert args[args.index("/demo/song.mp3") - 3 : args.index("/demo/song.mp3")] == [
        "-stream_loop",
        "-1",
        "-i",
    ]
    graph = args[args.index("-filter_complex") + 1]
    assert graph == (
        "[1:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=0.300,"
        "atrim=duration=6.000,afade=t=out:st=4.500:d=1.500[m];"
        "[0:a]asplit=2[voice][key];"
        "[m][key]sidechaincompress=threshold=0.02:ratio=10:attack=20:release=400[ducked];"
        "[voice][ducked]amix=inputs=2:duration=first:normalize=0[a]"
    )
    assert args[args.index("-c:v") + 1] == "copy"


def test_music_defaults():
    [step] = plan({"music": {"file": "song.mp3"}}).steps

    assert step.params == {
        "file": "song.mp3",
        "level": 0.2,
        "duck": 8,
        "fade_out_s": 2.0,
        "total_s": 12.0,
    }


def test_click_sounds_run_before_the_music():
    result = plan({"music": {"file": "song.mp3"}, "click_sounds": {}})

    assert [step.op for step in result.steps] == ["click_sounds", "music"]


@pytest.mark.parametrize(
    "recipe, message",
    [
        ({"click_sounds": {"volume": 1}}, "unknown key 'volume' in click_sounds"),
        ({"click_sounds": {"level": 1.5}}, r"click_sounds.level must be a number from 0 to 1"),
        ({"click_sounds": {"file": ""}}, r"click_sounds.file must be an audio file"),
        ({"music": {}}, r"music.file must be an audio file"),
        ({"music": {"file": "a.mp3", "level": -0.1}}, r"music.level must be a number from 0 to 1"),
        ({"music": {"file": "a.mp3", "duck": 0.5}}, r"music.duck must be a number from 1"),
        ({"music": {"file": "a.mp3", "fade_out_s": -1}}, r"music.fade_out_s must be"),
        ({"music": {"file": "a.mp3", "fade_out_s": 13}}, r"music.fade_out_s .* longer than the"),
        ({"music": {"file": "https://example.com/a.mp3"}}, "never downloaded"),
    ],
)
def test_invalid_click_sounds_and_music_fail_with_clear_messages(recipe, message):
    with pytest.raises(RecipeError, match=message):
        plan(recipe)


def test_click_sounds_without_an_edit_map_ask_to_re_run_compose():
    with pytest.raises(RecipeError, match="click_sounds.*re-run compose"):
        plan({"click_sounds": {}}, edit_map=None)


def test_music_plays_under_both_cards_and_clicks_shift_with_the_title_card():
    result = plan(
        {
            "cards": {"title": {"seconds": 2}, "closing": {"seconds": 3}},
            "click_sounds": {},
            "music": {"file": "song.mp3"},
        },
        demo=DEMO,
    )

    # music runs after the cards, so it covers the title and closing cards too
    assert [step.op for step in result.steps] == ["cards", "click_sounds", "music"]
    assert result.steps[2].params["total_s"] == pytest.approx(SOURCE_S + 5)
    assert result.steps[1].params["times"] == (6.6, 10.1)
