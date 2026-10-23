import base64
import io
import json
import math
import struct
import subprocess
import threading
import urllib.error
import urllib.request
import wave
from pathlib import Path

import pytest

import own_voice
import voice_config
from tts import choose_backend


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path / "product-demo" / "config.json"


def recording(seconds=4, silent=False, pause=0.5):
    """Speech-like tone for ``seconds``, ending in ``pause`` seconds of silence."""
    buffer = io.BytesIO()
    speech = int((seconds - pause) * 24000)
    with wave.open(buffer, "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(
            b"".join(
                struct.pack("<h", 0 if silent or i >= speech else int(4000 * math.sin(i / 10)))
                for i in range(int(seconds * 24000))
            )
        )
    return {"audio": base64.b64encode(buffer.getvalue()).decode(), "transcript": "My exact words."}


def test_register_and_reuse_without_prompt(registry, monkeypatch):
    assert not own_voice.status()["ready"]
    own_voice.register(recording())
    assert own_voice.status()["ready"]
    before = registry.read_bytes()
    monkeypatch.setattr(own_voice, "recorder_server", lambda: pytest.fail("must not record again"))
    monkeypatch.setattr(
        own_voice.webbrowser, "open", lambda _: pytest.fail("must not open browser")
    )
    assert own_voice.main([]) == 0
    assert registry.read_bytes() == before
    config = voice_config.resolve_voice_config({"profile": "own_voice"}, "qwen3", Path("/tmp"))
    assert Path(config["reference_audio"]).parent == registry.parent
    assert config["reference_text"] == "My exact words."
    with pytest.raises(ValueError, match="already registered"):
        own_voice.register(recording())
    assert registry.read_bytes() == before


def test_replace_records_over_a_registered_voice(registry, monkeypatch):
    own_voice.register(recording())
    first = voice_config.resolve_voice_config({"profile": "own_voice"}, "qwen3", Path("/tmp"))[
        "reference_audio"
    ]

    class Server:
        saved = False

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def handle_request(self):
            own_voice.register({**recording(), "transcript": "New words."}, replace)
            self.saved = True

    def fake_server(flag):
        nonlocal replace
        replace = flag
        return Server(), "http://127.0.0.1:1/token"

    replace = None
    monkeypatch.setattr(own_voice, "recorder_server", fake_server)
    assert own_voice.main(["--replace", "--no-open"]) == 0
    config = voice_config.resolve_voice_config({"profile": "own_voice"}, "qwen3", Path("/tmp"))
    assert replace is True and config["reference_text"] == "New words."
    assert config["reference_audio"] != first and not Path(first).exists()


def test_profile_selects_local_backend_before_api_keys():
    spec = {"voice": {"profile": "own_voice"}}
    assert choose_backend(None, spec, {"OPENAI_API_KEY": "secret"}) == "qwen3"
    assert choose_backend("kokoro", spec, {}) == "kokoro"


def test_missing_and_broken_registration(registry):
    with pytest.raises(ValueError, match="needs setup"):
        voice_config.resolve_voice_config({"profile": "own_voice"}, "qwen3", Path("/tmp"))
    registry.parent.mkdir()
    registry.write_text(
        json.dumps({"own_voice": {"reference_audio": "gone.wav", "reference_text": "Hello"}})
    )
    assert not own_voice.status()["ready"]
    own_voice.register(recording())
    assert own_voice.status()["ready"]


@pytest.mark.parametrize(
    "payload",
    [
        recording(1),
        recording(4, True),
        recording(4, pause=0),
        {"audio": "bad!", "transcript": "Hello"},
        {"audio": "AAAA", "transcript": ""},
    ],
)
def test_invalid_recordings_are_not_registered(registry, payload):
    with pytest.raises(ValueError):
        own_voice.register(payload)
    assert not registry.exists()


def test_status_does_not_start_recording(registry, monkeypatch):
    monkeypatch.setattr(own_voice, "recorder_server", lambda: pytest.fail("unexpected recorder"))
    assert own_voice.main(["--status"]) == 1


