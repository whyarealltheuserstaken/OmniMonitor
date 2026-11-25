"""Scrubber and `omnimonitor verify`: saved responses carry no secrets or personal data."""
import json

from omnimonitor import config as config_mod
from omnimonitor import redact, verify

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJlLXZhbHVl"
KEY = "sk-or-v1-0123456789abcdef0123456789abcdef"


def test_scrub_patterns():
    out = redact.scrub({"note": "mail me at a.b@example.com", "auth": "Bearer " + JWT,
                        "k": KEY, "blob": "A" * 40, "email": "x@y.z", "org_id": "org-123",
                        "accountId": "acct_9", "nested": [{"user": "henry"}],
                        "label": "Weekly"}, secrets=["Weekly"])
    assert out["note"] == "mail me at <email>"
    assert out["auth"] == "Bearer <jwt>"
    assert out["k"] == "<key>" and out["blob"] == "<token>"
    assert out["email"] == out["org_id"] == out["accountId"] == "<redacted>"
    assert out["nested"][0]["user"] == "<redacted>"
    assert out["label"] == "<secret>"  # explicit secrets always go


def test_scrub_keeps_numbers():
    src = {"utilization": 42.5, "resets_at": 1791979200, "ok": True, "limit": None,
           "window": "five_hour", "id": 7}
    out = redact.scrub(src)
    assert out == dict(src, id="<redacted>")


def test_verify_records_and_scrubs(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)

    def http(method, url, headers, body):
        assert headers["Authorization"].endswith(KEY)
        return 200, {"data": {"label": "me@example.com", "usage": 1.5, "limit": 10.0,
                              "limit_remaining": 8.5, "token": JWT}}
    cfg = {"providers": {"openrouter": {"enabled": True}}}
    res = verify.run(cfg, http=http, out_dir=tmp_path)
    assert res[0]["pid"] == "openrouter" and res[0]["ok"] is True
    saved = tmp_path / res[0]["saved"]
    text = saved.read_text("utf-8")
    assert KEY not in text and JWT not in text and "me@example.com" not in text
    doc = json.loads(text)
    assert doc["calls"][0]["status"] == 200 and doc["calls"][0]["url"].startswith("https://")
    assert "?" not in doc["calls"][0]["url"]


def test_verify_reports_failure_without_secret(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    res = verify.run({"providers": {"openrouter": {"enabled": True}}},
                     http=lambda *a: (401, None), out_dir=tmp_path)
    assert res[0]["ok"] is False and KEY not in json.dumps(res)


def test_verify_cli_lists_results(monkeypatch, capsys):
    from omnimonitor import cli
    monkeypatch.setattr(verify, "run", lambda cfg, pids=None, **kw: [
        {"pid": "openrouter", "ok": True, "error": "", "saved": "openrouter-1.json"},
        {"pid": "deepseek", "ok": False, "error": "no key", "saved": ""}])
    assert cli.main(["verify"]) == 1
    out = capsys.readouterr().out
    assert "✔ openrouter" in out and "✘ deepseek" in out and "no key" in out
    assert str(config_mod.config_dir() / "verify") in out


def test_scrub_dict_keys_and_more_fields():
    out = redact.scrub({"accounts": {"a.b@example.com": {"phone_number": "5551234",
                                                        "avatar_url": "https://x/u/99",
                                                        "address": "1 Main St",
                                                        "workspace": "Acme",
                                                        "used": 3}}})
    (k, v), = out["accounts"].items()
    assert k == "<email>" and v["used"] == 3
    assert v["phone_number"] == v["avatar_url"] == v["address"] == v["workspace"] == "<redacted>"


def test_verify_web_session_uses_web_fetch_and_scrubs_cookie(monkeypatch, tmp_path):
    from omnimonitor import sessions
    sessions.save("claude_plan", "sessionKey=sk-ant-SESSIONVALUE1")
    seen = []

    def http(method, url, headers, body):
        seen.append(url)
        return 200, [{"uuid": "org-1", "echo": "sk-ant-SESSIONVALUE1"}] if "organizations" in url \
            and url.endswith("organizations") else {"five_hour": {"utilization": 10}}
    res = verify.run({"providers": {"claude_plan": {"enabled": True}}}, http=http,
                     out_dir=tmp_path)
    assert res[0]["error"] != "no API reader for this provider"
    assert any("claude.ai" in u for u in seen)
    assert "SESSIONVALUE1" not in (tmp_path / res[0]["saved"]).read_text("utf-8")


def test_verify_scrubs_team_ids(monkeypatch, tmp_path):
    monkeypatch.setenv("XAI_MANAGEMENT_KEY", "xai-mgmt-key-123456")
    monkeypatch.setenv("XAI_TEAM_ID", "team42")
    res = verify.run({"providers": {"xai_api": {"enabled": True}}},
                     http=lambda m, u, h, b: (200, {"team": "team42", "url": u}), out_dir=tmp_path)
    assert "team42" not in (tmp_path / res[0]["saved"]).read_text("utf-8")
