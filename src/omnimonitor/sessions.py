"""Local storage for provider web-session cookies.

Cookies are NEVER logged, exported, shown in the UI, or included in diagnostics.
Preferred store: the OS keyring (via the optional ``keyring`` package). Fallback:
a chmod-600 file under the config dir. Callers only ever see a Cookie header string
in memory for the duration of one HTTPS request.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Dict, Optional

from omnimonitor import config as config_mod

# Providers that may store a web session. Keep in sync with catalog.WEB_SESSION_IDS.
SESSION_PROVIDERS = ("claude_plan", "chatgpt_web", "cursor")

_SERVICE = "omnimonitor.web-session"


def _keyring():
    try:
        import keyring  # type: ignore
        return keyring
    except Exception:  # noqa: BLE001
        return None


def sessions_dir() -> Path:
    d = config_mod.config_dir() / "sessions"
    config_mod.secure_mkdir(d)
    return d


def _file(pid: str) -> Path:
    if pid not in SESSION_PROVIDERS:
        raise ValueError("unknown session provider")
    return sessions_dir() / ("%s.cookie" % pid)


def save(pid: str, cookie_header: str, *, meta: Optional[Dict[str, str]] = None) -> str:
    """Persist a Cookie header. Returns where it was stored ('keyring' or 'file')."""
    if pid not in SESSION_PROVIDERS:
        raise ValueError("unknown session provider")
    cookie_header = (cookie_header or "").strip()
    if not cookie_header or len(cookie_header) > 64 * 1024:
        raise ValueError("empty or oversized cookie header")
    # Never accept headers that look like they contain a Bearer token pasted by mistake
    # into diagnostics paths — storage itself is fine either way; callers build headers.
    payload = json.dumps({"cookie": cookie_header, "meta": meta or {}}, separators=(",", ":"))
    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(_SERVICE, pid, payload)
            # drop any leftover file so only one copy exists
            try:
                _file(pid).unlink()
            except OSError:
                pass
            return "keyring"
        except Exception:  # noqa: BLE001 - fall through to file
            pass
    path = _file(pid)
    config_mod.secure_mkdir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".sess-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return "file"


def load(pid: str) -> Optional[str]:
    """Cookie header string, or None. Never log the return value."""
    if pid not in SESSION_PROVIDERS:
        return None
    kr = _keyring()
    if kr is not None:
        try:
            raw = kr.get_password(_SERVICE, pid)
            if raw:
                data = json.loads(raw)
                c = data.get("cookie") if isinstance(data, dict) else None
                if isinstance(c, str) and c.strip():
                    return c.strip()
        except Exception:  # noqa: BLE001
            pass
    path = _file(pid)
    try:
        st = path.stat()
    except OSError:
        return None
    if os.name != "nt" and st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        return None  # refuse world-readable session files
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    c = data.get("cookie") if isinstance(data, dict) else None
    return c.strip() if isinstance(c, str) and c.strip() else None


def forget(pid: str) -> bool:
    """Delete any stored session for the provider. Returns True if something was removed."""
    gone = False
    kr = _keyring()
    if kr is not None:
        try:
            if kr.get_password(_SERVICE, pid):
                kr.delete_password(_SERVICE, pid)
                gone = True
        except Exception:  # noqa: BLE001
            pass
    path = _file(pid)
    if path.is_file():
        try:
            path.unlink()
            gone = True
        except OSError:
            pass
    return gone


def status() -> Dict[str, str]:
    """Where each session is stored, without revealing cookies. Values: 'keyring', 'file', ''."""
    out = {}
    for pid in SESSION_PROVIDERS:
        if load(pid):
            # distinguish storage backend without re-reading the value into logs
            kr = _keyring()
            where = ""
            if kr is not None:
                try:
                    if kr.get_password(_SERVICE, pid):
                        where = "keyring"
                except Exception:  # noqa: BLE001
                    pass
            if not where and _file(pid).is_file():
                where = "file"
            out[pid] = where or "present"
        else:
            out[pid] = ""
    return out


def has(pid: str) -> bool:
    return bool(load(pid))
