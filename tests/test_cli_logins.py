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
        assert catalog.verified(pid) in ("fixtures", "live")
        assert pid not in catalog.enabled_ids({})  # off by default


def test_enabled_cli_providers_helper():
    from omnimonitor import config as config_mod
    cfg = {"providers": {"claude_cli_plan": {"enabled": True}}}
    assert config_mod.enabled_cli_providers(cfg) == ["claude_cli_plan"]


# ------------------------------------------- Claude and Codex plan fetchers (Task 2)
from pathlib import Path  # noqa: E402

from omnimonitor import accounts, cli_accounts  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def _fx(name):
    return json.loads((FIX / name).read_text("utf-8"))


class _Http:
    def __init__(self, status, data):
        self.status, self.data, self.calls = status, data, []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        return self.status, self.data


def test_claude_plan_windows(tmp_path):
    cfg = _claude_home(tmp_path)
    http = _Http(200, _fx("claude_oauth_usage.json"))
    r = cli_accounts.fetch("claude_cli_plan", cfg, http, NOW)
    assert r.ok and [(w["name"], w["used_percent"]) for w in r.limits] == [
        ("5-hour", 12.0), ("Weekly", 34.0), ("Weekly (Sonnet)", 5.0)]
    assert r.limit_percent == 34.0 and r.resets_at == r.limits[1]["resets_at"]
    method, url, headers, _ = http.calls[0]
    assert (method, url) == ("GET", "https://api.anthropic.com/api/oauth/usage")
    assert headers["Authorization"] == "Bearer " + TOKEN
    assert headers["anthropic-beta"] == "oauth-2025-04-20"


def test_claude_utilization_is_a_percent(tmp_path):
    http = _Http(200, {"five_hour": {"utilization": 0.4, "resets_at": None}})
    r = cli_accounts.fetch("claude_cli_plan", _claude_home(tmp_path), http, NOW)
    assert r.limits[0]["used_percent"] == 0.4 and r.limits[0]["resets_at"] is None


def test_claude_all_null_windows(tmp_path):
    r = cli_accounts.fetch("claude_cli_plan", _claude_home(tmp_path),
                           _Http(200, {"five_hour": None, "seven_day": None}), NOW)
    assert r.ok and r.limits == [] and r.limit_percent is None
    assert "no usage windows" in r.note


def test_claude_401(tmp_path):
    r = cli_accounts.fetch("claude_cli_plan", _claude_home(tmp_path), _Http(401, None), NOW)
    assert not r.ok and r.error == "login rejected - run `claude` once to refresh it"


def test_claude_expired_makes_no_request(tmp_path):
    http = _Http(200, {})
    cfg = _claude_home(tmp_path, expires_ms=(NOW - 5) * 1000)
    r = cli_accounts.fetch("claude_cli_plan", cfg, http, NOW)
    assert not r.ok and "expired" in r.error and http.calls == []


def test_claude_missing_login(tmp_path):
    r = cli_accounts.fetch("claude_cli_plan", {"claude_home": str(tmp_path / "x")},
                           _Http(200, {}), NOW)
    assert not r.ok and r.error == "not signed in to Claude Code on this computer"


def _codex_home(tmp_path):
    _write(tmp_path / "codex" / "auth.json", {"tokens": {
        "access_token": "eyJ.codex.TOKEN-DO-NOT-LEAK", "refresh_token": "R",
        "account_id": "acct-1"}})
    return {"codex_home": str(tmp_path / "codex")}


def test_codex_plan_windows(tmp_path):
    http = _Http(200, _fx("codex_wham_usage.json"))
    r = cli_accounts.fetch("codex_cli_plan", _codex_home(tmp_path), http, NOW)
    assert r.ok and [(w["name"], w["used_percent"]) for w in r.limits] == [
        ("5-hour", 15.0), ("Weekly", 40.0)]
    assert r.limit_percent == 40.0 and r.resets_at == 1792400000.0
    _, url, headers, _ = http.calls[0]
    assert url == "https://chatgpt.com/backend-api/wham/usage"
    assert headers["Authorization"].startswith("Bearer eyJ.codex")
    assert headers["chatgpt-account-id"] == "acct-1"


def test_no_token_in_reading(tmp_path):
    for pid, cfg, data in (
            ("claude_cli_plan", _claude_home(tmp_path), _fx("claude_oauth_usage.json")),
            ("codex_cli_plan", _codex_home(tmp_path), _fx("codex_wham_usage.json"))):
        for status in (200, 401, 500):
            r = cli_accounts.fetch(pid, cfg, _Http(status, data), NOW)
            text = json.dumps(r.to_dict())
            assert "DO-NOT-LEAK" not in text and "REFRESH" not in text


def test_refresh_dispatches_cli_login(tmp_path):
    cfg = dict(_claude_home(tmp_path), providers={"claude_cli_plan": {"enabled": True}},
               provider_poll_seconds=900)
    out = accounts.refresh(cfg, None, ["claude_cli_plan"], force=True,
                           http=_Http(200, _fx("claude_oauth_usage.json")), now=NOW)
    assert out["claude_cli_plan"].ok and len(out["claude_cli_plan"].limits) == 3


