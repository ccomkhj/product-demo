import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from compose import action_line, cue_svg, subtitles_burn_cmd
from edit_plan import ActionTime

COMPOSE = Path(__file__).resolve().parent.parent / "scripts" / "compose.py"

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="needs ffmpeg/ffprobe",
)

SCENES = [
    # (id, raw window in s, narration length in s)
    ("frozen", (0.5, 6.5), 3.0),  # 2.5 s of motion + 3.5 s frozen -> decimates to ~2.5 s: hold
    ("brisk", (7.0, 9.5), 2.0),  # 2.5 s of motion vs 2.0 s audio: speed up 1.25x
    ("long", (9.5, 12.0), 1.0),  # 2.5 s vs 1.0 s: capped at 1.5x, audio padded
]
EXPECTED_TOTAL = 3.0 + 2.0 + 2.5 / 1.5
NARRATION = {
    "frozen": "Nothing moves here. Dr. Who waits.",  # 19 + 14 chars share 3.0 s
    "brisk": "Scene brisk.",
    "long": "Scene long.",
}
# Cues follow the narration audio; offsets are cumulative scene lengths (3.0, 5.0).
EXPECTED_SRT = """1
00:00:00,000 --> 00:00:01,727
Nothing moves here.

2
00:00:01,727 --> 00:00:03,000
Dr. Who waits.

3
00:00:03,000 --> 00:00:05,000
Scene brisk.

4
00:00:05,000 --> 00:00:06,000
Scene long.
"""


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True)


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)


def write_demo(demo_dir, rec_start_ms=1_700_000_000_000):
    spec = {
        "title": "Smoke",
        "viewport": {"width": 320, "height": 240},
        "scenes": [
            {
                "id": sid,
                "title": sid.title(),
                "url": "https://example.com",
                "actions": [],
                "narration": NARRATION[sid],
            }
            for sid, _, _ in SCENES
        ],
    }
    (demo_dir / "demo.json").write_text(json.dumps(spec))
    events = [{"event": "rec_start", "t_ms": rec_start_ms}]
    for sid, (start, end), _ in SCENES:
        events.append({"scene": sid, "event": "start", "t_ms": rec_start_ms + int(start * 1000)})
        events.append({"scene": sid, "event": "end", "t_ms": rec_start_ms + int(end * 1000)})
    (demo_dir / "timeline.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    audio = demo_dir / "audio"
    audio.mkdir()
    (audio / "durations.json").write_text(json.dumps({sid: secs for sid, _, secs in SCENES}))
    return audio


def make_media(demo_dir, audio):
    # 12 s raw: 3 s of motion, a 4 s frozen stretch, then 5 s of motion. testsrc2
    # changes every frame (plain testsrc only every other one), so mpdecimate keeps all motion.
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x240:rate=30:duration=3",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x240:rate=30:duration=5",
        "-filter_complex",
        "[0:v]tpad=stop_mode=clone:stop_duration=4[a];[a][1:v]concat=n=2:v=1:a=0",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        demo_dir / "raw.mp4",
    )
    for sid, _, secs in SCENES:
        ffmpeg(
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={secs}",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            audio / f"{sid}.wav",
        )


def default_out(demo_dir):
    """compose.py names its video after the demo folder (the slug)."""
    return demo_dir / f"{demo_dir.name}.mp4"


def run_compose(*args):
    return subprocess.run(
        [sys.executable, str(COMPOSE), *map(str, args)],
        capture_output=True,
        text=True,
    )


