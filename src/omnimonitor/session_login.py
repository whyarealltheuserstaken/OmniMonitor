"""In-app sign-in: a pywebview window on the provider's real login page.

The window runs in its own process (``omnimonitor login <pid>``) so the GUI owns that
process's main thread on every OS (macOS requires it). It uses a private, in-memory
cookie jar, separate from your browsers. When the page reaches a signed-in URL and a
required cookie is present, only the allow-listed cookies from the provider's own domains
are stored in the encrypted vault and the window closes.

The parent only sees the child's exit code. Cookie values are never printed or logged.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from omnimonitor import login_specs, sessions, vault

log = logging.getLogger("omnimonitor.session_login")

OK, CANCELLED, STORE_REFUSED, NO_WEBVIEW, FAILED = 0, 3, 4, 5, 6
_MESSAGES = {OK: "signed in", CANCELLED: "closed before sign-in finished",
             STORE_REFUSED: sessions.NO_VAULT_MESSAGE,
             NO_WEBVIEW: "the sign-in window is not available on this system"}
TIMEOUT_S = 300


class LoginError(Exception):
    pass


def message_for(code: Optional[int]) -> str:
    return _MESSAGES.get(code, "sign-in failed")  # type: ignore[arg-type]


def child_command(pid: str) -> List[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "login", pid]
    return [sys.executable, "-m", "omnimonitor", "login", pid]


def start_login(pid: str, on_done: Optional[Callable[[bool, str], None]] = None,
                popen: Callable = subprocess.Popen) -> str:
    """Start the sign-in window in a child process. ``on_done(ok, message)`` runs when it
    exits (the message never contains cookie values)."""
    if pid not in login_specs.SPECS or pid not in sessions.SESSION_PROVIDERS:
        raise LoginError("provider %s has no in-app sign-in" % pid)
    if not vault.available():
        raise LoginError(sessions.NO_VAULT_MESSAGE)
    kw: Dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                          "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = popen(child_command(pid), **kw)

    def wait() -> None:
        try:
            code = proc.wait(timeout=TIMEOUT_S + 30)
        except Exception:  # noqa: BLE001 - timeout or the child vanished
            code = FAILED
        log.info("sign-in for %s ended (exit %s)", pid, code)
        if on_done:
            try:
                on_done(code == OK, message_for(code))
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=wait, name="omnimonitor-login-%s" % pid, daemon=True).start()
    return "opening sign-in window"


def _cookie_tuples(raw: Iterable[Any]) -> List[Tuple[str, str, str]]:
    out = []
    for c in raw or []:
        get = (lambda k, c=c: c.get(k)) if isinstance(c, dict) else (
            lambda k, c=c: getattr(c, k, None))
        name, value = get("name"), get("value")
        if name and value is not None:
            out.append((str(name), str(value), str(get("domain") or "")))
    return out


def capture_header(spec: login_specs.LoginSpec, raw_cookies: Iterable[Any]) -> str:
    """Cookie header from the webview's cookies, strictly filtered; '' if not enough."""
    kept = login_specs.filter_cookies(spec, _cookie_tuples(raw_cookies))
    if not login_specs.enough(spec, kept):
        return ""
    return "; ".join("%s=%s" % (n, v) for n, v in kept)


_BEARER_JS = (
    "(function(){try{var k=Object.keys(localStorage);for(var i=0;i<k.length;i++){"
    "var v=localStorage.getItem(k[i])||'';"
    "if(v.indexOf('accessToken')>=0||k[i].toLowerCase().indexOf('access')>=0){"
    "var m=v.match(/\"accessToken\"\\s*:\\s*\"([^\"]+)\"/); if(m) return m[1];}}"
    "var s=sessionStorage.getItem('accessToken'); return s||'';}catch(e){return ''}})()")


def run_login(pid: str) -> int:
    """Child-process entry point (``omnimonitor login <pid>``). Returns an exit code."""
    spec = login_specs.SPECS.get(pid)
    if spec is None or pid not in sessions.SESSION_PROVIDERS:
        return FAILED
    if not vault.available():
        return STORE_REFUSED
    try:
        import webview  # type: ignore
    except ImportError:
        return NO_WEBVIEW

    state = {"code": CANCELLED}
    done = threading.Event()

    def check(window) -> None:
        if done.is_set():
            return
        try:
            url = window.get_current_url() or ""
        except Exception:  # noqa: BLE001
            return
        if not spec.signed_in(url):
            return
        try:
            header = capture_header(spec, window.get_cookies())
        except Exception:  # noqa: BLE001
            header = ""
        if not header:
            return
        meta: Dict[str, str] = {"source": "in-app"}
        if pid == "chatgpt_web":
            try:
                tok = window.evaluate_js(_BEARER_JS)
                if isinstance(tok, str) and tok.startswith("eyJ") and len(tok) < 8192:
                    meta["bearer"] = tok
            except Exception:  # noqa: BLE001
                pass
        try:
            sessions.save(pid, header, meta=meta)
            state["code"] = OK
        except sessions.SessionStoreError:
            state["code"] = STORE_REFUSED
        except Exception:  # noqa: BLE001
            state["code"] = FAILED
        done.set()
        try:
            window.destroy()
        except Exception:  # noqa: BLE001
            pass

    window = webview.create_window("OmniMonitor - sign in", spec.login_url,
                                   width=520, height=720)

    def poll(window) -> None:  # some webviews fire `loaded` only once
        start = time.time()
        while not done.is_set() and time.time() - start < TIMEOUT_S:
            check(window)
            time.sleep(1.0)
        if not done.is_set():
            done.set()
            try:
                window.destroy()
            except Exception:  # noqa: BLE001
                pass

    try:
        window.events.loaded += lambda: check(window)
    except Exception:  # noqa: BLE001
        pass
    try:
        webview.start(poll, window, private_mode=True)
    except Exception as e:  # noqa: BLE001
        log.info("sign-in window failed: %s", type(e).__name__)
        return FAILED if not done.is_set() else state["code"]
    return state["code"]
