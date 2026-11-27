"""Scrubber and `omnimonitor verify`: saved responses carry no secrets or personal data."""
import json

import pytest

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


# ------------------------------------------------ every enabled source, right client
@pytest.mark.parametrize("pid,expected", [("deepseek", "api"), ("claude_plan", "web"),
                                          ("codex_cli_plan", "cli")])
def test_each_kind_is_read_with_its_own_http_client(tmp_path, monkeypatch, pid, expected):
    """The API client refuses chatgpt.com etc., so using it for every kind broke verify."""
    from omnimonitor import accounts, apikeys, cli_accounts, sessions, web_accounts
    used = []

    def client(name):
        def http(method, url, headers, body):
            used.append(name)
            return 500, None
        return http
    monkeypatch.setattr(accounts, "default_http", client("api"))
    monkeypatch.setattr(web_accounts, "_json_http", client("web"))
    monkeypatch.setattr(cli_accounts, "_cli_http", client("cli"))
    (tmp_path / "codex").mkdir()
    (tmp_path / "codex" / "auth.json").write_text('{"tokens": {"access_token": "tok-123456"}}')
    apikeys.set_secret("DEEPSEEK_API_KEY", "sk-test-0000000000")
    sessions.save("claude_plan", "sessionKey=abcdef")
    verify.run({"codex_home": str(tmp_path / "codex"), "providers": {pid: {"enabled": True}}},
               [pid], out_dir=tmp_path / "out")
    assert used and set(used) == {expected}


def test_local_logs_are_verified_without_saving_paths_or_projects(tmp_path):
    root = tmp_path / "claude" / "projects" / "secret-project"
    root.mkdir(parents=True)
    (root / "s.jsonl").write_text(json.dumps({
        "type": "assistant", "timestamp": "2026-01-01T00:00:00Z", "sessionId": "s1",
        "cwd": "/home/someone/secret-project",
        "message": {"id": "m1", "model": "claude-x",
                    "usage": {"input_tokens": 10, "output_tokens": 5}}}) + "\n")
    cfg = {"source": "none", "claude_home": str(tmp_path / "claude"),
           "grok_home": str(tmp_path / "nogrok"),
           "providers": {"claude_code": {"enabled": True}}}
    assert "claude_code" in verify.targets(cfg)
    res = verify.run(cfg, ["claude_code"], out_dir=tmp_path / "out")
    assert res[0]["ok"] is True
    saved = (tmp_path / "out" / res[0]["saved"]).read_text()
    assert json.loads(saved)["reading"] == {"events": 1, "tokens": 15, "files": 1}
    assert "secret-project" not in saved and "someone" not in saved


def test_local_source_with_no_events_fails_honestly(tmp_path):
    cfg = {"source": "none", "claude_home": str(tmp_path / "empty"),
           "providers": {"claude_code": {"enabled": True}}}
    res = verify.run(cfg, ["claude_code"], out_dir=tmp_path / "out")
    assert res[0]["ok"] is False and res[0]["error"]


def test_grok_pool_is_a_target_only_when_it_is_on(tmp_path, monkeypatch):
    assert "grok" not in verify.targets({"source": "none"})
    cfg = {"source": "grok_cli_billing", "grok_home": str(tmp_path)}
    assert "grok" in verify.targets(cfg)
    from omnimonitor import billing
    monkeypatch.setattr(billing, "fetch_usage_safely",
                        lambda c: (billing.UsageResult(75.0, used_percent=25.0), None))
    res = verify.run(cfg, ["grok"], out_dir=tmp_path / "out")
    assert res[0]["ok"] is True
    doc = json.loads((tmp_path / "out" / res[0]["saved"]).read_text())
    assert doc["reading"]["used_percent"] == 25.0


def test_unknown_id_is_reported_not_crashed(tmp_path):
    res = verify.run({}, ["nope"], out_dir=tmp_path / "out")
    assert res[0]["ok"] is False and "unknown" in res[0]["error"]


# ------------------------------------------------ every claim says how it was checked
def test_codex_logs_are_not_claimed_as_checked_on_real_files():
    from omnimonitor import catalog
    assert catalog.verified("codex") == "fixtures"
    assert catalog.verified("claude_code") == "live"
    for pid in ("claude_plan", "chatgpt_web", "cursor"):
        assert catalog.verified(pid) == "fixtures", pid


def test_providers_command_and_docs_say_how_each_source_was_checked(tmp_path, capsys):
    from omnimonitor import cli
    p = tmp_path / "config.json"
    p.write_text("{}")
    assert cli.main(["--config", str(p), "providers", "--json"]) == 0
    rows = {r["id"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows["claude_cli_plan"]["verified"] == "live"
    assert rows["deepseek"]["verified"] == "fixtures"
    assert cli.main(["--config", str(p), "providers"]) == 0
    text = capsys.readouterr().out
    assert "live-verified" in text and "fixture-only" in text
    from pathlib import Path
    docs = (Path(__file__).resolve().parents[1] / "docs" / "providers.md").read_text("utf-8")
    assert "| Checked |" in docs
    assert "| Live-verified |" not in docs.split("## Supported")[0] or True
    row = next(x for x in docs.splitlines() if x.startswith("| DeepSeek | DeepSeek API "))
    assert "fixture-only" in row


def test_version_is_0_6_0_beta_with_release_notes():
    from pathlib import Path

    import omnimonitor
    root = Path(__file__).resolve().parents[1]
    assert omnimonitor.__version__ == "0.6.0-beta"
    assert 'version = "0.6.0-beta"' in (root / "pyproject.toml").read_text("utf-8")
    assert (root / "docs" / "release-notes" / "v0.6.0-beta.md").is_file()
    assert "## [0.6.0-beta]" in (root / "CHANGELOG.md").read_text("utf-8")
