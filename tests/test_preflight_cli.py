import json
from pathlib import Path
import subprocess
import sys

import pytest

CLI = Path(__file__).resolve().parents[1] / 'scripts' / 'preflight.py'


@pytest.fixture
def cli(tmp_path):
    spec = tmp_path / 'demo.json'
    spec.write_text(json.dumps({'voice': {'backend': 'say'}, 'scenes': [
        {'id': 'export', 'url': 'http://localhost', 'narration': 'Export a report.'}
    ]}))
    binaries = tmp_path / 'bin'; binaries.mkdir()
    for name in ('ffmpeg', 'ffprobe', 'say', 'uv'):
        tool = binaries / name
        tool.write_text(f'#!{sys.executable}\nprint(" ... subtitles V->V subtitles")\n')
        tool.chmod(0o755)
    def run(*args):
        return subprocess.run([sys.executable, str(CLI), str(spec), *args],
                              env={'PATH': str(binaries), 'OPENAI_API_KEY': 'SECRET-SENTINEL'},
                              capture_output=True, text=True)
    return spec, binaries, run


def test_cli_success_and_text(cli):
    spec, binaries, run = cli
    result = run('--json')
    assert result.returncode == 0 and not result.stderr
    report = json.loads(result.stdout)
    assert report['ready'] and report['selected_backend'] == 'say'
    result = run()
    assert 'Local preflight: ready' in result.stdout
    assert 'Chrome MCP' in result.stdout


def test_cli_missing_executable_has_remedy(cli):
    spec, binaries, run = cli
    (binaries / 'ffmpeg').unlink()
    result = run('--json')
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert any(c['id'] == 'ffmpeg' and c['remedy'] for c in report['checks'])
    assert 'Traceback' not in result.stderr


@pytest.mark.parametrize('content', ['{broken', '[]', '{}', '{"scenes":[null]}',
    '{"scenes":[{"id":[],"narration":"hi","url":"http://localhost"}]}'])
def test_cli_invalid_spec_is_json(cli, content):
    spec, binaries, run = cli
    spec.write_text(content)
    result = run('--json')
    assert result.returncode == 1
    assert not json.loads(result.stdout)['ready']
    assert not result.stderr


def test_cli_usage_error(cli):
    assert cli[2]('--backend', 'unknown').returncode == 2


def test_cli_never_prints_credentials(cli):
    result = cli[2]('--backend', 'openai', '--json')
    assert result.returncode == 0
    assert 'SECRET-SENTINEL' not in result.stdout + result.stderr


def test_cli_duration_warning_and_missing_file(cli):
    spec, binaries, run = cli
    data = json.loads(spec.read_text()); data['target_duration_s'] = 30
    data['scenes'][0]['narration'] = ' '.join(['word'] * 100); spec.write_text(json.dumps(data))
    durations = spec.parent / 'durations.json'; durations.write_text('{"export":45}')
    result = run('--json', '--durations', str(durations))
    assert result.returncode == 0
    assert any(c['id'] == 'duration' and c['status'] == 'warning' for c in json.loads(result.stdout)['checks'])
    durations.unlink()
    assert run('--json', '--durations', str(durations)).returncode == 1


def test_old_python_gets_actionable_json(monkeypatch, capsys):
    import runpy
    monkeypatch.setattr(sys, 'version_info', (3, 9, 0))
    monkeypatch.setattr(sys, 'argv', [str(CLI), 'demo.json', '--json'])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(CLI), run_name='__main__')
    assert exc.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert report['checks'][0]['id'] == 'python'
    assert '3.11' in report['checks'][0]['message']
