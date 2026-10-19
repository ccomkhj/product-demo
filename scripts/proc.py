"""Run an external tool (ffmpeg, ffprobe, say, rsvg-convert) for the CLIs (stdlib only)."""

import subprocess

STDERR_LINES = 5  # how much of a failing tool's stderr goes into the error message


def run(cmd, *, error, cwd=None):
    """Run ``cmd`` and return its stdout.

    Raises ``error`` (the calling CLI's exception class) when the tool is not on
    PATH or exits non-zero, with the exit code and the end of its stderr.
    """
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    except FileNotFoundError as exc:
        raise error(f"{cmd[0]} not found on PATH") from exc
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-STDERR_LINES:])
        raise error(f"{cmd[0]} failed (exit {result.returncode}): {tail}")
    return result.stdout
