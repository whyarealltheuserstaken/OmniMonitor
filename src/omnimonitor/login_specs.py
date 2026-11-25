"""One table describing every web sign-in: where to log in, which cookies to keep, and how
to tell that the window has reached a signed-in page.

Used by the in-app sign-in window (:mod:`omnimonitor.session_login`) and the browser
import (:mod:`omnimonitor.browser_cookies`). Pure data and filters, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Tuple
from urllib.parse import urlsplit

#: Paths that are part of a login flow, never a signed-in page.
LOGIN_PATHS = ("/login", "/log-in", "/auth", "/sign", "/oauth", "/u/login", "/api/auth")


def host_ok(host: str, allowed: Iterable[str]) -> bool:
    h = (host or "").lstrip(".").lower()
    return any(h == a.lstrip(".").lower() or h.endswith("." + a.lstrip(".").lower())
               for a in allowed)


def _signed_in(app_hosts: Tuple[str, ...]) -> Callable[[str], bool]:
    def check(url: str) -> bool:
        try:
            u = urlsplit(url or "")
        except ValueError:
            return False
        if u.scheme != "https" or not host_ok(u.hostname or "", app_hosts):
            return False
        path = (u.path or "/").lower()
        return not any(path.startswith(p) for p in LOGIN_PATHS)
    return check


@dataclass(frozen=True)
class LoginSpec:
    pid: str
    login_url: str
    hosts: Tuple[str, ...]          # cookie domains we accept
    cookie_names: Tuple[str, ...]   # only these cookies are kept
    required: Tuple[str, ...]       # at least ONE of these must be kept (empty = any cookie)
    signed_in: Callable[[str], bool]
    webview_ok: bool = True         # False: the provider blocks embedded sign-in


SPECS: Dict[str, LoginSpec] = {
    # Domain claude.ai, cookie sessionKey (sk-ant-…)
    "claude_plan": LoginSpec(
        "claude_plan", "https://claude.ai/login", ("claude.ai",), ("sessionKey",),
        ("sessionKey",), _signed_in(("claude.ai",))),
    # chatgpt.com / openai.com; a short allow-list of session cookies
    "chatgpt_web": LoginSpec(
        "chatgpt_web", "https://chatgpt.com/auth/login",
        ("chatgpt.com", "openai.com", "auth.openai.com"),
        ("__Secure-next-auth.session-token", "next-auth.session-token",
         "__Secure-authjs.session-token", "authjs.session-token",
         "oai-did", "_account", "cf_clearance", "__cf_bm",
         "__Secure-next-auth.callback-url", "__Host-next-auth.csrf-token",
         "session-token"),
        (), _signed_in(("chatgpt.com",))),
    # WorkosCursorSessionToken and next-auth variants
    "cursor": LoginSpec(
        "cursor", "https://authenticator.cursor.sh/",
        ("cursor.com", "www.cursor.com", "cursor.sh", "authenticator.cursor.sh"),
        ("WorkosCursorSessionToken", "__Secure-next-auth.session-token",
         "next-auth.session-token", "wos-session", "__Secure-wos-session",
         "authjs.session-token", "__Secure-authjs.session-token"),
        ("WorkosCursorSessionToken", "__Secure-next-auth.session-token",
         "next-auth.session-token"),
        _signed_in(("cursor.com",))),
}


def ids() -> Tuple[str, ...]:
    return tuple(SPECS)


def filter_cookies(spec: LoginSpec, cookies: Iterable[Tuple[str, str, str]]
                   ) -> List[Tuple[str, str]]:
    """Keep (name, value) only for allow-listed names from allow-listed domains. A cookie
    without a domain (some webviews omit it) is kept only if its name is required."""
    out: List[Tuple[str, str]] = []
    for name, value, domain in cookies:
        if not name or value is None or name not in spec.cookie_names:
            continue
        if domain:
            if not host_ok(domain, spec.hosts):
                continue
        elif name not in spec.required:
            continue
        out.append((name, value))
    return out


def enough(spec: LoginSpec, kept: List[Tuple[str, str]]) -> bool:
    """Did sign-in capture a usable session? One required cookie is enough."""
    names = {n for n, _ in kept}
    return any(r in names for r in spec.required) if spec.required else bool(kept)
