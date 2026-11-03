"""Tray / menu-bar UI via pystray (macOS, Windows, Linux AppIndicator/GTK/Xorg)."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import webbrowser
from typing import Optional

from omnimonitor import config as config_mod
from omnimonitor.app import Controller
from omnimonitor.icon import render_icon, tray_size
from omnimonitor.timeutil import fmt_local, fmt_until
from omnimonitor.webui import StatsServer


def open_path(path: str) -> None:
    """Open a file with the OS default app (no shell)."""
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-t", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:  # noqa: BLE001
        pass


def fmt_tokens(n) -> str:
    if n is None:
        return "n/a"
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            return "%.1f%s" % (n / div, suf)
    return str(int(n))


class TrayApp:
    def __init__(self, ctrl: Controller):
        import pystray  # lazy: lets CLI commands work without a GUI backend

        self.pystray = pystray
        self.ctrl = ctrl
        self.server: Optional[StatsServer] = None
        try:
            self.server = StatsServer(ctrl.stats, int(ctrl.cfg.get("web_port") or 0)).start()
        except Exception as e:  # noqa: BLE001
            ctrl.token_note = (ctrl.token_note + "; " if ctrl.token_note else "") + \
                "stats page unavailable: %s" % e
        self.icon = pystray.Icon("omnimonitor", render_icon(None, size=tray_size()),
                                 "OmniMonitor · Grok usage", menu=self._menu())
        ctrl.add_listener(self.update)

    # ------------------------------------------------------------- text helpers
    def _title(self) -> str:
        u, err = self.ctrl.usage, self.ctrl.error
        if u is None:
            return "Grok usage: %s" % (err or "loading…")
        t = "%s%d%% left" % ("DEMO · " if u.is_demo else "", round(u.remaining_percent))
        return t + (" (stale: %s)" % err if err else "")

    def _reset_line(self) -> str:
        u = self.ctrl.usage
        if u is None or u.resets_at is None:
            return "Resets: unknown"
        return "Resets %s (%s)" % (fmt_local(u.resets_at), fmt_until(u.resets_at))

    def _tokens_line(self) -> str:
        st = self.ctrl.stats()
        wk = (st.get("week_tokens") or {}).get("value")
        if wk is None:
            return "Tokens this week: not available"
        lim = st.get("weekly_limit") or {}
        if lim.get("value"):
            return "Tokens: %s of %s" % (fmt_tokens(wk), fmt_tokens(lim["value"]))
        if lim.get("estimate"):
            return "Tokens: %s of ≈%s (estimate)" % (fmt_tokens(wk),
                                                    fmt_tokens(lim["estimate"]["value"]))
        return "Tokens this week: %s (limit not exposed)" % fmt_tokens(wk)

    def _error_line(self) -> str:
        return "⚠ %s" % (self.ctrl.error or "")[:90]

    # ------------------------------------------------------------- menu
    def _menu(self):
        Item, Menu = self.pystray.MenuItem, self.pystray.Menu
        return Menu(
            Item(lambda _: self._title(), None, enabled=False),
            Item(lambda _: self._reset_line(), None, enabled=False),
            Item(lambda _: self._tokens_line(), None, enabled=False),
            Item(lambda _: self._error_line(), None, enabled=False,
                 visible=lambda _: bool(self.ctrl.error)),
            Menu.SEPARATOR,
            Item("Refresh Now", self._on_refresh),
            Item("Open Stats Page", self._on_stats, default=True,
                 enabled=lambda _: self.server is not None),
            Item("Open Grok Usage Page", self._on_usage_page),
            Item("Edit Config", self._on_config),
            Menu.SEPARATOR,
            Item("Quit", self._on_quit),
        )

    def _on_refresh(self, *_):
        threading.Thread(target=self.ctrl.refresh, daemon=True).start()

    def _on_stats(self, *_):
        if self.server:
            webbrowser.open(self.server.url)

    def _on_usage_page(self, *_):
        webbrowser.open(self.ctrl.cfg.get("usage_page_url") or config_mod.DEFAULT_USAGE_PAGE)

    def _on_config(self, *_):
        path = config_mod.ensure_config_file()
        open_path(str(path))

    def _on_quit(self, *_):
        self.ctrl.stop()
        if self.server:
            try:
                self.server.stop()
            except Exception:  # noqa: BLE001
                pass
        self.icon.stop()

    # ------------------------------------------------------------- updates
    def update(self) -> None:
        u, err = self.ctrl.usage, self.ctrl.error
        try:
            self.icon.icon = render_icon(u.remaining_percent if u else None,
                                         error=bool(err) and u is None,
                                         demo=bool(u and u.is_demo), size=tray_size())
            self.icon.title = self._title()[:120]  # Windows tooltip limit ~128
            self.icon.update_menu()
        except Exception:  # noqa: BLE001
            pass

    def run(self) -> None:
        def setup(icon):
            icon.visible = True
            self.ctrl.start_background()
        # macOS requires the event loop on the main thread: pystray.Icon.run() blocks here.
        self.icon.run(setup=setup)
