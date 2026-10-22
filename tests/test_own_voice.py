import base64
import io
import json
import math
from pathlib import Path
import struct
import threading
import urllib.error
import urllib.request
import wave

import pytest

import own_voice
import voice_config
from demo_spec import choose_backend


@pytest.fixture
def registry(tmp_path, monkeypatch):
    config = tmp_path / '.voices' / 'config.json'
    monkeypatch.setattr(voice_config, 'PROFILE_CONFIG', config)
    monkeypatch.setattr(own_voice, 'PROFILE_CONFIG', config)
    return config


def recording(seconds=4, silent=False):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b''.join(struct.pack('<h', 0 if silent else int(4000 * math.sin(i / 10)))
                                 for i in range(int(seconds * 24000))))
    return {'audio': base64.b64encode(buffer.getvalue()).decode(), 'transcript': 'My exact words.'}


def test_register_and_reuse_without_prompt(registry, monkeypatch):
    assert not own_voice.status()['ready']
    own_voice.register(recording())
    assert own_voice.status()['ready']
    before = registry.read_bytes()
    monkeypatch.setattr(own_voice, 'recorder_server', lambda: pytest.fail('must not record again'))
    monkeypatch.setattr(own_voice.webbrowser, 'open', lambda _: pytest.fail('must not open browser'))
    assert own_voice.main([]) == 0
    assert registry.read_bytes() == before
    config = voice_config.resolve_voice_config({'profile': 'own_voice'}, 'qwen3', Path('/tmp'))
    assert Path(config['reference_audio']).parent == registry.parent
    assert config['reference_text'] == 'My exact words.'
    with pytest.raises(ValueError, match='already registered'):
        own_voice.register(recording())
    assert registry.read_bytes() == before


def test_profile_selects_local_backend_before_api_keys():
    spec = {'voice': {'profile': 'own_voice'}}
    assert choose_backend(None, spec, {'OPENAI_API_KEY': 'secret'}) == 'qwen3'
    assert choose_backend('kokoro', spec, {}) == 'kokoro'


def test_missing_and_broken_registration(registry):
    with pytest.raises(ValueError, match='needs setup'):
        voice_config.resolve_voice_config({'profile': 'own_voice'}, 'qwen3', Path('/tmp'))
    registry.parent.mkdir()
    registry.write_text(json.dumps({'own_voice': {'reference_audio': 'gone.wav', 'reference_text': 'Hello'}}))
    assert not own_voice.status()['ready']
    own_voice.register(recording())
    assert own_voice.status()['ready']


@pytest.mark.parametrize('payload', [recording(1), recording(4, True), {'audio': 'bad!', 'transcript': 'Hello'},
                                     {'audio': 'AAAA', 'transcript': ''}])
def test_invalid_recordings_are_not_registered(registry, payload):
    with pytest.raises(ValueError):
        own_voice.register(payload)
    assert not registry.exists()


def test_status_does_not_start_recording(registry, monkeypatch):
    monkeypatch.setattr(own_voice, 'recorder_server', lambda: pytest.fail('unexpected recorder'))
    assert own_voice.main(['--status']) == 1


def test_recorder_page_and_save_require_same_origin(registry):
    server, url = own_voice.recorder_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(url) as response:
            html = response.read().decode()
        assert 'getUserMedia' in html and '__TOKEN__' not in html
        request = urllib.request.Request(url + '/save', data=json.dumps(recording()).encode(),
                                         headers={'Content-Type': 'application/json'})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(request)
        assert exc.value.code == 403
        request.add_header('Origin', f'http://127.0.0.1:{server.server_port}')
        with urllib.request.urlopen(request) as response:
            assert json.load(response)['ready']
        assert server.saved and own_voice.status()['ready']
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_profile_cannot_mix_new_audio_with_old_transcript(registry):
    with pytest.raises(ValueError, match='together'):
        voice_config.resolve_voice_config({'profile': 'own_voice', 'reference_audio': 'new.wav'},
                                          'qwen3', Path('/tmp'))
