"""Cursor app state.vscdb session. The token must not appear in errors."""

import base64
import json
import sqlite3
import time

import pytest

from omnimonitor import cursor_app_auth as auth


def _jwt(sub, exp):
    def seg(obj):
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return seg({"alg": "none"}) + "." + seg({"sub": sub, "exp": exp}) + ".sig"


def _db(path, value):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE ItemTable (key TEXT, value TEXT)")
    con.execute("INSERT INTO ItemTable VALUES (?, ?)", ("cursorAuth/accessToken", value))
    con.commit()
    con.close()


def test_plaintext_token_becomes_a_session_cookie(tmp_path):
    token = _jwt("auth0|user_123", int(time.time()) + 3600)
    path = tmp_path / "state.vscdb"
    _db(path, token)
    auth.clear_cache()
    cookie, reason = auth.load_cookie(path, now=time.time())
    assert reason == ""
    assert cookie == "WorkosCursorSessionToken=user_123%3A%3A" + token
    assert "user_123" in cookie


def test_expired_and_missing_tokens_do_not_leak(tmp_path):
    token = _jwt("auth0|user_123", 1_700_000_000)
    path = tmp_path / "state.vscdb"
    _db(path, token)
    auth.clear_cache()
    cookie, reason = auth.load_cookie(path, now=1_700_000_100)
    assert cookie == ""
    assert token not in reason and "user_123" not in reason
    empty = tmp_path / "empty.vscdb"
    con = sqlite3.connect(empty)
    con.execute("CREATE TABLE ItemTable (key TEXT, value TEXT)")
    con.commit()
    con.close()
    cookie, reason = auth.load_cookie(empty, now=time.time())
    assert cookie == "" and "cursorAuth/accessToken" in reason
    missing, why = auth.load_cookie(tmp_path / "nope.vscdb")
    assert missing == "" and token not in why


def test_encrypted_token_round_trip_when_cryptography_is_installed(tmp_path, monkeypatch):
    pytest.importorskip("cryptography")
    from hashlib import pbkdf2_hmac

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    token = _jwt("auth0|user_123", int(time.time()) + 3600)
    password = "keychain-secret"
    key = pbkdf2_hmac("sha1", password.encode(), b"saltysalt", 1003, dklen=16)
    raw = token.encode()
    pad = 16 - (len(raw) % 16)
    raw += bytes([pad]) * pad
    encryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).encryptor()
    blob = b"v10" + encryptor.update(raw) + encryptor.finalize()
    stored = "cursor-safe-storage:v1:" + base64.b64encode(blob).decode()
    path = tmp_path / "state.vscdb"
    _db(path, stored)
    monkeypatch.setattr(auth.sys, "platform", "darwin")
    monkeypatch.setattr(auth, "_keychain_password", lambda: password)
    auth.clear_cache()
    cookie, reason = auth.load_cookie(path, now=time.time())
    assert reason == ""
    assert cookie.endswith(token)
    assert password not in cookie
    assert stored not in cookie


def test_non_mac_encrypted_token_is_not_available(tmp_path, monkeypatch):
    token = _jwt("auth0|user_123", int(time.time()) + 3600)
    stored = "cursor-safe-storage:v1:" + base64.b64encode(b"v10" + b"\x00" * 16).decode()
    path = tmp_path / "state.vscdb"
    _db(path, stored)
    monkeypatch.setattr(auth.sys, "platform", "linux")
    auth.clear_cache()
    cookie, reason = auth.load_cookie(path, now=time.time())
    assert cookie == ""
    assert "Keychain" in reason or "in-app" in reason
    assert token not in reason and "v10" not in reason
