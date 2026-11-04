"""Tray / menu-bar UI via pystray (macOS, Windows, Linux AppIndicator/GTK/Xorg)."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Optional

from omnimonitor import config as config_mod
from omnimonitor import overview as overview_mod
from omnimonitor.app import Controller
from omnimonitor.applog import log
from omnimonitor.icon import render_icon, tray_size
from omnimonitor.timeutil import fmt_local, fmt_until
from omnimonitor.webui import StatsServer, for_controller
from omnimonitor.window import WindowLauncher


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


def on_main_thread(fn) -> None:
    """Run fn on the GUI thread. AppKit (NSStatusItem / NSMenu / NSView) must only be touched
    from the main thread; doing it from a worker thread trips an AppKit assertion that kills
    the process with SIGTRAP ("trace trap"). Other backends are thread-safe enough in pystray.
    """
    if sys.platform == "darwin" and threading.current_thread() is not threading.main_thread():
        from PyObjCTools import AppHelper
        AppHelper.callAfter(_logged, fn)
    else:
        _logged(fn)


def _logged(fn) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001
        log.exception("tray update failed")


FIRST_RUN_MARKER = "first-run-done"


class TrayApp:
    def __init__(self, ctrl: Controller):
        self.window = WindowLauncher()
        import pystray  # lazy: lets CLI commands work without a GUI backend

        self.pystray = pystray
        self.ctrl = ctrl
        self.server: Optional[StatsServer] = None
        try:
            self.server = for_controller(ctrl, int(ctrl.cfg.get("web_port") or 0)).start()
        except Exception as e:  # noqa: BLE001
            ctrl.token_note = (ctrl.token_note + "; " if ctrl.token_note else "") + \
                "stats page unavailable: %s" % e
        self.icon = pystray.Icon("omnimonitor", render_icon(None, size=tray_size()),
                                 "OmniMonitor · AI usage", menu=self._menu())
        ctrl.add_listener(self.update)
        ctrl.notifier = self._notify_os

    def _notify_os(self, body: str, title: str) -> None:
        if not getattr(self.icon, "HAS_NOTIFICATION", False):
            raise RuntimeError("tray backend has no notifications")
        self.icon.notify(body, title)

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

    def _pace_line(self) -> str:
        avg = (self.ctrl.stats().get("forecast") or {}).get("average") or {}
        if avg.get("value", 0) is None or "percent_at_reset" not in avg:
            return "Pace: not available"
        p = avg["percent_at_reset"]
        if avg.get("runs_out_at"):
            from omnimonitor.timeutil import parse_time
            return "Pace: runs out %s (estimate)" % fmt_local(parse_time(avg["runs_out_at"]))
        return "Pace: ≈%.0f%% used at reset (estimate)" % min(p, 999)

    def _today_line(self) -> str:
        br = (self.ctrl.stats().get("breakdowns") or {}).get("today")
        if not br:
            return "Today: not available"
        tools = ", ".join("%s %s" % (t["name"], fmt_tokens(t["tokens"]))
                          for t in br["tools"][:3])
        return ("Today: %s tokens" % fmt_tokens(br["tokens"])) + (" (%s)" % tools
                                                               if len(br["tools"]) > 1 else "")

    def _ov(self):
        return self.ctrl.stats().get("providers") or {}

    def _provider_line(self, i: int) -> str:
        lines = overview_mod.menu_lines(self._ov())
        return lines[i] if i < len(lines) else ""

    def _provider_item(self, i: int):
        def text(_):
            return self._provider_line(i)

        def shown(_):
            return bool(self._provider_line(i))
        return self.pystray.MenuItem(text, None, enabled=False, visible=shown)

    def _primary_line(self) -> str:
        p = self._ov().get("primary") or {}
        if not p or p.get("id") == "grok":
            return ""
        if p.get("available"):
            return "Tray: %s %.0f%% used" % (p["label"], p["used_percent"])
        return "Tray: %s not available (%s)" % (p.get("name"), (p.get("reason") or "")[:50])

    def _error_line(self) -> str:
        return "⚠ %s" % (self.ctrl.error or "")[:90]

    # ------------------------------------------------------------- menu
    def _menu(self):
        Item, Menu = self.pystray.MenuItem, self.pystray.Menu
        return Menu(
            Item(lambda _: self._title(), None, enabled=False),
            Item(lambda _: self._reset_line(), None, enabled=False),
            Item(lambda _: self._tokens_line(), None, enabled=False),
            Item(lambda _: self._today_line(), None, enabled=False),
            Item(lambda _: self._pace_line(), None, enabled=False),
            Item(lambda _: self._error_line(), None, enabled=False,
                 visible=lambda _: bool(self.ctrl.error)),
            Item(lambda _: self._primary_line(), None, enabled=False,
                 visible=lambda _: bool(self._primary_line())),
            Menu.SEPARATOR,
            *[self._provider_item(i) for i in range(8)],
            Menu.SEPARATOR,
            Item("Refresh Now", self._on_refresh),
            Item("Open OmniMonitor", self._on_stats, default=True,
                 enabled=lambda _: self.server is not None),
            Item("Open in Browser", self._on_browser,
                 enabled=lambda _: self.server is not None),
            Item("Open Mini View", self._page("mini"),
                 enabled=lambda _: self.server is not None),
            Item("Settings…", self._page("settings"),
                 enabled=lambda _: self.server is not None),
            Item("Export", Menu(
                Item("Daily CSV", self._export("csv", "daily")),
                Item("Per model CSV", self._export("csv", "daily-models")),
                Item("Tool / model / project CSV", self._export("csv", "breakdown")),
                Item("Providers CSV", self._export("csv", "providers")),
                Item("Everything (JSON)", self._export("json", "daily")))),
            Item("Open Grok Usage Page", self._on_usage_page),
            Item("Edit Config", self._on_config),
            Menu.SEPARATOR,
            Item("Quit", self._on_quit),
        )

    def _on_refresh(self, *_):
        log.info("Refresh Now clicked")
        threading.Thread(target=self.ctrl.refresh, daemon=True).start()

    def _open(self, rel: str = "") -> None:
        """App window in its own process (macOS main-thread rule), else the browser."""
        if not self.server:
            return
        try:
            self.window.open(self.server.url + rel)
        except Exception:  # noqa: BLE001
            log.exception("open window")
            webbrowser.open(self.server.url + rel)

    def _on_stats(self, *_):
        self._open("")

    def _on_browser(self, *_):
        if self.server:
            webbrowser.open(self.server.url)

    def _page(self, rel: str):
        def go(*_):
            self._open(rel)
        return go

    def _export(self, fmt: str, kind: str):
        def go(*_):
            try:
                body, _ct, fname = self.ctrl.export(kind, fmt)
                folder = Path.home() / "Downloads"
                folder = folder if folder.is_dir() else Path.home()
                out = folder / fname
                out.write_bytes(body)
                open_path(str(folder))
            except Exception as e:  # noqa: BLE001
                self.ctrl.token_note = "export failed: %s" % e
        return go

    def _on_usage_page(self, *_):
        webbrowser.open(self.ctrl.cfg.get("usage_page_url") or config_mod.DEFAULT_USAGE_PAGE)

    def _on_config(self, *_):
        path = config_mod.ensure_config_file()
        open_path(str(path))

    def _on_quit(self, *_):
        self.ctrl.stop()
        proc = self.window.proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        if self.server:
            try:
                self.server.stop()
            except Exception:  # noqa: BLE001
                pass
        self.icon.stop()

    # ------------------------------------------------------------- updates
    def update(self) -> None:
        """Controller listener (called from the poll thread): hop to the GUI thread."""
        on_main_thread(self._apply_update)

    def _apply_update(self) -> None:
        u, err = self.ctrl.usage, self.ctrl.error
        try:
            ov = self._ov()
            value, _label = overview_mod.tray_value(ov, u.to_dict() if u else None)
            self.icon.icon = render_icon(value, error=bool(err) and value is None,
                                         demo=bool((u and u.is_demo) or ov.get("is_demo")),
                                         size=tray_size(),
                                         style=self.ctrl.cfg.get("icon_style") or "ring",
                                         show=self.ctrl.cfg.get("icon_value") or "remaining")
            # compact "today" tooltip (Windows limit ~128 chars)
            self.icon.title = overview_mod.tooltip(ov, self._title())[:120]
            self.icon.update_menu()
        except Exception:  # noqa: BLE001
            log.exception("tray icon update failed")

    def _show(self) -> None:
        self.icon.visible = True
        log.info("tray icon visible (backend %s)", type(self.icon).__module__)

    def first_run_hint(self) -> bool:
        """First launch only: menu-bar apps have no window, and on notched Macs the icon can be
        hidden behind the notch, so say where it is and open the stats page once."""
        try:
            marker = config_mod.config_dir() / FIRST_RUN_MARKER
            if marker.exists():
                return False
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("1\n", encoding="utf-8")
        except OSError:
            log.exception("first-run marker")
            return False
        from omnimonitor.alerts import deliver
        where = "menu bar (top right; on a notched Mac it can hide behind the notch)" \
            if sys.platform == "darwin" else "system tray"
        deliver("OmniMonitor is running", "Look for the ring icon in the %s." % where)
        if self.server and not config_mod.env("NO_BROWSER"):
            self._open("")
        log.info("first run: hint shown")
        return True

    def run(self) -> None:
        def setup(icon):
            # pystray calls this on a worker thread; AppKit calls must go to the main thread.
            on_main_thread(self._show)
            self.ctrl.start_background()
            self.first_run_hint()
        log.info("starting tray loop on %s", threading.current_thread().name)
        # macOS requires the event loop on the main thread: pystray.Icon.run() blocks here.
        self.icon.run(setup=setup)
        log.info("tray loop ended")
