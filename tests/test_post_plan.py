from pathlib import Path

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
])
def test_invalid_recipes_fail_with_clear_messages(recipe, message):
    with pytest.raises(RecipeError, match=message):
        plan(recipe)


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
    result = plan({"click_sounds": {}, "trim": {"start": 2.0, "cuts": [{"from": 4.0, "to": 7.0}]},
                   "speed": 2.0})

    # 4.6 is cut; 8.1 -> (8.1 - 2 - 3) / 2
    assert click_step(result).params["times"] == (1.55,)
    assert click_step(result).line == "click_sounds\t1 clicks\t1 unplaceable"
    assert result.warnings == (
        "no click sound for scene 'report' action 0 (click at 4.600s): inside a cut",)


def test_clicks_without_a_known_start_are_listed_as_unplaceable():
    edit_map = {**EDIT_MAP, "scenes": [{**EDIT_MAP["scenes"][1], "actions": [
        *EDIT_MAP["scenes"][1]["actions"], {"index": 1, "type": "type"}, {"index": 3, "type": "key"}]}]}

    result = plan({"click_sounds": {}}, edit_map=edit_map)

    assert click_step(result).params["times"] == (4.6, 8.1)
    assert result.warnings == ("no click sound for scene 'report' action 1 (type): start unknown "
                               "(not the scene's first action and no at phrase)",)


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
    assert graph == ("[1:a]volume=0.300,asplit=2[c0][c1];"
                     "[c0]adelay=4600:all=1[d0];[c1]adelay=8100:all=1[d1];"
                     "[0:a][d0][d1]amix=inputs=3:duration=first:normalize=0[a]")
    # the picture is copied, not re-encoded
    assert args[args.index("-c:v") + 1] == "copy"
    assert args[-1] == "out.mp4"


def test_music_is_looped_to_the_output_length_ducked_under_the_narration_and_faded_out():
    result = plan({"music": {"file": "/demo/song.mp3", "level": 0.3, "duck": 10, "fade_out_s": 1.5},
                   "speed": 2.0}, edit_map=None)
    [step] = [step for step in result.steps if step.op == "music"]

    assert step.line == "music\tsong.mp3\tlevel 0.30\tduck 10:1\tfade out 1.50s"
    args = step.cmd("in.mp4", "out.mp4")
    # looped forever, cut to the 6 s output
    assert args[args.index("/demo/song.mp3") - 3:args.index("/demo/song.mp3")] == [
        "-stream_loop", "-1", "-i"]
    graph = args[args.index("-filter_complex") + 1]
    assert graph == (
        "[1:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=0.300,"
        "atrim=duration=6.000,afade=t=out:st=4.500:d=1.500[m];"
        "[0:a]asplit=2[voice][key];"
        "[m][key]sidechaincompress=threshold=0.02:ratio=10:attack=20:release=400[ducked];"
        "[voice][ducked]amix=inputs=2:duration=first:normalize=0[a]")
    assert args[args.index("-c:v") + 1] == "copy"


def test_music_defaults():
    [step] = plan({"music": {"file": "song.mp3"}}).steps

    assert step.params == {"file": "song.mp3", "level": 0.2, "duck": 8, "fade_out_s": 2.0,
                           "total_s": 12.0}


def test_click_sounds_run_before_the_music():
    result = plan({"music": {"file": "song.mp3"}, "click_sounds": {}})

    assert [step.op for step in result.steps] == ["click_sounds", "music"]


@pytest.mark.parametrize("recipe, message", [
    ({"click_sounds": {"volume": 1}}, "unknown key 'volume' in click_sounds"),
    ({"click_sounds": {"level": 1.5}}, r"click_sounds.level must be a number from 0 to 1"),
    ({"click_sounds": {"file": ""}}, r"click_sounds.file must be an audio file"),
    ({"music": {}}, r"music.file must be an audio file"),
    ({"music": {"file": "a.mp3", "level": -0.1}}, r"music.level must be a number from 0 to 1"),
    ({"music": {"file": "a.mp3", "duck": 0.5}}, r"music.duck must be a number from 1"),
    ({"music": {"file": "a.mp3", "fade_out_s": -1}}, r"music.fade_out_s must be"),
    ({"music": {"file": "a.mp3", "fade_out_s": 13}}, r"music.fade_out_s .* longer than the"),
    ({"music": {"file": "https://example.com/a.mp3"}}, "never downloaded"),
])
def test_invalid_click_sounds_and_music_fail_with_clear_messages(recipe, message):
    with pytest.raises(RecipeError, match=message):
        plan(recipe)


def test_click_sounds_without_an_edit_map_ask_to_re_run_compose():
    with pytest.raises(RecipeError, match="click_sounds.*re-run compose"):
        plan({"click_sounds": {}}, edit_map=None)
