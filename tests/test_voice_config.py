import json
from pathlib import Path

import pytest

import voice_config

PHOTO = Path(__file__).resolve().parent.parent / "assets" / "avatars" / "huijo.png"
# Read at import, before the autouse fixture swaps in temporary folders.
SHIPPED_LEGACY_DIRS = voice_config.LEGACY_DIRS


def save_photo(directory):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "me.png").write_bytes(PHOTO.read_bytes())
    (directory / "config.json").write_text(json.dumps({"own_photo": {"image": "me.png"}}))


def test_registry_lives_under_xdg_config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    save_photo(tmp_path / "xdg" / "product-demo")
    assert voice_config.saved_photo() == (tmp_path / "xdg" / "product-demo" / "me.png").resolve()


def test_registry_falls_back_to_dot_config_in_home(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    save_photo(tmp_path / ".config" / "product-demo")
    assert (
        voice_config.saved_photo() == (tmp_path / ".config" / "product-demo" / "me.png").resolve()
    )


def test_old_registry_is_copied_once_and_kept(isolated_config):
    old = voice_config.LEGACY_DIRS[-1]
    save_photo(old)
    new = isolated_config / "config" / "product-demo"
    assert voice_config.saved_photo() == (new / "me.png").resolve()
    assert (old / "config.json").is_file() and (old / "me.png").is_file()

    (old / "config.json").write_text(json.dumps({"own_photo": {"image": "gone.png"}}))
    assert voice_config.saved_photo() == (new / "me.png").resolve()


def test_registry_of_an_old_skill_install_is_copied(tmp_path, monkeypatch, isolated_config):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(voice_config, "LEGACY_DIRS", SHIPPED_LEGACY_DIRS)
    save_photo(tmp_path / ".claude" / "skills" / "product-demo" / ".voices")
    new = isolated_config / "config" / "product-demo"
    assert voice_config.saved_photo() == (new / "me.png").resolve()


def test_first_old_registry_with_a_config_wins(isolated_config):
    empty, first, second = voice_config.LEGACY_DIRS
    empty.mkdir(parents=True)
    for old in (first, second):
        save_photo(old)
        (old / "from.txt").write_text(old.parent.name)
    new = isolated_config / "config" / "product-demo"
    assert voice_config.saved_photo() == (new / "me.png").resolve()
    assert (new / "from.txt").read_text() == first.parent.name


def test_no_old_registry_means_nothing_is_set_up(isolated_config):
    with pytest.raises(ValueError, match="own_photo needs setup"):
        voice_config.saved_photo()
    assert not (isolated_config / "config" / "product-demo").exists()
