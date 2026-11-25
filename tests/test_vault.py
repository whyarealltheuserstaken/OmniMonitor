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


# ------------------------------------------- sessions + API keys on the vault (Task 2)
import json  # noqa: E402
import os  # noqa: E402

from omnimonitor import apikeys, sessions  # noqa: E402


def _write_private(path, text):
    path.write_text(text)
    os.chmod(path, 0o600)  # POSIX ignores a secrets file other users can read


def _legacy_dir():
    d = config_mod.config_dir() / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_session_save_load_forget_vault():
    assert sessions.save("claude_plan", "sessionKey=sk-ant-x", meta={"bearer": "eyJabc"}) == "vault"
    assert sessions.load("claude_plan") == "sessionKey=sk-ant-x"
    assert sessions.meta("claude_plan") == {"bearer": "eyJabc"}
    assert sessions.status()["claude_plan"] == "vault"
    assert sessions.forget("claude_plan") and sessions.load("claude_plan") is None
    assert sessions.status()["claude_plan"] == ""


def test_session_refused_without_vault():
    vault.set_backend(False)
    with pytest.raises(sessions.SessionStoreError) as e:
        sessions.save("cursor", "WorkosCursorSessionToken=abc")
    assert str(e.value) == sessions.NO_VAULT_MESSAGE
    assert not list(config_mod.config_dir().rglob("*.cookie"))  # nothing in plaintext


def test_legacy_session_file_moved():
    p = _legacy_dir() / "cursor.cookie"
    p.write_text(json.dumps({"cookie": "WorkosCursorSessionToken=old", "meta": {}}))
    sessions._MIGRATED.clear()
    assert sessions.load("cursor") == "WorkosCursorSessionToken=old"
    assert not p.exists() and "session.cursor" in vault.names()


def test_legacy_session_file_deleted_without_vault():
    p = _legacy_dir() / "cursor.cookie"
    p.write_text(json.dumps({"cookie": "WorkosCursorSessionToken=old", "meta": {}}))
    vault.set_backend(False)
    sessions._MIGRATED.clear()
    assert sessions.load("cursor") is None  # -> the source says "Sign in again"
    assert not p.exists()


def test_legacy_keyring_entry_moved(_fake_keyring):
    _fake_keyring.store[("omnimonitor.web-session", "claude_plan")] = json.dumps(
        {"cookie": "sessionKey=legacy", "meta": {}})
    sessions._MIGRATED.clear()
    assert sessions.load("claude_plan") == "sessionKey=legacy"
    assert ("omnimonitor.web-session", "claude_plan") not in _fake_keyring.store


def test_apikey_file_migrated_then_deleted(monkeypatch):
    monkeypatch.delenv("OMNIMONITOR_SECRETS_FILE")  # the default file next to the config
    path = apikeys.secrets_path()
    _write_private(path, json.dumps({"DEEPSEEK_API_KEY": "sk-file-123"}))
    apikeys._MIGRATED.clear()
    assert apikeys.get("DEEPSEEK_API_KEY") == "sk-file-123"
    assert apikeys.where("DEEPSEEK_API_KEY") == "vault" and not path.exists()


def test_apikey_file_kept_without_vault():
    vault.set_backend(False)
    path = apikeys.secrets_path()
    apikeys.set_secret("DEEPSEEK_API_KEY", "sk-file-456")
    assert path.exists() and apikeys.where("DEEPSEEK_API_KEY") == "file"
    assert apikeys.get("DEEPSEEK_API_KEY") == "sk-file-456"


def test_apikey_set_goes_to_vault_and_env_wins(monkeypatch):
    apikeys.set_secret("OPENROUTER_API_KEY", "sk-or-vault")
    assert not apikeys.secrets_path().exists()
    assert apikeys.where("OPENROUTER_API_KEY") == "vault"
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    assert apikeys.get("OPENROUTER_API_KEY") == "sk-or-env" and apikeys.where("OPENROUTER_API_KEY") == "env"
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert apikeys.unset_secret("OPENROUTER_API_KEY") and apikeys.where("OPENROUTER_API_KEY") == ""
    assert os.environ.get("OPENROUTER_API_KEY") is None


# ------------------------------------------------------- security review fixes
def test_chatgpt_needs_a_session_cookie():
    from omnimonitor import login_specs
    spec = login_specs.SPECS["chatgpt_web"]
    assert not login_specs.enough(spec, [("oai-did", "x"), ("__cf_bm", "y")])  # logged out
    assert login_specs.enough(spec, [("__Secure-next-auth.session-token", "z")])


def test_missing_key_never_deletes_blobs(_fake_keyring):
    vault.put("session.cursor", SECRET)
    _fake_keyring.store.clear()  # keychain returned nothing (transient or wiped)
    vault.put("session.claude_plan", b"new")
    assert vault.get("session.claude_plan") == b"new"
    orphans = list((config_mod.config_dir() / "vault" / "orphaned").glob("*.bin"))
    assert [p.name for p in orphans] == ["session.cursor.bin"]  # kept, not destroyed


def test_key_created_once_under_race(_fake_keyring, monkeypatch):
    real_set = _fake_keyring.set_password

    def racing_set(service, user, value):  # another process writes its key right after us
        real_set(service, user, value)
        real_set(service, user, "Zm9yZWlnbi1rZXktZnJvbS1vdGhlci1wcm9jZXNzISE=")
    monkeypatch.setattr(_fake_keyring, "set_password", racing_set)
    vault.put("session.cursor", SECRET)
    assert vault.get("session.cursor") == SECRET  # used the key that is actually stored


def test_colon_names_rejected():
    with pytest.raises(ValueError):
        vault.put("a:b", b"x")


def test_failed_vault_put_keeps_legacy_session(monkeypatch):
    p = _legacy_dir() / "cursor.cookie"
    p.write_text(json.dumps({"cookie": "WorkosCursorSessionToken=old", "meta": {}}))
    monkeypatch.setattr(vault, "put", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    sessions._MIGRATED.clear()
    sessions.load("cursor")
    assert p.exists()  # not deleted: nothing was stored yet


def test_env_secrets_file_keeps_unmigrated_entries():
    path = apikeys.secrets_path()  # conftest points this at an env-provided path
    _write_private(path, json.dumps({"DEEPSEEK_API_KEY": "sk-file-123", "NOT_OURS": "keep"}))
    apikeys._MIGRATED.clear()
    assert apikeys.get("DEEPSEEK_API_KEY") == "sk-file-123"
    left = json.loads(path.read_text())
    assert "DEEPSEEK_API_KEY" not in left and left.get("NOT_OURS") == "keep"