@needs_ffmpeg
@pytest.mark.parametrize("with_readiness", [False, True])
def test_compose_writes_fitted_30fps_video_with_audio(tmp_path, with_readiness):
    audio = write_demo(tmp_path)
    if with_readiness:
        path = tmp_path / "demo.json"
        spec = json.loads(path.read_text())
        spec.update(
            purpose="feature-announcement", outcome="Explain the workflow", target_duration_s=30
        )
        spec["scenes"][0]["prerequisites"] = [{"type": "visible_text", "text": "Reports"}]
        spec["scenes"][0]["expected_outcomes"] = [
            {"type": "visible_target", "target": "the result"}
        ]
        path.write_text(json.dumps(spec))
    make_media(tmp_path, audio)

    result = run_compose(tmp_path / "demo.json")

    assert result.returncode == 0, result.stderr
    # Outputs are named after the demo folder (its slug); transcript.md keeps its name.
    final = tmp_path / f"{tmp_path.name}.mp4"
    assert not (tmp_path / "final.mp4").exists()
    info = probe(final)
    kinds = {s["codec_type"]: s for s in info["streams"]}
    assert set(kinds) == {"video", "audio"}
    assert kinds["video"]["codec_name"] == "h264"
    assert kinds["video"]["r_frame_rate"] == "30/1"
    assert (kinds["video"]["width"], kinds["video"]["height"]) == (320, 240)
    assert float(info["format"]["duration"]) == pytest.approx(EXPECTED_TOTAL, abs=0.15)
    assert float(kinds["audio"]["duration"]) == pytest.approx(EXPECTED_TOTAL, abs=0.15)
    assert "hold" in result.stdout and "speedup" in result.stdout and "pad" in result.stdout
    assert "burned in" in result.stdout
    assert (tmp_path / f"{tmp_path.name}.srt").read_text() == EXPECTED_SRT
    transcript = (tmp_path / "transcript.md").read_text()
    assert transcript.startswith("# Smoke\n")
    headings = [line for line in transcript.splitlines() if line.startswith("## ")]
    assert headings == ["## Frozen", "## Brisk", "## Long"]
    assert all(text in transcript for text in NARRATION.values())
    # the edit map lays the scenes end to end, ending on compose's printed total
    scenes = edit_map(tmp_path)["scenes"]
    assert [(s["id"], s["title"], s["actions"]) for s in scenes] == [
        ("frozen", "Frozen", []),
        ("brisk", "Brisk", []),
        ("long", "Long", []),
    ]
    assert [(s["start_s"], s["end_s"]) for s in scenes[:2]] == [(0.0, 3.0), (3.0, 5.0)]
    assert scenes[2]["start_s"] == 5.0
    total = next(line for line in result.stdout.splitlines() if line.startswith("total\t"))
    assert total.startswith(f"total\t{scenes[2]['end_s']:.3f}s\t")
    assert edit_map(tmp_path)["total_s"] == scenes[2]["end_s"]


def bottom_and_top_difference(a, b, width=320, height=240, at=0.5):
    """Mean absolute luma difference between two videos' frames at ``at`` s: (bottom, top)."""
    frames = []
    for path in (a, b):
        frames.append(
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-ss",
                    str(at),
                    "-i",
                    str(path),
                    "-frames:v",
                    "1",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "gray",
                    "-",
                ],
                capture_output=True,
                check=True,
            ).stdout
        )
    rows = len(frames[0]) // width
    assert rows == height

    def mean_diff(first_row, last_row):
        span = slice(first_row * width, last_row * width)
        return sum(abs(x - y) for x, y in zip(frames[0][span], frames[1][span])) / len(
            frames[0][span]
        )

    return mean_diff(height * 3 // 4, height), mean_diff(0, height // 4)


@needs_ffmpeg
def test_burn_subs_draws_the_subtitles_into_a_playable_video(tmp_path):
    audio = write_demo(tmp_path)
    make_media(tmp_path, audio)

    plain = run_compose(tmp_path / "demo.json", "--no-burn-subs")
    burned = run_compose(tmp_path / "demo.json", "--out", tmp_path / "burned.mp4")

    assert plain.returncode == 0, plain.stderr
    assert burned.returncode == 0, burned.stderr
    info = probe(tmp_path / "burned.mp4")
    assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}
    assert float(info["format"]["duration"]) == pytest.approx(EXPECTED_TOTAL, abs=0.15)
    assert (tmp_path / "burned.srt").read_text() == EXPECTED_SRT
    # the burned-in subtitle changes the bottom of the frame, not the top
    bottom, top = bottom_and_top_difference(default_out(tmp_path), tmp_path / "burned.mp4")
    assert bottom > 5 * max(top, 1.0)


