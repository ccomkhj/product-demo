import pytest

import voice_config


@pytest.fixture(autouse=True)
def isolated_config(tmp_path_factory, monkeypatch):
    """No test reads or writes the real ~/.config or an in-repo .voices registry."""
    root = tmp_path_factory.mktemp("user")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root / "config"))
    monkeypatch.setattr(
        voice_config,
        "LEGACY_DIRS",
        tuple(root / old / ".voices" for old in ("claude-skill", "agents-skill", "old-skill")),
    )
    return root
