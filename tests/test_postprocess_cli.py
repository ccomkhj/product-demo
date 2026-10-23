import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_compose_cli import ffmpeg, needs_ffmpeg, probe

POSTPROCESS = Path(__file__).resolve().parent.parent / "scripts" / "postprocess.py"

needs_rsvg = pytest.mark.skipif(shutil.which("rsvg-convert") is None, reason="needs rsvg-convert")

SOURCE_S = 6.0
SRT = """1
00:00:00,000 --> 00:00:02,000
First scene.

2
00:00:02,000 --> 00:00:04,000
Second scene starts.

3
00:00:04,000 --> 00:00:06,000
The end.
"""
EDIT_MAP = {
    "total_s": SOURCE_S,
    "scenes": [
        {
            "id": "one",
            "title": "One",
            "start_s": 0.0,
            "end_s": 2.0,
            "actions": [{"index": 0, "type": "point", "start_s": 0.0}],
        },
        {
            "id": "two",
            "title": "Two",
            "start_s": 2.0,
            "end_s": 6.0,
            "actions": [
                {"index": 0, "type": "click", "start_s": 2.0},
                {"index": 1, "type": "click", "start_s": 4.0, "at": "end"},
            ],
        },
    ],
}


def make_demo(tmp_path, recipe, edit_map=True):
    """A composed demo folder (slug = folder name): a 6 s source, its .srt, edit map, recipe."""
    demo = tmp_path / "smoke"
    demo.mkdir(parents=True)
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size=320x240:rate=30:duration={SOURCE_S}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:sample_rate=48000:duration={SOURCE_S}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-ac",
        "1",
        demo / "smoke.mp4",
    )
    (demo / "smoke.srt").write_text(SRT)
    (demo / "transcript.md").write_text("# Smoke\n")
    if edit_map:
        (demo / "smoke.editmap.json").write_text(json.dumps(EDIT_MAP))
    (demo / "post.json").write_text(json.dumps(recipe))
    return demo


def run_post(*args, env=None):
    return subprocess.run(
        [sys.executable, str(POSTPROCESS), *map(str, args)], capture_output=True, text=True, env=env
    )


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def printed_total(stdout):
    line = next(line for line in stdout.splitlines() if line.startswith("total\t"))
    return float(line.split("\t")[1].rstrip("s"))


@needs_ffmpeg
def test_trim_and_speed_write_the_post_video_and_retimed_subtitles(tmp_path):
    # cut 1.0-3.0 (splits cues 1 and 2), anchored to scene two's start; then 2x
    demo = make_demo(
        tmp_path,
        {"speed": 2.0, "trim": {"cuts": [{"from": 1.0, "to": {"scene": "two", "offset_s": 1.0}}]}},
    )
    originals = {
        name: digest(demo / name)
        for name in ("smoke.mp4", "smoke.srt", "transcript.md", "smoke.editmap.json")
    }

    result = run_post(demo / "post.json")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:2] == ["trim\tremoved 2.000s in 1 ranges\tkept 4.000s", "speed\t2.00x"]
    assert printed_total(result.stdout) == pytest.approx(2.0)
    info = probe(demo / "smoke-post.mp4")
    assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}
    assert float(info["format"]["duration"]) == pytest.approx(2.0, abs=1 / 30 + 0.03)
    assert (
        (demo / "smoke-post.srt").read_text()
        == """1
00:00:00,000 --> 00:00:00,500
First scene.

2
00:00:00,500 --> 00:00:01,000
Second scene starts.

3
00:00:01,000 --> 00:00:02,000
The end.
"""
    )
    assert "splits cue 1" in result.stderr and "splits cue 2" in result.stderr
    assert {name: digest(demo / name) for name in originals} == originals


@needs_ffmpeg
def test_a_re_run_with_an_unchanged_recipe_gives_the_same_video(tmp_path):
    demo = make_demo(tmp_path, {"trim": {"start": 0.5}, "speed": 1.5})

    first = run_post(demo / "post.json")
    before = digest(demo / "smoke-post.mp4")
    second = run_post(demo / "post.json")

    assert first.returncode == 0 and second.returncode == 0, first.stderr + second.stderr
    assert digest(demo / "smoke-post.mp4") == before


