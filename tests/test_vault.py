"""Encrypted vault: key in the OS keyring, AES-GCM blobs on disk, never plaintext."""
import pytest

from omnimonitor import config as config_mod
from omnimonitor import vault
from tests.conftest import FakeKeyring

SECRET = b"sessionKey=sk-ant-DO-NOT-LEAK-0123456789"


def test_roundtrip():
    assert vault.available()
    vault.put("session.claude_plan", SECRET)
    assert vault.get("session.claude_plan") == SECRET
    assert vault.names() == ["session.claude_plan"]
    assert vault.delete("session.claude_plan") and vault.get("session.claude_plan") is None


def test_no_backend_unavailable():
    vault.set_backend(False)  # explicitly: no keyring on this system
    assert not vault.available()
    with pytest.raises(vault.VaultUnavailable):
        vault.put("session.cursor", SECRET)
    assert vault.get("session.cursor") is None


def test_locked_backend_unavailable():
    vault.set_backend(FakeKeyring(fail=True))
    assert not vault.available()
    with pytest.raises(vault.VaultUnavailable):
        vault.put("session.cursor", SECRET)


def test_tampered_blob_is_none():
    vault.put("apikey.X", SECRET)
    p = config_mod.config_dir() / "vault" / "apikey.X.bin"
    raw = bytearray(p.read_bytes())
    raw[-1] ^= 1
    p.write_bytes(bytes(raw))
    assert vault.get("apikey.X") is None
    p.write_bytes(b"OMV1short")
    assert vault.get("apikey.X") is None


def test_lost_key_reads_none_then_recovers(_fake_keyring):
    vault.put("session.cursor", SECRET)
    _fake_keyring.store.clear()  # key gone (user cleared Credential Manager)
    assert vault.get("session.cursor") is None
    vault.put("session.claude_plan", b"new")  # creates a fresh key
    assert vault.get("session.claude_plan") == b"new"
    assert "session.cursor" not in vault.names()  # unreadable blobs are removed


def test_bad_name_rejected():
    for bad in ("../x", "A B", "", "x" * 65):
        with pytest.raises(ValueError):
            vault.put(bad, b"x")


def test_blob_has_no_plaintext():
    vault.put("session.claude_plan", SECRET)
    data = (config_mod.config_dir() / "vault" / "session.claude_plan.bin").read_bytes()
    assert SECRET not in data and b"sk-ant" not in data