def gray_frame(path, at):
    return subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{at:.3f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "scale=80:60",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout


def frame_difference(path, a, b):
    first, second = gray_frame(path, a), gray_frame(path, b)
    return sum(abs(x - y) for x, y in zip(first, second)) / len(first)


def write_beat_demo(tmp_path):
    audio = write_demo(tmp_path)
    make_media(tmp_path, audio)
    # One scene over the whole raw video: 2.5 s of motion, frozen, then motion from
    # 7 s. The beat at 7 s starts the click; "Second" is reached at 16/32 of 8 s = 4 s.
    spec = json.loads((tmp_path / "demo.json").read_text())
    spec["scenes"] = [
        {
            "id": "frozen",
            "url": "https://example.com",
            "narration": "First part, then. Second part now.",
            "actions": [
                {"type": "point", "target": "a"},
                {"type": "click", "target": "b", "at": "Second"},
            ],
        }
    ]
    (tmp_path / "demo.json").write_text(json.dumps(spec))
    (tmp_path / "timeline.jsonl").write_text(
        "".join(
            json.dumps(e) + "\n"
            for e in [
                {"event": "rec_start", "t_ms": 0},
                {"scene": "frozen", "event": "start", "t_ms": 500},
                {"scene": "frozen", "event": "beat", "t_ms": 7_000},
                {"scene": "frozen", "event": "end", "t_ms": 9_000},
            ]
        )
    )
    (audio / "durations.json").write_text(json.dumps({"frozen": 8.0}))
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=8",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        audio / "frozen.wav",
    )


@needs_ffmpeg
def test_a_beat_holds_the_rest_of_the_scene_until_the_narration_reaches_its_phrase(tmp_path):
    write_beat_demo(tmp_path)

    result = run_compose(tmp_path / "demo.json", "--no-burn-subs")

    assert result.returncode == 0, result.stderr
    final = default_out(tmp_path)
    assert float(probe(final)["format"]["duration"]) == pytest.approx(8.0, abs=0.1)
    # held from the end of the first motion (~2.5 s) until the phrase at 4 s, then moving
    assert frame_difference(final, 2.9, 3.8) < 1.0
    assert frame_difference(final, 4.1, 4.6) > 5.0
    # the scene line, then the click: "Second" is 17/33 of 8 s = 4.121 s, on frame 124
    lines = result.stdout.splitlines()
    assert lines[0].startswith("frozen\thold")
    assert lines[1] == 'at\tfrozen\t1\t4.133s\t"Second"'


def edit_map(demo_dir):
    return json.loads((demo_dir / f"{demo_dir.name}.editmap.json").read_text())


@needs_ffmpeg
def test_the_edit_map_lists_each_scene_and_the_known_action_times(tmp_path):
    write_beat_demo(tmp_path)

    result = run_compose(tmp_path / "demo.json", "--no-burn-subs")

    assert result.returncode == 0, result.stderr
    # the same times compose prints: 'at frozen 1 4.133s "Second"' and 'total 8.000s'
    assert edit_map(tmp_path) == {
        "total_s": 8.0,
        "scenes": [
            {
                "id": "frozen",
                "title": "frozen",
                "start_s": 0.0,
                "end_s": 8.0,
                "actions": [
                    {"index": 0, "type": "point", "start_s": 0.0},
                    {"index": 1, "type": "click", "start_s": 4.133, "at": "Second"},
                ],
            }
        ],
    }
    assert 'at\tfrozen\t1\t4.133s\t"Second"' in result.stdout
    assert "total\t8.000s\t" in result.stdout


def test_first_flash_is_the_first_frame_that_is_magenta_throughout():
    from compose import first_flash_s

    def frame(n, pts, umin, vmin):
        return (
            f"frame:{n}    pts:{n}       pts_time:{pts}\n"
            f"lavfi.signalstats.UMIN={umin}\nlavfi.signalstats.VMIN={vmin}\n"
        )

    stats = (
        frame(0, 0, 128, 128)
        + frame(1, 0.5, 202, 100)
        + frame(2, 1.25, 201, 221)
        + frame(3, 1.5, 202, 222)
    )

    assert first_flash_s(stats) == 1.25
    assert first_flash_s(frame(0, 0, 128, 128)) is None


