"""The README's sample (docs/sample/) is the reference end-to-end demo: its spec must stay valid.

Re-record it with `/product-demo:run` from the sample's spec to check the whole skill by hand.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from compose import spec_avatar
from demo_spec import load_spec

SAMPLE = Path(__file__).resolve().parent.parent / "docs" / "sample"


def test_sample_spec_validates_with_three_scenes():
    spec = load_spec(SAMPLE / "demo.json")

    assert [scene["id"] for scene in spec["scenes"]] == ["home", "overview", "president"]
    assert all(scene["url"].startswith("https://www.korea.net") for scene in spec["scenes"])


def test_sample_avatar_resolves_to_the_photo_in_assets():
    avatar = spec_avatar(load_spec(SAMPLE / "demo.json"), SAMPLE)

    assert avatar.image.resolve() == (SAMPLE.parent.parent / "assets" / "avatars" / "huijo.png")
    assert avatar.corner == "bottom-left"


@pytest.mark.skipif(not shutil.which("ffprobe"), reason="needs ffprobe")
def test_sample_video_runs_as_long_as_its_subtitles():
    info = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json",
         str(SAMPLE / "korea-tour.mp4")],
        capture_output=True, text=True, check=True,
    ).stdout)
    last_cue = (SAMPLE / "korea-tour.srt").read_text().strip().split("\n\n")[-1]
    end = last_cue.splitlines()[1].split(" --> ")[1]
    hours, minutes, rest = end.split(":")
    end_s = int(hours) * 3600 + int(minutes) * 60 + float(rest.replace(",", "."))

    assert {stream["codec_type"] for stream in info["streams"]} == {"video", "audio"}
    assert float(info["format"]["duration"]) == pytest.approx(end_s, abs=0.1)
