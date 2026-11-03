"""Account readers: official usage / billing APIs, called with a key YOU supply.

Rules (see docs/providers.md):
* opt-in per provider (``providers.<id>.enabled``) and only when its key is set;
* HTTPS to an allow-listed host only, short timeout, no redirects to other hosts;
* keys come from :mod:`omnimonitor.apikeys` and are never logged, stored in readings or
  echoed in errors (errors carry the HTTP status / exception class only);
* a value the API did not return is simply absent - nothing is invented.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from omnimonitor import apikeys, catalog

ALLOWED_HOSTS = {
    "api.deepseek.com", "openrouter.ai", "api.moonshot.ai", "api.moonshot.cn", "ollama.com",
    "management-api.x.ai", "api.anthropic.com", "api.openai.com", "api.github.com",
    "api.cursor.com",
}
TIMEOUT = 15
MAX_BODY = 4 * 1024 * 1024
USER_AGENT = "OmniMonitor (+https://github.com/; read-only usage monitor)"

# http(method, url, headers, body_bytes_or_None) -> (status, parsed_json_or_None)
Http = Callable[[str, str, Dict[str, str], Optional[bytes]], Tuple[int, Any]]


class AccountError(Exception):
    pass


@dataclass
class Reading:
    provider: str
    ok: bool
    fetched_at: float
    metrics: List[Dict[str, Any]] = field(default_factory=list)
    limit_percent: Optional[float] = None  # % USED of the provider's own limit
    resets_at: Optional[float] = None
    period_start: Optional[float] = None  # start of the window limit_percent covers
    period: str = ""
    note: str = ""
    confidence: str = ""
    error: str = ""
    demo: bool = False
    attempted_at: float = 0.0  # last try, successful or not (rate limiting)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Reading":
        keys = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in keys})

    def metric(self, key: str, label: str, value: Any, unit: str = "", kind: str = "") -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        if value != value or abs(value) > 1e12:  # NaN / absurd
            return
        self.metrics.append({"key": key, "label": label, "value": round(float(value), 4),
                             "unit": unit, "kind": kind})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # noqa: D401 - never follow redirects
        return None


def default_http(method: str, url: str, headers: Dict[str, str],
                 body: Optional[bytes]) -> Tuple[int, Any]:
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or u.hostname not in ALLOWED_HOSTS:
        raise AccountError("refusing non-allow-listed URL")
    req = urllib.request.Request(url, data=body, method=method,
                                 headers=dict(headers, **{"User-Agent": USER_AGENT,
                                                          "Accept": "application/json"}))
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=TIMEOUT) as r:
            raw = r.read(MAX_BODY + 1)
            status = r.status
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise AccountError("network error (%s)" % e.__class__.__name__) from None
    if len(raw) > MAX_BODY:
        raise AccountError("response too large")
    try:
        return status, json.loads(raw.decode("utf-8"))
    except ValueError:
        raise AccountError("response was not JSON") from None


def _get(http: Http, url: str, headers: Dict[str, str], body: Any = None,
         method: str = "GET") -> Any:
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        headers = dict(headers, **{"Content-Type": "application/json"})
    status, payload = http(method, url, headers, data)
    if status in (401, 403):
        raise AccountError("HTTP %d: key rejected (check it is the right key type)" % status)
    if status == 429:
        raise AccountError("HTTP 429: rate limited, will retry later")
    if status != 200 or payload is None:
        raise AccountError("HTTP %d" % status)
    return payload


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _iso(v: Any) -> Optional[float]:
    if not isinstance(v, str) or not v:
        return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _pct(used: Optional[float], limit: Optional[float]) -> Optional[float]:
    if used is None or not limit or limit <= 0:
        return None
    return max(0.0, min(100.0, used / limit * 100.0))


def month_start_utc(now: float) -> datetime:
    d = datetime.fromtimestamp(now, timezone.utc)
    return d.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def next_month_utc(now: float) -> datetime:
    s = month_start_utc(now)
    return (s + timedelta(days=32)).replace(day=1)


def _bearer(key: str) -> Dict[str, str]:
    return {"Authorization": "Bearer " + key}


# ------------------------------------------------------------------------- fetchers
def fetch_deepseek(cfg, key, http, now) -> Reading:
    """GET https://api.deepseek.com/user/balance  (api-docs.deepseek.com/api/get-user-balance)"""
    d = _get(http, "https://api.deepseek.com/user/balance", _bearer(key))
    r = Reading("deepseek", True, now, period="prepaid balance", confidence="high")
    infos = d.get("balance_infos") if isinstance(d, dict) else None
    for b in infos if isinstance(infos, list) else []:
        if not isinstance(b, dict):
            continue
        cur = str(b.get("currency") or "")[:5]
        r.metric("balance_" + cur.lower(), "Balance", _num(b.get("total_balance")), cur, "balance")
        r.metric("granted_" + cur.lower(), "Granted", _num(b.get("granted_balance")), cur,
                 "balance")
        r.metric("topped_up_" + cur.lower(), "Topped up", _num(b.get("topped_up_balance")), cur,
                 "balance")
    if isinstance(d, dict) and d.get("is_available") is False:
        r.note = "DeepSeek reports the balance is not sufficient for API calls"
    return r


