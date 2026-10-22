import json
import subprocess

import pytest

import preflight


@pytest.fixture
def local(tmp_path, monkeypatch):
    spec = tmp_path / 'demo.json'
    spec.write_text(json.dumps({'voice': {'backend': 'say'}, 'scenes': [
        {'id': 'export', 'url': 'http://localhost', 'narration': 'Export a report.'}
    ]}))
    monkeypatch.setattr(preflight.shutil, 'which', lambda name, **kw: '/bin/' + name)
    def run(argv, **kwargs):
        assert kwargs['timeout'] == 10
        assert argv[1:] in (['-version'], ['--version'], ['-v', '?'], ['-hide_banner', '-filters'])
        output = 'Samantha  en_US  Voice' if argv[0] == 'say' else ' ... subtitles V->V subtitles'
        return subprocess.CompletedProcess(argv, 0, stdout=output, stderr='')
    monkeypatch.setattr(preflight.subprocess, 'run', run)
    monkeypatch.setattr(preflight.os, 'environ', {'PATH': '/bin'})
    return spec


def check(report, name):
    return next(item for item in report['checks'] if item['id'] == name)


def test_ready_is_explicitly_local(local):
    report = preflight.run_checks(local)
    assert report['ready'] and report['scope'] == 'local'
    assert report['selected_backend'] == 'say'
    assert check(report, 'browser')['status'] == 'warning'
    assert list(local.parent.iterdir()) == [local]


@pytest.mark.parametrize('binary', ['ffmpeg', 'ffprobe', 'say'])
def test_missing_required_binary(local, monkeypatch, binary):
    monkeypatch.setattr(preflight.shutil, 'which', lambda name, **kw: None if name == binary else '/bin/' + name)
    report = preflight.run_checks(local)
    assert not report['ready']
    assert check(report, binary)['remedy']


@pytest.mark.parametrize('failure', ['timeout', 'exit', 'permission'])
def test_broken_executable(local, monkeypatch, failure):
    def run(argv, **kwargs):
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(argv, 10)
        if failure == 'permission':
            raise PermissionError('not executable')
        return subprocess.CompletedProcess(argv, 1, stdout='', stderr='private data')
    monkeypatch.setattr(preflight.subprocess, 'run', run)
    report = preflight.run_checks(local)
    assert check(report, 'ffmpeg')['status'] == 'fail'
    assert 'private data' not in json.dumps(report)


def test_unwritable_output(local, monkeypatch):
    def denied(**kwargs):
        raise PermissionError('denied')
    monkeypatch.setattr(preflight.tempfile, 'TemporaryFile', denied)
    assert check(preflight.run_checks(local), 'output')['status'] == 'fail'


@pytest.mark.parametrize('policy,status', [(None, 'warning'), (True, 'fail'), (False, 'pass')])
def test_subtitle_policy(local, monkeypatch, policy, status):
    monkeypatch.setattr(preflight, 'burn_method', lambda **kw: None)
    assert check(preflight.run_checks(local, burn_subs=policy), 'subtitles')['status'] == status


@pytest.mark.parametrize('backend,key', [('openai', 'OPENAI_API_KEY'), ('elevenlabs', 'ELEVENLABS_API_KEY')])
def test_paid_credentials(local, monkeypatch, backend, key):
    monkeypatch.setitem(preflight.os.environ, key, '   ')
    assert not preflight.run_checks(local, backend=backend)['ready']
    monkeypatch.setitem(preflight.os.environ, key, 'SECRET-SENTINEL')
    report = preflight.run_checks(local, backend=backend)
    assert report['ready'] and report['selected_backend'] == backend
    assert 'SECRET-SENTINEL' not in json.dumps(report)


def test_backend_precedence(local, monkeypatch):
    monkeypatch.setitem(preflight.os.environ, 'OPENAI_API_KEY', 'SECRET')
    assert preflight.run_checks(local)['selected_backend'] == 'say'
    assert preflight.run_checks(local, backend='openai')['selected_backend'] == 'openai'
    spec = json.loads(local.read_text()); del spec['voice']; local.write_text(json.dumps(spec))
    assert preflight.run_checks(local)['selected_backend'] == 'openai'


def test_kokoro_only_probes_uv(local):
    report = preflight.run_checks(local, backend='kokoro')
    assert report['ready']
    assert 'download' in check(report, 'kokoro')['message']


def test_say_voice(local):
    spec = json.loads(local.read_text()); spec['voice']['voice'] = 'Missing'; local.write_text(json.dumps(spec))
    assert check(preflight.run_checks(local), 'voice')['status'] == 'fail'


@pytest.mark.parametrize('seconds,status', [(30, 'pass'), (33, 'pass'), (33.01, 'warning'), (45, 'warning')])
def test_duration_target(local, seconds, status):
    spec = json.loads(local.read_text()); spec['target_duration_s'] = 30
    spec['scenes'][0]['narration'] = ' '.join(['word'] * 100); local.write_text(json.dumps(spec))
    durations = local.parent / 'durations.json'
    durations.write_text(json.dumps({'export': seconds, 'unrelated': 'ignored'}))
    report = preflight.run_checks(local, durations=durations)
    assert report['ready']
    assert check(report, 'duration')['status'] == status
    assert 'narration' in check(report, 'duration')['message']


@pytest.mark.parametrize('value', [None, True, False, 0, -1, '30', float('nan'), float('inf')])
def test_invalid_duration(local, value):
    durations = local.parent / 'durations.json'; durations.write_text(json.dumps({'export': value}))
    assert not preflight.run_checks(local, durations=durations)['ready']


@pytest.mark.parametrize('content', ['{}', '[]', '{broken'])
def test_bad_duration_file(local, content):
    durations = local.parent / 'durations.json'; durations.write_text(content)
    assert check(preflight.run_checks(local, durations=durations), 'duration')['status'] == 'fail'


def test_missing_duration_file(local):
    assert not preflight.run_checks(local, durations=local.parent / 'missing')['ready']


def test_duration_without_target(local):
    durations = local.parent / 'durations.json'; durations.write_text('{"export": 45}')
    result = check(preflight.run_checks(local, durations=durations), 'duration')
    assert result['status'] == 'pass' and '45' in result['message']
    assert 'no target' in result['message']


@pytest.mark.parametrize('seconds,status', [(1.2, 'pass'), (6, 'pass'), (6.01, 'fail'), (655, 'fail')])
def test_pace_flags_runaway_scene_without_target(local, seconds, status):
    durations = local.parent / 'durations.json'; durations.write_text(json.dumps({'export': seconds}))
    report = preflight.run_checks(local, durations=durations)
    result = check(report, 'pace')
    assert result['status'] == status and report['ready'] == (status == 'pass')
    if status == 'fail':
        assert 'export' in result['message'] and '3 words' in result['message']
        assert 'resynthesize' in result['remedy']
