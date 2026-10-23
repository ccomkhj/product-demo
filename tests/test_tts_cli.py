import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import pytest

TTS = Path(__file__).resolve().parent.parent / "scripts" / "tts.py"

KOKORO_MODELS = Path.home() / ".cache" / "product-demo" / "kokoro"
# Skipped until the ~340 MB model is cached (any kokoro run caches it), so the suite
# never starts a large download by itself.
needs_kokoro = pytest.mark.skipif(
    not (
        shutil.which("uv")
        and shutil.which("ffmpeg")
        and (KOKORO_MODELS / "kokoro-v1.0.onnx").exists()
        and (KOKORO_MODELS / "voices-v1.0.bin").exists()
    ),
    reason="needs uv, ffmpeg and the cached Kokoro model",
)
HOME = {"HOME": str(Path.home())}

needs_say_and_ffmpeg = pytest.mark.skipif(
    not (shutil.which("say") and shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="needs macOS say plus ffmpeg/ffprobe",
)


def write_spec(demo_dir, voice=None):
    spec = {
        "title": "Smoke",
        "scenes": [
            {
                "id": "intro",
                "title": "Intro",
                "url": "https://example.com",
                "actions": [],
                "narration": "Welcome to the demo.",
            },
            {
                "id": "outro",
                "title": "Outro",
                "url": "https://example.com",
                "actions": [{"type": "wait", "ms": 500}],
                "narration": "Thanks for watching.",
            },
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
        capture_output=True,
        text=True,
        env=env,
    )


@needs_say_and_ffmpeg
def test_say_backend_writes_48k_mono_wav_per_scene_and_durations(tmp_path):
    spec_path = write_spec(tmp_path, voice={"backend": "say"})

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
    "args, voice, key",
    [
        (["--backend", "openai"], None, "OPENAI_API_KEY"),
        ([], {"backend": "elevenlabs"}, "ELEVENLABS_API_KEY"),
    ],
)
def test_forced_http_backend_without_key_fails_clearly(tmp_path, args, voice, key):
    spec_path = write_spec(tmp_path, voice=voice)

    result = run_tts(spec_path, *args, env_extra={key: ""})

    assert result.returncode != 0
    assert f"{key} is not set" in result.stderr
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


@needs_say_and_ffmpeg
def test_voice_speed_shortens_narration(tmp_path):
    lengths = []
    for speed in (1, 2):
        demo = tmp_path / str(speed)
        demo.mkdir()
        result = run_tts(write_spec(demo, voice={"backend": "say", "speed": speed}))
        assert result.returncode == 0, result.stderr
        lengths.append(json.loads((demo / "audio" / "durations.json").read_text())["intro"])
    assert lengths[1] == pytest.approx(lengths[0] / 2, rel=0.1)


@needs_kokoro
def test_kokoro_backend_writes_48k_mono_wav_per_scene(tmp_path):
    spec_path = write_spec(tmp_path, voice={"backend": "kokoro"})

    result = run_tts(spec_path, env_extra=HOME)

    assert result.returncode == 0, result.stderr
    assert "(backend: kokoro)" in result.stdout
    for scene_id in ("intro", "outro"):
        with wave.open(str(tmp_path / "audio" / f"{scene_id}.wav")) as wav:
            assert wav.getframerate() == 48000
            assert wav.getnchannels() == 1
            assert wav.getnframes() > 48000 // 2


@needs_kokoro
def test_unknown_kokoro_voice_is_rejected(tmp_path):
    spec_path = write_spec(tmp_path, voice={"backend": "kokoro", "voice": "af_nosuchvoice"})

    result = run_tts(spec_path, env_extra=HOME)

    assert result.returncode != 0
    assert "unknown voice 'af_nosuchvoice'" in result.stderr
    assert "Traceback" not in result.stderr


@needs_kokoro
def test_unsupported_kokoro_language_is_rejected(tmp_path):
    spec_path = write_spec(tmp_path, voice={"backend": "kokoro", "voice": "jf_alpha"})

    result = run_tts(spec_path, env_extra=HOME)

    assert result.returncode != 0
    assert "voice 'jf_alpha' is not supported" in result.stderr


needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="needs ffmpeg/ffprobe",
)


@pytest.fixture
def heard(monkeypatch):
    """Swap the say backend for a fake that records the narration each scene is spoken from."""
    import tts

    texts = []

    def fake(scenes, _config, directory, _env, _transport):
        for index, scene in enumerate(scenes):
            texts.append(scene["narration"])
            raw = directory / f"{index}.wav"
            with wave.open(str(raw), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(24000)
                out.writeframes(b"\x00\x00" * 2400)
            yield scene["id"], raw

    monkeypatch.setitem(tts.SYNTHESIZERS, "say", fake)
    return texts


def pronounce_spec(narration, pronounce):
    return {
        "title": "Smoke",
        "voice": {"backend": "say", "pronounce": pronounce},
        "scenes": [
            {"id": "intro", "url": "https://example.com", "actions": [], "narration": narration}
        ],
    }


@needs_ffmpeg
def test_pronounce_replaces_terms_in_the_text_the_synthesizer_speaks(tmp_path, heard):
    from tts import generate

    spec = pronounce_spec("Our OOS alerts flag OOS items.", {"OOS": "out-of-stock"})

    generate(spec, "say", tmp_path, {})

    assert heard == ["Our out-of-stock alerts flag out-of-stock items."]


@needs_ffmpeg
def test_pronounce_replaces_whole_words_only(tmp_path, heard):
    from tts import generate

    spec = pronounce_spec("BOOST the OOS count, not OOSE or oos.", {"OOS": "O-O-S"})

    generate(spec, "say", tmp_path, {})

    assert heard == ["BOOST the O-O-S count, not OOSE or oos."]


@needs_ffmpeg
def test_pronounce_never_replaces_a_replacement(tmp_path, heard):
    from tts import generate

    spec = pronounce_spec("SKU and API.", {"SKU": "S-K-U API", "API": "A-P-I", "S-K-U": "x"})

    generate(spec, "say", tmp_path, {})

    assert heard == ["S-K-U API and A-P-I."]


@needs_ffmpeg
def test_pronounce_prefers_the_longest_key(tmp_path, heard):
    from tts import generate

    spec = pronounce_spec("Open OOS list.", {"OOS": "O-O-S", "OOS list": "stock-out list"})

    generate(spec, "say", tmp_path, {})

    assert heard == ["Open stock-out list."]


@needs_ffmpeg
def test_pronounce_leaves_the_spec_unchanged(tmp_path, heard):
    import copy

    from tts import generate

    spec = pronounce_spec("Our OOS alerts.", {"OOS": "out-of-stock"})
    before = copy.deepcopy(spec)

    generate(spec, "say", tmp_path, {})

    assert spec == before


@pytest.mark.parametrize(
    "pronounce", [{"": "x"}, {"OOS": ""}, {"OOS": "  "}, {"OOS": 3}, ["OOS"], "OOS"]
)
def test_malformed_pronounce_is_rejected(tmp_path, pronounce):
    spec_path = tmp_path / "demo.json"
    spec_path.write_text(json.dumps(pronounce_spec("Our OOS alerts.", pronounce)))

    result = run_tts(spec_path)

    assert result.returncode != 0
    assert "voice.pronounce" in result.stderr
    assert "Traceback" not in result.stderr