@needs_ffmpeg
def test_an_invalid_recipe_fails_before_any_output(tmp_path):
    demo = make_demo(tmp_path, {"speed": 3})

    result = run_post(demo / "post.json")

    assert result.returncode == 1
    assert "speed must be a number from 0.5 to 2" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (demo / "smoke-post.mp4").exists()


@needs_ffmpeg
def test_without_an_edit_map_it_asks_to_re_run_compose(tmp_path):
    plain = make_demo(tmp_path / "a", {"trim": {"start": 1.0}}, edit_map=False)
    anchored = make_demo(tmp_path / "b", {"trim": {"start": {"scene": "two"}}}, edit_map=False)

    ok, failed = run_post(plain / "post.json"), run_post(anchored / "post.json")

    assert ok.returncode == 0, ok.stderr
    assert "re-run compose" in ok.stderr
    assert failed.returncode == 1
    assert "re-run compose" in failed.stderr


@needs_ffmpeg
@needs_rsvg
def test_contact_sheet_export_is_written_with_its_frames(tmp_path):
    demo = make_demo(tmp_path, {"exports": {"contact_sheet": {"columns": 3, "interval_s": 1}}})

    result = run_post(demo / "post.json")

    assert result.returncode == 0, result.stderr
    sheet = demo / "smoke-post-contact.png"
    assert f"contact_sheet\t6 frames\t{sheet}" in result.stdout
    [stream] = probe(sheet)["streams"]
    # 3 columns x 2 rows of 320 px frames
    assert stream["width"] > 3 * 320 and stream["height"] > 2 * 240
    assert printed_total(result.stdout) == pytest.approx(SOURCE_S)


@needs_ffmpeg
def test_size_cap_gif_and_poster_exports_are_written_within_their_limits(tmp_path):
    demo = make_demo(
        tmp_path,
        {
            "speed": 2.0,
            "exports": {
                "size_cap_mb": 0.2,
                "gif": {"from": 2.0, "to": 6.0, "width": 160, "fps": 10},
                "poster": {"time": {"scene": "two", "at": "end"}},
            },
        },
    )

    result = run_post(demo / "post.json")

    assert result.returncode == 0, result.stderr
    capped, gif, poster = (
        demo / f"smoke-post-{name}" for name in ("0.2mb.mp4", "gif.gif", "poster.png")
    )
    assert f"size_cap\t0.2 MB\t{capped}" in result.stdout
    assert f"gif\t1.000s-3.000s\t160px 10fps\t{gif}" in result.stdout
    assert f"poster\t2.000s\t{poster}" in result.stdout
    assert 0 < capped.stat().st_size <= 200_000
    info = probe(capped)
    assert float(info["format"]["duration"]) == pytest.approx(3.0, abs=0.1)
    assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}
    [stream] = probe(gif)["streams"]
    assert (stream["codec_name"], stream["width"], stream["height"]) == ("gif", 160, 120)
    assert float(probe(gif)["format"]["duration"]) == pytest.approx(2.0, abs=0.15)
    [stream] = probe(poster)["streams"]
    assert (stream["width"], stream["height"]) == (320, 240)


@needs_ffmpeg
def test_an_unreachable_size_cap_fails_with_the_smallest_reachable_size(tmp_path):
    demo = make_demo(tmp_path, {"exports": {"size_cap_mb": 0.01}})

    result = run_post(demo / "post.json")

    assert result.returncode == 1
    assert "smallest reachable size is 0.08 MB" in result.stderr
    assert "Traceback" not in result.stderr
    assert not list(demo.glob("smoke-post-*mb.mp4"))


@needs_ffmpeg
@needs_rsvg
def test_title_and_closing_cards_lengthen_the_video_and_shift_the_subtitles(tmp_path):
    demo = make_demo(
        tmp_path,
        {
            "speed": 2.0,
            "cards": {
                "title": {"seconds": 2, "subtitle": "A smoke test", "logo": "logo.png"},
                "closing": {"background": "#ffffff", "color": "#000000"},
            },
        },
    )
    (demo / "demo.json").write_text(
        json.dumps(
            {
                "title": "Smoke",
                "scenes": [
                    {"id": "one", "url": "https://example.com/one"},
                    {"id": "two", "url": "https://example.com/two"},
                ],
            }
        )
    )
    ffmpeg("-f", "lavfi", "-i", "color=c=red:s=64x32", "-frames:v", "1", demo / "logo.png")

    result = run_post(demo / "post.json")

    assert result.returncode == 0, result.stderr
    assert "cards\ttitle 2.000s\tclosing 3.000s" in result.stdout.splitlines()
    # 2 s title + 6 s at 2x + 3 s closing
    assert printed_total(result.stdout) == pytest.approx(8.0)
    info = probe(demo / "smoke-post.mp4")
    assert float(info["format"]["duration"]) == pytest.approx(8.0, abs=1 / 30 + 0.05)
    assert (
        (demo / "smoke-post.srt")
        .read_text()
        .startswith("1\n00:00:02,000 --> 00:00:03,000\nFirst scene.\n")
    )