@needs_ffmpeg
def test_compose_lines_the_timeline_up_on_the_sync_flash(tmp_path):
    audio = write_demo(tmp_path, rec_start_ms=1_700_000_000_000)
    make_media(tmp_path, audio)
    # Splice a 0.4 s magenta flash in front of the raw video: the scenes now sit 0.4 s
    # later in it. The flash was painted at wall time 1_699_999_999_000, so raw time 0
    # is that, and rec_start's own (later) t_ms no longer matters.
    raw, spliced = tmp_path / "raw.mp4", tmp_path / "spliced.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=0xff00ff:s=320x240:r=30:d=0.4",
        "-i",
        raw,
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        spliced,
    )
    spliced.replace(raw)
    timeline = tmp_path / "timeline.jsonl"
    events = [json.loads(line) for line in timeline.read_text().splitlines()]
    events[0]["sync_ms"] = 1_699_999_999_000
    for event in events[1:]:
        event["t_ms"] += -1_000 + 400  # 1 s earlier origin, 0.4 s later in the raw video
    events[0]["t_ms"] += 5_000
    timeline.write_text("".join(json.dumps(e) + "\n" for e in events))

    result = run_compose(tmp_path / "demo.json", "--no-burn-subs")

    assert result.returncode == 0, result.stderr
    assert "sync\tflash at 0.000s" in result.stdout
    assert float(probe(default_out(tmp_path))["format"]["duration"]) == pytest.approx(
        EXPECTED_TOTAL, abs=0.15
    )
    assert "hold" in result.stdout and "speedup" in result.stdout and "pad" in result.stdout


@needs_ffmpeg
def test_compose_fails_when_the_sync_flash_is_missing(tmp_path):
    audio = write_demo(tmp_path)
    make_media(tmp_path, audio)
    timeline = tmp_path / "timeline.jsonl"
    events = [json.loads(line) for line in timeline.read_text().splitlines()]
    events[0]["sync_ms"] = events[0]["t_ms"]
    timeline.write_text("".join(json.dumps(e) + "\n" for e in events))

    result = run_compose(tmp_path / "demo.json", "--no-burn-subs")

    assert result.returncode == 1
    assert "no sync flash found" in result.stderr
    assert "Traceback" not in result.stderr


def test_compose_reports_timeline_errors_without_a_traceback(tmp_path):
    write_demo(tmp_path)
    timeline = tmp_path / "timeline.jsonl"
    lines = timeline.read_text().splitlines()
    timeline.write_text("\n".join(lines[1:]) + "\n")  # drop rec_start

    result = run_compose(tmp_path / "demo.json")

    assert result.returncode == 1
    assert "compose: error:" in result.stderr
    assert "rec_start" in result.stderr
    assert "Traceback" not in result.stderr
    assert not default_out(tmp_path).exists()


@pytest.mark.parametrize(
    "content, expected",
    [
        (None, "run tts.py first"),
        ("{not json", "durations"),
        ("[3.0, 2.0]", "scene id -> seconds"),
    ],
)
def test_compose_reports_unreadable_durations_without_a_traceback(tmp_path, content, expected):
    audio = write_demo(tmp_path)
    durations = audio / "durations.json"
    if content is None:
        durations.unlink()
    else:
        durations.write_text(content)

    result = run_compose(tmp_path / "demo.json")

    assert result.returncode == 1
    assert "compose: error:" in result.stderr
    assert expected in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("seconds", [0, -2.0, "two"])
def test_compose_reports_bad_durations_without_a_traceback(tmp_path, seconds):
    audio = write_demo(tmp_path)
    (audio / "durations.json").write_text(
        json.dumps({"frozen": 3.0, "brisk": seconds, "long": 1.0})
    )

    result = run_compose(tmp_path / "demo.json")

    assert result.returncode == 1
    assert "compose: error:" in result.stderr
    assert "'brisk'" in result.stderr
    assert "Traceback" not in result.stderr


PHOTO = Path(__file__).resolve().parent.parent / "assets" / "avatars" / "huijo.png"


def with_avatar(demo_dir, **fields):
    path = demo_dir / "demo.json"
    spec = json.loads(path.read_text())
    spec["avatar"] = {"image": str(PHOTO), **fields}
    path.write_text(json.dumps(spec))


