"""0.3.0: API keys, account readers (fake HTTP), overview / budgets / primary, alerts,
settings validation, export, CLI (providers, secrets, config export/import), doctor."""
import io
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from omnimonitor import accounts, alerts, apikeys, catalog, cli, doctor, export, overview
from omnimonitor import settings as settings_mod
from omnimonitor.history import History

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc).timestamp()  # a Wednesday
FAKE = "sk-test-DO-NOT-LEAK-1234567890"


class FakeHttp:
    """http(method, url, headers, body) -> (status, json); records every call."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        for prefix, resp in self.routes.items():
            if url.startswith(prefix):
                return resp(url, body) if callable(resp) else resp
        return 404, None


# ----------------------------------------------------------------------- apikeys
def test_apikeys_env_wins_file_is_private_and_values_never_listed(monkeypatch):
    path = apikeys.secrets_path()
    apikeys.set_secret("DEEPSEEK_API_KEY", FAKE + "\n")
    if os.name == "posix":  # chmod is a no-op on Windows (per-user %APPDATA% ACLs)
        assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
    assert apikeys.get("DEEPSEEK_API_KEY") == FAKE and apikeys.where("DEEPSEEK_API_KEY") == "file"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-env")
    assert apikeys.get("DEEPSEEK_API_KEY") == "from-env" and apikeys.where(
        "DEEPSEEK_API_KEY") == "env"
    assert FAKE not in json.dumps(apikeys.status())
    with pytest.raises(ValueError):
        apikeys.set_secret("NOT_A_KEY", "x")
    with pytest.raises(ValueError):
        apikeys.set_secret("DEEPSEEK_API_KEY", "a\nb")
    assert apikeys.unset_secret("DEEPSEEK_API_KEY") and not apikeys.unset_secret(
        "DEEPSEEK_API_KEY")


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_apikeys_loose_permissions_file_is_ignored():
    path = apikeys.secrets_path()
    path.write_text(json.dumps({"OPENROUTER_API_KEY": FAKE, "EVIL": "x"}))
    os.chmod(path, 0o644)
    assert apikeys.file_problem() and apikeys.get("OPENROUTER_API_KEY") == ""
    os.chmod(path, 0o600)
    assert apikeys.get("OPENROUTER_API_KEY") == FAKE and "EVIL" not in apikeys.status()


# ----------------------------------------------------------------------- fetchers
def fetch_with(pid, monkeypatch, routes, cfg=None):
    env = accounts.key_for(pid)[1]
    monkeypatch.setenv(env, FAKE)
    http = FakeHttp(routes)
    return accounts.fetch(pid, cfg or {}, http=http, now=NOW), http


def metrics(r):
    return {m["key"]: m["value"] for m in r.metrics}


def test_deepseek(monkeypatch):
    r, http = fetch_with("deepseek", monkeypatch, {"https://api.deepseek.com/user/balance": (
        200, {"is_available": True, "balance_infos": [
            {"currency": "USD", "total_balance": "110.00", "granted_balance": "10.00",
             "topped_up_balance": "100.00"}]})})
    assert r.ok and metrics(r) == {"balance_usd": 110.0, "granted_usd": 10.0,
                                   "topped_up_usd": 100.0}
    assert http.calls[0][2]["Authorization"] == "Bearer " + FAKE
    assert r.limit_percent is None  # a balance is not a % limit: never invented


def test_openrouter_limit_and_utc_window(monkeypatch):
    r, _ = fetch_with("openrouter", monkeypatch, {"https://openrouter.ai/api/v1/key": (200, {
        "data": {"usage": 12.5, "usage_daily": 1.0, "usage_weekly": 3.0, "usage_monthly": 9.0,
                 "limit": 20, "limit_remaining": 15, "limit_reset": "weekly",
                 "is_free_tier": False}})})
    assert r.ok and r.limit_percent == 25.0 and r.period == "weekly"
    assert datetime.fromtimestamp(r.resets_at, timezone.utc).weekday() == 0  # next Monday UTC
    assert r.period_start < NOW < r.resets_at
    r2, _ = fetch_with("openrouter", monkeypatch, {"https://openrouter.ai/api/v1/key": (
        200, {"data": {"usage": 1, "limit": None, "limit_remaining": None}})})
    assert r2.limit_percent is None and r2.period == "no key limit set"


def test_moonshot_regions(monkeypatch):
    body = (200, {"code": 0, "data": {"available_balance": 49.5, "voucher_balance": 0,
                                      "cash_balance": 49.5}})
    r, http = fetch_with("moonshot", monkeypatch, {"https://api.moonshot.ai/": body})
    assert r.ok and r.metrics[0]["unit"] == "USD"
    r, http = fetch_with("moonshot", monkeypatch, {"https://api.moonshot.cn/": body},
                         {"moonshot_region": "cn"})
    assert r.ok and r.metrics[0]["unit"] == "CNY" and "CNY" in r.note


def test_ollama_new_and_legacy_plans(monkeypatch):
    new = (200, {"included": {"balance_usd": 15.0, "allowance_usd": 20.0,
                              "period": {"from": "2026-10-01T00:00:00Z",
                                         "until": "2026-11-01T00:00:00Z"}},
                 "purchased": {"balance_usd": 5.0}})
    r, _ = fetch_with("ollama_cloud", monkeypatch, {"https://ollama.com/api/balance": new})
    assert r.ok and r.limit_percent == 25.0 and metrics(r)["purchased"] == 5.0
    assert r.resets_at == datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp()
    legacy = (200, {"included": {"session": {"remaining_percent": 90,
                                             "resets_at": "2026-10-14T14:00:00Z"},
                                 "weekly": {"remaining_percent": 70,
                                            "resets_at": "2026-10-19T00:00:00Z"}}})
    r, _ = fetch_with("ollama_cloud", monkeypatch, {"https://ollama.com/api/balance": legacy})
    assert r.limit_percent == 30.0 and r.period == "weekly limit"
    assert r.resets_at - r.period_start == 7 * 86400


def test_xai_needs_team_and_converts_cents(monkeypatch):
    r, http = fetch_with("xai_api", monkeypatch, {})
    assert not r.ok and "xai_team_id" in r.error and http.calls == []
    r, http = fetch_with("xai_api", monkeypatch, {"https://management-api.x.ai/": (
        200, {"total": {"val": "-2550"}})}, {"xai_team_id": "team-1"})
    assert r.ok and metrics(r) == {"balance": 25.5}
    r, http = fetch_with("xai_api", monkeypatch, {}, {"xai_team_id": "../etc"})
    assert not r.ok and http.calls == []  # path injection refused


def test_anthropic_cost_report_pages(monkeypatch):
    pages = iter([
        (200, {"data": [{"starting_at": "2026-10-13T00:00:00Z",
                         "results": [{"amount": "1250.5", "currency": "USD"}]}],
               "has_more": True, "next_page": "p2"}),
        (200, {"data": [{"starting_at": "2026-10-14T00:00:00Z",
                         "results": [{"amount": "300", "currency": "USD"}]}],
               "has_more": False}),
    ])
    r, http = fetch_with("anthropic_api", monkeypatch, {
        "https://api.anthropic.com/": lambda url, body: next(pages)})
    assert r.ok and metrics(r) == {"month": 15.505, "today": 3.0}
    assert http.calls[0][2]["x-api-key"] == FAKE and "page=p2" in http.calls[1][1]
    assert "starting_at=2026-10-01T00%3A00%3A00Z" in http.calls[0][1]


def test_openai_costs(monkeypatch):
    today = datetime(2026, 10, 14, tzinfo=timezone.utc).timestamp()
    r, _ = fetch_with("openai_api", monkeypatch, {"https://api.openai.com/": (200, {"data": [
        {"start_time": today - 86400, "results": [{"amount": {"value": 4.0, "currency": "usd"}}]},
        {"start_time": today, "results": [{"amount": {"value": 1.25, "currency": "usd"}}]}],
        "has_more": False})})
    assert r.ok and metrics(r) == {"month": 5.25, "today": 1.25}
    r, _ = fetch_with("openai_api", monkeypatch, {"https://api.openai.com/": (
        200, {"data": [], "has_more": False})})
    assert r.ok and r.metrics == []  # no buckets -> nothing shown, not "$0"


def test_github_copilot_premium_requests(monkeypatch):
    body = (200, {"usageItems": [{"grossQuantity": 120, "netAmount": 0.0},
                                 {"grossQuantity": 200, "netAmount": 3.2}]})
    r, http = fetch_with("github_copilot", monkeypatch, {"https://api.github.com/": body},
                         {"github_username": "octo"})
    assert r.ok and metrics(r)["requests"] == 320 and r.limit_percent is None
    assert "year=2026&month=10" in http.calls[0][1]
    assert http.calls[0][2]["X-GitHub-Api-Version"] == "2026-03-10"
    r, _ = fetch_with("github_copilot", monkeypatch, {"https://api.github.com/": body},
                      {"github_username": "octo", "copilot_premium_allowance": 300})
    assert r.limit_percent == 100.0  # capped
    r, http = fetch_with("github_copilot", monkeypatch, {}, {"github_username": "a/b"})
    assert not r.ok and http.calls == []


def test_cursor_team_spend(monkeypatch):
    body = (200, {"teamMemberSpend": [
        {"email": "me@x.com", "spendCents": 1250, "overallSpendCents": 4000,
         "fastPremiumRequests": 300, "effectivePerUserLimitDollars": 50},
        {"email": "you@x.com", "spendCents": 750}],
        "subscriptionCycleStart": 1790000000000, "totalMembers": 2, "totalPages": 1})
    r, http = fetch_with("cursor_team", monkeypatch, {"https://api.cursor.com/": body},
                         {"cursor_email": "ME@x.com"})
    m = metrics(r)
    assert r.ok and m["team_on_demand"] == 20.0 and m["my_spend"] == 12.5
    assert r.limit_percent == 25.0 and http.calls[0][0] == "POST"
    assert http.calls[0][2]["Authorization"].startswith("Basic ")


@pytest.mark.parametrize("status,word", [(401, "rejected"), (403, "rejected"),
                                         (429, "rate limited"), (500, "HTTP 500")])
def test_http_errors_are_reported_without_the_key(monkeypatch, status, word):
    r, _ = fetch_with("deepseek", monkeypatch, {"https://api.deepseek.com/": (status, None)})
    assert not r.ok and word in r.error and FAKE not in json.dumps(r.to_dict())


def test_malformed_payload_and_missing_key(monkeypatch):
    r, _ = fetch_with("openrouter", monkeypatch, {"https://openrouter.ai/": (200, ["x"])})
    assert not r.ok and r.error
    r = accounts.fetch("deepseek", {}, http=FakeHttp({}), now=NOW)
    assert not r.ok and "DEEPSEEK_API_KEY" in r.error
    assert not accounts.fetch("nope", {}, now=NOW).ok


def test_default_http_refuses_other_hosts():
    with pytest.raises(accounts.AccountError):
        accounts.default_http("GET", "https://evil.example/x", {}, None)
    with pytest.raises(accounts.AccountError):
        accounts.default_http("GET", "http://api.deepseek.com/user/balance", {}, None)


def test_refresh_rate_limits_and_keeps_last_good(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE)
    h = History(tmp_path / "h.db")
    good = (200, {"balance_infos": [{"currency": "USD", "total_balance": "5"}]})
    http = FakeHttp({"https://api.deepseek.com/": good})
    cfg = {"provider_poll_seconds": 900}
    out = accounts.refresh(cfg, h, ["deepseek", "not_api"], http=http, now=NOW)
    assert out["deepseek"].ok and len(http.calls) == 1 and "not_api" not in out
    accounts.refresh(cfg, h, ["deepseek"], http=http, now=NOW + 60)
    assert len(http.calls) == 1  # within the poll interval
    http.routes = {"https://api.deepseek.com/": (429, None)}
    out = accounts.refresh(cfg, h, ["deepseek"], http=http, now=NOW + 1000)
    r = out["deepseek"]
    assert len(http.calls) == 2 and r.ok and "429" in r.error  # last good numbers kept
    assert metrics(r)["balance_usd"] == 5.0 and r.attempted_at == NOW + 1000
    accounts.refresh(cfg, h, ["deepseek"], http=http, now=NOW + 1100)
    assert len(http.calls) == 2  # failed attempts are rate limited too
    assert accounts.load(h, "deepseek").attempted_at == NOW + 1000
    accounts.refresh(cfg, h, ["deepseek"], http=http, now=NOW + 1100, force=True)
    assert len(http.calls) == 3


# ----------------------------------------------------------------------- overview
def ev(ts, source, inp=1000, out=100, model="m", cost=None):
    return {"ts": ts, "source": source, "model": model, "input_tokens": inp,
            "output_tokens": out, "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "reasoning_tokens": 0, "cost_usd": cost, "session_id": "s", "project": ""}


def build(events=(), cfg=None, readings=None, tools=("claude_code", "gemini_cli"), **kw):
    now = datetime.fromtimestamp(NOW, timezone.utc)
    return overview.build(list(events), cfg or {}, now, tools=list(tools),
                          readings=readings or {}, tz=timezone.utc, **kw)


def test_overview_cards_ranges_and_unpriced_is_not_available():
    evs = [ev(NOW - 3600, "claude_code", cost=0.5), ev(NOW - 3 * 86400, "gemini_cli"),
           ev(NOW - 40 * 86400, "claude_code")]
    ov = build(evs)
    cards = {c["id"]: c for c in ov["cards"]}
    assert cards["claude_code"]["ranges"]["today"]["usd"] == 0.5
    assert cards["gemini_cli"]["ranges"]["today"]["tokens"] == 0
    assert cards["gemini_cli"]["ranges"]["7d"]["usd"] is None  # no price -> not available
    assert ov["all_tools"]["30d"]["tokens"] == 2200 and ov["today"]["top_tool"] == "Claude Code"
    assert ov["primary"]["id"] == "grok" and not ov["primary"]["available"]


def test_budgets_and_pace():
    evs = [ev(NOW - 3600, "claude_code", cost=0.9), ev(NOW - 2 * 86400, "claude_code", cost=4)]
    ov = build(evs, {"budgets": {"daily": 1, "weekly": 4, "monthly": 0},
                     "budget_alert_percent": 80})
    p = ov["budgets"]["periods"]
    assert p["daily"]["percent"] == 90.0 and p["daily"]["state"] == "warn"
    assert p["weekly"]["percent"] == 122.5 and p["weekly"]["state"] == "over"
    assert p["monthly"]["percent"] is None and p["monthly"]["spent_usd"] == 4.9
    assert overview.pace(50, 1000, 1100, 1025)["status"] == "over"  # 50 % used, 25 % elapsed
    assert overview.pace(10, 1000, 1100, 1050)["status"] == "ok"
    assert overview.pace(None, 1000, 1100, 1050) is None


def test_primary_choices_and_tray_value():
    readings = {"openrouter": {"provider": "openrouter", "ok": True, "limit_percent": 40.0,
                               "resets_at": NOW + 3600, "metrics": []}}
    ov = build(readings=readings, cfg={"primary_provider": "openrouter"})
    assert ov["primary"]["available"] and ov["primary"]["remaining_percent"] == 60.0
    assert overview.tray_value(ov, None) == (60.0, "OpenRouter")
    ov = build(cfg={"primary_provider": "openrouter"})  # no reading yet
    val, lab = overview.tray_value(ov, {"remaining_percent": 70.0})
    assert val == 70.0 and "not available" in lab  # falls back to the Grok pool, says so
    assert overview.tray_value(ov, None)[0] is None  # nothing invented
    snap = {"observed_at": NOW, "windows": {"secondary": {"used_percent": 30.0,
                                                          "window_minutes": 10080,
                                                          "resets_at": NOW + 86400}}}
    ov = build(cfg={"primary_provider": "codex"}, tools=["codex"], codex_limits=snap)
    assert ov["primary"]["used_percent"] == 30.0
    ov = build([ev(NOW - 60, "claude_code", cost=3)], cfg={"primary_provider": "budget",
                                                         "budgets": {"daily": 2}})
    assert ov["primary"]["used_percent"] == 100.0  # capped for the icon


def test_tooltip_and_menu_lines_are_short():
    readings = {"deepseek": {"ok": True, "metrics": [
        {"key": "b", "label": "Balance", "value": 12.3, "unit": "USD", "kind": "balance"}]},
        "moonshot": {"ok": False, "error": "HTTP 401: key rejected" + "x" * 100}}
    ov = build([ev(NOW - 60, "claude_code", 123456789, 1000, cost=1.5)], readings=readings)
    tip = overview.tooltip(ov, "Grok weekly 40% used")
    assert len(tip) <= 127 and "today 123.5M tok" in tip and "$1.50" in tip
    lines = overview.menu_lines(ov)
    assert any("Balance $12.30" in x for x in lines) and all(len(x) <= 80 for x in lines)
    assert any(x.startswith("Kimi API") and "⚠" in x for x in lines)
    demo = build(is_demo=True)
    assert overview.tooltip(demo).startswith("DEMO")


def test_provider_alerts_fire_once_per_window_and_never_in_demo():
    readings = {"openrouter": {"ok": True, "limit_percent": 85.0, "resets_at": NOW + 10,
                               "metrics": []}}
    cfg = {"provider_thresholds": {"openrouter": [50, 80, 95]}, "budgets": {"daily": 1}}
    ov = build([ev(NOW - 60, "claude_code", cost=0.85)], cfg=cfg, readings=readings)
    out, st = alerts.decide_providers(cfg, ov, {})
    kinds = sorted(a["kind"] for a in out)
    assert kinds == ["budget", "provider"] and "85%" in out[1]["title"] + out[0]["title"]
    again, _ = alerts.decide_providers(cfg, ov, st)
    assert again == []
    demo = dict(ov, is_demo=True)
    assert alerts.decide_providers(cfg, demo, {})[0] == []
    assert alerts.decide_providers(dict(cfg, notifications=False), ov, {})[0] == []


# ----------------------------------------------------------------------- settings
def test_settings_validators():
    v = settings_mod.validate
    assert v("providers", {"gemini_cli": True, "deepseek": {"enabled": "false"}}) == {
        "gemini_cli": {"enabled": True}, "deepseek": {"enabled": False}}
    for bad in ({"nope": True}, {"deepseek": {"enabled": True, "key": "x"}}, "[1]"):
        with pytest.raises(settings_mod.SettingError):
            v("providers", bad)
    assert v("provider_thresholds", '{"codex": [95, 50]}') == {"codex": [50.0, 95.0]}
    with pytest.raises(settings_mod.SettingError):
        v("provider_thresholds", {"zzz": [1]})
    assert v("budgets", {"daily": 2.5, "weekly": 0}) == {"daily": 2.5, "weekly": 0, "monthly": 0}
    with pytest.raises(settings_mod.SettingError):
        v("budgets", {"yearly": 1})
    with pytest.raises(settings_mod.SettingError):
        v("budgets", {"daily": -1})
    with pytest.raises(settings_mod.SettingError):
        v("primary_provider", "not-a-provider")


def test_public_view_reports_key_presence_only(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE)
    apikeys.set_secret("DEEPSEEK_API_KEY", FAKE + "-file")
    view = settings_mod.public_view({"_config_path": "/x"})
    text = json.dumps(view)
    assert FAKE not in text and "LEAK" not in text
    keys = view["_readonly"]["keys"]
    assert keys["OPENROUTER_API_KEY"] == "env" and keys["DEEPSEEK_API_KEY"] == "file"
    ids = {p["id"] for p in view["_readonly"]["catalog"]}
    assert ids == set(catalog.SWITCHABLE)
    assert not any("KEY" in k or "TOKEN" in k for k in settings_mod.web_keys())


def test_catalog_enabled_ids_precedence_and_counts():
    cfg = {"extra_token_sources": ["claude_code", "codex"],
           "providers": {"claude_code": {"enabled": False}, "deepseek": {"enabled": True}}}
    assert catalog.enabled_ids(cfg) == [p for p in catalog.SWITCHABLE
                                        if p in ("codex", "deepseek")]
    c = catalog.counts()
    assert sum(c.values()) == len(catalog.CATALOG)
    for p in catalog.CATALOG:
        if p.status == catalog.NOT_SUPPORTED:
            assert p.reason and p.needs, p.id
        if p.kind == "api":
            assert p.env and p.id in accounts.FETCHERS and p.docs, p.id
        assert "cookie" not in p.source.lower() or "never" in p.source.lower(), p.id


def test_every_catalog_id_is_documented():
    doc = (ROOT / "docs" / "providers.md").read_text("utf-8")
    missing = [p.id for p in catalog.CATALOG if "`%s`" % p.id not in doc]
    assert not missing


def test_no_cookie_or_keychain_access_in_sources():
    pat = re.compile(r"keyring|keychain|Cookies|browser_cookie|secretstorage|SecKeychain",
                     re.I)
    for f in (ROOT / "src" / "omnimonitor").glob("*.py"):
        for n, line in enumerate(f.read_text("utf-8").splitlines(), 1):
            s = line.split("#")[0]
            if pat.search(s) and not re.search(r"\b(never|not|no|nothing|without)\b", s, re.I):
                pytest.fail("%s:%d %s" % (f.name, n, line))


# ----------------------------------------------------------------------- export
def test_provider_export_rows_and_json():
    readings = {"deepseek": {"ok": True, "metrics": [
        {"key": "balance_usd", "label": "Balance", "value": 5.0, "unit": "USD",
         "kind": "balance"}], "limit_percent": None}}
    ov = build([ev(NOW - 60, "claude_code", cost=0.2)], readings=readings)
    rows = export.provider_rows(ov)
    assert {"provider_id": "deepseek", "measure": "balance_usd", "value": 5.0}.items() <= \
        next(r for r in rows if r["provider_id"] == "deepseek").items()
    assert any(r["measure"] == "usd_today" and r["value"] == 0.2 for r in rows)
    csv = export.to_csv(rows)
    assert csv.splitlines()[0].startswith("provider,provider_id,demo,measure")
    doc = json.loads(export.providers_json(ov))
    assert doc["is_demo"] is False and doc["cards"]


# ----------------------------------------------------------------------- CLI
@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIMONITOR_HOME", str(tmp_path))
    return tmp_path


def cfgfile(home):
    return json.loads(next(home.rglob("config.json")).read_text())


def test_cli_providers_enable_disable_primary(home, capsys):
    assert cli.main(["providers", "enable", "gemini_cli"]) == 0
    assert cli.main(["providers", "enable", "deepseek"]) == 0
    assert "DEEPSEEK_API_KEY" in capsys.readouterr().out  # key hint
    assert cli.main(["providers", "disable", "gemini_cli"]) == 0
    assert cli.main(["providers", "enable", "chatgpt"]) == 2  # not switchable
    assert cli.main(["providers", "primary", "deepseek"]) == 2  # no % limit to show
    assert cli.main(["providers", "primary", "openrouter"]) == 0
    assert cli.main(["providers", "primary", "nope"]) == 2
    c = cfgfile(home)
    assert c["providers"] == {"gemini_cli": {"enabled": False}, "deepseek": {"enabled": True}}
    assert c["primary_provider"] == "openrouter"
    capsys.readouterr()
    assert cli.main(["providers", "--json"]) == 0
    rows = {r["id"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows["deepseek"]["enabled"] is True and rows["deepseek"]["key"] == "not set"
    assert rows["chatgpt_app"]["status"] == "not_supported" and rows["chatgpt_app"]["reason"]
    assert cli.main(["providers"]) == 0
    assert "Not supported" in capsys.readouterr().out


def test_cli_secrets_via_stdin_never_echoes(home, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(FAKE + "\n"))
    assert cli.main(["secrets", "set", "OPENROUTER_API_KEY"]) == 0
    assert cli.main(["secrets", "list"]) == 0
    out = capsys.readouterr().out
    assert FAKE not in out and "set (secrets file)" in out
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert cli.main(["secrets", "set", "OPENROUTER_API_KEY"]) == 2
    assert cli.main(["secrets", "set", "BOGUS"]) == 2
    assert cli.main(["secrets", "unset", "OPENROUTER_API_KEY"]) == 0
    assert apikeys.get("OPENROUTER_API_KEY") == ""


def test_cli_config_export_import_round_trip(home, capsys, monkeypatch):
    monkeypatch.setenv("OMNIMONITOR_TOKEN", "ENV-SECRET")
    cli.main(["config", "set", "budgets", '{"daily": 3}'])
    cli.main(["providers", "enable", "kimi"])
    out = home / "settings.json"
    assert cli.main(["config", "export", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert doc["omnimonitor_config_export"] and doc["budgets"]["daily"] == 3
    assert "ENV-SECRET" not in out.read_text() and "token" not in doc
    cfg = next(home.rglob("config.json"))
    cfg.write_text("{}")
    doc["unknown_key"] = 1
    out.write_text(json.dumps(doc))
    assert cli.main(["config", "import", str(out)]) == 0
    assert "ignored: unknown_key" in capsys.readouterr().out
    assert cfgfile(home)["providers"]["kimi"] == {"enabled": True}
    bad = home / "bad.json"
    bad.write_text(json.dumps({"budgets": {"daily": 1}}))
    assert cli.main(["config", "import", str(bad)]) == 2  # no marker
    bad.write_text(json.dumps({"omnimonitor_config_export": "x", "budgets": {"daily": -1},
                               "theme": "light"}))
    assert cli.main(["config", "import", str(bad)]) == 2
    assert "theme" not in cfgfile(home) or cfgfile(home)["theme"] != "light"  # all or nothing


def test_doctor_provider_checks(monkeypatch):
    cfg = {"providers": {"deepseek": {"enabled": True}, "openrouter": {"enabled": True}},
           "primary_provider": "moonshot"}
    res = {name: (lvl, msg) for lvl, name, msg in doctor.provider_checks(cfg, online=False)}
    assert res["deepseek"][0] == "fail" and "DEEPSEEK_API_KEY" in res["deepseek"][1]
    assert res["primary"][0] == "warn"
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE)
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE)
    http = FakeHttp({"https://api.deepseek.com/": (200, {"balance_infos": [
        {"currency": "USD", "total_balance": "7"}]}), "https://openrouter.ai/": (401, None)})
    got = doctor.provider_checks(cfg, online=True, http=http)
    res = {name: (lvl, msg) for lvl, name, msg in got}
    assert res["deepseek"][0] == "ok" and "Balance 7.0 USD" in res["deepseek"][1]
    assert res["openrouter"][0] == "fail" and "401" in res["openrouter"][1]
    assert FAKE not in json.dumps(got)
    off = doctor.provider_checks(cfg, online=False)
    assert all("live check skipped" in m for lvl, n, m in off if n == "deepseek")


# ----------------------------------------------------------------------- web
def test_webui_serves_providers_and_never_keys(tmp_path, monkeypatch):
    import urllib.request

    from omnimonitor import config as config_mod
    from omnimonitor.app import Controller
    from omnimonitor.webui import for_controller

    monkeypatch.setenv("OMNIMONITOR_HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE)
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"source": "demo"}))
    ctrl = Controller(config_mod.load_config(p), tmp_path / "h.db")
    srv = for_controller(ctrl).start()
    try:
        def get(rel):
            with urllib.request.urlopen(srv.url + rel, timeout=5) as r:
                return r.read().decode()
        assert "OMProviders" in get("providers.js")
        assert 'id="providers-card"' in get("") or "providers-card" in get("stats.html")
        for rel in ("api/stats", "api/settings", "api/export.csv?kind=providers",
                    "api/export.json?kind=providers"):
            assert FAKE not in get(rel), rel
        st = json.loads(get("api/stats"))
        assert st["providers"]["is_demo"] and st["providers"]["cards"]
        assert all(c["demo"] for c in st["providers"]["cards"] if c["id"] != "grok")
    finally:
        srv.stop()


def test_providers_doc_is_generated_from_catalog():
    import importlib.util
    spec = importlib.util.spec_from_file_location("gen", ROOT / "scripts" / "gen_providers_md.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    assert (ROOT / "docs" / "providers.md").read_text("utf-8") == gen.render(), \
        "run: PYTHONPATH=src python scripts/gen_providers_md.py"
