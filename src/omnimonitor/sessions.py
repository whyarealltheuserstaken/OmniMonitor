"""Local storage for provider web-session cookies.

Cookies are NEVER logged, exported, shown in the UI, or included in diagnostics. They are
stored only in the encrypted :mod:`omnimonitor.vault` (key in the OS keychain). Without a
keychain, sign-in is refused: there is no plaintext fallback.

Older versions wrote ``<config>/sessions/<pid>.cookie`` files or a keyring entry under
``omnimonitor.web-session``. Those are moved into the vault once per process, or deleted
when no vault is available (the source then shows "Sign in again").
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional, Set

from omnimonitor import config as config_mod
from omnimonitor import vault

# Providers that may store a web session. Keep in sync with catalog.WEB_SESSION_IDS.
SESSION_PROVIDERS = ("claude_plan", "chatgpt_web", "cursor")

NO_VAULT_MESSAGE = ("Sign-in needs the system keychain (Windows Credential Manager, macOS "
                    "Keychain or a Secret Service on Linux). API keys and CLI logins still "
                    "work.")

_LEGACY_SERVICE = "omnimonitor.web-session"
_MIGRATED: Set[str] = set()


class SessionStoreError(Exception):
    pass


def _check(pid: str) -> None:
    if pid not in SESSION_PROVIDERS:
        raise ValueError("unknown session provider")


def _name(pid: str) -> str:
    return "session.%s" % pid


def _legacy_file(pid: str) -> Path:
    return config_mod.config_dir() / "sessions" / ("%s.cookie" % pid)


def _parse(raw) -> Optional[Dict]:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("cookie"), str):
        return None
    return data


def _migrate(pid: str) -> None:
    """Move a pre-0.6 session into the vault (or delete it if there is no vault)."""
    if pid in _MIGRATED:
        return
    _MIGRATED.add(pid)
    found = None
    path = _legacy_file(pid)
    if path.is_file():
        try:
            found = _parse(path.read_text("utf-8"))
        except OSError:
            found = None
    kr = vault._kr()
    if kr is not None:
        try:
            raw = kr.get_password(_LEGACY_SERVICE, pid)
            if raw:
                found = found or _parse(raw)
                kr.delete_password(_LEGACY_SERVICE, pid)
        except Exception:  # noqa: BLE001
            pass
    if found and found["cookie"].strip() and vault.available():
        try:
            vault.put(_name(pid), json.dumps(found, separators=(",", ":")).encode("utf-8"))
        except (vault.VaultUnavailable, OSError):
            pass
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass


def _read(pid: str) -> Optional[Dict]:
    if pid not in SESSION_PROVIDERS:
        return None
    _migrate(pid)
    raw = vault.get(_name(pid))
    return _parse(raw.decode("utf-8")) if raw else None


def save(pid: str, cookie_header: str, *, meta: Optional[Dict[str, str]] = None) -> str:
    """Store a Cookie header (+ non-cookie meta such as a bearer). Returns ``"vault"``."""
    _check(pid)
    cookie_header = (cookie_header or "").strip()
    if not cookie_header or len(cookie_header) > 64 * 1024:
        raise ValueError("empty or oversized cookie header")
    payload = json.dumps({"cookie": cookie_header, "meta": meta or {}}, separators=(",", ":"))
    try:
        vault.put(_name(pid), payload.encode("utf-8"))
    except vault.VaultUnavailable as e:
        raise SessionStoreError(NO_VAULT_MESSAGE) from e
    _MIGRATED.add(pid)
    return "vault"


def load(pid: str) -> Optional[str]:
    """Cookie header string, or None. Never log the return value."""
    data = _read(pid)
    c = data["cookie"].strip() if data else ""
    return c or None


def meta(pid: str) -> Dict[str, str]:
    """Non-cookie values saved with the session (e.g. a bearer). Never log them."""
    data = _read(pid) or {}
    m = data.get("meta")
    return {k: v for k, v in m.items() if isinstance(v, str)} if isinstance(m, dict) else {}


def forget(pid: str) -> bool:
    """Delete any stored session for the provider. Returns True if something was removed."""
    _check(pid)
    _migrate(pid)
    return vault.delete(_name(pid))


def status() -> Dict[str, str]:
    """Where each session is stored, without revealing it: ``"vault"`` or ``""``."""
    return {pid: ("vault" if load(pid) else "") for pid in SESSION_PROVIDERS}


def has(pid: str) -> bool:
    return bool(load(pid))
