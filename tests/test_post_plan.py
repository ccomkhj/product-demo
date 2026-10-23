import pytest

from post_plan import Cue, RecipeError, build_post_plan

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


def plan(recipe, source_s=SOURCE_S, cues=CUES, edit_map=EDIT_MAP):
    return build_post_plan(recipe, source_s, cues, edit_map)


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
    ({"watermark": {}}, "watermark needs either an 'image' or a 'text'"),
    ({"watermark": {"text": "x", "corner": "middle"}}, "watermark.corner must be one of"),
    ({"watermark": {"text": "x", "size": 0.5}}, "watermark.size must be a share"),
    ({"watermark": {"text": "x", "opacity": 0}}, "watermark.opacity must be a number"),
    ({"callouts": {}}, "callouts must be a list"),
    ({"callouts": [{"type": "circle"}]}, r"callouts\[0\].type must be one of arrow, text"),
    ({"callouts": [{"type": "arrow", "from": [1, 1], "start": 1, "end": 2}]},
     r"callouts\[0\] needs 'to'"),
    ({"callouts": [{"type": "text", "text": "x", "position": [1], "start": 1, "end": 2}]},
     r"callouts\[0\].position must be \[x, y\]"),
    ({"progress_bar": {"edge": "left"}}, "progress_bar.edge must be one of bottom, top"),
    ({"progress_bar": {"colour": "blue"}}, "progress_bar.colour must be a colour"),
    ({"progress_bar": {"thickness": 0}}, "progress_bar.thickness must be a whole number"),
])
def test_invalid_recipes_fail_with_clear_messages(recipe, message):
    with pytest.raises(RecipeError, match=message):
        plan(recipe)


# --- watermark, callouts, progress bar ------------------------------------------------

FRAME = (1280, 720)


def overlay_plan(recipe, cues=CUES, **kwargs):
    return build_post_plan(recipe, SOURCE_S, cues, EDIT_MAP, frame=FRAME, **kwargs)


def graph_of(step):
    args = step.cmd("in.mp4", "out.mp4")
    return args[args.index("-filter_complex") + 1]


def test_progress_bar_spans_the_demo_on_its_edge():
    _, step = overlay_plan({"speed": 2.0, "progress_bar": {
        "edge": "top", "colour": "#ff0000", "thickness": 4}}).steps

    assert step.line == "progress_bar\ttop 4px\t0.000s-6.000s"
    assert "color=c=0xff0000:s=1280x4:r=30:d=6.000[bar]" in graph_of(step)
    assert "overlay=x='-w+w*t/6.000':y=0" in graph_of(step)
    args = step.cmd("in.mp4", "out.mp4")
    assert args[args.index("-c:a") + 1] == "copy"


def test_overlays_run_after_speed_and_before_exports():
    result = overlay_plan({"exports": {"contact_sheet": {}}, "progress_bar": {},
                           "callouts": [label(1.0, 2.0)], "watermark": {"text": "x"},
                           "speed": 1.5})

    assert [step.op for step in result.steps] == [
        "speed", "watermark", "callouts", "progress_bar", "contact_sheet"]


def test_progress_bar_defaults_to_a_thin_bar_at_the_bottom():
    [step] = overlay_plan({"progress_bar": {}}).steps

    assert "s=1280x6:" in graph_of(step)
    assert "y=H-h" in graph_of(step)


def arrow(start, end, frm=(100, 100), to=(300, 200)):
    return {"type": "arrow", "from": list(frm), "to": list(to), "start": start, "end": end}


def label(start, end, text="Click here", position=(100, 100)):
    return {"type": "text", "text": text, "position": list(position), "start": start, "end": end}


def test_callout_windows_follow_anchors_through_cuts_and_speed():
    result = overlay_plan({"trim": {"cuts": [{"from": 1.0, "to": 3.0}]}, "speed": 2.0,
                           "callouts": [arrow(scene("report"), scene("report", at="Export")),
                                        label(8.0, 10.0)]})
    step = result.steps[-1]

    assert [(layer.start_s, layer.end_s) for layer in step.params["layers"]] == [
        (pytest.approx(1.0), pytest.approx(2.75)), (pytest.approx(3.0), pytest.approx(4.0))]
    assert step.line == "callouts\t2\tarrow 1.000s-2.750s, text 3.000s-4.000s"


