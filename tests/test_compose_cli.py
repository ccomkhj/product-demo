import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

COMPOSE = Path(__file__).resolve().parent.parent / "scripts" / "compose.py"

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="needs ffmpeg/ffprobe",
)

SCENES = [
    # (id, raw window in s, narration length in s)
    ("frozen", (0.5, 6.5), 3.0),   # 2.5 s of motion + 3.5 s frozen -> decimates to ~2.5 s: hold
    ("brisk", (7.0, 9.5), 2.0),    # 2.5 s of motion vs 2.0 s audio: speed up 1.25x
    ("long", (9.5, 12.0), 1.0),    # 2.5 s vs 1.0 s: capped at 1.5x, audio padded
]
EXPECTED_TOTAL = 3.0 + 2.0 + 2.5 / 1.5


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True)


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def write_demo(demo_dir, rec_start_ms=1_700_000_000_000):
    spec = {
        "title": "Smoke",
        "viewport": {"width": 320, "height": 240},
        "scenes": [
            {"id": sid, "title": sid.title(), "url": "https://example.com", "actions": [],
             "narration": f"Scene {sid}."}
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
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=3",
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=5",
        "-filter_complex", "[0:v]tpad=stop_mode=clone:stop_duration=4[a];[a][1:v]concat=n=2:v=1:a=0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", demo_dir / "raw.mp4",
    )
    for sid, _, secs in SCENES:
        ffmpeg("-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={secs}",
               "-ac", "1", "-c:a", "pcm_s16le", audio / f"{sid}.wav")


def run_compose(*args):
    return subprocess.run(
        [sys.executable, str(COMPOSE), *map(str, args)], capture_output=True, text=True,
    )


@needs_ffmpeg
def test_compose_writes_fitted_30fps_video_with_audio(tmp_path):
    audio = write_demo(tmp_path)
    make_media(tmp_path, audio)

    result = run_compose(tmp_path / "demo.json")

    assert result.returncode == 0, result.stderr
    final = tmp_path / "final.mp4"
    info = probe(final)
    kinds = {s["codec_type"]: s for s in info["streams"]}
    assert set(kinds) == {"video", "audio"}
    assert kinds["video"]["codec_name"] == "h264"
    assert kinds["video"]["r_frame_rate"] == "30/1"
    assert (kinds["video"]["width"], kinds["video"]["height"]) == (320, 240)
    assert float(info["format"]["duration"]) == pytest.approx(EXPECTED_TOTAL, abs=0.15)
    assert float(kinds["audio"]["duration"]) == pytest.approx(EXPECTED_TOTAL, abs=0.15)
    assert "hold" in result.stdout and "speedup" in result.stdout and "pad" in result.stdout


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
    assert not (tmp_path / "final.mp4").exists()