@needs_ffmpeg
def test_a_missing_card_logo_fails_before_any_output(tmp_path):
    demo = make_demo(tmp_path, {"cards": {"title": {"title": "Smoke", "logo": "nope.png"}}})

    result = run_post(demo / "post.json")

    assert result.returncode == 1
    assert "logo not found" in result.stderr
    assert not (demo / "smoke-post.mp4").exists()


# --- click sounds and music (#20) ------------------------------------------------------


def volume_db(path):
    """(mean, max) volume of a file's audio, in dB."""
    err = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stderr
    return tuple(
        float(err.split(f"{key}: ")[1].split(" dB")[0]) for key in ("mean_volume", "max_volume")
    )


def add_song(demo):
    """A 1 s stereo song.wav (shorter than the video, so it loops) in the demo folder."""
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=880:sample_rate=44100:duration=1",
        "-ac",
        "2",
        demo / "song.wav",
    )


@needs_ffmpeg
def test_click_sounds_and_looped_music_are_mixed_into_the_audio(tmp_path):
    # the click at 2.6 s is cut; the one at 4.6 s plays; the 1 s song loops under all 5 s
    demo = make_demo(
        tmp_path,
        {
            "trim": {"cuts": [{"from": 2.5, "to": 3.5}]},
            "click_sounds": {},
            "music": {"file": "song.wav", "level": 0.8},
        },
    )
    add_song(demo)

    result = run_post(demo / "post.json")

    assert result.returncode == 0, result.stderr
    assert "click_sounds\t1 clicks\t1 unplaceable" in result.stdout
    assert "music\tsong.wav\tlevel 0.80\tduck 8:1\tfade out 2.00s" in result.stdout
    assert (
        "no click sound for scene 'two' action 0 (click at 2.600s): inside a cut" in result.stderr
    )
    info = probe(demo / "smoke-post.mp4")
    [audio] = [s for s in info["streams"] if s["codec_type"] == "audio"]
    assert float(audio["duration"]) == pytest.approx(5.0, abs=0.05)
    # the click peaks well above the narration, which is not made quieter
    source_mean, source_max = volume_db(demo / "smoke.mp4")
    post_mean, post_max = volume_db(demo / "smoke-post.mp4")
    assert post_max > source_max + 3 and post_mean > source_mean - 0.5


@needs_ffmpeg
def test_music_is_ducked_under_the_narration(tmp_path):
    def mean_db(duck):
        demo = make_demo(
            tmp_path / str(duck), {"music": {"file": "song.wav", "level": 0.8, "duck": duck}}
        )
        add_song(demo)
        result = run_post(demo / "post.json")
        assert result.returncode == 0, result.stderr
        return volume_db(demo / "smoke-post.mp4")[0]

    # the source's narration (a tone) never stops, so ducked music stays quiet throughout
    assert mean_db(1) > mean_db(20) + 1


@needs_ffmpeg
def test_a_missing_music_file_fails_before_any_output(tmp_path):
    demo = make_demo(tmp_path, {"music": {"file": "missing.mp3"}})

    result = run_post(demo / "post.json")

    assert result.returncode == 1
    assert "music file not found" in result.stderr and "missing.mp3" in result.stderr
    assert not (demo / "smoke-post.mp4").exists()


