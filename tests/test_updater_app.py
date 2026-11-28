"""The updater as the user meets it: settings, the tray item and the `update` command."""
import os

import pytest

from omnimonitor import config as config_mod
from omnimonitor import settings, updater

os.environ.setdefault("PYSTRAY_BACKEND", "dummy")


def rel(version="9.0.0", installable=True):
    return updater.Release(version, "https://github.com/page", "url", 1, "0" * 64, installable)


def test_update_settings_default_to_automatic_and_follow_the_build():
    assert config_mod.DEFAULT_CONFIG["update_check"] is True
    assert config_mod.DEFAULT_CONFIG["update_channel"] == "auto"
    assert settings.validate("update_channel", "beta", web=True) == "beta"
    assert settings.validate("update_check", "off", web=True) is False
    with pytest.raises(settings.SettingError):
        settings.validate("update_channel", "nightly", web=True)


def test_settings_page_has_both_controls():
    from pathlib import Path

    static = Path(updater.__file__).parent / "static"
    html = (static / "settings.html").read_text("utf-8")
    script = (static / "settings.js").read_text("utf-8")
    for name in ("update_check", "update_channel"):
        assert 'id="%s"' % name in html
        assert '"%s"' % name in script


@pytest.fixture
def tray(tmp_path, monkeypatch):
    pytest.importorskip("pystray")
    from omnimonitor.app import Controller
    from omnimonitor.tray import TrayApp

    monkeypatch.setenv("OMNIMONITOR_HOME", str(tmp_path))
    cfg = config_mod.load_config()
    cfg["source"] = "demo"
    app = TrayApp(Controller(cfg))
    app.notes, app.quits, app.opened = [], [], []
    monkeypatch.setattr(app, "_say", lambda title, body: app.notes.append(body))
    monkeypatch.setattr(app, "_on_quit", lambda *a: app.quits.append(True))
    monkeypatch.setattr("webbrowser.open", app.opened.append)
    yield app
    if app.server:
        app.server.stop()


def update_label(app):
    texts = [i.text for i in app.icon.menu.items if hasattr(i, "text") and i.visible]
    return [t for t in texts if "pdate" in t or "available" in t]


def test_tray_item_says_what_a_click_will_do(tray):
    assert update_label(tray) == ["Check for updates"]
    tray.updater.available = rel()
    assert update_label(tray) == ["Update to 9.0.0 and restart"]
    tray.updater.available = rel(installable=False)
    assert update_label(tray) == ["Version 9.0.0 available (download page)"]


def test_checking_by_hand_reports_the_result(tray, monkeypatch):
    monkeypatch.setattr(tray.updater, "refresh", lambda: None)
    tray._update_action()
    assert "up to date" in tray.notes[-1]

    def offline():
        raise updater.UpdateError("could not reach GitHub")

    monkeypatch.setattr(tray.updater, "refresh", offline)
    tray._update_action()
    assert "could not reach GitHub" in tray.notes[-1]
    assert tray.quits == []


def test_checking_never_installs_by_itself(tray, monkeypatch):
    monkeypatch.setattr(tray.updater, "refresh", rel)
    monkeypatch.setattr(updater, "update_app", lambda r: pytest.fail("needs a second click"))
    tray._update_action()
    assert "9.0.0" in tray.notes[-1]


def test_clicking_update_installs_then_quits(tray, monkeypatch):
    installed = []
    monkeypatch.setattr(updater, "update_app", installed.append)
    tray.updater.available = rel()
    tray._update_action()
    assert [r.version for r in installed] == ["9.0.0"]
    assert tray.quits == [True]


def test_a_failed_update_keeps_the_app_running_and_says_why(tray, monkeypatch):
    def broken(release):
        raise updater.UpdateError("the download does not match the signed release")

    monkeypatch.setattr(updater, "update_app", broken)
    tray.updater.available = rel()
    tray._update_action()
    assert "does not match" in tray.notes[-1]
    assert tray.quits == []


def test_a_notice_only_update_opens_the_download_page(tray, monkeypatch):
    monkeypatch.setattr(updater, "update_app", lambda r: pytest.fail("cannot self-update"))
    tray.updater.available = rel(installable=False)
    tray._update_action()
    assert tray.opened == ["https://github.com/page"]


def test_a_second_click_while_updating_does_nothing(tray, monkeypatch):
    calls = []

    def slow(release):
        calls.append(release)
        tray._update_action()  # a click arriving mid-update

    monkeypatch.setattr(updater, "update_app", slow)
    tray.updater.available = rel()
    tray._update_action()
    assert len(calls) == 1


@pytest.mark.parametrize("result, code, words", [
    (None, 0, "up to date"), (rel(), 0, "9.0.0 is available"),
    (updater.UpdateError("offline"), 1, "offline")])
def test_update_command(tmp_path, monkeypatch, capsys, result, code, words):
    from omnimonitor.cli import main

    def check(current, chan):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setenv("OMNIMONITOR_HOME", str(tmp_path))
    monkeypatch.setattr(updater, "check", check)
    assert main(["update"]) == code
    out = capsys.readouterr()
    assert words in out.out + out.err
