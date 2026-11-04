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