def test_callouts_fade_in_and_out_only_inside_their_window():
    [step] = overlay_plan({"callouts": [label(2.0, 4.0)]}).steps

    graph = graph_of(step)
    assert "fade=t=in:st=2.000:d=0.250:alpha=1,fade=t=out:st=3.750:d=0.250:alpha=1" in graph
    assert "enable='between(t,2.000,4.000)'" in graph


def test_callouts_are_drawn_as_full_frame_svgs():
    [step] = overlay_plan({"callouts": [arrow(1.0, 2.0), label(1.0, 2.0, text="Q&A")]}).steps

    first, second = step.params["layers"]
    assert 'width="1280" height="720"' in first.svg and "<line" in first.svg
    assert "Q&amp;A" in second.svg and "<rect" in second.svg


def test_a_callout_over_the_subtitle_band_while_a_cue_shows_warns():
    low = label(0.5, 1.5, position=(400, 650))

    assert overlay_plan({"callouts": [low]}).warnings == (
        "callouts[0] (text) overlaps the burned-in subtitle band at 0.500s-1.500s; "
        "move it above y=576",)
    assert overlay_plan({"callouts": [low]}, cues=()).warnings == ()
    assert overlay_plan({"callouts": [label(0.5, 1.5, position=(400, 300))]}).warnings == ()


def test_a_callout_over_the_presenter_bubble_warns():
    corner = arrow(1.0, 2.0, frm=(300, 300), to=(60, 60))

    assert overlay_plan({"callouts": [corner]}, bubble=("top-left", 0.2)).warnings == (
        "callouts[0] (arrow) overlaps the presenter bubble (top-left)",)
    assert overlay_plan({"callouts": [corner]}, bubble=("top-right", 0.2)).warnings == ()
    assert overlay_plan({"callouts": [corner]}).warnings == ()


def test_image_watermark_sits_in_its_corner_at_its_size_and_opacity():
    [step] = overlay_plan({"watermark": {"image": "logo.png", "corner": "bottom-left",
                                         "size": 0.1, "opacity": 0.5}}).steps

    assert step.line == "watermark\timage logo.png\tbottom-left"
    assert "[1:v]format=rgba,scale=-1:72,colorchannelmixer=aa=0.50[l1]" in graph_of(step)
    assert "overlay=x=22:y=H-h-22:shortest=1[v]" in graph_of(step)


def test_text_watermark_is_an_svg_and_stays_for_the_whole_video():
    [step] = overlay_plan({"watermark": {"text": "acme.io"}}).steps
    [layer] = step.params["layers"]

    assert step.line == 'watermark\ttext "acme.io"\ttop-right'
    assert "acme.io" in layer.svg and 'opacity="0.8"' in layer.svg
    assert "enable=" not in graph_of(step)


def test_the_default_watermark_corner_avoids_the_overlay_and_the_bubble():
    # keycaps sit bottom-right, the Back badge top-left, the bubble defaults to bottom-left
    assert overlay_plan({"watermark": {"text": "x"}}).steps[0].line.endswith("top-right")
    assert overlay_plan({"watermark": {"text": "x"}},
                        bubble=("top-right", 0.2)).steps[0].line.endswith("bottom-left")


@pytest.mark.parametrize("callout, message", [
    (label(2.0, 1.0), r"callouts\[0\] must end after it starts"),
    (label(1.0, 2.0, position=(1300, 10)), r"callouts\[0\].position \[1300, 10\] is outside"),
])
def test_bad_callouts_are_clear_errors(callout, message):
    with pytest.raises(RecipeError, match=message):
        overlay_plan({"callouts": [callout]})


def test_a_callout_window_inside_a_cut_is_an_error():
    with pytest.raises(RecipeError, match=r"callouts\[0\].start.*inside a cut"):
        overlay_plan({"trim": {"cuts": [{"from": 1.0, "to": 3.0}]}, "callouts": [label(2.0, 4.0)]})
