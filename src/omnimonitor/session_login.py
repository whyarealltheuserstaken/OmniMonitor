"""In-app sign-in: a pywebview window on the provider's real login page.

The window keeps its own cookie jar (separate from the user's browsers). When the
page reaches a logged-in URL and the cookies we need are present, we save them via
:mod:`omnimonitor.sessions` and close the window. Cookie values are never logged.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from omnimonitor import browser_cookies, sessions

log = logging.getLogger("omnimonitor.session_login")

# provider -> (login URL, success URL substrings, required cookie names)
LOGIN_SPECS: Dict[str, Tuple[str, Tuple[str, ...], Tuple[str, ...]]] = {
    "claude_plan": (
        "https://claude.ai/login",
        ("claude.ai/", "claude.ai/chats", "claude.ai/settings", "claude.ai/new"),
        ("sessionKey",),
    ),
    "chatgpt_web": (
        "https://chatgpt.com/auth/login",
        ("chatgpt.com/", "chatgpt.com/c/", "chatgpt.com/?"),
        (),  # any chatgpt.com cookies; bearer may be captured via JS
    ),
    "cursor": (
        "https://authenticator.cursor.sh/",
        ("cursor.com/", "cursor.com/settings", "cursor.com/dashboard"),
        ("WorkosCursorSessionToken", "__Secure-next-auth.session-token",
         "next-auth.session-token"),
    ),
}


class LoginError(Exception):
    pass


def start_login(pid: str, on_done: Optional[Callable[[bool, str], None]] = None) -> str:
    """Open the in-app login window on a background thread. Returns a status string.

    ``on_done(ok, message)`` is called when the window closes (message never contains
    cookie values).
    """
    if pid not in LOGIN_SPECS:
        raise LoginError("provider %s has no in-app login" % pid)
    if pid not in sessions.SESSION_PROVIDERS:
        raise LoginError("provider %s cannot store a web session" % pid)

    def run() -> None:
        ok, msg = False, "login window unavailable"
        try:
            ok, msg = _run_window(pid)
        except Exception as e:  # noqa: BLE001
            ok, msg = False, "login failed (%s)" % type(e).__name__
            log.info("session login ended for %s: %s", pid, type(e).__name__)
        if on_done:
            try:
                on_done(ok, msg)
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=run, name="omnimonitor-login-%s" % pid, daemon=True).start()
    return "opening sign-in window"


def _run_window(pid: str) -> Tuple[bool, str]:
    url, success_bits, required = LOGIN_SPECS[pid]
    try:
        import webview  # type: ignore
    except ImportError as e:
        raise LoginError("pywebview is not installed on this platform") from e

    state = {"done": False, "ok": False, "msg": "closed before sign-in finished"}

    def _cookie_header(window) -> Tuple[str, Dict[str, str]]:
        """Build a Cookie header from the webview cookie store + optional JS bearer."""
        parts: List[str] = []
        meta: Dict[str, str] = {}
        try:
            # pywebview >=4: window.get_cookies() -> list of Cookie objects / http.cookiejar
            raw = window.get_cookies()
        except Exception:  # noqa: BLE001
            raw = []
        for c in raw or []:
            name = getattr(c, "name", None) or (c.get("name") if isinstance(c, dict) else None)
            value = getattr(c, "value", None) or (c.get("value") if isinstance(c, dict) else None)
            domain = getattr(c, "domain", None) or (c.get("domain") if isinstance(c, dict) else "")
            if not name or value is None:
                continue
            hosts, names = browser_cookies.PROVIDER_COOKIE_FILTER.get(pid, ((), ()))
            if hosts and not browser_cookies._host_ok(str(domain or ""), hosts):
                # some webviews omit domain; keep if name is in the allow-list
                if names and name not in names and required and name not in required:
                    continue
            if names and name not in names and required and name not in required:
                continue
            parts.append("%s=%s" % (name, value))
        # ChatGPT often needs a bearer from localStorage
        if pid == "chatgpt_web":
            try:
                tok = window.evaluate_js(
                    "(function(){try{var k=Object.keys(localStorage);for(var i=0;i<k.length;i++){"
                    "var v=localStorage.getItem(k[i])||'';"
                    "if(v.indexOf('accessToken')>=0||k[i].toLowerCase().indexOf('access')>=0){"
                    "var m=v.match(/\"accessToken\"\\s*:\\s*\"([^\"]+)\"/); if(m) return m[1];}}"
                    "var s=sessionStorage.getItem('accessToken'); return s||'';}catch(e){return ''}})()")
                if isinstance(tok, str) and tok.startswith("eyJ") and len(tok) < 8192:
                    meta["bearer"] = tok
            except Exception:  # noqa: BLE001
                pass
        return "; ".join(parts), meta

    def _enough(header: str) -> bool:
        if not header:
            return False
        if not required:
            return len(header) > 20
        low = header  # names are case-sensitive in Cookie headers
        return all(("%s=" % n) in low for n in required)

    def on_loaded():
        if state["done"]:
            return
        try:
            cur = window.get_current_url() or ""
        except Exception:  # noqa: BLE001
            cur = ""
        if not any(s in cur for s in success_bits):
            return
        header, meta = _cookie_header(window)
        if not _enough(header):
            return
        try:
            sessions.save(pid, header, meta=meta)
        except Exception as e:  # noqa: BLE001
            state.update(done=True, ok=False, msg="could not store session (%s)" % type(e).__name__)
            try:
                window.destroy()
            except Exception:  # noqa: BLE001
                pass
            return
        state.update(done=True, ok=True, msg="signed in")
        try:
            window.destroy()
        except Exception:  # noqa: BLE001
            pass

    window = webview.create_window(
        "OmniMonitor — sign in (%s)" % pid, url, width=520, height=720)
    # loaded event
    try:
        window.events.loaded += on_loaded
    except Exception:  # noqa: BLE001
        pass

    # Also poll: some webviews fire loaded only once
    def poll():
        for _ in range(300):  # ~5 minutes
            if state["done"]:
                return
            time.sleep(1.0)
            try:
                on_loaded()
            except Exception:  # noqa: BLE001
                pass
        if not state["done"]:
            state.update(done=True, ok=False, msg="sign-in timed out")
            try:
                window.destroy()
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=poll, daemon=True).start()
    # webview.start blocks; may already be started by the tray window — then use GUI loop
    try:
        if webview.windows and len(webview.windows) > 1:
            # another window already owns the loop; just wait
            while not state["done"]:
                time.sleep(0.2)
        else:
            webview.start()
    except Exception as e:  # noqa: BLE001
        if not state["done"]:
            return False, "could not open sign-in window (%s)" % type(e).__name__
    return bool(state["ok"]), str(state["msg"])
