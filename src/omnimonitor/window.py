"""Native app window (pywebview) for the local stats UI.

The window always runs in a **separate process** (`omnimonitor window`): on macOS both the
tray (pystray/AppKit) and pywebview need the main thread, and touching AppKit off the main
thread is what crashed v0.3.0 (SIGTRAP). The tray process spawns the child and hands it the
private 127.0.0.1 URL over **stdin** (never argv, which other local users can read via `ps`).
If pywebview is unavailable or the child dies at startup, we fall back to the browser.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from typing import Callable, List, Optional

log = logging.getLogger(__name__)

TITLE = "OmniMonitor"
SIZE = (1180, 820)
MIN_SIZE = (720, 520)
STARTUP_GRACE_S = 4.0
_LOCAL_URL = re.compile(r"^http://(127\.0\.0\.1|localhost):\d{1,5}/s/[A-Za-z0-9_\-]+/[A-Za-z0-9_\-/.?=&]*$")


def valid_local_url(url: str) -> bool:
    """Only our own private loopback URLs may be opened in the window."""
    return bool(url) and len(url) < 512 and bool(_LOCAL_URL.match(url))


def webview_available() -> bool:
    if os.environ.get("OMNIMONITOR_NO_WINDOW"):
        return False
    try:
        return importlib.util.find_spec("webview") is not None
    except (ImportError, ValueError):
        return False


def child_command() -> List[str]:
    """Command that starts `omnimonitor window` with the same interpreter / frozen binary."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "window"]
    return [sys.executable, "-m", "omnimonitor", "window"]


class WindowLauncher:
    """Used by the tray. One window process at a time; re-opening focuses a new one."""

    def __init__(self, popen: Callable = subprocess.Popen,
                 browser: Callable[[str], object] = webbrowser.open,
                 available: Callable[[], bool] = webview_available,
                 grace_s: float = STARTUP_GRACE_S):
        self._popen, self._browser, self._available = popen, browser, available
        self._grace = grace_s
        self.proc = None
        self.last_mode = None  # "window" | "browser"

    def open(self, url: str) -> str:
        if not valid_local_url(url):
            raise ValueError("refusing to open a non-local URL")
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()  # simplest cross-platform "bring to front": reopen
            except OSError:
                pass
        if not self._available():
            return self._fallback(url, "pywebview not installed")
        try:
            kw = {}
            if sys.platform == "win32":
                kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = self._popen(child_command(), stdin=subprocess.PIPE,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
            proc.stdin.write((url + "\n").encode("ascii"))
            proc.stdin.close()
        except Exception as e:  # noqa: BLE001
            return self._fallback(url, "could not start window: %s" % e)
        self.proc, self.last_mode = proc, "window"
        threading.Thread(target=self._watch, args=(proc, url), daemon=True,
                         name="omnimonitor-window-watch").start()
        return "window"

    def _watch(self, proc, url: str) -> None:
        deadline = time.monotonic() + self._grace
        while time.monotonic() < deadline:
            rc = proc.poll()
            if rc is not None:
                if rc != 0:
                    self._fallback(url, "window process exited with %s" % rc)
                return
            time.sleep(0.1)

    def _fallback(self, url: str, why: str) -> str:
        log.info("app window unavailable (%s); opening the browser", why)
        self.last_mode = "browser"
        self._browser(url)
        return "browser"


# ---------------------------------------------------------------- child process side
def run_window(url: str, smoke: bool = False, timeout_s: float = 60.0) -> int:
    """Blocking; must run on the main thread of its own process."""
    import webview  # noqa: PLC0415

    win = webview.create_window(TITLE, url, width=SIZE[0], height=SIZE[1],
                                min_size=MIN_SIZE, text_select=True)
    result = {"rc": 1}
    if smoke:
        seen = {"shown": False, "loaded": False, "err": None, "title": None, "ready": None}
        win.events.shown += lambda *a: seen.__setitem__("shown", True)
        win.events.loaded += lambda *a: seen.__setitem__("loaded", True)

        def watchdog():
            # evaluate_js blocks until the page has loaded; never hang CI forever
            time.sleep(timeout_s + 15)
            print("WINDOW FAILED: watchdog timeout; state=%r" % seen, flush=True)
            os._exit(1)

        threading.Thread(target=watchdog, daemon=True).start()

        def check():
            t0 = time.monotonic()
            while time.monotonic() - t0 < timeout_s:
                if not seen["loaded"]:
                    time.sleep(0.5)
                    continue
                try:
                    seen["title"] = title = win.evaluate_js("document.title")
                    seen["ready"] = ready = win.evaluate_js("document.readyState")
                    if title and "OmniMonitor" in str(title) and ready == "complete":
                        print("WINDOW OK: %s" % title, flush=True)
                        result["rc"] = 0
                        break
                except Exception as e:  # noqa: BLE001
                    seen["err"] = repr(e)[:200]
                time.sleep(0.5)
            else:
                print("WINDOW FAILED: page did not load; state=%r" % seen, flush=True)
            win.destroy()
        webview.start(check, private_mode=True)
        return result["rc"]
    webview.start(private_mode=True)
    return 0


def read_url_from_stdin(stream=None) -> Optional[str]:
    stream = stream or sys.stdin
    try:
        line = stream.readline(600).strip()
    except (OSError, ValueError):
        return None
    return line if valid_local_url(line) else None
