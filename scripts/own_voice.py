#!/usr/bin/env python3
"""Reuse a registered own_voice, or serve a local microphone recorder; --replace records a new
one, --photo saves own_photo, --save-defaults saves the settings chosen by --configure."""

import argparse
import base64
import binascii
import json
import secrets
import shutil
import subprocess
import tempfile
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from demo_spec import SpecError, validate_spec
from proc import ffprobe_cmd
from tts import BACKENDS
from voice_config import (
    QWEN_MODEL,
    probe_reference,
    registry_path,
    resolve_voice_config,
    saved_photo,
)

READING = (
    "Hello, this is my voice. I will use it to guide you through a short product "
    "demonstration. We will look at the main features together, one step at a time, "
    "and explain how they work in everyday use."
)
MAX_UPLOAD = 16 * 1024 * 1024
DEFAULT_FIELDS = ("voice", "avatar", "viewport", "burn_subs")


def run(command):
    return subprocess.run(command, capture_output=True, text=True, check=True, timeout=30).stdout


def status():
    try:
        saved_photo()
        photo = True
    except ValueError:
        photo = False
    try:
        defaults = read_registry().get("defaults", {})
    except (ValueError, OSError):
        defaults = {}
    try:
        config = resolve_voice_config({"profile": "own_voice"}, "qwen3", Path.cwd())
        probe_reference(config["reference_audio"], run)
        return {"ready": True, "profile": "own_voice", "photo": photo, "defaults": defaults}
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        return {
            "ready": False,
            "profile": "own_voice",
            "photo": photo,
            "defaults": defaults,
            "reason": str(exc),
        }


def read_registry():
    registry = registry_path()
    if not registry.exists():
        return {}
    data = json.loads(registry.read_text())
    if not isinstance(data, dict):
        raise ValueError("The voice registry must be a JSON object; repair it before saving")
    return data


def write_registry(data, temporary):
    pending = Path(temporary) / "config.json"
    pending.write_text(json.dumps(data, indent=2) + "\n")
    pending.replace(registry_path())


def remove_replaced(name):
    """Delete a recording or photo the registry no longer points at, but only inside the registry folder."""
    directory = registry_path().parent
    if name and (directory / name).resolve().parent == directory.resolve():
        (directory / name).unlink(missing_ok=True)


def register_photo(source):
    """Copy a decodable image beside the registry as own_photo, replacing an earlier one."""
    source = Path(source).expanduser()
    output = run(ffprobe_cmd(source, "stream=width,height", "v"))
    streams = json.loads(output).get("streams") or [{}]
    if not streams[0].get("width") or not streams[0].get("height"):
        raise ValueError(f"{source} is not an image")
    directory = registry_path().parent
    directory.mkdir(parents=True, exist_ok=True)
    data = read_registry()
    previous = (
        data.get("own_photo", {}).get("image") if isinstance(data.get("own_photo"), dict) else None
    )
    name = f"own_photo-{uuid.uuid4().hex}{source.suffix.lower()}"
    with tempfile.TemporaryDirectory(dir=directory) as temporary:
        shutil.copyfile(source, directory / name)
        data["own_photo"] = {"image": name}
        write_registry(data, temporary)
    remove_replaced(previous)
    return directory / name


def save_defaults(settings):
    """Merge ``settings`` into the saved defaults after checking them as a demo spec would.

    ``avatar: false`` means no presenter even when own_photo exists; ``burn_subs`` is compose's
    subtitle policy. Pronunciations belong to a product's demo, not to every demo."""
    if not isinstance(settings, dict) or set(settings) - set(DEFAULT_FIELDS):
        raise ValueError(f"defaults must be a JSON object with only {', '.join(DEFAULT_FIELDS)}")
    if "burn_subs" in settings and not isinstance(settings["burn_subs"], bool):
        raise ValueError("burn_subs must be true or false")
    voice = settings.get("voice") or {}
    if "pronounce" in voice:
        raise ValueError("voice.pronounce belongs in the demo spec, not the defaults")
    if voice.get("backend", "qwen3") not in BACKENDS:
        raise ValueError(f"voice.backend must be one of {', '.join(BACKENDS)}")
    spec = {key: settings[key] for key in ("voice", "avatar", "viewport") if key in settings}
    if spec.get("avatar") is False:
        del spec["avatar"]
    try:
        validate_spec({**spec, "scenes": [{"id": "x", "url": "x", "narration": "x"}]})
    except SpecError as exc:
        raise ValueError(str(exc)) from exc
    directory = registry_path().parent
    directory.mkdir(parents=True, exist_ok=True)
    data = read_registry()
    data["defaults"] = {**data.get("defaults", {}), **settings}
    with tempfile.TemporaryDirectory(dir=directory) as temporary:
        write_registry(data, temporary)