# ------------------------------------------------- Gemini CLI quota + verify (Task 3)
class _GeminiHttp:
    def __init__(self, load=None, quota=None, status=200):
        self.load, self.quota, self.status, self.calls = load, quota, status, []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, json.loads(body) if body else None))
        if self.status != 200:
            return self.status, None
        return 200, (self.load if url.endswith(":loadCodeAssist") else self.quota)


def _gemini_home(tmp_path, expires_ms=(NOW + 3600) * 1000):
    _write(tmp_path / "g" / "oauth_creds.json", {
        "access_token": "ya29.GEMINI-DO-NOT-LEAK", "refresh_token": "REFRESH", "expiry_date": expires_ms})
    return {"gemini_home": str(tmp_path / "g")}


def test_gemini_quota_buckets(tmp_path):
    fx = _fx("gemini_quota.json")
    http = _GeminiHttp(fx["loadCodeAssist"], fx["retrieveUserQuota"])
    r = cli_accounts.fetch("gemini_cli_quota", _gemini_home(tmp_path), http, NOW)
    assert r.ok
    # one window per model, the tightest bucket of that model; sorted tightest first
    assert [(w["name"], round(w["used_percent"], 1)) for w in r.limits] == [
        ("gemini-2.5-pro", 20.0), ("gemini-2.5-flash", 5.0)]
    assert r.limit_percent == 20.0 and r.resets_at is not None  # not 19.999999999999996
    (m1, u1, h1, b1), (m2, u2, _, b2) = http.calls
    assert (m1, u1) == ("POST", "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist")
    assert (m2, u2) == ("POST", "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota")
    assert b2 == {"project": "my-project-123"} and h1["Authorization"].startswith("Bearer ya29.")


def test_gemini_no_project(tmp_path):
    http = _GeminiHttp({}, _fx("gemini_quota.json")["retrieveUserQuota"])
    r = cli_accounts.fetch("gemini_cli_quota", _gemini_home(tmp_path), http, NOW)
    assert r.ok and http.calls[1][3] == {}


def test_gemini_401_and_no_token_leak(tmp_path):
    r = cli_accounts.fetch("gemini_cli_quota", _gemini_home(tmp_path),
                           _GeminiHttp(status=401), NOW)
    assert not r.ok and r.error == "login rejected - run `gemini` once to refresh it"
    assert "DO-NOT-LEAK" not in json.dumps(r.to_dict())


def test_gemini_expired_makes_no_request(tmp_path):
    http = _GeminiHttp({}, {})
    r = cli_accounts.fetch("gemini_cli_quota", _gemini_home(tmp_path, (NOW - 1) * 1000), http, NOW)
    assert not r.ok and "expired" in r.error and http.calls == []


def test_gemini_no_buckets(tmp_path):
    r = cli_accounts.fetch("gemini_cli_quota", _gemini_home(tmp_path),
                           _GeminiHttp({}, {"buckets": []}), NOW)
    assert r.ok and r.limits == [] and "no quota buckets" in r.note


def test_verify_includes_cli_logins(tmp_path, monkeypatch):
    import time

    from omnimonitor import verify
    cfg = dict(_claude_home(tmp_path, expires_ms=(time.time() + 3600) * 1000),  # real clock
               providers={"claude_cli_plan": {"enabled": True}})
    assert verify.targets(cfg) == ["claude_cli_plan"]
    res = verify.run(cfg, http=_Http(200, _fx("claude_oauth_usage.json")), out_dir=tmp_path / "v")
    assert res[0]["pid"] == "claude_cli_plan" and res[0]["ok"] is True
    saved = (tmp_path / "v" / res[0]["saved"]).read_text("utf-8")
    assert TOKEN not in saved and "Bearer" not in saved


def test_reading_card_marks_past_reset_stale():
    from datetime import datetime, timezone
    r = {"provider": "claude_cli_plan", "ok": True, "metrics": [], "limits": [
        {"name": "5-hour", "used_percent": 89.0, "resets_at": NOW - 60},
        {"name": "Weekly", "used_percent": 23.0, "resets_at": NOW + 600}]}
    ov = overview.build([], {}, datetime.fromtimestamp(NOW, timezone.utc), tools=[],
                        readings={"claude_cli_plan": r}, tz=timezone.utc)
    card = next(c for c in ov["cards"] if c["id"] == "claude_cli_plan")
    assert [(w["name"], w["stale"]) for w in card["limits"]] == [("5-hour", True), ("Weekly", False)]


def test_live_verified_sources_are_recorded():
    # read successfully with real logins on the owner's PC, 2026-11-25 (see CHANGELOG)
    assert catalog.verified("claude_cli_plan") == "live"
    assert catalog.verified("codex_cli_plan") == "live"
    assert catalog.verified("gemini_cli_quota") == "fixtures"  # Gemini CLI not installed there
