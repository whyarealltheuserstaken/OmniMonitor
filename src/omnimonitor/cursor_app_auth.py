"""Opt-in Cursor session from the Cursor app's local state.vscdb.

Off unless the cursor provider's auth method is ``cursor_app``. Reads
``ItemTable`` key ``cursorAuth/accessToken`` and builds a
``WorkosCursorSessionToken`` cookie the way CodexBar's CursorAppAuth does:

* user id is the JWT ``sub`` after the last ``|``
* cookie value is ``<user id>%3A%3A<jwt>``
* the token is used only while ``exp`` is more than 60 seconds away
* this app never refreshes it

Cursor 3.27+ may store ``cursor-safe-storage:v1:<base64>``. That blob is
Chromium safe-storage (``v10`` plus AES-CBC). On macOS the key is the Keychain
item "Cursor Safe Storage" (account "Cursor Key", then "Cursor"), which raises
one prompt. The token is not written to OmniMonitor's keyring, logs, or UI.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import sys
import time
import urllib.parse
from hashlib import pbkdf2_hmac
from pathlib import Path
from typing import Optional, Tuple

PREFIX = "cursor-safe-storage:v1:"
_CACHE: dict = {}


class CursorAppAuthError(Exception):
    """A reason the local Cursor session cannot be used. Never includes the token."""


def default_db_path() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or (home / "AppData/Roaming"))
        return base / "Cursor/User/globalStorage/state.vscdb"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (home / ".config"))
    return base / "Cursor/User/globalStorage/state.vscdb"


def load_cookie(db_path: Optional[Path] = None, now: Optional[float] = None
               ) -> Tuple[str, str]:
    """Return ``(cookie_header, "")`` or ``("", reason)``. The cookie is not logged."""
    path = db_path or default_db_path()
    now = time.time() if now is None else now
    try:
        token = _cached_token(path, now)
    except CursorAppAuthError as e:
        return "", str(e)
    try:
        return cookie_header(token, now), ""
    except CursorAppAuthError as e:
        return "", str(e)


def cookie_header(access_token: str, now: float) -> str:
    user_id = _user_id(access_token)
    exp = _expires_at(access_token)
    if exp - now <= 60:
        raise CursorAppAuthError(
            "Cursor app token is expired or about to expire — open Cursor to refresh it "
            "(OmniMonitor does not refresh it)")
    return "WorkosCursorSessionToken=%s%%3A%%3A%s" % (user_id, access_token.strip())


def _cached_token(path: Path, now: float) -> str:
    try:
        mtime = path.stat().st_mtime
    except OSError as e:
        raise CursorAppAuthError(
            "Cursor app state.vscdb was not found (%s)" % type(e).__name__) from None
    hit = _CACHE.get(str(path))
    if hit and hit[0] == mtime and hit[2] - now > 60:
        return hit[1]
    token = read_access_token(path)
    try:
        exp = _expires_at(token)
    except CursorAppAuthError:
        exp = 0
    if exp - now > 60:
        _CACHE[str(path)] = (mtime, token, exp)
    return token


def clear_cache() -> None:
    _CACHE.clear()


def read_access_token(path: Path) -> str:
    if not path.is_file():
        raise CursorAppAuthError("Cursor app state.vscdb was not found")
    raw = _read_item(path)
    if raw is None or not str(raw).strip():
        raise CursorAppAuthError(
            "state.vscdb has no cursorAuth/accessToken — sign in to the Cursor app")
    return _maybe_decrypt(str(raw).strip())


def _read_item(path: Path) -> Optional[str]:
    try:
        return _query(path, immutable=False)
    except sqlite3.Error:
        wal = path.with_name(path.name + "-wal")
        shm = path.with_name(path.name + "-shm")
        if wal.exists() or shm.exists():
            raise CursorAppAuthError("could not read Cursor state.vscdb") from None
        try:
            return _query(path, immutable=True)
        except sqlite3.Error:
            raise CursorAppAuthError("could not read Cursor state.vscdb") from None


def _query(path: Path, immutable: bool) -> Optional[str]:
    quoted = urllib.parse.quote(str(path))
    if immutable:
        uri = "file:%s?immutable=1" % quoted
    else:
        uri = "file:%s?mode=ro" % quoted
    con = sqlite3.connect(uri, uri=True, timeout=0.25)
    try:
        con.execute("PRAGMA query_only=ON")
        row = con.execute(
            "SELECT value FROM ItemTable WHERE key = ? LIMIT 1",
            ("cursorAuth/accessToken",)).fetchone()
    finally:
        con.close()
    if not row:
        return None
    return _decode_cell(row[0])


def _decode_cell(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        if value and len(value) % 2 == 0 and all(
                1 <= value[i] < 128 and value[i + 1] == 0 for i in range(0, len(value), 2)):
            return value.decode("utf-16-le", "replace")
        return value.decode("utf-8", "replace")
    return str(value)


def _maybe_decrypt(value: str) -> str:
    if not value.startswith("cursor-safe-storage:"):
        return value
    if not value.startswith(PREFIX):
        raise CursorAppAuthError(
            "Cursor token uses an unrecognized safe-storage version")
    try:
        blob = base64.b64decode(value[len(PREFIX):], validate=False)
    except (ValueError, TypeError):
        raise CursorAppAuthError("Cursor safe-storage token was not valid base64") from None
    if not (blob.startswith(b"v10") and len(blob) > 3 and (len(blob) - 3) % 16 == 0):
        raise CursorAppAuthError("Cursor safe-storage token was not a v10 payload")
    if sys.platform != "darwin":
        raise CursorAppAuthError(
            "this Cursor token is encrypted (cursor-safe-storage). OmniMonitor decrypts "
            "that with the macOS Keychain prompt; on this OS use in-app sign-in instead")
    password = _keychain_password()
    if password is None:
        raise CursorAppAuthError(
            "could not read the Cursor Safe Storage key — approve the one Keychain "
            "prompt, or use in-app sign-in")
    try:
        return _decrypt_v10(blob, password)
    except CursorAppAuthError:
        raise
    except Exception:  # noqa: BLE001
        raise CursorAppAuthError("could not decrypt the Cursor app token") from None


def _keychain_password() -> Optional[str]:
    """One Keychain prompt on macOS. Other platforms have no Cursor Keychain item."""
    if sys.platform != "darwin":
        return None
    import subprocess
    denied = False
    for account in ("Cursor Key", "Cursor"):
        try:
            proc = subprocess.run(
                ["security", "find-generic-password", "-w", "-s", "Cursor Safe Storage",
                 "-a", account],
                capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode == 0:
            pw = proc.stdout.decode("utf-8", "replace").rstrip("\n")
            return pw or None
        err = proc.stderr.decode("utf-8", "replace").lower()
        if "could not be found" in err or "item not found" in err:
            continue
        denied = True
        break
    if denied:
        return None
    return None


def _decrypt_v10(blob: bytes, password: str) -> str:
    key = pbkdf2_hmac("sha1", password.encode("utf-8"), b"saltysalt", 1003, dklen=16)
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as e:
        raise CursorAppAuthError(
            "decrypting the Cursor app token needs the cryptography package") from e
    iv = b" " * 16
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    data = decryptor.update(blob[3:]) + decryptor.finalize()
    pad = data[-1]
    if isinstance(pad, int) and 1 <= pad <= 16 and data.endswith(bytes([pad]) * pad):
        data = data[:-pad]
    text = data.decode("utf-8")
    if not text.strip():
        raise CursorAppAuthError("Cursor safe-storage token decrypted to empty")
    return text.strip()


def _payload(token: str) -> dict:
    parts = token.split(".")
    if len(parts) < 2 or not parts[1]:
        raise CursorAppAuthError("Cursor app access token is not a JWT")
    segment = parts[1]
    segment += "=" * ((4 - len(segment) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode(segment.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise CursorAppAuthError("Cursor app access token has an invalid payload") from None
    if not isinstance(data, dict):
        raise CursorAppAuthError("Cursor app access token has an invalid payload")
    return data


def _user_id(token: str) -> str:
    subject = _payload(token).get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise CursorAppAuthError("Cursor app access token is missing a user id")
    user_id = subject.split("|")[-1].strip()
    if not user_id or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                          "0123456789._-" for ch in user_id):
        raise CursorAppAuthError("Cursor app access token has an invalid user id")
    return user_id


def _expires_at(token: str) -> float:
    exp = _payload(token).get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        raise CursorAppAuthError("Cursor app access token is missing an expiration")
    return float(exp)
