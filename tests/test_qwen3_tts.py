import json
import shutil
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

import qwen3_tts
import tts
from tts import choose_backend
from voice_config import QWEN_MODEL, resolve_voice_config, validate_voice

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg/ffprobe"
)


@pytest.fixture
def voice(tmp_path):
    reference = tmp_path / "my voice.wav"
    with wave.open(str(reference), "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 2400)
    return {
        "backend": "qwen3",
        "reference_audio": reference.name,
        "reference_text": "This is my voice.",
    }


def test_resolve_paths_defaults_and_selection(voice, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path.parent)
    config = resolve_voice_config(voice, "qwen3", tmp_path)
    assert config["model"] == QWEN_MODEL
    assert config["language"] == "english"
    assert config["reference_audio"] == str(tmp_path / "my voice.wav")
    assert voice["reference_audio"] == "my voice.wav"
    assert choose_backend(None, {"voice": voice}, {"OPENAI_API_KEY": "secret"}) == "qwen3"
    assert resolve_voice_config({}, "say", tmp_path) == {}


@pytest.mark.parametrize(
    "field,value",
    [
        ("reference_text", ""),
        ("reference_audio", ""),
        ("model", "unknown/model"),
        ("language", "unknown"),
        ("voice", "Vivian"),
    ],
)
def test_bad_clone_config(voice, tmp_path, field, value):
    with pytest.raises(ValueError):
        resolve_voice_config({**voice, field: value}, "qwen3", tmp_path)


@pytest.mark.parametrize(
    "voice",
    [
        [],
        "bad",
        {"reference_text": 3},
        {"speed": "1.2"},
        {"speed": True},
        {"speed": 0.4},
        {"speed": 2.5},
    ],
)
def test_bad_types(voice):
    with pytest.raises(ValueError):
        validate_voice(voice)


def test_worker_loads_once_and_forwards_every_scene(tmp_path):
    calls = []
    loads = []

    def generate(**kwargs):
        calls.append(kwargs)
        return iter(["chunk1", "chunk2"])

    def loader(model):
        loads.append(model)
        return SimpleNamespace(generate=generate)

    def writer(results, path):
        assert list(results) == ["chunk1", "chunk2"]
        path.write_bytes(b"raw")

    scenes = [{"id": name, "narration": f" {name} "} for name in ("one", "two")]
    request = {
        "voice": {
            "model": QWEN_MODEL,
            "reference_audio": "/reference.wav",
            "reference_text": "Reference.",
            "language": "english",
        },
        "scenes": scenes,
    }
    qwen3_tts.synthesize(request, tmp_path, loader, writer)
    assert loads == [QWEN_MODEL]
    assert [call["text"] for call in calls] == ["one", "two"]
    assert all(
        call["lang_code"] == "english" and call["ref_text"] == "Reference." for call in calls
    )
    assert json.loads((tmp_path / "manifest.json").read_text()) == {"one": "0.wav", "two": "1.wav"}


def test_worker_failure_names_scene(tmp_path):
    request = {
        "voice": {
            "model": "x",
            "reference_audio": "a",
            "reference_text": "b",
            "language": "english",
        },
        "scenes": [{"id": "intro", "narration": "Hello"}],
    }

    def fail(**kwargs):
        raise ValueError("broken model")

    with pytest.raises(RuntimeError, match="scene 'intro': broken model"):
        qwen3_tts.synthesize(request, tmp_path, lambda _: SimpleNamespace(generate=fail))
    assert not (tmp_path / "manifest.json").exists()


@needs_ffmpeg
def test_qwen_pipeline_one_worker_and_normalized_output(voice, tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "check_platform", lambda: None)
    original_run = tts._run
    launches = []

    def run(command):
        if command[0] != "uv":
            return original_run(command)
        launches.append(command)
        request = json.loads(Path(command[command.index("--request") + 1]).read_text())
        output = Path(command[command.index("--out-dir") + 1])
        output.mkdir()
        manifest = {}
        for index, scene in enumerate(request["scenes"]):
            path = output / f"{index}.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                wav.writeframes(b"\0\0" * 2400)
            manifest[scene["id"]] = path.name
        (output / "manifest.json").write_text(json.dumps(manifest))
        return ""

    monkeypatch.setattr(tts, "_run", run)

    def no_http(*args):
        pytest.fail("local backend attempted HTTP")

    scenes = [{"id": name, "narration": name} for name in ("one", "two")]
    durations = tts.generate(
        {"voice": voice, "scenes": scenes},
        "qwen3",
        tmp_path / "audio",
        {"OPENAI_API_KEY": "secret"},
        no_http,
        spec_dir=tmp_path,
    )
    assert len(launches) == 1
    assert durations == {"one": 0.1, "two": 0.1}
    for name in durations:
        with wave.open(str(tmp_path / "audio" / f"{name}.wav")) as wav:
            assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (48000, 1, 2)


@needs_ffmpeg
def test_preflight_qwen_is_local(voice, tmp_path, monkeypatch):
    import preflight

    monkeypatch.setattr(tts, "check_platform", lambda: None)
    monkeypatch.setattr(
        preflight, "_executable", lambda name, args: preflight.CheckResult(name, "pass", "ok")
    )
    monkeypatch.setattr(preflight, "burn_method", lambda **kwargs: None)
    spec = tmp_path / "demo.json"
    spec.write_text(
        json.dumps(
            {
                "voice": voice,
                "scenes": [{"id": "intro", "url": "https://example.com", "narration": "Hello"}],
            }
        )
    )
    assert preflight.run_checks(spec)["ready"]
    (tmp_path / voice["reference_audio"]).write_bytes(b"not audio")
    assert not preflight.run_checks(spec)["ready"]


def test_writer_preserves_all_chunks_and_sample_rate(tmp_path):
    np = pytest.importorskip("numpy")
    results = [
        SimpleNamespace(audio=np.array([0.1, -0.2]), sample_rate=24000),
        SimpleNamespace(audio=np.array([0.3]), sample_rate=24000),
    ]
    path = tmp_path / "chunks.wav"
    qwen3_tts.write_audio(results, path)
    with wave.open(str(path)) as wav:
        assert wav.getnframes() == 3
        assert wav.getframerate() == 24000


@pytest.mark.parametrize("samples", [[], [float("nan")]])
def test_writer_rejects_invalid_audio(tmp_path, samples):
    np = pytest.importorskip("numpy")
    with pytest.raises(ValueError, match="empty or nonfinite"):
        qwen3_tts.write_audio(
            [SimpleNamespace(audio=np.array(samples), sample_rate=24000)], tmp_path / "bad.wav"
        )


def test_writer_rejects_no_results(tmp_path):
    pytest.importorskip("numpy")
    with pytest.raises(ValueError, match="no audio"):
        qwen3_tts.write_audio([], tmp_path / "empty.wav")


def test_local_snapshot_and_invalid_model(voice, tmp_path):
    model = tmp_path / "my model"
    model.mkdir()
    (model / "config.json").write_text(
        json.dumps(
            {
                "model_type": "qwen3_tts",
                "tts_model_type": "base",
                "talker_config": {"hidden_size": 1024},
            }
        )
    )
    assert resolve_voice_config({**voice, "model": "my model"}, "qwen3", tmp_path)["model"] == str(
        model
    )
    (model / "config.json").write_text("{}")
    with pytest.raises(ValueError, match="0.6B Base"):
        resolve_voice_config({**voice, "model": "my model"}, "qwen3", tmp_path)


def test_platform_failure(monkeypatch):
    import voice_config

    monkeypatch.setattr(voice_config.platform, "machine", lambda: "x86_64")
    with pytest.raises(ValueError, match="Rosetta"):
        voice_config.check_platform()
