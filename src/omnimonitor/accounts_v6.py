"""API readers added in 0.4 (batch 2). Each one calls a single documented, read-only
endpoint (links in docs/providers.md). Keys come only from env vars or secrets.json via
apikeys; they are never logged, displayed or web-settable.

Amounts are shown exactly as the provider reports them. A "% used" is only set when the
provider returns both a limit and a usage/remaining figure for the same window."""
from __future__ import annotations

import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Optional

from omnimonitor import apikeys
from omnimonitor.accounts import (
    AccountError,
    Reading,
    _bearer,
    _get,
    _iso,
    _num,
    _pct,
    month_start_utc,
    next_month_utc,
)


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc)


# ---------------------------------------------------------------------------- xAI spend

def xai_extras(r: Reading, team: str, key: str, http, now: float) -> None:
    """Month-to-date spend (POST .../usage, value "usd") and the effective postpaid
    spending limit (GET .../postpaid/invoice/preview). Best effort: the balance reading
    stays valid if either call fails."""
    base = "https://management-api.x.ai/v1/billing/teams/%s/" % urllib.parse.quote(team)
    start, end = month_start_utc(now), _utc(now) + timedelta(days=1)
    body = {"analyticsRequest": {
        "timeRange": {"startTime": start.strftime("%Y-%m-%d 00:00:00"),
                      "endTime": end.strftime("%Y-%m-%d 00:00:00"), "timezone": "Etc/GMT"},
        "timeUnit": "TIME_UNIT_NONE",
        "values": [{"name": "usd", "aggregation": "AGGREGATION_SUM"}],
        "groupBy": [], "filters": []}}
    spend: Optional[float] = None
    try:
        d = _get(http, base + "usage", _bearer(key), body=body, method="POST")
        series = d.get("timeSeries") if isinstance(d, dict) else None
        if isinstance(series, list):
            vals = [_num(v) for s in series if isinstance(s, dict)
                    for p in s.get("dataPoints") or [] if isinstance(p, dict)
                    for v in (p.get("values") or [])[:1]]
            vals = [v for v in vals if v is not None]
            if vals or series == []:
                spend = sum(vals)
                r.metric("spend_month", "Spent this month (UTC)", spend, "USD", "spend")
            if d.get("limitReached") is True:
                r.note = (r.note + "; " if r.note else "") + "usage query hit xAI's row limit"
    except AccountError:
        pass
    try:
        d = _get(http, base + "postpaid/invoice/preview", _bearer(key))
        lim = _num(d.get("effectiveSpendingLimit")) if isinstance(d, dict) else None
        if lim is not None:
            r.metric("spending_limit", "Postpaid spending limit", lim / 100.0, "USD", "limit")
            core = d.get("coreInvoice") if isinstance(d.get("coreInvoice"), dict) else {}
            av = core.get("amountAfterVat")
            post = _num(av.get("val") if isinstance(av, dict) else av)
            if post is not None and lim > 0:
                r.metric("postpaid_month", "Postpaid this month", post / 100.0, "USD", "spend")
                r.limit_percent = _pct(post, lim)
                r.period = "postpaid spending limit, monthly (UTC)"
                r.period_start, r.resets_at = start.timestamp(), next_month_utc(now).timestamp()
    except AccountError:
        pass


# ---------------------------------------------------------------------------- Venice

def fetch_venice(cfg, key, http, now) -> Reading:
    """GET https://api.venice.ai/api/v1/billing/balance (admin key; Billing API is beta)."""
    d = _get(http, "https://api.venice.ai/api/v1/billing/balance", _bearer(key))
    if not isinstance(d, dict) or not isinstance(d.get("balances"), dict):
        raise AccountError("unexpected response shape")
    b = d["balances"]
    r = Reading("venice", True, now, period="balance", confidence="medium",
                note="Venice marks its Billing API as beta")
    r.metric("usd", "USD balance", _num(b.get("usd")), "USD", "balance")
    r.metric("diem", "DIEM left this epoch", _num(b.get("diem")), "DIEM", "balance")
    r.metric("bundled", "Bundled credits", _num(b.get("bundledCredits")), "credits", "balance")
    r.metric("earned", "Earned credits", _num(b.get("earnedCredits")), "credits", "balance")
    alloc, diem = _num(d.get("diemEpochAllocation")), _num(b.get("diem"))
    if alloc and alloc > 0 and diem is not None:
        r.metric("diem_alloc", "DIEM allocation per epoch", alloc, "DIEM", "limit")
        r.limit_percent = _pct(alloc - diem, alloc)
        r.period = "DIEM epoch (reset time not returned by the API)"
    if d.get("canConsume") is False:
        r.note += "; Venice reports the balance cannot pay for requests"
    return r


# ---------------------------------------------------------------------------- DeepInfra

