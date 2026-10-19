import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import pytest

TTS = Path(__file__).resolve().parent.parent / "scripts" / "tts.py"

needs_say_and_ffmpeg = pytest.mark.skipif(
    not (shutil.which("say") and shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="needs macOS say plus ffmpeg/ffprobe",
)


def write_spec(demo_dir, voice=None):
    spec = {
        "title": "Smoke",
        "scenes": [
            {"id": "intro", "title": "Intro", "url": "https://example.com",
             "actions": [], "narration": "Welcome to the demo."},
            {"id": "outro", "title": "Outro", "url": "https://example.com",
             "actions": [{"type": "wait", "ms": 500}], "narration": "Thanks for watching."},
        ],
    }
    if voice is not None:
        spec["voice"] = voice
    path = demo_dir / "demo.json"
    path.write_text(json.dumps(spec))
    return path


def run_tts(*args, env_extra=None):
    env = {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"}
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(TTS), *map(str, args)],
        capture_output=True, text=True, env=env,
    )


@needs_say_and_ffmpeg
def test_say_backend_writes_48k_mono_wav_per_scene_and_durations(tmp_path):
    spec_path = write_spec(tmp_path)

    result = run_tts(spec_path)

    assert result.returncode == 0, result.stderr
    audio = tmp_path / "audio"
    durations = json.loads((audio / "durations.json").read_text())
    assert list(durations) == ["intro", "outro"]
    for scene_id, seconds in durations.items():
        with wave.open(str(audio / f"{scene_id}.wav")) as wav:
            assert wav.getframerate() == 48000
            assert wav.getnchannels() == 1
            actual = wav.getnframes() / wav.getframerate()
        assert seconds > 0
        assert seconds == pytest.approx(actual, abs=0.05)


@pytest.mark.parametrize(
    "args, env_extra, backend",
    [
        (["--backend", "openai"], {}, "openai"),
        ([], {"ELEVENLABS_API_KEY": "el-test"}, "elevenlabs"),
    ],
)
def test_unbuilt_backend_fails_with_not_implemented(tmp_path, args, env_extra, backend):
    spec_path = write_spec(tmp_path)

    result = run_tts(spec_path, *args, env_extra=env_extra)

    assert result.returncode != 0
    assert f"'{backend}' is not implemented" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "audio" / "durations.json").exists()


def test_malformed_spec_fails_with_validation_message(tmp_path):
    spec_path = tmp_path / "demo.json"
    spec_path.write_text(json.dumps({"scenes": [{"id": "a", "narration": ""}]}))

    result = run_tts(spec_path)

    assert result.returncode != 0
    assert "empty narration" in result.stderr
    assert "Traceback" not in result.stderr


@needs_say_and_ffmpeg
def test_unknown_say_voice_is_rejected(tmp_path):
    spec_path = write_spec(tmp_path, voice={"backend": "say", "voice": "NoSuchVoiceXyz"})

    result = run_tts(spec_path)

    assert result.returncode != 0
    assert "unknown say voice 'NoSuchVoiceXyz'" in result.stderr


@needs_say_and_ffmpeg
def test_known_say_voice_is_used(tmp_path):
    spec_path = write_spec(tmp_path, voice={"backend": "say", "voice": "Samantha"})

    result = run_tts(spec_path)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "audio" / "intro.wav").exists()
