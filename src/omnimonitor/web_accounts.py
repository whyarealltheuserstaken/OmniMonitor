"""Cookie-authenticated usage readers (opt-in web_session providers).

Endpoints and cookie names are taken from CodexBar's public docs (MIT), cited in
docs/cookies/codexbar.md. Confidence is **experimental** until verified against a
real signed-in response on Henry's Mac.

Cookies are loaded from :mod:`omnimonitor.sessions` (never logged). A 401/403 is
surfaced as "signed out — sign in again".
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple

from omnimonitor import sessions
from omnimonitor.accounts import ALLOWED_HOSTS, AccountError, Reading, _NoRedirect

# Extend the allow-list used by account HTTPS helpers for web dashboards.
WEB_HOSTS = frozenset({
    "claude.ai", "www.claude.ai",
    "chatgpt.com", "www.chatgpt.com", "openai.com", "www.openai.com",
    "cursor.com", "www.cursor.com", "authenticator.cursor.sh",
})

TIMEOUT = 20
MAX_BODY = 4 * 1024 * 1024
USER_AGENT = "OmniMonitor (+https://github.com/; read-only usage monitor)"

HttpFn = Callable[[str, str, Dict[str, str], Optional[bytes]], Tuple[int, Any]]


def _json_http(method: str, url: str, headers: Dict[str, str],
               body: Optional[bytes] = None) -> Tuple[int, Any]:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    if host not in WEB_HOSTS and host not in ALLOWED_HOSTS:
        raise AccountError("host not allow-listed")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            raw = resp.read(MAX_BODY + 1)
            if len(raw) > MAX_BODY:
                raise AccountError("response too large")
            try:
                return resp.status, json.loads(raw.decode("utf-8") or "null")
            except ValueError:
                return resp.status, None
    except urllib.error.HTTPError as e:
        raw = e.read(MAX_BODY) if hasattr(e, "read") else b""
        try:
            parsed = json.loads(raw.decode("utf-8") or "null")
        except ValueError:
            parsed = None
        return e.code, parsed
    except urllib.error.URLError as e:
        raise AccountError("network error (%s)" % type(getattr(e, "reason", e)).__name__) from e


def _cookie_or_signed_out(pid: str) -> str:
    c = sessions.load(pid)
    if not c:
        raise AccountError("signed out — enable this provider and choose in-app sign-in "
                           "or browser import in Settings")
    return c


def _auth_failed(status: int) -> bool:
    return status in (401, 403)


def fetch_claude_plan(cfg, http: HttpFn, now: float) -> Reading:
    """Claude Pro/Max session + weekly limits via claude.ai sessionKey cookie.

    CodexBar: GET /api/organizations then GET /api/organizations/{id}/usage
    (docs/claude.md, Web API section).
    """
    cookie = _cookie_or_signed_out("claude_plan")
    hdr = {
        "Cookie": cookie,
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Referer": "https://claude.ai/settings/usage",
        "Origin": "https://claude.ai",
    }
    st, orgs = http("GET", "https://claude.ai/api/organizations", hdr, None)
    if _auth_failed(st):
        return Reading("claude_plan", False, now, error="signed out — sign in again",
                       confidence="experimental")
    if st != 200 or not isinstance(orgs, list) or not orgs:
        return Reading("claude_plan", False, now,
                       error="claude.ai organizations HTTP %s" % st,
                       confidence="experimental")
    org = orgs[0] if isinstance(orgs[0], dict) else {}
    org_id = str(org.get("uuid") or org.get("id") or "")
    if not org_id or "/" in org_id or ".." in org_id:
        return Reading("claude_plan", False, now, error="no organization id in response",
                       confidence="experimental")
    url = "https://claude.ai/api/organizations/%s/usage?cedar_ember=1" % org_id
    st, usage = http("GET", url, hdr, None)
    if st in (400, 403, 404):  # some accounts reject cedar_ember
        st, usage = http("GET", "https://claude.ai/api/organizations/%s/usage" % org_id,
                         hdr, None)
    if _auth_failed(st):
        return Reading("claude_plan", False, now, error="signed out — sign in again",
                       confidence="experimental")
    if st != 200 or not isinstance(usage, dict):
        return Reading("claude_plan", False, now, error="claude.ai usage HTTP %s" % st,
                       confidence="experimental")
    r = Reading("claude_plan", True, now, period="plan windows", confidence="experimental",
                note="from claude.ai session cookie (experimental; not verified on a live account)")
    # Shape observed by CodexBar / community: five_hour / seven_day with utilization
    windows = []
    for key, label in (("five_hour", "5-hour session"), ("seven_day", "Weekly"),
                       ("seven_day_sonnet", "Weekly Sonnet"), ("seven_day_opus", "Weekly Opus")):
        block = usage.get(key) or usage.get(key.replace("_", ""))
        if not isinstance(block, dict):
            # nested under 'usage' or 'limits'
            continue
        windows.append((key, label, block))
    # Alternate shape: limits[] array (ClaudeBar /oauth-like)
    if not windows and isinstance(usage.get("limits"), list):
        for lim in usage["limits"]:
            if not isinstance(lim, dict):
                continue
            windows.append((str(lim.get("type") or lim.get("name") or "limit"),
                            str(lim.get("name") or lim.get("type") or "limit"), lim))
    best_used = None
    best_reset = None
    for key, label, block in windows:
        used = _util(block)
        if used is None:
            continue
        r.metric(key, label + " used", used, "%", "used_percent")
        resets = _reset_ts(block)
        if resets:
            r.metric(key + "_resets", label + " resets", resets, "unix", "resets_at")
        if best_used is None or used > best_used:
            best_used, best_reset = used, resets
    if best_used is None:
        # Free accounts often return null windows
        r.ok = True
        r.note = "signed in, but no quota windows in the response (Claude Free, or shape changed)"
        return r
    r.limit_percent = best_used
    r.resets_at = best_reset
    return r


def fetch_chatgpt_web(cfg, http: HttpFn, now: float) -> Reading:
    """ChatGPT / Codex web plan windows via chatgpt.com session cookies.

    CodexBar documents GET https://chatgpt.com/backend-api/wham/usage (often with a
    Bearer from the web session). We send the Cookie header; if the API still wants a
    Bearer, the in-app login path can store an Authorization value under meta later.
    Confidence: experimental.
    """
    cookie = _cookie_or_signed_out("chatgpt_web")
    hdr = {
        "Cookie": cookie,
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Referer": "https://chatgpt.com/",
        "Origin": "https://chatgpt.com",
        "oai-language": "en-US",
    }
    # Optional bearer stored alongside cookies (in-app login may capture it)
    meta_bearer = _session_meta_token("chatgpt_web")
    if meta_bearer:
        hdr["Authorization"] = "Bearer " + meta_bearer
    st, data = http("GET", "https://chatgpt.com/backend-api/wham/usage", hdr, None)
    if _auth_failed(st):
        return Reading("chatgpt_web", False, now, error="signed out — sign in again",
                       confidence="experimental")
    if st != 200 or not isinstance(data, dict):
        return Reading("chatgpt_web", False, now, error="chatgpt.com usage HTTP %s" % st,
                       confidence="experimental")
    r = Reading("chatgpt_web", True, now, period="plan windows", confidence="experimental",
                note="from chatgpt.com session (experimental; shape from CodexBar docs)")
    # rate_limit.primary_window / secondary_window (CodexBar codex.md)
    rl = data.get("rate_limit") if isinstance(data.get("rate_limit"), dict) else data
    best = None
    for key, label in (("primary_window", "Session"), ("secondary_window", "Weekly"),
                       ("primary", "Session"), ("secondary", "Weekly")):
        block = rl.get(key) if isinstance(rl, dict) else None
        if not isinstance(block, dict):
            continue
        used = _util(block)
        # alternate: used_percent / limit_window used ratio
        if used is None and "used_percent" in block:
            try:
                used = float(block["used_percent"])
            except (TypeError, ValueError):
                used = None
        if used is None and block.get("limit_window") and block.get("used"):
            try:
                used = 100.0 * float(block["used"]) / float(block["limit_window"])
            except (TypeError, ValueError, ZeroDivisionError):
                used = None
        if used is None:
            continue
        r.metric(key, label + " used", used, "%", "used_percent")
        resets = _reset_ts(block)
        if best is None or used > best:
            best = used
            r.limit_percent = used
            r.resets_at = resets
    if best is None:
        r.note += "; no rate_limit windows parsed — response shape may have changed"
    return r


def fetch_cursor(cfg, http: HttpFn, now: float) -> Reading:
    """Cursor individual plan usage via cursor.com session cookie.

    CodexBar: GET https://cursor.com/api/usage-summary (docs/cursor.md).
    """
    cookie = _cookie_or_signed_out("cursor")
    hdr = {
        "Cookie": cookie,
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Referer": "https://cursor.com/settings",
        "Origin": "https://cursor.com",
    }
    st, data = http("GET", "https://cursor.com/api/usage-summary", hdr, None)
    if _auth_failed(st):
        return Reading("cursor", False, now, error="signed out — sign in again",
                       confidence="experimental")
    if st != 200 or not isinstance(data, dict):
        return Reading("cursor", False, now, error="cursor.com usage-summary HTTP %s" % st,
                       confidence="experimental")
    r = Reading("cursor", True, now, period="billing cycle", confidence="experimental",
                note="from cursor.com session cookie (experimental)")
    # Flexible parse: individual plan fields vary; accept several documented shapes
    used = None
    limit = None
    plan = data.get("individualUsage") or data.get("planUsage") or data.get("usage") or data
    if isinstance(plan, dict):
        nested = plan.get("plan") if isinstance(plan.get("plan"), dict) else plan
        for ukey, lkey in (("used", "limit"), ("numRequests", "maxRequestUsage"),
                           ("totalSpend", "spendLimit"), ("spend", "limit")):
            if ukey in nested and lkey in nested:
                try:
                    used, limit = float(nested[ukey]), float(nested[lkey])
                    break
                except (TypeError, ValueError):
                    pass
        if "usedPercent" in nested:
            try:
                used_pct = float(nested["usedPercent"])
                r.limit_percent = used_pct
                r.metric("plan_used", "Plan used", used_pct, "%", "used_percent")
            except (TypeError, ValueError):
                pass
        if used is not None and limit and limit > 0 and r.limit_percent is None:
            r.limit_percent = max(0.0, min(100.0, 100.0 * used / limit))
            r.metric("plan_used", "Plan used", r.limit_percent, "%", "used_percent")
            r.metric("plan_used_abs", "Plan used (raw)", used, "", "count")
            r.metric("plan_limit", "Plan limit", limit, "", "limit")
        # billing cycle end
        for rk in ("billingCycleEnd", "nextReset", "resetsAt", "periodEnd"):
            if rk in data or (isinstance(nested, dict) and rk in nested):
                src = data if rk in data else nested
                r.resets_at = _reset_ts({rk: src.get(rk), "resets_at": src.get(rk)})
                break
    on_demand = data.get("onDemandUsage") or data.get("onDemand")
    if isinstance(on_demand, dict):
        for k in ("used", "spend", "totalSpend"):
            if k in on_demand:
                try:
                    r.metric("on_demand", "On-demand spend", float(on_demand[k]), "USD", "usd")
                except (TypeError, ValueError):
                    pass
                break
    if r.limit_percent is None and not r.metrics:
        r.note += "; signed in but could not parse usage-summary fields"
    _attach_grok_bot_meter(r, http, hdr)
    return r


def parse_sand_usage(data: Any) -> Dict[str, Any]:
    """Grok Bot weekly meter from get-sand-usage-status.

    Returns ``{available, used_percent, resets_at, reason}``. ``usagePercent`` is a
    percent, not a token count. A missing percent is not 0. An account with no
    personal allowance (``hasNonZeroIncludedLimit`` is not true, pooled enterprise,
    or ``includedLimitZero``) is not shown as 0% used.
    """
    if not isinstance(data, dict):
        return {"available": False, "reason": "sand usage response was not an object"}
    if data.get("usesPooledEnterpriseAllowance") is True or data.get(
            "uses_pooled_enterprise_allowance") is True:
        return {"available": False,
                "reason": "uses a pooled enterprise allowance; no personal Grok Bot percent"}
    if data.get("includedLimitZero") is True or data.get("included_limit_zero") is True:
        return {"available": False,
                "reason": "includedLimitZero: this account has no Grok Bot allowance"}
    flag = data.get("hasNonZeroIncludedLimit", data.get("has_non_zero_included_limit"))
    if flag is not True:
        return {"available": False,
                "reason": "hasNonZeroIncludedLimit is not true, so there is no personal "
                          "Grok Bot allowance"}
    raw = data.get("usagePercent", data.get("usage_percent"))
    if raw is None:
        return {"available": False, "reason": "usagePercent was omitted (not treated as 0)"}
    try:
        pct = float(raw)
    except (TypeError, ValueError):
        return {"available": False, "reason": "usagePercent was not a number"}
    reset = data.get("nextResetTimestampUtc") or data.get("next_reset_timestamp_utc")
    return {"available": True, "used_percent": pct,
            "resets_at": reset if isinstance(reset, str) else None, "reason": ""}


def _attach_grok_bot_meter(r: Reading, http: HttpFn, hdr: Dict[str, str]) -> None:
    """Best-effort. A failure leaves the Cursor plan reading intact. No cookie is logged."""
    sand_hdr = dict(hdr)
    sand_hdr["Content-Type"] = "application/json"
    sand_hdr["Referer"] = "https://cursor.com/bot"
    try:
        st, data = http("POST", "https://cursor.com/api/dashboard/get-sand-usage-status",
                        sand_hdr, b"{}")
    except AccountError:
        r.note = (r.note + "; " if r.note else "") + "Grok Bot meter was not read (request failed)"
        return
    if st != 200:
        r.note = (r.note + "; " if r.note else "") + (
            "Grok Bot meter was not read (HTTP %s from get-sand-usage-status)" % st)
        return
    parsed = parse_sand_usage(data)
    if not parsed.get("available"):
        r.note = (r.note + "; " if r.note else "") + "Grok Bot meter: " + parsed.get("reason", "")
        return
    r.metric("grok_bot_used_percent", "Grok Bot used", parsed["used_percent"], "%", "used_percent")
    reset = _reset_ts({"resets_at": parsed.get("resets_at")}) if parsed.get("resets_at") else None
    if reset:
        r.metric("grok_bot_resets_at", "Grok Bot resets", reset, "unix", "resets_at")


def _session_meta_token(pid: str) -> str:
    """Optional bearer stored in the session file's meta (never logged)."""
    # sessions.load only returns the cookie; re-read file/keyring payload for meta
    from omnimonitor import sessions as S
    kr = S._keyring()
    raw = None
    if kr is not None:
        try:
            raw = kr.get_password(S._SERVICE, pid)
        except Exception:  # noqa: BLE001
            raw = None
    if not raw:
        path = S._file(pid)
        try:
            raw = path.read_text("utf-8")
        except OSError:
            return ""
    try:
        data = json.loads(raw)
    except ValueError:
        return ""
    meta = data.get("meta") if isinstance(data, dict) else None
    tok = (meta or {}).get("bearer") if isinstance(meta, dict) else None
    return tok.strip() if isinstance(tok, str) else ""


