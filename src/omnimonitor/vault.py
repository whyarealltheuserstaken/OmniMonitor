"""Encrypted local store for secrets (web sessions, API keys).

A random 256-bit key lives in the OS keyring (Windows Credential Manager, macOS Keychain,
Secret Service on Linux). Each secret is an AES-GCM blob under ``<config_dir>/vault/``,
bound to its name as associated data. Nothing is ever written in plaintext: without a
working keyring the vault is *unavailable* and callers must refuse to store secrets.

Blob format: ``b"OMV1" + nonce(12) + ciphertext+tag``. Values are never logged.
"""
from __future__ import annotations

import base64
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, List, Optional

from omnimonitor import config as config_mod

SERVICE = "omnimonitor.vault"
KEY_USER = "key-v1"
MAGIC = b"OMV1"
_NAME = re.compile(r"^[a-z0-9_.-]{1,64}$", re.IGNORECASE)

_backend: Any = None  # None = real ``keyring`` module; False = none; else a test double


class VaultUnavailable(Exception):
    """No usable OS keyring: secrets cannot be stored safely on this system."""


def set_backend(kr: Any) -> None:
    """Tests: inject a keyring double (``False`` = no keyring, ``None`` = the real one)."""
    global _backend
    _backend = kr


def _kr() -> Any:
    if _backend is False:
        return None
    if _backend is not None:
        return _backend
    try:
        import keyring  # type: ignore
        from keyring.backends import fail, null  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    try:
        kb = keyring.get_keyring()
        if isinstance(kb, (fail.Keyring, null.Keyring)):
            return None
    except Exception:  # noqa: BLE001
        return None
    return keyring


def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise VaultUnavailable("the cryptography package is missing") from e
    return AESGCM


def _dir() -> Path:
    d = config_mod.config_dir() / "vault"
    config_mod.secure_mkdir(d)
    return d


def _path(name: str) -> Path:
    if not isinstance(name, str) or not _NAME.match(name) or ".." in name:
        raise ValueError("bad vault name")
    return _dir() / ("%s.bin" % name)


def _decode(raw: Any) -> Optional[bytes]:
    if not raw:
        return None
    try:
        key = base64.b64decode(raw)
    except (ValueError, TypeError):
        return None
    return key if len(key) == 32 else None


def _read_key(kr: Any) -> Optional[bytes]:
    try:
        return _decode(kr.get_password(SERVICE, KEY_USER))
    except Exception as e:  # noqa: BLE001 - locked / denied keychain
        raise VaultUnavailable("system keychain unavailable (%s)" % type(e).__name__) from e


class _KeyLock:
    """Cross-process lock (tray + sign-in child) around creating the key."""

    def __init__(self, path: Path):
        self.path, self.fd = path, None

    def __enter__(self):
        deadline = time.time() + 10
        while True:
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                return self
            except FileExistsError:
                try:  # a crashed holder: break locks older than 30 s
                    if time.time() - self.path.stat().st_mtime > 30:
                        self.path.unlink()
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise VaultUnavailable("vault is busy")
                time.sleep(0.05)

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
        try:
            self.path.unlink()
        except OSError:
            pass


def _key(create: bool) -> Optional[bytes]:
    kr = _kr()
    if kr is None:
        raise VaultUnavailable("no system keychain")
    key = _read_key(kr)
    if key or not create:
        return key
    with _KeyLock(_dir() / ".key.lock"):
        key = _read_key(kr)  # another process may have created it meanwhile
        if key:
            return key
        _set_aside_unreadable()
        try:
            kr.set_password(SERVICE, KEY_USER, base64.b64encode(os.urandom(32)).decode("ascii"))
        except Exception as e:  # noqa: BLE001
            raise VaultUnavailable("could not save to the system keychain (%s)"
                                   % type(e).__name__) from e
        key = _read_key(kr)  # use whatever is actually stored
    if not key:
        raise VaultUnavailable("system keychain did not keep the vault key")
    return key


def _set_aside_unreadable() -> None:
    """Without a key, existing blobs cannot be read. Keep them (never delete secrets the
    user might recover by restoring the keychain entry) but out of the active vault."""
    blobs = list(_dir().glob("*.bin"))
    if not blobs:
        return
    side = _dir() / "orphaned"
    config_mod.secure_mkdir(side)
    for p in blobs:
        try:
            os.replace(str(p), str(side / p.name))
        except OSError:
            pass


def available() -> bool:
    """True when a keychain is usable and the cryptography package is present."""
    try:
        _aesgcm()
        _key(create=False)
        return True
    except VaultUnavailable:
        return False


def self_check(require_os_store: bool = False) -> int:
    """``omnimonitor vault-check``: can this build encrypt, and is there a keychain? Reads
    and writes no secrets. The release smoke test runs it, because a bundling mistake once
    shipped builds that kept sessions in plaintext."""
    try:
        aes = _aesgcm()(bytes(32))
        nonce = bytes(12)
        ok = aes.decrypt(nonce, aes.encrypt(nonce, b"check", None), None) == b"check"
        print("encryption: %s" % ("OK" if ok else "FAILED (round trip mismatch)"))
    except Exception as e:  # noqa: BLE001
        ok = False
        print("encryption: FAILED (%s: %s)" % (type(e).__name__, e.__cause__ or e))
    store = _kr() is not None
    print("keychain: %s" % ("OK" if store else "not available"))
    return 0 if ok and (store or not require_os_store) else 1


def put(name: str, data: bytes) -> None:
    path = _path(name)
    aes = _aesgcm()
    _key(create=True)
    key = _read_key(_kr())  # the key as stored right now (another process may have won)
    if not key:
        raise VaultUnavailable("system keychain did not keep the vault key")
    nonce = os.urandom(12)
    blob = MAGIC + nonce + aes(key).encrypt(nonce, bytes(data), name.encode("utf-8"))
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".v-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(blob)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def get(name: str) -> Optional[bytes]:
    """The secret, or None if missing, unreadable, tampered or the key is gone."""
    try:
        path = _path(name)
        blob = path.read_bytes()
    except (OSError, ValueError):
        return None
    if len(blob) < len(MAGIC) + 12 + 16 or not blob.startswith(MAGIC):
        return None
    try:
        aes = _aesgcm()
        key = _key(create=False)
    except VaultUnavailable:
        return None
    if key is None:
        return None
    nonce, ct = blob[4:16], blob[16:]
    try:
        return aes(key).decrypt(nonce, ct, name.encode("utf-8"))
    except Exception:  # noqa: BLE001 - InvalidTag: tampered or wrong key
        return None


def delete(name: str) -> bool:
    try:
        _path(name).unlink()
        return True
    except (OSError, ValueError):
        return False


def names() -> List[str]:
    return sorted(p.name[:-4] for p in _dir().glob("*.bin"))
