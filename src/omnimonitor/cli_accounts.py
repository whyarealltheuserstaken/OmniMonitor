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
CONF = "experimental"

_CLAUDE_WINDOWS = (("five_hour", "5-hour"), ("seven_day", "Weekly"),
                   ("seven_day_opus", "Weekly (Opus)"), ("seven_day_sonnet", "Weekly (Sonnet)"))


def _default_http() -> HttpFn:
    from omnimonitor import web_accounts
    return web_accounts._json_http


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
        r.metric(key, name + " used", used, "%", "used_percent")
        r.limits.append({"name": name, "used_percent": used, "resets_at": resets})
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


FETCHERS = {"claude_cli_plan": fetch_claude, "codex_cli_plan": fetch_codex}


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