def corner_differences(a, b, width=320, height=240, at=1.0):
    """Mean absolute luma difference between two videos' frames at ``at`` s, per quarter-corner."""
    frames = [
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                str(at),
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "gray",
                "-",
            ],
            capture_output=True,
            check=True,
        ).stdout
        for path in (a, b)
    ]

    def mean_diff(x0, y0):
        pixels = [
            (y * width + x) for y in range(y0, y0 + height // 4) for x in range(x0, x0 + width // 4)
        ]
        return sum(abs(frames[0][p] - frames[1][p]) for p in pixels) / len(pixels)

    right, bottom = width * 3 // 4, height * 3 // 4
    return {
        "top-left": mean_diff(0, 0),
        "top-right": mean_diff(right, 0),
        "bottom-left": mean_diff(0, bottom),
        "bottom-right": mean_diff(right, bottom),
    }


@needs_ffmpeg
def test_avatar_draws_the_bubble_in_its_corner_for_the_whole_video(tmp_path):
    audio = write_demo(tmp_path)
    make_media(tmp_path, audio)
    plain = run_compose(tmp_path / "demo.json", "--no-burn-subs")
    with_avatar(tmp_path, corner="top-right", size=0.3)

    result = run_compose(tmp_path / "demo.json", "--no-burn-subs", "--out", tmp_path / "bubble.mp4")

    assert plain.returncode == 0, plain.stderr
    assert result.returncode == 0, result.stderr
    assert f"avatar\ttop-right\t{PHOTO}" in result.stdout
    info = probe(tmp_path / "bubble.mp4")
    assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}
    assert float(info["format"]["duration"]) == pytest.approx(EXPECTED_TOTAL, abs=0.15)
    for at in (0.5, EXPECTED_TOTAL - 0.3):
        diff = corner_differences(default_out(tmp_path), tmp_path / "bubble.mp4", at=at)
        assert diff["top-right"] > 20
        # elsewhere only the extra encode's noise
        assert max(diff["top-left"], diff["bottom-left"], diff["bottom-right"]) < 3


@needs_ffmpeg
def test_avatar_with_subtitles_composes(tmp_path):
    audio = write_demo(tmp_path)
    make_media(tmp_path, audio)
    with_avatar(tmp_path)

    result = run_compose(tmp_path / "demo.json")

    assert result.returncode == 0, result.stderr
    assert "burned in" in result.stdout and "avatar\tbottom-left" in result.stdout


def test_compose_reports_a_missing_avatar_image_without_a_traceback(tmp_path):
    write_demo(tmp_path)
    with_avatar(tmp_path)
    spec = json.loads((tmp_path / "demo.json").read_text())
    spec["avatar"]["image"] = "nobody.png"
    (tmp_path / "demo.json").write_text(json.dumps(spec))

    result = run_compose(tmp_path / "demo.json")

    assert result.returncode == 1
    assert f"compose: error: avatar image not found: {tmp_path / 'nobody.png'}" in result.stderr
    assert "Traceback" not in result.stderr


def test_subtitles_wrap_clear_of_a_bottom_bubble():
    text = "A sentence long enough to need wrapping when the bubble takes room at the side."
    wide, wide_h = cue_svg(text, 1920, 1080)
    narrow, narrow_h = cue_svg(text, 1920, 1080, reserve=600)

    assert wide.count("<tspan") < narrow.count("<tspan") and wide_h < narrow_h
    assert "MarginL=10,MarginR=10'" in " ".join(subtitles_burn_cmd("v.mp4", "s.srt", "o.mp4"))
    assert "MarginL=80,MarginR=80'" in " ".join(subtitles_burn_cmd("v.mp4", "s.srt", "o.mp4", 80))


def test_avatar_profile_uses_the_saved_photo(tmp_path, monkeypatch):
    import compose

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    spec = {"avatar": {"profile": "own_photo", "corner": "top-left", "size": 0.3}}
    with pytest.raises(compose.ComposeError, match="own_photo needs setup"):
        compose.spec_avatar(spec, tmp_path)
    (tmp_path / "product-demo").mkdir()
    (tmp_path / "product-demo" / "me.png").write_bytes(PHOTO.read_bytes())
    (tmp_path / "product-demo" / "config.json").write_text(
        json.dumps({"own_photo": {"image": "me.png"}})
    )
    avatar = compose.spec_avatar(spec, tmp_path)
    assert (avatar.image, avatar.corner, avatar.size) == (
        (tmp_path / "product-demo" / "me.png").resolve(),
        "top-left",
        0.3,
    )


def test_an_action_line_names_its_time_and_phrase_and_how_late_it_is():
    on_time = ActionTime("home", 1, 4.1333, "Then", 0.0)
    late = ActionTime("home", 2, 12.5, "we click", 1.25)

    assert action_line(on_time) == 'at\thome\t1\t4.133s\t"Then"'
    assert action_line(late) == 'at\thome\t2\t12.500s\t"we click"\tlate 1.250s'
