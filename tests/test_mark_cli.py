import json
import subprocess
import sys
import time
from pathlib import Path

MARK = Path(__file__).resolve().parent.parent / "scripts" / "mark.py"


def run_mark(*args):
    return subprocess.run(
        [sys.executable, str(MARK), *map(str, args)], capture_output=True, text=True
    )


def read_events(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_mark_appends_wall_clock_events_for_a_recording(tmp_path):
    timeline = tmp_path / "timeline.jsonl"
    before = int(time.time() * 1000)

    for args in (["rec_start"], ["start", "home"], ["beat", "home"], ["end", "home"],
                 ["start", "more"]):
        result = run_mark(timeline, *args)
        assert result.returncode == 0, result.stderr

    after = int(time.time() * 1000)
    events = read_events(timeline)
    assert [(e.get("scene"), e["event"]) for e in events] == [
        (None, "rec_start"), ("home", "start"), ("home", "beat"), ("home", "end"),
        ("more", "start"),
    ]
    stamps = [e["t_ms"] for e in events]
    assert all(isinstance(t, int) for t in stamps)
    assert stamps == sorted(stamps)
    assert before <= stamps[0] and stamps[-1] <= after


def test_rec_start_appends_and_keeps_the_earlier_recording(tmp_path):
    timeline = tmp_path / "timeline.jsonl"
    run_mark(timeline, "rec_start")
    run_mark(timeline, "start", "home")

    result = run_mark(timeline, "rec_start")

    assert result.returncode == 0, result.stderr
    assert [e["event"] for e in read_events(timeline)] == ["rec_start", "start", "rec_start"]


def test_scene_event_needs_a_scene_id(tmp_path):
    result = run_mark(tmp_path / "timeline.jsonl", "start")

    assert result.returncode != 0
    assert "scene id" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "timeline.jsonl").exists()


def test_rec_start_records_the_sync_flash_time(tmp_path):
    timeline = tmp_path / "timeline.jsonl"

    result = run_mark(timeline, "rec_start", "--sync-ms", "1760900000123")

    assert result.returncode == 0, result.stderr
    (event,) = read_events(timeline)
    assert (event["event"], event["sync_ms"]) == ("rec_start", 1760900000123)


def test_sync_ms_is_only_for_rec_start(tmp_path):
    result = run_mark(tmp_path / "timeline.jsonl", "start", "home", "--sync-ms", "5")

    assert result.returncode != 0
    assert "--sync-ms goes with rec_start only" in result.stderr
    assert not (tmp_path / "timeline.jsonl").exists()
