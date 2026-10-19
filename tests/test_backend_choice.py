import pytest

from demo_spec import BACKENDS, SpecError, choose_backend

NO_VOICE = {"scenes": []}


def test_say_is_used_when_no_flag_spec_or_keys():
    assert choose_backend(None, NO_VOICE, {}) == "say"


def test_openai_key_selects_openai():
    assert choose_backend(None, NO_VOICE, {"OPENAI_API_KEY": "sk-x"}) == "openai"


def test_elevenlabs_key_selects_elevenlabs():
    assert choose_backend(None, NO_VOICE, {"ELEVENLABS_API_KEY": "el-x"}) == "elevenlabs"


def test_elevenlabs_beats_openai_when_both_keys_set():
    env = {"OPENAI_API_KEY": "sk-x", "ELEVENLABS_API_KEY": "el-x"}

    assert choose_backend(None, NO_VOICE, env) == "elevenlabs"


def test_empty_key_counts_as_unset():
    assert choose_backend(None, NO_VOICE, {"OPENAI_API_KEY": ""}) == "say"


@pytest.fixture
def uv_path(tmp_path):
    """A PATH holding a fake executable ``uv``."""
    uv = tmp_path / "uv"
    uv.write_text("#!/bin/sh\n")
    uv.chmod(0o755)
    return str(tmp_path)


def test_uv_on_path_selects_kokoro(uv_path):
    assert choose_backend(None, NO_VOICE, {"PATH": uv_path}) == "kokoro"


def test_say_is_used_when_uv_is_not_on_path(tmp_path):
    assert choose_backend(None, NO_VOICE, {"PATH": str(tmp_path)}) == "say"


def test_api_keys_beat_kokoro(uv_path):
    env = {"PATH": uv_path, "OPENAI_API_KEY": "sk-x"}

    assert choose_backend(None, NO_VOICE, env) == "openai"


def test_spec_backend_beats_env():
    spec = {"voice": {"backend": "say"}, "scenes": []}
    env = {"OPENAI_API_KEY": "sk-x", "ELEVENLABS_API_KEY": "el-x"}

    assert choose_backend(None, spec, env) == "say"


def test_flag_beats_spec_and_env():
    spec = {"voice": {"backend": "openai"}, "scenes": []}
    env = {"ELEVENLABS_API_KEY": "el-x"}

    assert choose_backend("say", spec, env) == "say"


def test_flag_beats_env_without_spec_backend():
    assert choose_backend("openai", NO_VOICE, {}) == "openai"


def test_unknown_backend_in_flag_is_rejected():
    with pytest.raises(SpecError, match="unknown TTS backend 'polly'"):
        choose_backend("polly", NO_VOICE, {})


def test_every_known_backend_has_a_synthesizer():
    from tts import SYNTHESIZERS

    assert set(SYNTHESIZERS) == set(BACKENDS)


def test_tts_cli_offers_exactly_the_known_backends(capsys):
    import tts

    with pytest.raises(SystemExit):
        tts.main(["demo.json", "--backend", "polly"])

    usage = capsys.readouterr().err
    offered = usage.split("--backend {", 1)[1].split("}", 1)[0].split(",")
    assert set(offered) == set(BACKENDS)


def test_unknown_backend_in_spec_is_rejected():
    spec = {"voice": {"backend": "polly"}, "scenes": []}

    with pytest.raises(SpecError, match="unknown TTS backend 'polly'"):
        choose_backend(None, spec, {})