def _utc_window(kind: str, now: float) -> Tuple[Optional[float], Optional[float]]:
    """(start, next reset) of OpenRouter's UTC daily / weekly (Mon-Sun) / monthly window."""
    d = datetime.fromtimestamp(now, timezone.utc).replace(hour=0, minute=0, second=0,
                                                          microsecond=0)
    if kind == "daily":
        return d.timestamp(), (d + timedelta(days=1)).timestamp()
    if kind == "weekly":
        mon = d - timedelta(days=d.weekday())
        return mon.timestamp(), (mon + timedelta(days=7)).timestamp()
    if kind == "monthly":
        return month_start_utc(now).timestamp(), next_month_utc(now).timestamp()
    return None, None


def fetch_openrouter(cfg, key, http, now) -> Reading:
    """GET https://openrouter.ai/api/v1/key  (openrouter.ai/docs/api/reference/limits)"""
    d = _get(http, "https://openrouter.ai/api/v1/key", _bearer(key))
    d = d.get("data") if isinstance(d, dict) else None
    if not isinstance(d, dict):
        raise AccountError("unexpected response shape")
    r = Reading("openrouter", True, now, confidence="high")
    r.metric("usage_daily", "Spent today (UTC)", _num(d.get("usage_daily")), "USD", "spend")
    r.metric("usage_weekly", "Spent this week", _num(d.get("usage_weekly")), "USD", "spend")
    r.metric("usage_monthly", "Spent this month", _num(d.get("usage_monthly")), "USD", "spend")
    r.metric("usage", "Spent (all time)", _num(d.get("usage")), "USD", "spend")
    limit, remaining = _num(d.get("limit")), _num(d.get("limit_remaining"))
    r.metric("limit", "Key limit", limit, "USD", "limit")
    r.metric("limit_remaining", "Limit remaining", remaining, "USD", "balance")
    reset = d.get("limit_reset") if isinstance(d.get("limit_reset"), str) else None
    if limit and remaining is not None:
        r.limit_percent = _pct(limit - remaining, limit)
        r.period = reset or "no reset (lifetime limit)"
        r.period_start, r.resets_at = _utc_window(reset or "", now)
    else:
        r.period = "no key limit set"
    if d.get("is_free_tier") is True:
        r.note = "free tier key"
    return r


def fetch_moonshot(cfg, key, http, now) -> Reading:
    """GET https://api.moonshot.{ai,cn}/v1/users/me/balance  (platform.moonshot.ai/docs)"""
    cn = cfg.get("moonshot_region") == "cn"
    host = "api.moonshot.cn" if cn else "api.moonshot.ai"
    d = _get(http, "https://%s/v1/users/me/balance" % host, _bearer(key))
    d = d.get("data") if isinstance(d, dict) else None
    if not isinstance(d, dict):
        raise AccountError("unexpected response shape")
    cur = "CNY" if cn else "USD"
    r = Reading("moonshot", True, now, period="prepaid balance", confidence="high",
                note="" if not cn else "platform.moonshot.cn balances are in CNY")
    r.metric("available", "Available", _num(d.get("available_balance")), cur, "balance")
    r.metric("cash", "Cash", _num(d.get("cash_balance")), cur, "balance")
    r.metric("voucher", "Voucher", _num(d.get("voucher_balance")), cur, "balance")
    return r


