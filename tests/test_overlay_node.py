"""Run the overlay's node tests (tests/overlay/*.test.mjs) under pytest.

They drive headless Chrome and skip themselves when Chrome is missing; this
wrapper skips when node itself is missing.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

OVERLAY_TESTS = Path(__file__).resolve().parent / "overlay"


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node on PATH")
def test_overlay_node_tests_pass():
    files = sorted(str(p) for p in OVERLAY_TESTS.glob("*.test.mjs"))
    assert files, f"no *.test.mjs files in {OVERLAY_TESTS}"

    # `node --test <dir>` does not pick up .mjs files here, so pass them by name.
    result = subprocess.run(["node", "--test", *files], capture_output=True, text=True,
                            timeout=300)

    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
