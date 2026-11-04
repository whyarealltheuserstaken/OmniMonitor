import json

from omnimonitor import accounts
from tests.test_providers_v3 import FAKE, fetch_with, metrics


def test_vercel_gateway(monkeypatch):
    r, http = fetch_with("vercel_ai_gateway", monkeypatch, {
        "https://ai-gateway.vercel.sh/v1/credits": (200, {"balance": "95.50",
                                                         "total_used": "4.50"})})
    assert r.ok and metrics(r) == {"balance": 95.5, "total_used": 4.5}
    assert http.calls[0][2]["Authorization"] == "Bearer " + FAKE
    assert r.limit_percent is None


def test_venice_diem_percent(monkeypatch):
    r, _ = fetch_with("venice", monkeypatch, {"https://api.venice.ai/": (200, {
        "canConsume": True, "consumptionCurrency": "DIEM",
        "balances": {"diem": 25, "usd": 10.0, "bundledCredits": None, "earnedCredits": None},
        "diemEpochAllocation": 100})})
    assert r.ok and metrics(r) == {"usd": 10.0, "diem": 25.0, "diem_alloc": 100.0}
    assert r.limit_percent == 75.0


def test_deepinfra_sign_convention(monkeypatch):
    r, _ = fetch_with("deepinfra", monkeypatch, {"https://api.deepinfra.com/payment/checklist": (
        200, {"stripe_balance": -12.5, "recent": 3.0, "limit": 20, "suspended": False})})
    assert r.ok and metrics(r) == {"balance": 12.5, "recent": 3.0, "limit": 20.0}
    assert r.limit_percent == 15.0
    r, _ = fetch_with("deepinfra", monkeypatch, {"https://api.deepinfra.com/": (
        200, {"stripe_balance": 4, "suspended": True, "suspend_reason": "unpaid"})})
    assert metrics(r) == {"balance": 0.0, "owed": 4.0} and "suspended" in r.note


def test_fireworks_needs_account_and_sums_money(monkeypatch):
    r, http = fetch_with("fireworks", monkeypatch, {})
    assert not r.ok and "fireworks_account_id" in r.error and http.calls == []
    r, http = fetch_with("fireworks", monkeypatch, {"https://api.fireworks.ai/": (200, {
        "lineItems": [{"totalCost": {"currencyCode": "USD", "units": "3", "nanos": 500000000}},
                      {"totalCost": {"currencyCode": "USD", "units": "1"}}]})},
        {"fireworks_account_id": "acme"})
    assert r.ok and metrics(r) == {"spend_month": 4.5}
    assert "/accounts/acme/billing/summary?startTime=" in http.calls[0][1]
    r, http = fetch_with("fireworks", monkeypatch, {}, {"fireworks_account_id": "a/../b"})
    assert not r.ok and http.calls == []


def test_xai_spend_and_limit(monkeypatch):
    routes = {
        "https://management-api.x.ai/v1/billing/teams/t1/prepaid/balance": (
            200, {"total": {"val": "-1000"}}),
        "https://management-api.x.ai/v1/billing/teams/t1/usage": (200, {"timeSeries": [
            {"group": [], "dataPoints": [{"timestamp": "x", "values": [2.5]},
                                         {"timestamp": "y", "values": [1.5]}]}]}),
        "https://management-api.x.ai/v1/billing/teams/t1/postpaid/invoice/preview": (200, {
            "effectiveSpendingLimit": "5000", "coreInvoice": {"amountAfterVat": "1250"}}),
    }
    r, http = fetch_with("xai_api", monkeypatch, routes, {"xai_team_id": "t1"})
    assert r.ok and metrics(r) == {"balance": 10.0, "spend_month": 4.0,
                                   "spending_limit": 50.0, "postpaid_month": 12.5}
    assert r.limit_percent == 25.0
    body = json.loads(http.calls[1][3])
    assert body["analyticsRequest"]["values"][0]["name"] == "usd"


def test_litellm_base_url_rules(monkeypatch):
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    r, http = fetch_with("litellm", monkeypatch, {})
    assert not r.ok and "LITELLM_BASE_URL" in r.error and http.calls == []
    monkeypatch.setenv("LITELLM_BASE_URL", "http://proxy.example.com")  # plain http refused
    r, http = fetch_with("litellm", monkeypatch, {})
    assert not r.ok and http.calls == []
    monkeypatch.setenv("LITELLM_BASE_URL", "https://llm.example.com/")
    r, http = fetch_with("litellm", monkeypatch, {"https://llm.example.com/key/info": (200, {
        "info": {"spend": 2.0, "max_budget": 8.0, "budget_duration": "30d",
                 "budget_reset_at": "2026-11-30T00:00:00Z"}})})
    assert r.ok and metrics(r) == {"spend": 2.0, "max_budget": 8.0}
    assert r.limit_percent == 25.0 and r.resets_at and "30d" in r.period
    assert accounts._extra_host_ok("https://llm.example.com/key/info")
    assert not accounts._extra_host_ok("https://evil.example.com/key/info")
    assert accounts.key_for("litellm")[1] == "LITELLM_API_KEY"
