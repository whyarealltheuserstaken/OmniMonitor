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
MINI_SIZE = (340, 540)
MINI_MIN_SIZE = (280, 320)
VIEWS = ("full", "mini")
STARTUP_GRACE_S = 4.0
_LOCAL_URL = re.compile(r"^http://(127\.0\.0\.1|localhost):\d{1,5}/s/[A-Za-z0-9_\-]+/[A-Za-z0-9_\-/.?=&]*$")


def valid_local_url(url: str) -> bool:
    """Only our own private loopback URLs may be opened in the window."""
    return bool(url) and len(url) < 512 and bool(_LOCAL_URL.match(url))


def app_url(base: str, view: str = "full", page: str = "") -> str:
    """URL for the native window: the mini page or a full-window page, flagged ?app=1 so the
    page routes downloads / external links / view switches through native app actions."""
    page = "mini" if view == "mini" else (page if re.match(r"^[a-z\-]{0,20}$", page or "") else "")
    return base + page + "?app=1"


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
    """Used by the tray. One native window at a time, either the full app or the compact mini
    view. Opening one view closes the other (switching never leaves you without a window);
    re-opening the same view restarts it, which also brings it to the front. The tray process
    and its stats server keep running whatever the window does."""

    def __init__(self, popen: Callable = subprocess.Popen,
                 browser: Callable[[str], object] = webbrowser.open,
                 available: Callable[[], bool] = webview_available,
                 grace_s: float = STARTUP_GRACE_S):
        self._popen, self._browser, self._available = popen, browser, available
        self._grace = grace_s
        self.proc = None
        self.view: Optional[str] = None
        self.last_mode = None  # "window" | "browser"
        self._lock = threading.Lock()

    def close(self) -> None:
        proc = self.proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    def open(self, url: str, view: str = "full", on_top: bool = False) -> str:
        if not valid_local_url(url):
            raise ValueError("refusing to open a non-local URL")
        view = view if view in VIEWS else "full"
        with self._lock:
            self.close()  # simplest cross-platform "bring to front" / view switch: reopen
            if not self._available():
                return self._fallback(url, "pywebview not installed")
            try:
                kw = {}
                if sys.platform == "win32":
                    kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                proc = self._popen(child_command(), stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
                opts = '{"view": "%s", "on_top": %s}' % (view, "true" if on_top else "false")
                proc.stdin.write((url + "\n" + opts + "\n").encode("ascii"))
                proc.stdin.close()
            except Exception as e:  # noqa: BLE001
                return self._fallback(url, "could not start window: %s" % e)
            self.proc, self.view, self.last_mode = proc, view, "window"
        log.info("app window process started pid=%s view=%s", getattr(proc, "pid", "?"), view)
        threading.Thread(target=self._watch, args=(proc, url), daemon=True,
                         name="omnimonitor-window-watch").start()
        return "window"

    def _watch(self, proc, url: str) -> None:
        deadline = time.monotonic() + self._grace
        while time.monotonic() < deadline:
            rc = proc.poll()
            if rc is not None:
                log.info("app window process exited rc=%s", rc)
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
def window_geometry(view: str = "full") -> dict:
    if view == "mini":
        return {"width": MINI_SIZE[0], "height": MINI_SIZE[1], "min_size": MINI_MIN_SIZE}
    return {"width": SIZE[0], "height": SIZE[1], "min_size": MIN_SIZE}


def run_window(url: str, smoke: bool = False, timeout_s: float = 60.0,
               served: Optional[threading.Event] = None, view: str = "full",
               on_top: bool = False) -> int:
    """Blocking; must run on the main thread of its own process.

    Smoke mode never injects JS (the page CSP forbids eval, which pywebview's evaluate_js
    needs); it passes when the window fired `loaded` and the page's own script fetched
    api/stats from the server (`served`), proving the dashboard ran under its real CSP."""
    import webview  # noqa: PLC0415

    title = TITLE + (" · Mini" if view == "mini" else "")
    win = webview.create_window(title, url, text_select=True, on_top=bool(on_top),
                                **window_geometry(view))
    result = {"rc": 1}
    if smoke:
        seen = {"shown": False, "loaded": False, "served": False}
        win.events.shown += lambda *a: seen.__setitem__("shown", True)
        win.events.loaded += lambda *a: seen.__setitem__("loaded", True)

        def watchdog():
            # evaluate_js blocks until the page has loaded; never hang CI forever
            time.sleep(timeout_s + 10)
            print("WINDOW FAILED: watchdog timeout; state=%r" % seen, flush=True)
            os._exit(1)

        threading.Thread(target=watchdog, daemon=True).start()

        def check():
            t0 = time.monotonic()
            while time.monotonic() - t0 < timeout_s:
                seen["served"] = bool(served is not None and served.is_set())
                if seen["loaded"] and seen["served"]:
                    print("WINDOW OK: dashboard loaded and fetched api/stats", flush=True)
                    result["rc"] = 0
                    break
                time.sleep(0.5)
            else:
                print("WINDOW FAILED: page did not load; state=%r" % seen, flush=True)
            win.destroy()
        webview.start(check, private_mode=True)
        return result["rc"]
    webview.start(private_mode=True)
    return 0


def read_url_from_stdin(stream=None) -> Optional[str]:
    return read_request_from_stdin(stream)[0]


def read_request_from_stdin(stream=None):
    """-> (url or None, {"view": "full"|"mini", "on_top": bool}). Line 2 is optional."""
    import json
    stream = stream or sys.stdin
    opts = {"view": "full", "on_top": False}
    try:
        line = stream.readline(600).strip()
        extra = stream.readline(200).strip()
    except (OSError, ValueError):
        return None, opts
    if extra:
        try:
            o = json.loads(extra)
            if isinstance(o, dict):
                if o.get("view") in VIEWS:
                    opts["view"] = o["view"]
                opts["on_top"] = o.get("on_top") is True
        except ValueError:
            pass
    return (line if valid_local_url(line) else None), opts


def _wait(pred, timeout: float) -> bool:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.2)
    return False


