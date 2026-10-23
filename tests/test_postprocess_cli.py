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
        {"id": "one", "title": "One", "start_s": 0.0, "end_s": 2.0,
         "actions": [{"index": 0, "type": "point", "start_s": 0.0}]},
        {"id": "two", "title": "Two", "start_s": 2.0, "end_s": 6.0,
         "actions": [{"index": 0, "type": "click", "start_s": 2.0},
                     {"index": 1, "type": "click", "start_s": 4.0, "at": "end"}]},
    ],
}


def make_demo(tmp_path, recipe, edit_map=True):
    """A composed demo folder (slug = folder name): a 6 s source, its .srt, edit map, recipe."""
    demo = tmp_path / "smoke"
    demo.mkdir(parents=True)
    ffmpeg("-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=30:duration={SOURCE_S}",
           "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={SOURCE_S}",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "1",
           demo / "smoke.mp4")
    (demo / "smoke.srt").write_text(SRT)
    (demo / "transcript.md").write_text("# Smoke\n")
    if edit_map:
        (demo / "smoke.editmap.json").write_text(json.dumps(EDIT_MAP))
    (demo / "post.json").write_text(json.dumps(recipe))
    return demo


def run_post(*args):
    return subprocess.run([sys.executable, str(POSTPROCESS), *map(str, args)],
                          capture_output=True, text=True)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def printed_total(stdout):
    line = next(line for line in stdout.splitlines() if line.startswith("total\t"))
    return float(line.split("\t")[1].rstrip("s"))


@needs_ffmpeg
def test_trim_and_speed_write_the_post_video_and_retimed_subtitles(tmp_path):
    # cut 1.0-3.0 (splits cues 1 and 2), anchored to scene two's start; then 2x
    demo = make_demo(tmp_path, {"speed": 2.0, "trim": {
        "cuts": [{"from": 1.0, "to": {"scene": "two", "offset_s": 1.0}}]}})
    originals = {name: digest(demo / name)
                 for name in ("smoke.mp4", "smoke.srt", "transcript.md", "smoke.editmap.json")}

    result = run_post(demo / "post.json")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:2] == ["trim\tremoved 2.000s in 1 ranges\tkept 4.000s", "speed\t2.00x"]
    assert printed_total(result.stdout) == pytest.approx(2.0)
    info = probe(demo / "smoke-post.mp4")
    assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}
    assert float(info["format"]["duration"]) == pytest.approx(2.0, abs=1 / 30 + 0.03)
    assert (demo / "smoke-post.srt").read_text() == """1
00:00:00,000 --> 00:00:00,500
First scene.

2
00:00:00,500 --> 00:00:01,000
Second scene starts.

3
00:00:01,000 --> 00:00:02,000
The end.
"""
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
    demo = make_demo(tmp_path, {"speed": 2.0, "exports": {
        "size_cap_mb": 0.2,
        "gif": {"from": 2.0, "to": 6.0, "width": 160, "fps": 10},
        "poster": {"time": {"scene": "two", "at": "end"}}}})

    result = run_post(demo / "post.json")

    assert result.returncode == 0, result.stderr
    capped, gif, poster = (demo / f"smoke-post-{name}" for name in ("0.2mb.mp4", "gif.gif", "poster.png"))
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
