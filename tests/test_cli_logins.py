"""Plan limits from the official CLIs' own logins: read-only, never refreshed, never stored."""
import json

import pytest

from omnimonitor import catalog, cli_logins, overview, suites

NOW = 1_791_979_200.0  # fixed clock
TOKEN = "sk-ant-oat01-DO-NOT-LEAK-0123456789"


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))
    return path


def _claude_home(tmp_path, expires_ms=(NOW + 3600) * 1000):
    _write(tmp_path / "claude" / ".credentials.json", {"claudeAiOauth": {
        "accessToken": TOKEN, "refreshToken": "REFRESH-DO-NOT-USE", "expiresAt": expires_ms,
        "subscriptionType": "max"}})
    return {"claude_home": str(tmp_path / "claude")}


def test_claude_reads_file_only(tmp_path):
    cfg = _claude_home(tmp_path)
    p = tmp_path / "claude" / ".credentials.json"
    before = p.read_bytes()
    login = cli_logins.claude(cfg, now=NOW)
    assert login.token == TOKEN and login.expires_at == NOW + 3600
    assert "REFRESH" not in repr(login)  # refresh token is never even loaded
    assert p.read_bytes() == before  # read-only


def test_claude_expired(tmp_path):
    cfg = _claude_home(tmp_path, expires_ms=(NOW - 60) * 1000)
    with pytest.raises(cli_logins.LoginExpired) as e:
        cli_logins.claude(cfg, now=NOW)
    assert "run `claude`" in str(e.value) and TOKEN not in str(e.value)


def test_codex_reads_account_id(tmp_path):
    _write(tmp_path / "codex" / "auth.json", {"auth_mode": "chatgpt", "tokens": {
        "access_token": "eyJ.access.tok", "refresh_token": "R", "account_id": "acct-1"}})
    login = cli_logins.codex({"codex_home": str(tmp_path / "codex")}, now=NOW)
    assert login.token == "eyJ.access.tok" and login.extra == {"account_id": "acct-1"}


def test_gemini_missing(tmp_path):
    with pytest.raises(cli_logins.LoginMissing) as e:
        cli_logins.gemini({"gemini_home": str(tmp_path / "nope")}, now=NOW)
    assert "not signed in to Gemini CLI" in str(e.value)


def test_gemini_reads_expiry_ms(tmp_path):
    _write(tmp_path / "g" / "oauth_creds.json", {"access_token": "ya29.tok", "refresh_token": "R",
                                                 "expiry_date": (NOW + 100) * 1000})
    login = cli_logins.gemini({"gemini_home": str(tmp_path / "g")}, now=NOW)
    assert login.token == "ya29.tok" and login.expires_at == NOW + 100


def test_corrupt_file_is_missing(tmp_path):
    p = tmp_path / "claude" / ".credentials.json"
    p.parent.mkdir()
    p.write_text("{not json")
    with pytest.raises(cli_logins.LoginMissing):
        cli_logins.claude({"claude_home": str(tmp_path / "claude")}, now=NOW)


def test_card_uses_reading_limits():
    from datetime import datetime, timezone
    r = {"provider": "claude_cli_plan", "ok": True, "limit_percent": 34.0, "resets_at": NOW + 99,
         "metrics": [], "limits": [
             {"name": "5-hour", "used_percent": 12.0, "resets_at": NOW + 10},
             {"name": "Weekly", "used_percent": 34.0, "resets_at": NOW + 99}]}
    ov = overview.build([], {}, datetime.fromtimestamp(NOW, timezone.utc), tools=[],
                        readings={"claude_cli_plan": r}, tz=timezone.utc)
    card = next(c for c in ov["cards"] if c["id"] == "claude_cli_plan")
    assert [w["name"] for w in card["limits"]] == ["5-hour", "Weekly"]
    s = next(x for x in ov["suites"] if x["id"] == "anthropic")
    assert s["headline"]["used_percent"] == 34.0


def test_catalog_cli_login_entries():
    assert set(catalog.CLI_LOGIN_IDS) == {"claude_cli_plan", "codex_cli_plan", "gemini_cli_quota"}
    for pid, sid in (("claude_cli_plan", "anthropic"), ("codex_cli_plan", "openai"),
                     ("gemini_cli_quota", "google")):
        info = catalog.get(pid)
        assert info.kind == "cli_login" and info.status == catalog.EXPERIMENTAL and info.has_limit
        assert suites.suite_of(pid) == sid and pid in catalog.SWITCHABLE
        assert catalog.verified(pid) == "fixtures"
        assert pid not in catalog.enabled_ids({})  # off by default


def test_enabled_cli_providers_helper():
    from omnimonitor import config as config_mod
    cfg = {"providers": {"claude_cli_plan": {"enabled": True}}}
    assert config_mod.enabled_cli_providers(cfg) == ["claude_cli_plan"]
