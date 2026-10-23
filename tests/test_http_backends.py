import io
import json
import shutil
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tts import TtsError, generate, urllib_transport

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="needs ffmpeg/ffprobe",
)

SPEC = {
    "title": "Smoke",
    "scenes": [
        {"id": "intro", "actions": [], "narration": "Welcome to the demo."},
        {"id": "outro", "actions": [], "narration": "  Thanks for watching.  "},
    ],
}


def wav_bytes(seconds=0.5, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


class FakeTransport:
    """Records each request and answers with a canned (status, body)."""

    def __init__(self, status=200, body=None):
        self.status = status
        self.body = wav_bytes() if body is None else body
        self.requests = []

    def __call__(self, url, headers, body):
        self.requests.append({"url": url, "headers": headers, "json": json.loads(body)})
        return self.status, self.body


def spec_with_voice(backend, voice):
    return {**SPEC, "voice": {"backend": backend, "voice": voice}}


@needs_ffmpeg
def test_openai_sends_voice_and_narration_and_writes_wavs(tmp_path):
    transport = FakeTransport()
    env = {"OPENAI_API_KEY": "sk-test"}

    durations = generate(
        spec_with_voice("openai", "coral"), "openai", tmp_path, env, transport=transport
    )

    assert [r["json"]["input"] for r in transport.requests] == [
        "Welcome to the demo.",
        "Thanks for watching.",
    ]
    first = transport.requests[0]
    assert first["url"] == "https://api.openai.com/v1/audio/speech"
    assert first["headers"]["Authorization"] == "Bearer sk-test"
    assert first["json"]["model"] == "gpt-4o-mini-tts"
    assert first["json"]["voice"] == "coral"
    assert list(durations) == ["intro", "outro"]
    assert json.loads((tmp_path / "durations.json").read_text()) == durations
    for scene_id, seconds in durations.items():
        with wave.open(str(tmp_path / f"{scene_id}.wav")) as w:
            assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (48000, 1, 2)
        assert seconds == pytest.approx(0.5, abs=0.05)


@needs_ffmpeg
def test_elevenlabs_sends_voice_and_narration_and_writes_wavs(tmp_path):
    transport = FakeTransport()
    env = {"ELEVENLABS_API_KEY": "el-test"}

    durations = generate(
        spec_with_voice("elevenlabs", "voice123"), "elevenlabs", tmp_path, env, transport=transport
    )

    assert [r["json"]["text"] for r in transport.requests] == [
        "Welcome to the demo.",
        "Thanks for watching.",
    ]
    first = transport.requests[0]
    assert first["url"].startswith("https://api.elevenlabs.io/v1/text-to-speech/voice123")
    assert first["headers"]["xi-api-key"] == "el-test"
    assert list(durations) == ["intro", "outro"]
    for scene_id, seconds in durations.items():
        with wave.open(str(tmp_path / f"{scene_id}.wav")) as w:
            assert (w.getframerate(), w.getnchannels()) == (48000, 1)
        assert seconds == pytest.approx(0.5, abs=0.05)


@pytest.mark.parametrize(
    "backend, key, env",
    [
        ("openai", "OPENAI_API_KEY", {}),
        ("elevenlabs", "ELEVENLABS_API_KEY", {"ELEVENLABS_API_KEY": "   "}),
        ("openai", "OPENAI_API_KEY", {"ELEVENLABS_API_KEY": "el-test"}),
    ],
)
def test_missing_api_key_fails_before_any_request(tmp_path, backend, key, env):
    transport = FakeTransport()

    with pytest.raises(TtsError, match=f"{key} is not set"):
        generate(SPEC, backend, tmp_path, env, transport=transport)

    assert transport.requests == []
    assert not (tmp_path / "durations.json").exists()


@pytest.mark.parametrize(
    "backend, env",
    [("openai", {"OPENAI_API_KEY": "sk-test"}), ("elevenlabs", {"ELEVENLABS_API_KEY": "el-test"})],
)
def test_non_2xx_response_names_backend_scene_and_status(tmp_path, backend, env):
    transport = FakeTransport(status=401, body=b'{"error": {"message": "Invalid API key"}}')

    with pytest.raises(TtsError) as excinfo:
        generate(SPEC, backend, tmp_path, env, transport=transport)

    message = str(excinfo.value)
    assert backend in message
    assert "'intro'" in message
    assert "401" in message
    assert "Invalid API key" in message
    assert not (tmp_path / "durations.json").exists()


@pytest.fixture
def local_server():
    """A local HTTP server: POST /ok echoes the body with 200, anything else is a 401."""
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append(
                {"path": self.path, "auth": self.headers["Authorization"], "body": body}
            )
            status, reply = (200, body) if self.path == "/ok" else (401, b"denied")
            self.send_response(status)
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", received
    server.shutdown()
    server.server_close()


def test_urllib_transport_posts_and_returns_status_for_success_and_errors(local_server):
    base, received = local_server

    assert urllib_transport(f"{base}/ok", {"Authorization": "Bearer x"}, b"hello") == (
        200,
        b"hello",
    )
    assert urllib_transport(f"{base}/nope", {}, b"{}") == (401, b"denied")
    assert received[0] == {"path": "/ok", "auth": "Bearer x", "body": b"hello"}


def test_urllib_transport_unreachable_host_raises_tts_error():
    with pytest.raises(TtsError, match="could not reach 127.0.0.1:9"):
        urllib_transport("http://127.0.0.1:9/", {}, b"{}", timeout=5)