def fetch_deepinfra(cfg, key, http, now) -> Reading:
    """GET https://api.deepinfra.com/payment/checklist"""
    d = _get(http, "https://api.deepinfra.com/payment/checklist", _bearer(key))
    if not isinstance(d, dict):
        raise AccountError("unexpected response shape")
    r = Reading("deepinfra", True, now, period="since last invoice", confidence="medium")
    sb = _num(d.get("stripe_balance"))
    if sb is not None:
        # documented: negative = funds ready to spend, positive = money owed
        r.metric("balance", "Prepaid balance", max(-sb, 0.0), "USD", "balance")
        if sb > 0:
            r.metric("owed", "Amount owed", sb, "USD", "spend")
    recent, limit = _num(d.get("recent")), _num(d.get("limit"))
    r.metric("recent", "Usage since last invoice", recent, "USD", "spend")
    r.metric("limit", "Spending limit", limit, "USD", "limit")
    if limit and limit > 0 and recent is not None:
        r.limit_percent = _pct(recent, limit)
    if d.get("suspended") is True:
        r.note = "account suspended (%s)" % str(d.get("suspend_reason") or "unknown")[:40]
    return r


# ---------------------------------------------------------------------------- Fireworks

def _money(m) -> Optional[float]:
    if not isinstance(m, dict):
        return None
    u, n = _num(m.get("units") or 0), _num(m.get("nanos") or 0)
    return None if u is None or n is None else u + n / 1e9


def fetch_fireworks(cfg, key, http, now) -> Reading:
    """GET https://api.fireworks.ai/v1/accounts/{id}/billing/summary (month to date)."""
    acct = str(cfg.get("fireworks_account_id") or apikeys.get("FIREWORKS_ACCOUNT_ID") or "")
    acct = acct.strip()
    if not acct or not all(c.isalnum() or c in "-_" for c in acct):
        raise AccountError("set fireworks_account_id (omnimonitor config set "
                           "fireworks_account_id <id>)")
    start = month_start_utc(now)
    end = (_utc(now) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    q = urllib.parse.urlencode({"startTime": start.strftime("%Y-%m-%dT00:00:00Z"),
                                "endTime": end.strftime("%Y-%m-%dT00:00:00Z")})
    d = _get(http, "https://api.fireworks.ai/v1/accounts/%s/billing/summary?%s"
             % (urllib.parse.quote(acct), q), _bearer(key))
    items = d.get("lineItems") if isinstance(d, dict) else None
    if not isinstance(items, list):
        raise AccountError("unexpected response shape")
    total, cur = 0.0, "USD"
    for it in items:
        c = it.get("totalCost") if isinstance(it, dict) else None
        v = _money(c)
        if v is not None:
            total += v
            cur = str(c.get("currencyCode") or cur)[:5]
    r = Reading("fireworks", True, now, period="month to date (UTC, daily granularity)",
                confidence="medium", note="Fireworks exposes no public credit-balance endpoint")
    r.metric("spend_month", "Spent this month (UTC)", total, cur, "spend")
    r.period_start = start.timestamp()
    return r


# ---------------------------------------------------------------------------- Vercel

def fetch_vercel_gateway(cfg, key, http, now) -> Reading:
    """GET https://ai-gateway.vercel.sh/v1/credits"""
    d = _get(http, "https://ai-gateway.vercel.sh/v1/credits", _bearer(key))
    if not isinstance(d, dict):
        raise AccountError("unexpected response shape")
    r = Reading("vercel_ai_gateway", True, now, period="credits", confidence="high")
    r.metric("balance", "Credit balance", _num(d.get("balance")), "USD", "balance")
    r.metric("total_used", "Used (lifetime)", _num(d.get("total_used")), "USD", "spend")
    return r


# ---------------------------------------------------------------------------- LiteLLM

def litellm_base() -> str:
    """LITELLM_BASE_URL from env/secrets only (never web-settable). https, or http on
    localhost; anything else is refused."""
    raw = (apikeys.get("LITELLM_BASE_URL") or "").strip().rstrip("/")
    if not raw:
        return ""
    u = urllib.parse.urlsplit(raw)
    local = u.hostname in ("localhost", "127.0.0.1", "::1")
    if u.scheme not in ("https", "http") or (u.scheme == "http" and not local) or \
            not u.hostname or u.username or u.password or u.query or u.fragment:
        return ""
    return raw


def litellm_host_ok(url: str) -> bool:
    base = litellm_base()
    if not base:
        return False
    a, b = urllib.parse.urlsplit(base), urllib.parse.urlsplit(url)
    return (a.scheme, a.hostname, a.port) == (b.scheme, b.hostname, b.port)


def fetch_litellm(cfg, key, http, now) -> Reading:
    """GET {LITELLM_BASE_URL}/key/info (the calling key's own spend and budget)."""
    base = litellm_base()
    if not base:
        raise AccountError("set LITELLM_BASE_URL (https://..., or http://localhost:4000)")
    d = _get(http, base + "/key/info", _bearer(key))
    info = d.get("info") if isinstance(d, dict) else None
    if not isinstance(info, dict):
        raise AccountError("unexpected response shape")
    r = Reading("litellm", True, now, period="key budget", confidence="medium")
    spend, budget = _num(info.get("spend")), _num(info.get("max_budget"))
    r.metric("spend", "Key spend", spend, "USD", "spend")
    r.metric("max_budget", "Key budget", budget, "USD", "limit")
    if budget and budget > 0 and spend is not None:
        r.limit_percent = _pct(spend, budget)
        r.resets_at = _iso(info.get("budget_reset_at"))
        dur = info.get("budget_duration")
        r.period = "budget per %s" % str(dur)[:20] if dur else "lifetime budget"
    else:
        r.period = "no key budget set"
    return r
