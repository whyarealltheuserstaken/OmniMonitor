"""TLS setup shared by every HTTPS call.

Frozen builds (PyInstaller) and python.org Pythons on macOS have no usable system CA store,
which shows up as CERTIFICATE_VERIFY_FAILED. Use certifi's bundle when it is available,
otherwise fall back to the platform default. Verification is never turned off.
"""

from __future__ import annotations

import os
import ssl
from typing import Optional, Tuple

_CTX: Optional[ssl.SSLContext] = None
_SOURCE = ""


def _certifi_file() -> Optional[str]:
    try:
        import certifi
    except Exception:  # noqa: BLE001
        return None
    path = certifi.where()
    return path if path and os.path.isfile(path) else None


def ca_source() -> Tuple[str, str]:
    """(kind, detail): kind is "env", "certifi" or "system"."""
    env_file = os.environ.get("SSL_CERT_FILE")
    if env_file and os.path.isfile(env_file):
        return "env", env_file
    cf = _certifi_file()
    if cf:
        return "certifi", cf
    paths = ssl.get_default_verify_paths()
    return "system", paths.cafile or paths.capath or "(platform default)"


def ssl_context() -> ssl.SSLContext:
    global _CTX, _SOURCE
    if _CTX is None:
        kind, detail = ca_source()
        if kind in ("env", "certifi"):
            ctx = ssl.create_default_context(cafile=detail)
        else:
            ctx = ssl.create_default_context()
        _CTX, _SOURCE = ctx, "%s (%s)" % (kind, detail)
    return _CTX


def reset_for_tests() -> None:
    global _CTX, _SOURCE
    _CTX, _SOURCE = None, ""


# ----------------------------------------------------------------------------- proxy
# ``proxy_url`` (config file / CLI only) wins; otherwise urllib's standard HTTPS_PROXY /
# HTTP_PROXY / NO_PROXY environment variables apply. Credentials are refused in the config
# (it is not a secrets file): use the environment variables for an authenticated proxy.
_PROXY = ""


def validate_proxy(raw: str) -> str:
    import urllib.parse
    raw = (raw or "").strip()
    if not raw:
        return ""
    u = urllib.parse.urlsplit(raw)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("proxy must be http://host:port or https://host:port")
    if u.username or u.password:
        raise ValueError("no credentials in proxy_url; set HTTPS_PROXY in the environment")
    if u.path not in ("", "/") or u.query or u.fragment:
        raise ValueError("proxy_url must be just scheme://host:port")
    try:
        _ = u.port
    except ValueError:
        raise ValueError("bad proxy port") from None
    return raw.rstrip("/")


def configure(cfg) -> None:
    global _PROXY
    try:
        _PROXY = validate_proxy(str((cfg or {}).get("proxy_url") or ""))
    except ValueError:
        _PROXY = ""


def proxy_url() -> str:
    return _PROXY


def build_opener(*handlers):
    """urllib opener with the app's CA bundle and proxy setting."""
    import urllib.request
    proxy = urllib.request.ProxyHandler({"https": _PROXY, "http": _PROXY}) if _PROXY \
        else urllib.request.ProxyHandler()  # env HTTPS_PROXY / NO_PROXY
    return urllib.request.build_opener(proxy, urllib.request.HTTPSHandler(context=ssl_context()),
                                       *handlers)