def fetch_ollama(cfg, key, http, now) -> Reading:
    """GET https://ollama.com/api/balance  (docs.ollama.com/api/balance)"""
    d = _get(http, "https://ollama.com/api/balance", _bearer(key))
    if not isinstance(d, dict):
        raise AccountError("unexpected response shape")
    inc = d.get("included") if isinstance(d.get("included"), dict) else {}
    r = Reading("ollama_cloud", True, now, confidence="high")
    if "allowance_usd" in inc:
        bal, allow = _num(inc.get("balance_usd")), _num(inc.get("allowance_usd"))
        r.metric("included_left", "Included credits left", bal, "USD", "balance")
        r.metric("allowance", "Monthly allowance", allow, "USD", "limit")
        if bal is not None and allow:
            r.metric("included_used", "Included credits used", max(0.0, allow - bal), "USD",
                     "spend")
            r.limit_percent = _pct(max(0.0, allow - bal), allow)
        per = inc.get("period") if isinstance(inc.get("period"), dict) else {}
        r.resets_at, r.period_start = _iso(per.get("until")), _iso(per.get("from"))
        r.period = "monthly included credits"
    else:
        windows = []
        for name in ("session", "weekly"):
            w = inc.get(name) if isinstance(inc.get(name), dict) else None
            rem = _num(w.get("remaining_percent")) if w else None
            if rem is None or not 0 <= rem <= 100:
                continue
            r.metric(name + "_used_pct", name.title() + " used", 100 - rem, "%", "percent")
            windows.append((name, 100 - rem, _iso(w.get("resets_at"))))
        if windows:  # the weekly window drives the tray, else the session one
            name, used, reset = windows[-1]
            r.limit_percent, r.resets_at, r.period = used, reset, name + " limit"
            mins = {"session": 300, "weekly": 10080}[name]  # documented window lengths
            r.period_start = reset - mins * 60 if reset else None
    pur = d.get("purchased") if isinstance(d.get("purchased"), dict) else {}
    r.metric("purchased", "Purchased credits", _num(pur.get("balance_usd")), "USD", "balance")
    return r


def fetch_xai(cfg, key, http, now) -> Reading:
    """GET https://management-api.x.ai/v1/billing/teams/{team}/prepaid/balance"""
    team = str(cfg.get("xai_team_id") or apikeys.get("XAI_TEAM_ID") or "").strip()
    if not team or not all(c.isalnum() or c in "-_" for c in team):
        raise AccountError("set xai_team_id (omnimonitor config set xai_team_id <id>)")
    d = _get(http, "https://management-api.x.ai/v1/billing/teams/%s/prepaid/balance"
             % urllib.parse.quote(team), _bearer(key))
    total = d.get("total") if isinstance(d, dict) else None
    cents = _num(total.get("val")) if isinstance(total, dict) else None
    r = Reading("xai_api", True, now, period="prepaid credits", confidence="medium",
                note="xAI reports the ledger total in cents with credits as negative values; "
                     "shown here as a positive balance")
    if cents is not None:
        r.metric("balance", "Prepaid balance", -cents / 100.0, "USD", "balance")
    return r


