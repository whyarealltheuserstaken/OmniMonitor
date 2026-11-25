"""Tray menu logic, using pystray's headless 'dummy' backend (no GUI needed)."""

import os

import pytest

os.environ.setdefault("PYSTRAY_BACKEND", "dummy")
pystray = pytest.importorskip("pystray")

from omnimonitor import config as config_mod  # noqa: E402
from omnimonitor.app import Controller  # noqa: E402
from omnimonitor.tray import TrayApp, fmt_tokens  # noqa: E402


@pytest.fixture
def tray(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIMONITOR_HOME", str(tmp_path))
    cfg = config_mod.load_config()
    cfg["source"] = "demo"
    ctrl = Controller(cfg)
    app = TrayApp(ctrl)
    yield app
    if app.server:
        app.server.stop()


def labels(app):
    out = []
    for item in app.icon.menu.items:
        if item is pystray.Menu.SEPARATOR or not item.visible:
            continue
        out.append(item.text)
    return out


def test_menu_before_first_refresh(tray, monkeypatch):
    monkeypatch.setattr(tray, "_ov", lambda: {})  # nothing computed yet
    items = labels(tray)
    assert items[0] == "OmniMonitor: loading…"
    assert "Resets: unknown" in items
    for want in ("Refresh Now", "Open OmniMonitor", "Open in Browser", "Edit Config", "Quit"):
        assert want in items


def test_menu_after_demo_refresh_is_labeled(tray):
    tray.ctrl.refresh()
    tray.update()
    items = labels(tray)
    assert items[0].startswith("DEMO · ") and items[0].endswith("% left")
    assert items[1].startswith("Resets ")
    assert any(t.startswith("DEMO Grok & Cursor:") for t in items)  # one line per suite
    assert not any(t.startswith("Grok suite:") for t in items)  # no Grok-only block
    assert "Usage pages" in items and "Providers" in items
    assert tray.icon.title.startswith("DEMO")


def test_error_line_only_when_error(tray):
    assert not any(t.startswith("⚠") for t in labels(tray))
    tray.ctrl.error = "network down"
    assert "⚠ network down" in labels(tray)


def test_fmt_tokens():
    assert fmt_tokens(999) == "999"
    assert fmt_tokens(1_500) == "1.5K"
    assert fmt_tokens(2_000_000) == "2.0M"
