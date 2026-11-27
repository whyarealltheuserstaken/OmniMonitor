"""Plan-limit readers that reuse the login an official CLI already holds.

Claude Code, Codex and Gemini CLI each keep an OAuth login on this computer
(:mod:`omnimonitor.cli_logins`). The same endpoints their own apps use report the plan's
usage windows. These endpoints are undocumented by the vendors (shapes come from public
community clients), so every source is ``experimental`` and degrades to a clear message.

Tokens are used for one request and never stored, logged or placed in a ``Reading``.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

from omnimonitor import cli_logins
from omnimonitor.accounts import Reading
from omnimonitor.timeutil import parse_time

HttpFn = Callable[[str, str, Dict[str, str], Optional[bytes]], Tuple[int, Any]]

CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
GEMINI_LOAD_URL = "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
GEMINI_QUOTA_URL = "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota"
CONF = "experimental"

_CLAUDE_WINDOWS = (("five_hour", "5-hour"), ("seven_day", "Weekly"),
                   ("seven_day_opus", "Weekly (Opus)"), ("seven_day_sonnet", "Weekly (Sonnet)"))


#: The only hosts a CLI login token may be sent to (kept apart from the cookie readers').
CLI_HOSTS = frozenset({"api.anthropic.com", "chatgpt.com", "cloudcode-pa.googleapis.com"})


def _cli_http(method: str, url: str, headers: Dict[str, str],
              body: Optional[bytes] = None) -> Tuple[int, Any]:
    from urllib.parse import urlparse

    from omnimonitor import web_accounts
    from omnimonitor.accounts import AccountError
    u = urlparse(url)
    if u.scheme != "https" or (u.hostname or "").lower() not in CLI_HOSTS:
        raise AccountError("host not allow-listed")
    return web_accounts._request(method, url, headers, body)


def _default_http() -> HttpFn:
    return _cli_http


def _failed(pid: str, now: float, msg: str) -> Reading:
    return Reading(pid, False, now, error=msg, confidence=CONF)


def _iso(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v) / 1000.0 if v > 1e11 else float(v)
    if isinstance(v, str) and v:
        dt = parse_time(v)
        return dt.timestamp() if dt else None
    return None


def fetch_claude(cfg: Dict[str, Any], http: HttpFn, now: float) -> Reading:
    pid = "claude_cli_plan"
    try:
        login = cli_logins.claude(cfg, now)
    except (cli_logins.LoginMissing, cli_logins.LoginExpired) as e:
        return _failed(pid, now, str(e))
    st, data = http("GET", CLAUDE_USAGE_URL, {
        "Authorization": "Bearer " + login.token,
        "anthropic-beta": "oauth-2025-04-20", "Accept": "application/json"}, None)
    if st in (401, 403):
        return _failed(pid, now, "login rejected - run `claude` once to refresh it")
    if st != 200 or not isinstance(data, dict):
        return _failed(pid, now, "api.anthropic.com usage HTTP %s" % st)
    r = Reading(pid, True, now, period="plan windows", confidence=CONF,
                note="from your Claude Code login (experimental; undocumented endpoint)")
    best = None
    for key, name in _CLAUDE_WINDOWS:
        block = data.get(key)
        if not isinstance(block, dict):
            continue
        u = block.get("utilization")
        if isinstance(u, bool) or not isinstance(u, (int, float)):
            continue
        used, resets = max(0.0, min(200.0, float(u))), _iso(block.get("resets_at"))
        r.metric(key, name + " used", used, "%", "used_percent", in_limits=True)
        r.limits.append({"name": name, "used_percent": used, "resets_at": resets,
                         "window_seconds": 18000 if key == "five_hour" else 7 * 86400})
        if best is None or used > best:
            best, r.limit_percent, r.resets_at = used, used, resets
    if best is None:
        r.note = "this plan reports no usage windows (for example an enterprise plan)"
    return r


def fetch_codex(cfg: Dict[str, Any], http: HttpFn, now: float) -> Reading:
    pid = "codex_cli_plan"
    try:
        login = cli_logins.codex(cfg, now)
    except (cli_logins.LoginMissing, cli_logins.LoginExpired) as e:
        return _failed(pid, now, str(e))
    hdr = {"Authorization": "Bearer " + login.token, "Accept": "application/json"}
    if login.extra.get("account_id"):
        hdr["chatgpt-account-id"] = login.extra["account_id"]
    st, data = http("GET", CODEX_USAGE_URL, hdr, None)
    if st in (401, 403):
        return _failed(pid, now, "login rejected - run `codex` once to refresh it")
    if st != 200 or not isinstance(data, dict):
        return _failed(pid, now, "chatgpt.com usage HTTP %s" % st)
    from omnimonitor import web_accounts
    r = Reading(pid, True, now, period="plan windows", confidence=CONF,
                note="from your Codex login (experimental; undocumented endpoint)")
    web_accounts.parse_plan_windows(data, r)
    return r


def fetch_gemini(cfg: Dict[str, Any], http: HttpFn, now: float) -> Reading:
    """Per-model request quota for the Google login Gemini CLI holds (Code Assist)."""
    import json
    pid = "gemini_cli_quota"
    try:
        login = cli_logins.gemini(cfg, now)
    except (cli_logins.LoginMissing, cli_logins.LoginExpired) as e:
        return _failed(pid, now, str(e))
    hdr = {"Authorization": "Bearer " + login.token, "Content-Type": "application/json",
           "Accept": "application/json"}
    body = json.dumps({"metadata": {"ideType": "GEMINI_CLI", "pluginType": "GEMINI"}}).encode()
    st, loaded = http("POST", GEMINI_LOAD_URL, hdr, body)
    if st in (401, 403):
        return _failed(pid, now, "login rejected - run `gemini` once to refresh it")
    if st != 200:
        return _failed(pid, now, "Gemini Code Assist HTTP %s" % st)
    project = loaded.get("cloudaicompanionProject") if isinstance(loaded, dict) else None
    qbody = {"project": project} if isinstance(project, str) and project else {}
    st, data = http("POST", GEMINI_QUOTA_URL, hdr, json.dumps(qbody).encode())
    if st in (401, 403):
        return _failed(pid, now, "login rejected - run `gemini` once to refresh it")
    if st != 200 or not isinstance(data, dict):
        return _failed(pid, now, "Gemini quota HTTP %s" % st)
    r = Reading(pid, True, now, period="per-model quota", confidence=CONF,
                note="from your Gemini CLI login (experimental; internal endpoint)")
    tightest: Dict[str, Tuple[float, Optional[float]]] = {}
    for b in data.get("buckets") or []:
        if not isinstance(b, dict) or not isinstance(b.get("modelId"), str):
            continue
        frac = b.get("remainingFraction")
        if isinstance(frac, bool) or not isinstance(frac, (int, float)):
            continue
        used = round(max(0.0, min(100.0, 100.0 * (1.0 - float(frac)))), 4)
        cur = tightest.get(b["modelId"])
        if cur is None or used > cur[0]:
            tightest[b["modelId"]] = (used, _iso(b.get("resetTime")))
    for model, (used, resets) in sorted(tightest.items(), key=lambda kv: -kv[1][0]):
        r.metric(model, model + " used", used, "%", "used_percent")
        r.limits.append({"name": model, "used_percent": used, "resets_at": resets})
    if r.limits:
        r.limit_percent, r.resets_at = r.limits[0]["used_percent"], r.limits[0]["resets_at"]
    else:
        r.note = "Gemini returned no quota buckets for this login"
    return r


FETCHERS = {"claude_cli_plan": fetch_claude, "codex_cli_plan": fetch_codex,
            "gemini_cli_quota": fetch_gemini}


def fetch(pid: str, cfg: Dict[str, Any], http: Optional[HttpFn] = None,
          now: Optional[float] = None) -> Reading:
    """Never raises: any failure becomes ``Reading(ok=False, error=...)`` with no secrets."""
    import time
    now = time.time() if now is None else now
    fn = FETCHERS.get(pid)
    if fn is None:
        return _failed(pid, now, "no reader for this source")
    try:
        return fn(cfg, http or _default_http(), now)
    except Exception as e:  # noqa: BLE001 - malformed data must not crash the tray
        return _failed(pid, now, "could not read response (%s)" % e.__class__.__name__)
