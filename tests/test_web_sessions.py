"""Opt-in web-session providers: storage, browser filters, fetchers, redaction."""
from __future__ import annotations

import pytest

from omnimonitor import appactions, browser_cookies, catalog, sessions, web_accounts


@pytest.fixture
def sess_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIMONITOR_HOME", str(tmp_path))
    # force file backend (no keyring in CI)
    monkeypatch.setattr(sessions, "_keyring", lambda: None)
    return tmp_path


def test_catalog_web_session_ids():
    assert set(catalog.WEB_SESSION_IDS) >= {"claude_plan", "chatgpt_web", "cursor"}
    for pid in catalog.WEB_SESSION_IDS:
        p = catalog.BY_ID[pid]
        assert p.kind == "web_session" and p.status == catalog.EXPERIMENTAL
        assert p.has_limit and pid in catalog.SWITCHABLE


def test_session_save_load_forget_file(sess_home):
    where = sessions.save("claude_plan", "sessionKey=sk-ant-testVALUE")
    assert where == "file"
    assert sessions.load("claude_plan") == "sessionKey=sk-ant-testVALUE"
    assert sessions.status()["claude_plan"] == "file"
    assert sessions.forget("claude_plan") is True
    assert sessions.load("claude_plan") is None


def test_session_rejects_unknown_provider(sess_home):
    with pytest.raises(ValueError):
        sessions.save("not_a_provider", "a=b")


def test_browser_filter_only_allowlisted_names():
    hosts, names = browser_cookies.PROVIDER_COOKIE_FILTER["claude_plan"]
    assert "claude.ai" in hosts and "sessionKey" in names
    assert "WorkosCursorSessionToken" in browser_cookies.PROVIDER_COOKIE_FILTER["cursor"][1]


def test_firefox_reader(tmp_path, monkeypatch):
    # Build a tiny cookies.sqlite
    import sqlite3
    profile = tmp_path / "ff-profile"
    profile.mkdir()
    db = profile / "cookies.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE moz_cookies (host TEXT, name TEXT, value TEXT)")
    con.execute("INSERT INTO moz_cookies VALUES (?,?,?)",
                (".claude.ai", "sessionKey", "sk-ant-FROMFF"))
    con.execute("INSERT INTO moz_cookies VALUES (?,?,?)",
                (".claude.ai", "other", "IGNORE_ME"))
    con.execute("INSERT INTO moz_cookies VALUES (?,?,?)",
                (".evil.example", "sessionKey", "NOPE"))
    con.commit()
    con.close()
    monkeypatch.setattr(browser_cookies, "_profile_roots",
                        lambda bid: [profile] if bid == "firefox" else [])
    monkeypatch.setattr(browser_cookies, "_browser_ready", lambda bid: (True, "ok"))
    hdr = browser_cookies.read_for_provider("claude_plan", "firefox")
    assert "sk-ant-FROMFF" in hdr and "IGNORE_ME" not in hdr and "NOPE" not in hdr


def _http_map(routes):
    def http(method, url, headers, body):
        # longest prefix first so /organizations does not steal /organizations/id/usage
        for prefix, (status, payload) in sorted(routes.items(), key=lambda kv: -len(kv[0])):
            if url.startswith(prefix):
                return status, payload
        return 404, {"error": "missing route " + url}
    return http


def test_fetch_claude_plan_ok(sess_home):
    sessions.save("claude_plan", "sessionKey=sk-ant-x")
    http = _http_map({
        "https://claude.ai/api/organizations": (200, [{"uuid": "org-1", "name": "Me"}]),
        "https://claude.ai/api/organizations/org-1/usage": (200, {
            "five_hour": {"utilization": 0.42, "resets_at": "2026-11-06T00:00:00Z"},
            "seven_day": {"utilization": 0.10, "resets_at": "2026-11-10T00:00:00Z"},
        }),
    })
    r = web_accounts.fetch("claude_plan", {}, http=http, now=1.0)
    assert r.ok and r.limit_percent == pytest.approx(42.0)
    assert r.confidence == "experimental"


def test_fetch_claude_signed_out(sess_home):
    sessions.save("claude_plan", "sessionKey=sk-ant-x")
    http = _http_map({"https://claude.ai/api/organizations": (401, {"error": "auth"})})
    r = web_accounts.fetch("claude_plan", {}, http=http, now=1.0)
    assert not r.ok and "signed out" in r.error


def test_fetch_claude_no_session(sess_home):
    r = web_accounts.fetch("claude_plan", {}, http=_http_map({}), now=1.0)
    assert not r.ok and "signed out" in r.error


def test_fetch_cursor_usage_summary(sess_home):
    sessions.save("cursor", "WorkosCursorSessionToken=abc")
    http = _http_map({
        "https://cursor.com/api/usage-summary": (200, {
            "individualUsage": {"plan": {"used": 30, "limit": 100}},
            "billingCycleEnd": "2026-12-01T00:00:00Z",
            "onDemandUsage": {"spend": 1.5},
        }),
    })
    r = web_accounts.fetch("cursor", {}, http=http, now=1.0)
    assert r.ok and r.limit_percent == pytest.approx(30.0)
    assert any(m["key"] == "on_demand" for m in r.metrics)


def test_fetch_chatgpt_web_windows(sess_home):
    sessions.save("chatgpt_web", "oai-did=x; __Secure-next-auth.session-token=y")
    http = _http_map({
        "https://chatgpt.com/backend-api/wham/usage": (200, {
            "rate_limit": {
                "primary_window": {"used_percent": 55, "resets_at": "2026-11-05T20:00:00Z"},
                "secondary_window": {"used_percent": 12, "resets_at": "2026-11-11T00:00:00Z"},
            }
        }),
    })
    r = web_accounts.fetch("chatgpt_web", {}, http=http, now=1.0)
    assert r.ok and r.limit_percent == pytest.approx(55.0)


def test_redact_strips_session_cookies():
    t = appactions.redact("Cookie: sessionKey=sk-ant-SECRETVALUE; WorkosCursorSessionToken=WWW")
    assert "SECRETVALUE" not in t and "WWW" not in t
    assert "sessionKey=<redacted>" in t


def test_settings_providers_accept_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIMONITOR_HOME", str(tmp_path))
    from omnimonitor import settings
    out = settings._providers({
        "claude_plan": {"enabled": True, "auth": "browser", "browser": "firefox"},
        "claude_code": {"enabled": True},
    })
    assert out["claude_plan"]["auth"] == "browser"
    assert out["claude_code"] == {"enabled": True}
    from omnimonitor.settings import SettingError
    with pytest.raises(SettingError):
        settings._providers({"claude_code": {"enabled": True, "auth": "browser"}})


def test_diagnostics_has_session_flags_not_values(sess_home):
    sessions.save("claude_plan", "sessionKey=sk-ant-SHOULDNEVERAPPEAR")

    class C:
        cfg = {"source": "demo"}
        token_note = ""

        def status_dict(self):
            return {"ok": True}
    d = appactions.diagnostics(C())
    assert "SHOULDNEVERAPPEAR" not in d["text"]
    assert "web sessions:" in d["text"] and "claude_plan=" in d["text"]