def test_recorder_page_and_save_require_same_origin(registry):
    server, url = own_voice.recorder_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(url) as response:
            html = response.read().decode()
        assert "getUserMedia" in html and "__TOKEN__" not in html
        request = urllib.request.Request(
            url + "/save",
            data=json.dumps(recording()).encode(),
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(request)
        assert exc.value.code == 403
        request.add_header("Origin", f"http://127.0.0.1:{server.server_port}")
        with urllib.request.urlopen(request) as response:
            assert json.load(response)["ready"]
        assert server.saved and own_voice.status()["ready"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_profile_cannot_mix_new_audio_with_old_transcript(registry):
    with pytest.raises(ValueError, match="together"):
        voice_config.resolve_voice_config(
            {"profile": "own_voice", "reference_audio": "new.wav"}, "qwen3", Path("/tmp")
        )


def test_saved_profile_cannot_carry_pronunciations(registry):
    own_voice.register(recording())
    saved = json.loads(registry.read_text())
    saved["own_voice"]["pronounce"] = {"OOS": "out-of-stock"}
    registry.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="pronounce belongs in the demo spec"):
        voice_config.resolve_voice_config({"profile": "own_voice"}, "qwen3", Path("/tmp"))


PHOTO = Path(__file__).resolve().parent.parent / "assets" / "avatars" / "huijo.png"


def test_photo_is_saved_beside_the_registry_and_replaced(registry):
    assert not own_voice.status()["photo"]
    own_voice.register(recording())
    first = own_voice.register_photo(PHOTO)
    assert own_voice.status() == {
        "ready": True,
        "profile": "own_voice",
        "photo": True,
        "defaults": {},
    }
    assert voice_config.saved_photo() == first.resolve()
    assert first.parent == registry.parent and first.read_bytes() == PHOTO.read_bytes()
    second = own_voice.register_photo(PHOTO)
    assert voice_config.saved_photo() == second.resolve() and not first.exists()
    config = voice_config.resolve_voice_config({"profile": "own_voice"}, "qwen3", Path("/tmp"))
    assert config["reference_text"] == "My exact words."


def test_photo_without_a_voice_and_a_non_image_is_rejected(registry, tmp_path):
    assert own_voice.main(["--photo", str(PHOTO)]) == 0
    assert own_voice.status()["photo"] and not own_voice.status()["ready"]
    text = tmp_path / "notes.txt"
    text.write_text("not a photo")
    with pytest.raises((ValueError, subprocess.SubprocessError)):
        own_voice.register_photo(text)
    assert voice_config.saved_photo().suffix == ".png"


def test_missing_photo_needs_setup(registry):
    with pytest.raises(ValueError, match="own_photo needs setup"):
        voice_config.saved_photo()


def test_defaults_are_saved_merged_and_reported(registry, capsys):
    assert own_voice.status()["defaults"] == {}
    own_voice.register(recording())
    before = json.loads(registry.read_text())["own_voice"]
    assert (
        own_voice.main(
            [
                "--save-defaults",
                json.dumps(
                    {
                        "voice": {"backend": "kokoro", "voice": "am_michael", "speed": 1.1},
                        "avatar": False,
                    }
                ),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["defaults"]["avatar"] is False
    own_voice.save_defaults({"burn_subs": False, "viewport": {"width": 1280, "height": 720}})
    assert own_voice.status()["defaults"] == {
        "voice": {"backend": "kokoro", "voice": "am_michael", "speed": 1.1},
        "avatar": False,
        "burn_subs": False,
        "viewport": {"width": 1280, "height": 720},
    }
    assert json.loads(registry.read_text())["own_voice"] == before


@pytest.mark.parametrize(
    "settings",
    [
        {"colour": "red"},
        {"burn_subs": "yes"},
        {"voice": {"backend": "robot"}},
        {"voice": {"speed": 3}},
        {
            "voice": {
                "backend": "qwen3",
                "profile": "own_voice",
                "pronounce": {"OOS": "out of stock"},
            }
        },
        {"avatar": {"profile": "own_photo", "corner": "middle"}},
        {"viewport": {"width": 0, "height": 720}},
        [],
    ],
)
def test_invalid_defaults_are_not_saved(registry, settings):
    with pytest.raises(ValueError):
        own_voice.save_defaults(settings)
    assert not registry.exists()
    assert own_voice.main(["--save-defaults", json.dumps(settings)]) == 1
