import sys

import pytest

from proc import run


class ToolFailed(RuntimeError):
    pass


def test_run_returns_stdout():
    assert run([sys.executable, "-c", "print('hi')"], error=ToolFailed) == "hi\n"


def test_missing_tool_raises_the_callers_error():
    with pytest.raises(ToolFailed, match="no-such-tool-xyz not found on PATH"):
        run(["no-such-tool-xyz"], error=ToolFailed)


def test_failing_tool_reports_exit_code_and_the_end_of_stderr():
    script = "import sys; sys.stderr.write(''.join(f'line {i}\\n' for i in range(20))); sys.exit(3)"

    with pytest.raises(ToolFailed) as excinfo:
        run([sys.executable, "-c", script], error=ToolFailed)

    message = str(excinfo.value)
    assert "exit 3" in message
    assert "line 19" in message
    assert "line 0\n" not in message


def test_run_uses_the_given_working_directory(tmp_path):
    out = run([sys.executable, "-c", "import os; print(os.getcwd())"], cwd=tmp_path,
              error=ToolFailed)

    assert out.strip() == str(tmp_path.resolve())