def _util(block: Dict[str, Any]) -> Optional[float]:
    for k in ("utilization", "used_percent", "usedPercent", "percent_used",
              "percentage", "pct"):
        if k in block and block[k] is not None:
            try:
                v = float(block[k])
            except (TypeError, ValueError):
                continue
            # utilization is often 0..1
            if 0.0 <= v <= 1.0 and k == "utilization":
                return round(v * 100.0, 4)
            return max(0.0, min(200.0, v))  # allow >100 like Grok
    # remaining_percent → used
    if "remaining_percent" in block:
        try:
            return max(0.0, min(100.0, 100.0 - float(block["remaining_percent"])))
        except (TypeError, ValueError):
            pass
    return None


def _reset_ts(block: Dict[str, Any]) -> Optional[float]:
    for k in ("resets_at", "reset_at", "resetsAt", "resetAt", "nextReset",
              "next_reset", "end", "expires_at", "deadline"):
        v = block.get(k)
        if v is None:
            continue
        if isinstance(v, (int, float)):
            # ms vs s
            return float(v) / 1000.0 if v > 1e12 else float(v)
        if isinstance(v, str):
            from omnimonitor.timeutil import parse_time
            dt = parse_time(v)
            if dt:
                return dt.timestamp()
    return None


FETCHERS = {
    "claude_plan": fetch_claude_plan,
    "chatgpt_web": fetch_chatgpt_web,
    "cursor": fetch_cursor,
}


def fetch(pid: str, cfg, http: Optional[HttpFn] = None, now: Optional[float] = None) -> Reading:
    now = time.time() if now is None else now
    fn = FETCHERS.get(pid)
    if not fn:
        return Reading(pid, False, now, error="no web-session reader")
    try:
        return fn(cfg, http or _json_http, now)
    except AccountError as e:
        return Reading(pid, False, now, error=str(e), confidence="experimental")
    except Exception as e:  # noqa: BLE001
        return Reading(pid, False, now, error="could not read (%s)" % type(e).__name__,
                       confidence="experimental")
