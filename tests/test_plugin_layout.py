"""The repository installs as the Claude Code plugin `product-demo` from its own marketplace."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def read_json(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def skill_front_matter(name):
    text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n", text, flags=re.DOTALL)
    assert match, f"skills/{name}/SKILL.md has no front matter"
    return dict(line.split(": ", 1) for line in match.group(1).splitlines())


def instruction_docs():
    return [*ROOT.glob("skills/*/SKILL.md"), *ROOT.glob("references/**/*.md")]


def test_plugin_manifest_names_the_plugin_without_a_fixed_version():
    manifest = read_json(".claude-plugin/plugin.json")

    assert manifest["name"] == "product-demo"
    assert "version" not in manifest


def test_marketplace_offers_the_plugin_from_this_repository():
    marketplace = read_json(".claude-plugin/marketplace.json")

    [plugin] = [entry for entry in marketplace["plugins"] if entry["name"] == "product-demo"]
    assert plugin["source"] == "./"
    assert "version" not in plugin


def test_run_skill_replaces_the_root_skill():
    assert skill_front_matter("run")["name"] == "run"
    assert not (ROOT / "SKILL.md").exists()


def test_instructions_refer_to_the_plugin_root_not_a_placeholder():
    for doc in instruction_docs():
        assert "<skill dir>" not in doc.read_text(encoding="utf-8"), doc
    assert "--project ${CLAUDE_PLUGIN_ROOT}" in (ROOT / "skills/run/SKILL.md").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
def test_claude_plugin_validate_passes():
    result = subprocess.run(["claude", "plugin", "validate", str(ROOT)],
                            capture_output=True, text=True, timeout=120)

    assert result.returncode == 0, result.stdout + result.stderr
