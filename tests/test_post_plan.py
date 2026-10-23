import pytest

from post_plan import Cue, RecipeError, build_post_plan, card_defaults, card_svg, cards_cmd

SOURCE_S = 12.0
EDIT_MAP = {
    "total_s": 12.0,
    "scenes": [
        {"id": "intro", "title": "Intro", "start_s": 0.0, "end_s": 4.0,
         "actions": [{"index": 0, "type": "point", "start_s": 0.0}]},
        {"id": "report", "title": "Report", "start_s": 4.0, "end_s": 12.0,
         "actions": [{"index": 0, "type": "click", "start_s": 4.0},
                     {"index": 2, "type": "click", "start_s": 7.5, "at": "Export"}]},
    ],
}
CUES = (
    Cue(0.0, 2.0, "Welcome."),
    Cue(2.0, 4.0, "This is the intro."),
    Cue(4.0, 7.0, "Open the report."),
    Cue(7.0, 12.0, "Export it."),
)


def plan(recipe, source_s=SOURCE_S, cues=CUES, edit_map=EDIT_MAP, demo=None):
    return build_post_plan(recipe, source_s, cues, edit_map, demo)


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
    result = plan({"trim": {"start": 2.0, "cuts": [{"from": 1.0, "to": 3.0}, {"from": 2.5, "to": 4.0}]}})

    assert result.total_s == pytest.approx(8.0)
    assert result.output_s(4.0) == 0.0


def test_cut_times_snap_to_whole_frames():
    result = plan({"trim": {"start": 1.01}})

    assert result.total_s == pytest.approx(11.0)


@pytest.mark.parametrize("time, message", [
    (5.5, "inside a cut"),
    (0.5, "inside a cut"),
    (12.5, "past the end"),
])
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
    result = plan({"trim": {"start": scene("report"), "end": scene("report", at="Export", offset_s=1)}})

    assert result.total_s == pytest.approx(4.5)


@pytest.mark.parametrize("ref, message", [
    (scene("missing"), "unknown scene 'missing'.*intro, report"),
    (scene("report", at="Nope"), "scene 'report' has no action at 'Nope'.*Export"),
    (scene("intro", offset_s=-1), "before the start"),
])
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
    assert ("[0:a]atrim=start=1.000:end=5.000,asetpts=PTS-STARTPTS,"
            "afade=t=in:d=0.030,afade=t=out:st=3.970:d=0.030[a0]") in graph
    assert "[0:a]atrim=start=6.000:end=12.000,asetpts=PTS-STARTPTS,afade=t=in:d=0.030[a1]" in graph
    assert "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]" in graph
    assert args[-1] == "out.mp4"


def test_speed_step_retimes_video_by_timestamps_and_audio_by_tempo():
    [step] = plan({"speed": 1.25}).steps

    args = step.cmd("in.mp4", "out.mp4")
    graph = args[args.index("-filter_complex") + 1]
    assert graph == "[0:v]setpts=PTS/1.250000,fps=30[v];[0:a]atempo=1.250000[a]"


def test_contact_sheet_samples_the_output_at_its_interval():
    [step] = plan({"speed": 2.0, "exports": {"contact_sheet": {"columns": 3, "interval_s": 2}}}).steps[1:]

    assert step.params == {"columns": 3, "times": (0.0, 2.0, 4.0)}
    assert step.line == "contact_sheet\t3 frames"


def test_contact_sheet_defaults_to_twelve_frames_in_four_columns():
    [step] = plan({"exports": {"contact_sheet": {}}}).steps

    assert step.params["columns"] == 4
    assert step.params["times"] == tuple(float(t) for t in range(12))


# --- validation ------------------------------------------------------------------------

@pytest.mark.parametrize("recipe, message", [
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
    ({"trim": {"start": {"scene": "intro", "offset": 1}}}, r"unknown key 'offset' in trim.start"),
    ({"trim": {"start": 6, "end": 5}}, "trim.end must be after trim.start"),
    ({"trim": {"end": 13}}, "past the end"),
    ({"trim": {"cuts": [{"from": 0, "to": 12}]}}, "nothing left"),
    ({"exports": {"gif": {}}}, "unknown key 'gif' in exports"),
    ({"exports": {"contact_sheet": {"columns": 0}}}, "columns must be a whole number from 1 to 8"),
    ({"exports": {"contact_sheet": {"interval_s": 0}}}, "interval_s must be a positive number"),
    ({"exports": {"contact_sheet": {"interval_s": 0.01}}}, "more than 60 frames"),
])
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
    [step] = plan({"cards": {
        "title": {"title": "Reports", "subtitle": "in 30 seconds", "logo": "logo.png",
                  "background": "#fff", "color": "#000000", "seconds": 2},
        "closing": {"text": "Try it today"}}}, demo=DEMO).steps

    title, closing = step.params["cards"]["title"], step.params["cards"]["closing"]
    assert title == {"seconds": 2, "background": "#fff", "color": "#000000", "logo": "logo.png",
                     "lines": (("Reports", "title"), ("in 30 seconds", "subtitle"))}
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

    args = cards_cmd("in.mp4", "out.mp4", pngs={"title": "t.png", "closing": "c.png"},
                     **step.params)
    assert args[args.index("t.png") - 2] == "2.000" and args[args.index("c.png") - 2] == "3.000"
    graph = args[args.index("-filter_complex") + 1]
    assert "[1:v]format=yuv420p,setsar=1,fade=t=in:st=0:d=0.500,fade=t=out:st=1.500:d=0.500[titlev]" in graph
    assert ("[0:v]fps=30,format=yuv420p,setsar=1,fade=t=in:st=0:d=0.500,"
            "fade=t=out:st=11.500:d=0.500[demov]") in graph
    assert "anullsrc=r=48000:cl=stereo,atrim=duration=3.000[closinga]" in graph
    assert "[titlev][titlea][demov][demoa][closingv][closinga]concat=n=3:v=1:a=1[v][a]" in graph


def test_card_svg_draws_its_colours_and_escaped_text():

    svg = card_svg({"background": "#123456", "color": "#abcdef",
                    "lines": (("Q&A <live>", "title"),)}, 640, 360, logo_href="logo.png")

    assert 'width="640" height="360"' in svg
    assert 'fill="#123456"' in svg and 'fill="#abcdef"' in svg
    assert "Q&amp;A &lt;live&gt;" in svg
    assert 'href="logo.png"' in svg


@pytest.mark.parametrize("cards, message", [
    ({"intro": {}}, "unknown key 'intro' in cards"),
    ({"title": {"url": "x"}}, "unknown key 'url' in cards.title"),
    ({"title": {"title": ""}}, "cards.title.title must be non-empty text"),
    ({"closing": {"seconds": 0.5}}, "cards.closing.seconds must be a number from 1 to 10"),
    ({"title": {"background": "red"}}, "cards.title.background must be a hex colour"),
])
def test_invalid_cards_fail_with_clear_messages(cards, message):
    with pytest.raises(RecipeError, match=message):
        plan({"cards": cards}, demo=DEMO)
