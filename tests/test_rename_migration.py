"""OmniMonitor was renamed from grok-usage-tray: old config dirs, env vars and autostart
entries must keep working or be cleaned up."""

import json
import os
import sys

import pytest

from omnimonitor import APP_ID, APP_NAME, LEGACY_APP_NAME, autostart
from omnimonitor import config as config_mod


def test_names():
    assert APP_NAME == "omnimonitor" and APP_ID == "com.omnimonitor.tray"
    assert LEGACY_APP_NAME == "grok-usage-tray"


@pytest.fixture
def linux_home(tmp_path, monkeypatch):
    if sys.platform in ("darwin", "win32"):
        pytest.skip("XDG layout only")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def test_default_dirs(linux_home):
    assert config_mod.default_config_dir() == linux_home / "omnimonitor"
    assert config_mod.legacy_config_dir() == linux_home / "grok-usage-tray"


def test_fresh_install_uses_new_dir(linux_home):
    assert config_mod.config_dir() == linux_home / "omnimonitor"
    assert not (linux_home / "omnimonitor").exists()  # nothing created just by asking


def test_legacy_dir_is_migrated(linux_home):
    old = linux_home / "grok-usage-tray"
    old.mkdir()
    (old / "config.json").write_text(json.dumps({"source": "grok_cli_billing"}))
    (old / "history.sqlite3").write_bytes(b"db")
    d = config_mod.config_dir()
    assert d == linux_home / "omnimonitor"
    assert json.loads((d / "config.json").read_text())["source"] == "grok_cli_billing"
    assert (d / "history.sqlite3").read_bytes() == b"db"
    assert (old / "config.json").exists()  # old dir left untouched
    if os.name == "posix":
        assert (d / "config.json").stat().st_mode & 0o777 == 0o600
    assert config_mod.load_config()["source"] == "grok_cli_billing"


def test_new_dir_wins_over_legacy(linux_home):
    (linux_home / "grok-usage-tray").mkdir()
    (linux_home / "grok-usage-tray" / "config.json").write_text('{"source": "command"}')
    (linux_home / "omnimonitor").mkdir()
    assert config_mod.config_dir() == linux_home / "omnimonitor"
    assert not (linux_home / "omnimonitor" / "config.json").exists()


def test_failed_copy_falls_back_to_legacy(tmp_path, monkeypatch):
    old = tmp_path / "old"
    old.mkdir()
    (old / "config.json").write_text("{}")

    def boom(*a, **k):
        raise OSError("read-only")

    monkeypatch.setattr(config_mod.shutil, "copytree", boom)
    assert config_mod.migrate_legacy_config(tmp_path / "new", old) == old
    assert not (tmp_path / "new").exists()


def test_env_vars_new_and_legacy(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_USAGE_TRAY_HOME", str(tmp_path / "legacy"))
    assert config_mod.config_dir() == tmp_path / "legacy"
    monkeypatch.setenv("OMNIMONITOR_HOME", str(tmp_path / "new"))
    assert config_mod.config_dir() == tmp_path / "new"
    monkeypatch.setenv("GROK_USAGE_TRAY_SOURCE", "command")
    assert config_mod.load_config(tmp_path / "none.json")["source"] == "command"
    monkeypatch.setenv("OMNIMONITOR_SOURCE", "demo")
    assert config_mod.load_config(tmp_path / "none.json")["source"] == "demo"


def test_desktop_entry_name():
    entry = autostart.desktop_entry(["/usr/bin/omnimonitor"])
    assert "Name=OmniMonitor\n" in entry
    assert autostart.linux_desktop_path().name == "omnimonitor.desktop"
    assert autostart.mac_plist_path().name == "com.omnimonitor.tray.plist"


def test_legacy_autostart_removed(linux_home):
    legacy = linux_home / "autostart" / "grok-usage-tray.desktop"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("[Desktop Entry]\n")
    ok, msg = autostart.install()
    assert ok and not legacy.exists() and "removed old entry" in msg
    assert (linux_home / "autostart" / "omnimonitor.desktop").exists()
    legacy.write_text("[Desktop Entry]\n")
    ok, msg = autostart.uninstall()
    assert ok and not legacy.exists()
    assert not (linux_home / "autostart" / "omnimonitor.desktop").exists()