@needs_ffmpeg
@needs_rsvg
def test_music_plays_under_the_title_and_closing_cards(tmp_path):
    demo = make_demo(
        tmp_path,
        {
            "cards": {"title": {"title": "Smoke", "seconds": 2}, "closing": {"text": "Bye"}},
            "music": {"file": "song.wav", "level": 0.8, "fade_out_s": 0.5},
        },
    )
    add_song(demo)

    result = run_post(demo / "post.json")

    assert result.returncode == 0, result.stderr
    # 2 s title + 6 s demo + 3 s closing, the music under all of it
    assert printed_total(result.stdout) == pytest.approx(11.0)
    info = probe(demo / "smoke-post.mp4")
    [audio] = [s for s in info["streams"] if s["codec_type"] == "audio"]
    assert float(audio["duration"]) == pytest.approx(11.0, abs=0.05)
    assert (audio["sample_rate"], audio["channels"]) == ("48000", 2)
    # the cards' audio is silent, so only the music can be heard in them
    title = demo / "title.wav"
    ffmpeg("-i", demo / "smoke-post.mp4", "-t", "1.5", "-vn", title)
    assert volume_db(title)[0] > -40


def pixel(video, at_s, x, y):
    """(r, g, b) of one pixel of ``video``'s frame at ``at_s``."""
    return tuple(
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                str(at_s),
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-vf",
                f"crop=2:2:{x}:{y}",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-",
            ],
            capture_output=True,
            check=True,
        ).stdout[:3]
    )


@needs_ffmpeg
def test_progress_bar_fills_along_its_edge(tmp_path):
    demo = make_demo(tmp_path, {"progress_bar": {"color": "#ff0000", "thickness": 10}})

    result = run_post(demo / "post.json")

    assert result.returncode == 0, result.stderr
    assert "progress_bar\tbottom 10px\t0.000s-6.000s" in result.stdout
    r, g, b = pixel(demo / "smoke-post.mp4", 3.0, 40, 235)
    assert r > 200 and g < 60 and b < 60
    assert pixel(demo / "smoke-post.mp4", 3.0, 300, 235) != pytest.approx((r, g, b), abs=60)
    assert float(probe(demo / "smoke-post.mp4")["format"]["duration"]) == pytest.approx(
        SOURCE_S, abs=0.05
    )


@needs_ffmpeg
def test_image_watermark_sits_in_its_corner_without_rsvg(tmp_path):
    demo = make_demo(tmp_path, {"watermark": {"image": "logo.png", "size": 0.2, "opacity": 1}})
    ffmpeg("-f", "lavfi", "-i", "color=c=white:s=40x40", "-frames:v", "1", demo / "logo.png")

    result = run_post(demo / "post.json", env={"PATH": _path_without_rsvg(tmp_path)})

    assert result.returncode == 0, result.stderr
    assert "watermark\timage logo.png\ttop-right" in result.stdout
    assert min(pixel(demo / "smoke-post.mp4", 1.0, 290, 30)) > 220


@needs_ffmpeg
@needs_rsvg
def test_text_callout_shows_only_inside_its_window(tmp_path):
    demo = make_demo(
        tmp_path,
        {
            "watermark": {"text": "acme"},
            "callouts": [
                {
                    "type": "text",
                    "text": "Here",
                    "position": [20, 20],
                    "start": {"scene": "two"},
                    "end": 4.0,
                },
                {"type": "arrow", "from": [200, 200], "to": [150, 120], "start": 1.0, "end": 5.0},
            ],
        },
    )

    result = run_post(demo / "post.json")

    assert result.returncode == 0, result.stderr
    assert "callouts\t2\ttext 2.000s-4.000s, arrow 1.000s-5.000s" in result.stdout
    streams = probe(demo / "smoke-post.mp4")["streams"]
    assert [float(s["duration"]) for s in streams] == pytest.approx([SOURCE_S] * 2, abs=0.05)
    assert max(pixel(demo / "smoke-post.mp4", 3.0, 24, 24)) < 60
    assert pixel(demo / "smoke-post.mp4", 1.0, 24, 24) == pytest.approx(
        pixel(demo / "smoke.mp4", 1.0, 24, 24), abs=40
    )


@needs_ffmpeg
def test_text_overlays_without_rsvg_fail_with_the_install_hint(tmp_path):
    demo = make_demo(tmp_path, {"watermark": {"text": "acme"}})

    result = run_post(demo / "post.json", env={"PATH": _path_without_rsvg(tmp_path)})

    assert result.returncode == 1
    assert "rsvg-convert" in result.stderr and "brew install librsvg" in result.stderr
    assert not (demo / "smoke-post.mp4").exists()


def _path_without_rsvg(tmp_path):
    """A PATH holding only ffmpeg and ffprobe."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("ffmpeg", "ffprobe"):
        (bin_dir / tool).symlink_to(shutil.which(tool))
    return str(bin_dir)
