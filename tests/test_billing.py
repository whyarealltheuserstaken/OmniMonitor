import json

import pytest

from omnimonitor import billing
from omnimonitor.billing import UsageError, parse_billing

from .conftest import FIXTURES


def body(cfg):
    return json.dumps({"config": cfg})


def test_typical_weekly():
    r = parse_billing((FIXTURES / "billing_weekly.json").read_text())
    assert r.used_percent == 28.5
    assert r.remaining_percent == pytest.approx(71.5)
    assert r.resets_at.isoformat() == "2026-11-09T00:00:00+00:00"
    assert r.period_start.isoformat() == "2026-11-02T00:00:00+00:00"
    assert ("GrokBuild", 20.0) in r.products
    assert r.on_demand_cap == 0.0
    assert r.token_fields == {}  # billing exposes no token counts


def test_zero_percent_is_omitted_by_api():
    r = parse_billing((FIXTURES / "billing_zero_omitted.json").read_text())
    assert r.used_percent == 0.0 and r.remaining_percent == 100.0
    assert r.products == [("GrokBuild", 0.0), ("GrokChat", 0.0)]


def test_explicit_zero():
    r = parse_billing(body({"creditUsagePercent": 0, "currentPeriod": {}}))
    assert r.remaining_percent == 100.0


def test_hundred_percent():
    r = parse_billing(body({"creditUsagePercent": 100, "billingPeriodEnd": "2026-11-09T00:00:00Z"}))
    assert r.remaining_percent == 0.0
    assert r.resets_at is not None  # falls back to billingPeriodEnd


def test_over_hundred_clamps_but_keeps_raw():
    r = parse_billing(body({"creditUsagePercent": 137.25, "currentPeriod": {"end": "2026-11-09T00:00:00Z"}}))
    assert r.remaining_percent == 0.0 and r.used_percent == 100.0
    assert r.raw_used_percent == 137.25
    assert any("over" in line for line in r.detail_lines)


def test_negative_clamps_to_zero_used():
    r = parse_billing(body({"creditUsagePercent": -3, "currentPeriod": {}}))
    assert r.remaining_percent == 100.0


def test_string_number_ok():
    assert parse_billing(body({"creditUsagePercent": "12.5", "currentPeriod": {}})).used_percent == 12.5


@pytest.mark.parametrize("payload", [
    "not json", "[]", "{}", json.dumps({"config": {}}), json.dumps({"config": "x"}),
    json.dumps({"config": {"creditUsagePercent": "lots", "currentPeriod": {}}}),
    json.dumps({"config": {"creditUsagePercent": True, "currentPeriod": {}}}),
    json.dumps({"config": {"creditUsagePercent": "nan", "currentPeriod": {}}}),
])
def test_bad_shapes_raise_instead_of_guessing(payload):
    with pytest.raises(UsageError):
        parse_billing(payload)


def test_missing_reset_is_none_not_invented():
    r = parse_billing(body({"creditUsagePercent": 10, "productUsage": []}))
    assert r.resets_at is None


def test_team_account_message():
    with pytest.raises(UsageError, match="team"):
        parse_billing(json.dumps({"error": "No personal team."}))


def test_future_token_fields_are_surfaced():
    r = parse_billing(body({"creditUsagePercent": 5, "currentPeriod": {},
                            "weeklyTokenLimit": {"val": "1000000"}, "tokensUsed": 50000}))
    assert r.token_fields == {"weeklyTokenLimit": 1000000.0, "tokensUsed": 50000.0}


def write_auth(tmp_path, data):
    p = tmp_path / "auth.json"
    p.write_text(json.dumps(data))
    return p


def test_auth_prefers_oidc_xai(tmp_path):
    p = write_auth(tmp_path, {
        "web": {"auth_mode": "web_login", "key": "WEB"},
        "other::x": {"auth_mode": "oidc", "key": "OTHER"},
        "https://auth.x.ai::cid": {"auth_mode": "oidc", "key": " GOOD ",
                                   "expires_at": "2030-01-01T00:00:00Z"},
    })
    tok, exp = billing.read_grok_auth(p)
    assert tok == "GOOD" and exp.year == 2030


def test_auth_web_login_only_is_rejected(tmp_path):
    p = write_auth(tmp_path, {"web": {"auth_mode": "web_login", "key": "WEB"}})
    with pytest.raises(UsageError, match="grok login"):
        billing.read_grok_auth(p)


def test_auth_missing_file(tmp_path):
    with pytest.raises(UsageError, match="not found"):
        billing.read_grok_auth(tmp_path / "nope.json")


def test_auth_file_is_not_modified(tmp_path):
    p = write_auth(tmp_path, {"https://auth.x.ai::c": {"auth_mode": "oidc", "key": "K"}})
    before = (p.read_bytes(), p.stat().st_mtime)
    billing.read_grok_auth(p)
    assert (p.read_bytes(), p.stat().st_mtime) == before


@pytest.mark.parametrize("url,ok", [
    ("https://cli-chat-proxy.grok.com/v1/billing", True),
    ("https://grok.com/x", True),
    ("http://127.0.0.1:9999/v1/billing", True),
    ("http://cli-chat-proxy.grok.com/v1/billing", False),
    ("https://evil.com/?grok.com", False),
    ("https://grok.com.evil.com/", False),
    ("https://evilgrok.com/", False),
])
def test_billing_url_allowlist(url, ok):
    if ok:
        assert billing.check_billing_url(url) == url
    else:
        with pytest.raises(UsageError):
            billing.check_billing_url(url)


def test_demo_is_labeled():
    r = billing.fetch_demo({})
    assert r.is_demo and "DEMO" in r.label
    assert 0 <= r.remaining_percent <= 100


def test_unknown_source():
    res, err = billing.fetch_usage_safely({"source": "bogus"})
    assert res is None and "unknown source" in err


def test_refresh_argv_auto_without_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(billing.shutil, "which", lambda _n: None)
    monkeypatch.setattr(billing.os.path, "isfile", lambda _p: False)
    assert billing.refresh_argv({"grok_refresh_command": "auto", "grok_home": str(tmp_path)}) is None
    assert billing.refresh_argv({"grok_refresh_command": ""}) is None
    assert billing.refresh_argv({"grok_refresh_command": ["~/x", "models"]})[1] == "models"