def register(payload, replace=False):
    """Validate audio before atomically registering it. Replace a usable profile only on request."""
    if status()["ready"] and not replace:
        raise ValueError("own_voice is already registered; the existing recording was kept")
    if not isinstance(payload, dict):
        raise ValueError("Expected a recording and transcript")
    transcript = payload.get("transcript")
    if not isinstance(transcript, str) or not transcript.strip() or len(transcript) > 5000:
        raise ValueError("Enter the exact words you spoke (up to 5000 characters)")
    try:
        audio = base64.b64decode(payload.get("audio", ""), validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ValueError("Invalid audio upload") from exc
    if not audio or len(audio) > MAX_UPLOAD:
        raise ValueError("Empty or oversized recording")
    directory = registry_path().parent
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory) as temporary:
        source = Path(temporary) / "recording"
        target = Path(temporary) / "reference.wav"
        source.write_bytes(audio)
        # No remote inputs or playlists: only decode the uploaded local recording.
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(source),
                "-t",
                "31",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "24000",
                "-c:a",
                "pcm_s16le",
                str(target),
            ]
        )
        import array
        import math
        import wave

        with wave.open(str(target)) as wav:
            duration = wav.getnframes() / wav.getframerate()
            samples = array.array("h", wav.readframes(wav.getnframes()))
        if not 3 <= duration <= 30:
            raise ValueError("Record between 3 and 30 seconds, then try again")
        rms = math.sqrt(sum(value * value for value in samples) / len(samples))
        if rms < 30:
            raise ValueError("The recording is too quiet. Check your microphone and try again")
        # A clip cut off mid-word makes qwen3 speak the missing words at the start of every scene.
        tail = samples[-int(0.3 * 24000) :]
        if math.sqrt(sum(value * value for value in tail) / len(tail)) > 0.25 * rms:
            raise ValueError(
                "The recording ends mid-speech. Pause a moment after your last word, then press Stop"
            )
        data = read_registry()
        previous = (
            data.get("own_voice", {}).get("reference_audio")
            if isinstance(data.get("own_voice"), dict)
            else None
        )
        name = f"own_voice-{uuid.uuid4().hex}.wav"
        target.replace(directory / name)
        data["own_voice"] = {
            "backend": "qwen3",
            "model": QWEN_MODEL,
            "reference_audio": name,
            "reference_text": transcript.strip(),
            "language": "English",
        }
        write_registry(data, temporary)
    remove_replaced(previous)


def recorder_server(replace=False):
    token = secrets.token_urlsafe(32)
    page = Path(__file__).with_name("own_voice.html").read_text()
    page = page.replace("__TOKEN__", token).replace("__READING__", READING)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, code, body, mime="application/json"):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; media-src blob:; connect-src 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(data)

        def valid_host(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self.valid_host() or self.path != f"/{token}":
                self.respond(404, "{}")
                return
            self.respond(200, page, "text/html; charset=utf-8")

        def do_POST(self):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if (
                not self.valid_host()
                or self.path != f"/{token}/save"
                or self.headers.get("Origin") != origin
            ):
                self.respond(403, json.dumps({"error": "Invalid recording request"}))
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_UPLOAD:
                    raise ValueError("Empty or oversized recording")
                self.connection.settimeout(15)
                register(json.loads(self.rfile.read(length)), replace)
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                self.respond(400, json.dumps({"error": str(exc)}))
                return
            self.respond(200, json.dumps({"ready": True, "profile": "own_voice"}))
            self.server.saved = True

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 1
    server.saved = False
    return server, f"http://127.0.0.1:{server.server_port}/{token}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--status", action="store_true", help="check registration without recording"
    )
    parser.add_argument(
        "--replace", action="store_true", help="record a new voice over the registered one"
    )
    parser.add_argument(
        "--no-open", action="store_true", help="print the recorder URL without opening it"
    )
    parser.add_argument(
        "--photo", metavar="PATH", help="save this photo as own_photo, the presenter bubble"
    )
    parser.add_argument(
        "--save-defaults",
        metavar="JSON",
        help=f"merge these settings ({', '.join(DEFAULT_FIELDS)}) into the saved defaults",
    )
    args = parser.parse_args(argv)
    if args.save_defaults:
        try:
            save_defaults(json.loads(args.save_defaults))
        except (ValueError, OSError) as exc:
            print(json.dumps({"defaults": False, "reason": str(exc)}), flush=True)
            return 1
        print(json.dumps(status()), flush=True)
        return 0
    if args.photo:
        try:
            register_photo(args.photo)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            print(json.dumps({"photo": False, "reason": str(exc)}), flush=True)
            return 1
        print(json.dumps(status()), flush=True)
        return 0
    result = status()
    print(json.dumps(result), flush=True)
    if args.status or (result["ready"] and not args.replace):
        return 0 if result["ready"] else 1
    server, url = recorder_server(args.replace)
    print(f"Record your voice: {url}", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        with server:
            while not server.saved:
                server.handle_request()
    except KeyboardInterrupt:
        return 1
    print(json.dumps({"ready": True, "profile": "own_voice"}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