def fetch_anthropic(cfg, key, http, now) -> Reading:
    """GET https://api.anthropic.com/v1/organizations/cost_report (Admin API key)"""
    start = month_start_utc(now)
    base = "https://api.anthropic.com/v1/organizations/cost_report"
    q = {"starting_at": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "bucket_width": "1d",
         "limit": "31"}
    hdr = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    total, today = 0.0, 0.0
    today_start = datetime.fromtimestamp(now, timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp()
    seen = False
    for _page in range(5):
        d = _get(http, base + "?" + urllib.parse.urlencode(q), hdr)
        for b in (d.get("data") or []) if isinstance(d, dict) else []:
            bstart = _iso(b.get("starting_at")) if isinstance(b, dict) else None
            for res in (b.get("results") or []) if isinstance(b, dict) else []:
                v = _num(res.get("amount")) if isinstance(res, dict) else None
                if v is None:
                    continue
                seen = True
                total += v / 100.0  # amounts are decimal strings in cents
                if bstart is not None and bstart >= today_start:
                    today += v / 100.0
        if not (isinstance(d, dict) and d.get("has_more") and d.get("next_page")):
            break
        q["page"] = str(d["next_page"])
    r = Reading("anthropic_api", True, now, period="this month (UTC)", confidence="high",
                resets_at=next_month_utc(now).timestamp(),
                note="Claude API organisation costs; claude.ai / Claude Code plan usage is "
                     "not included")
    if seen:
        r.metric("month", "Cost this month", total, "USD", "spend")
        r.metric("today", "Cost today (UTC)", today, "USD", "spend")
    return r


def fetch_openai(cfg, key, http, now) -> Reading:
    """GET https://api.openai.com/v1/organization/costs (Admin key)"""
    start = int(month_start_utc(now).timestamp())
    q = {"start_time": str(start), "bucket_width": "1d", "limit": "31"}
    total = today = 0.0
    today_start = datetime.fromtimestamp(now, timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp()
    seen = False
    for _page in range(5):
        d = _get(http, "https://api.openai.com/v1/organization/costs?"
                 + urllib.parse.urlencode(q), _bearer(key))
        for b in (d.get("data") or []) if isinstance(d, dict) else []:
            if not isinstance(b, dict):
                continue
            bstart = _num(b.get("start_time"))
            for res in b.get("results") or []:
                amt = res.get("amount") if isinstance(res, dict) else None
                v = _num(amt.get("value")) if isinstance(amt, dict) else None
                if v is None:
                    continue
                seen = True
                total += v
                if bstart is not None and bstart >= today_start:
                    today += v
        if not (isinstance(d, dict) and d.get("has_more") and d.get("next_page")):
            break
        q["page"] = str(d["next_page"])
    r = Reading("openai_api", True, now, period="this month (UTC)", confidence="high",
                resets_at=next_month_utc(now).timestamp(),
                note="OpenAI API organisation costs; ChatGPT plan usage is not included")
    if seen:
        r.metric("month", "Cost this month", total, "USD", "spend")
        r.metric("today", "Cost today (UTC)", today, "USD", "spend")
    return r


def fetch_github_copilot(cfg, key, http, now) -> Reading:
    """GET /users/{username}/settings/billing/premium_request/usage (docs.github.com)"""
    user = str(cfg.get("github_username") or "").strip()
    if not user or not all(c.isalnum() or c == "-" for c in user):
        raise AccountError("set github_username (omnimonitor config set github_username <you>)")
    d0 = datetime.fromtimestamp(now, timezone.utc)
    url = ("https://api.github.com/users/%s/settings/billing/premium_request/usage?year=%d"
           "&month=%d" % (user, d0.year, d0.month))
    d = _get(http, url, dict(_bearer(key), **{"X-GitHub-Api-Version": "2026-03-10"}))
    items = d.get("usageItems") if isinstance(d, dict) else None
    if not isinstance(items, list):
        raise AccountError("unexpected response shape")
    gross = net_usd = 0.0
    for it in items:
        if not isinstance(it, dict):
            continue
        gross += _num(it.get("grossQuantity")) or 0.0
        net_usd += _num(it.get("netAmount")) or 0.0
    r = Reading("github_copilot", True, now, period="this month (UTC)",
                resets_at=next_month_utc(now).timestamp(), confidence="medium",
                period_start=month_start_utc(now).timestamp(),
                note="premium requests billed to your personal account")
    r.metric("requests", "Premium requests this month", gross, "requests", "count")
    r.metric("net_usd", "Billed beyond allowance", net_usd, "USD", "spend")
    allowance = _num(cfg.get("copilot_premium_allowance"))
    if allowance and allowance > 0:
        r.metric("allowance", "Monthly allowance (your setting)", allowance, "requests", "limit")
        r.limit_percent = _pct(gross, allowance)
    else:
        r.note += "; set copilot_premium_allowance to see a %"
    return r


def fetch_cursor_team(cfg, key, http, now) -> Reading:
    """POST https://api.cursor.com/teams/spend (Admin API, team plans)"""
    hdr = {"Authorization": "Basic " + base64.b64encode((key + ":").encode()).decode()}
    me = str(cfg.get("cursor_email") or "").strip().lower()
    members: List[Dict[str, Any]] = []
    cycle = None
    total_members = None
    for page in range(1, 11):
        d = _get(http, "https://api.cursor.com/teams/spend", hdr,
                 body={"page": page, "pageSize": 100}, method="POST")
        if not isinstance(d, dict):
            raise AccountError("unexpected response shape")
        rows = d.get("teamMemberSpend")
        members += [m for m in rows if isinstance(m, dict)] if isinstance(rows, list) else []
        cycle = _num(d.get("subscriptionCycleStart")) or cycle
        total_members = _num(d.get("totalMembers")) or total_members
        if page >= int(_num(d.get("totalPages")) or 1):
            break
    r = Reading("cursor_team", True, now, period="current billing cycle", confidence="medium",
                note="team Admin API; individual Pro plans have no official usage API")
    on_demand = sum((_num(m.get("spendCents")) or 0) for m in members) / 100.0
    overall = sum((_num(m.get("overallSpendCents")) or 0) for m in members) / 100.0
    r.metric("team_on_demand", "Team on-demand spend", on_demand, "USD", "spend")
    if any("overallSpendCents" in m for m in members):
        r.metric("team_overall", "Team overall spend", overall, "USD", "spend")
    r.metric("members", "Members", total_members, "", "count")
    if cycle:
        r.metric("cycle_start", "Cycle started", cycle / 1000.0, "unix", "time")
        r.period_start = cycle / 1000.0
    if me:
        mine = next((m for m in members if str(m.get("email") or "").lower() == me), None)
        if mine is None:
            r.note += "; cursor_email not found in the team"
        else:
            spend = (_num(mine.get("spendCents")) or 0) / 100.0
            r.metric("my_spend", "My on-demand spend", spend, "USD", "spend")
            r.metric("my_requests", "My fast premium requests",
                     _num(mine.get("fastPremiumRequests")), "requests", "count")
            lim = _num(mine.get("effectivePerUserLimitDollars"))
            if lim is not None:
                r.metric("my_limit", "My spend limit", lim, "USD", "limit")
                r.limit_percent = _pct(spend, lim)
    return r


FETCHERS: Dict[str, Tuple[str, Callable[..., Reading]]] = {
    "deepseek": ("DEEPSEEK_API_KEY", fetch_deepseek),
    "openrouter": ("OPENROUTER_API_KEY", fetch_openrouter),
    "moonshot": ("MOONSHOT_API_KEY", fetch_moonshot),
    "ollama_cloud": ("OLLAMA_API_KEY", fetch_ollama),
    "xai_api": ("XAI_MANAGEMENT_KEY", fetch_xai),
    "anthropic_api": ("ANTHROPIC_ADMIN_KEY", fetch_anthropic),
    "openai_api": ("OPENAI_ADMIN_KEY", fetch_openai),
    "github_copilot": ("GITHUB_COPILOT_TOKEN", fetch_github_copilot),
    "cursor_team": ("CURSOR_ADMIN_KEY", fetch_cursor_team),
}


def key_for(pid: str) -> Tuple[str, str]:
    """(value, env-name) of the first configured key for a provider ('' if none)."""
    info = catalog.BY_ID.get(pid)
    names = [n for n in (info.env if info else ()) if n != "XAI_TEAM_ID"]
    for n in names:
        v = apikeys.get(n)
        if v:
            return v, n
    return "", names[0] if names else ""


def fetch(pid: str, cfg: Dict[str, Any], http: Optional[Http] = None,
          now: Optional[float] = None) -> Reading:
    now = time.time() if now is None else now
    if pid not in FETCHERS:
        return Reading(pid, False, now, error="no API reader for this provider")
    key, name = key_for(pid)
    if not key:
        return Reading(pid, False, now, error="no key: set %s (env or `omnimonitor secrets "
                                              "set %s`)" % (name, name))
    try:
        r = FETCHERS[pid][1](cfg, key, http or default_http, now)
    except AccountError as e:
        r = Reading(pid, False, now, error=str(e))
    except Exception as e:  # noqa: BLE001 - malformed data must not crash the tray
        r = Reading(pid, False, now, error="could not read response (%s)" % e.__class__.__name__)
    if key in r.error or key in r.note:  # belt and braces: never surface a key
        r.error, r.note = "error (details hidden)", ""
    return r


META_PREFIX = "reading:"


def load(history, pid: str) -> Optional[Reading]:
    if history is None:
        return None
    try:
        raw = history.meta(META_PREFIX + pid)
        return Reading.from_dict(json.loads(raw)) if raw else None
    except (ValueError, TypeError):
        return None


def refresh(cfg: Dict[str, Any], history, ids: List[str], force: bool = False,
            http: Optional[Http] = None, now: Optional[float] = None) -> Dict[str, Reading]:
    """Fetch due providers (rate-limited by provider_poll_seconds); return latest readings.
    A failed fetch keeps showing the last good numbers, marked with the error."""
    now = time.time() if now is None else now
    every = max(300, int(cfg.get("provider_poll_seconds") or 900))
    out: Dict[str, Reading] = {}
    for pid in ids:
        if pid not in FETCHERS:
            continue
        last = load(history, pid)
        if last and not force and now - max(last.fetched_at, last.attempted_at) < every:
            out[pid] = last
            continue
        r = fetch(pid, cfg, http, now)
        r.attempted_at = now
        if not r.ok and last and last.ok:
            last.error, last.attempted_at = r.error, now
            r = last
        out[pid] = r
        if history is not None:
            try:
                history.set_meta(META_PREFIX + pid, json.dumps(r.to_dict()))
            except Exception:  # noqa: BLE001
                pass
    return out
