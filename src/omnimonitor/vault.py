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
from pathlib import Path
from typing import Any, List, Optional

from omnimonitor import config as config_mod

SERVICE = "omnimonitor.vault"
KEY_USER = "key-v1"
MAGIC = b"OMV1"
_NAME = re.compile(r"^[a-z0-9_.:-]{1,64}$", re.IGNORECASE)

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


def _key(create: bool) -> Optional[bytes]:
    kr = _kr()
    if kr is None:
        raise VaultUnavailable("no system keychain")
    try:
        raw = kr.get_password(SERVICE, KEY_USER)
    except Exception as e:  # noqa: BLE001 - locked / denied keychain
        raise VaultUnavailable("system keychain unavailable (%s)" % type(e).__name__) from e
    if raw:
        try:
            key = base64.b64decode(raw)
            if len(key) == 32:
                return key
        except (ValueError, TypeError):
            pass
    if not create:
        return None
    _drop_unreadable()  # blobs from a lost key can never be read again
    key = os.urandom(32)
    try:
        kr.set_password(SERVICE, KEY_USER, base64.b64encode(key).decode("ascii"))
    except Exception as e:  # noqa: BLE001
        raise VaultUnavailable("could not save to the system keychain (%s)"
                               % type(e).__name__) from e
    return key


def _drop_unreadable() -> None:
    for p in _dir().glob("*.bin"):
        try:
            p.unlink()
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


def put(name: str, data: bytes) -> None:
    path = _path(name)
    aes = _aesgcm()
    key = _key(create=True)
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