def switch_smoke(srv, ctrl, timeout_s: float = 60.0, launcher: Optional[WindowLauncher] = None,
                 post: Optional[Callable[[str, dict], dict]] = None) -> int:
    """Tray-equivalent smoke (this process plays the tray): open the mini window as a real
    child process, wait until its page fetched api/stats, then ask for the full window through
    the same CSRF-protected POST api/app/window the page's "Full window" link uses, and check
    that the full window loads while this process and the stats server stay alive."""
    import json
    import urllib.request

    from omnimonitor import appactions
    browser_calls: List[str] = []
    wl = launcher or WindowLauncher(browser=browser_calls.append)
    srv.app_actions.update(appactions.window_actions(wl, lambda: srv.url, ctrl.cfg.get,
                                                     browser=browser_calls.append))

    def _post(action: str, body: dict) -> dict:
        req = urllib.request.Request(srv.url + "api/app/" + action, method="POST",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "X-OmniMonitor-CSRF": srv.csrf})
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - our 127.0.0.1 URL
            return json.loads(r.read())
    post = post or _post

    def alive() -> bool:
        try:
            with urllib.request.urlopen(srv.url + "api/stats", timeout=5) as r:  # noqa: S310
                return r.status == 200
        except Exception:  # noqa: BLE001
            return False

    def fail(msg: str) -> int:
        print("SWITCH FAILED: %s" % msg, flush=True)
        wl.close()
        return 1

    srv.stats_served.clear()
    res = post("window", {"view": "mini"})
    if res.get("mode") != "window" or wl.view != "mini":
        return fail("mini did not open as a native window: %r browser=%r" % (res, browser_calls))
    mini = wl.proc
    if not _wait(srv.stats_served.is_set, timeout_s):
        return fail("mini page never fetched api/stats")
    if mini.poll() is not None:
        return fail("mini window exited rc=%s" % mini.poll())
    print("SWITCH: mini window up (pid %s); server alive=%s" % (mini.pid, alive()), flush=True)
    srv.stats_served.clear()
    res = post("window", {"view": "full"})
    if res.get("mode") != "window" or wl.view != "full":
        return fail("full did not open as a native window: %r" % (res,))
    if not _wait(lambda: mini.poll() is not None, 15):
        return fail("mini window was not closed when switching to full")
    if not _wait(srv.stats_served.is_set, timeout_s):
        return fail("full window never fetched api/stats")
    if wl.proc.poll() is not None:
        return fail("full window exited rc=%s" % wl.proc.poll())
    if not alive():
        return fail("stats server stopped answering")
    if browser_calls:
        return fail("fell back to the browser: %r" % browser_calls)
    print("SWITCH OK: mini -> full stayed native; parent and stats server alive", flush=True)
    wl.close()
    return 0
