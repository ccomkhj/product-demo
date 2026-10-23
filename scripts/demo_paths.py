"""Where a demo's files live in its folder, ``demos/<slug>/`` (stdlib only).

The folder's name is the slug. The CLIs take these as defaults; flags may override them.
"""

from pathlib import Path


def slug(demo_dir):
    return Path(demo_dir).resolve().name


def spec(demo_dir):
    return Path(demo_dir) / "demo.json"


def raw(demo_dir):
    return Path(demo_dir) / "raw.mp4"


def timeline(demo_dir):
    return Path(demo_dir) / "timeline.jsonl"


def audio_dir(demo_dir):
    return Path(demo_dir) / "audio"


def durations(audio_dir):
    return Path(audio_dir) / "durations.json"


def video(demo_dir):
    """The composed video, <slug>.mp4."""
    return Path(demo_dir) / f"{slug(demo_dir)}.mp4"


def post_prefix(demo_dir):
    """Post-processing's outputs start with this: <slug>-post.mp4, <slug>-post.srt, ..."""
    return Path(demo_dir) / f"{slug(demo_dir)}-post"


def srt(video, language=None):
    """The subtitles next to ``video``; with a language, its translation (<name>.<lang>.srt)."""
    return Path(video).with_suffix(f".{language}.srt" if language else ".srt")


def edit_map(video):
    return Path(video).with_suffix(".editmap.json")
