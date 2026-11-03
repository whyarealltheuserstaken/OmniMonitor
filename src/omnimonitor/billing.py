"""Weekly usage % sources. Everything about WHERE the % comes from lives here.

UNOFFICIAL: the `grok_cli_billing` source calls the same undocumented endpoint the
Grok CLI's `/usage` panel calls. It can change or break at any time.

    GET https://cli-chat-proxy.grok.com/v1/billing?format=credits
    Authorization: Bearer <access token from the Grok CLI's auth.json>
    X-XAI-Token-Auth: xai-grok-cli

Observed response (third-party captures, no schema guarantee; proto-JSON OMITS zero values):
    {"config": {"currentPeriod": {"type": "USAGE_PERIOD_TYPE_WEEKLY", "start": "...", "end": "..."},
                "creditUsagePercent": 28.0,                      # USED percent, may exceed 100
                "productUsage": [{"product": "GrokBuild", "usagePercent": 20.0}, {"product": "GrokChat"}],
                "onDemandCap": {"val": 0}, "onDemandUsed": {"val": 0}, "prepaidBalance": {"val": 0},
                "isUnifiedBillingUser": true, "billingPeriodStart": "...", "billingPeriodEnd": "..."}}
"""

from __future__ import annotations

import json
import os
import random
import shlex
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from omnimonitor import __version__
from omnimonitor.config import DEFAULT_BILLING_URL, grok_auth_file, grok_home
from omnimonitor.timeutil import parse_time, utcnow


class UsageError(Exception):
    """A user-presentable reason why the number is not available."""

@dataclass
class UsageResult:
    remaining_percent: float                    # 0..100 (clamped)
    resets_at: Optional[datetime] = None
    label: str = ""
    is_demo: bool = False
    used_percent: Optional[float] = None        # 0..100 (clamped)
    raw_used_percent: Optional[float] = None    # as reported (may be > 100)
    period_start: Optional[datetime] = None
    period_type: str = ""
    products: List[Tuple[str, float]] = field(default_factory=list)
    on_demand_cap: Optional[float] = None
    on_demand_used: Optional[float] = None
    token_fields: Dict[str, float] = field(default_factory=dict)  # any token-ish numbers seen
    detail_lines: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        def iso(d):
            return d.isoformat() if d else None
        return {
            "remaining_percent": self.remaining_percent,
            "used_percent": self.used_percent,
            "raw_used_percent": self.raw_used_percent,
            "resets_at": iso(self.resets_at),
            "period_start": iso(self.period_start),
            "period_type": self.period_type,
            "label": self.label,
            "is_demo": self.is_demo,
            "products": [{"product": p, "used_percent": v} for p, v in self.products],
            "on_demand_cap": self.on_demand_cap,
            "on_demand_used": self.on_demand_used,
            "token_fields": dict(self.token_fields),
            "detail_lines": list(self.detail_lines),
        }

# --------------------------------------------------------------------------- parsing

def _num(x: Any, what: str) -> float:
    if isinstance(x, bool):
        raise UsageError("%s is not a number: %r" % (what, x))
    try:
        v = float(x)
    except (TypeError, ValueError):
        raise UsageError("%s missing or not a number: %r" % (what, x))
    if v != v or v in (float("inf"), float("-inf")):
        raise UsageError("%s is not finite: %r" % (what, x))
    return v

def _clamp(v: float) -> float:
    return min(100.0, max(0.0, v))

def _val(obj: Any) -> Optional[float]:
    """Read proto-JSON money/int wrappers like {"val": "123"}; missing -> 0 (zero is omitted)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        obj = obj.get("val", 0)
    try:
        return float(obj)
    except (TypeError, ValueError):
        return None

def _find_token_fields(obj: Any, prefix: str = "", out: Optional[Dict[str, float]] = None,
                       depth: int = 0) -> Dict[str, float]:
    """Defensively collect any numeric field whose name mentions tokens.

    As of 2026-11 the billing response is NOT known to contain token counts or a token
    limit; this exists so that if xAI ever adds e.g. `weeklyTokenLimit`, it is surfaced
    (raw, labeled with its field name) instead of silently ignored.
    """
    out = {} if out is None else out
    if depth > 6:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = "%s.%s" % (prefix, k) if prefix else str(k)
            if "token" in str(k).lower():
                n = _val(v) if isinstance(v, dict) else (
                    float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None)
                if n is not None:
                    out[key] = n
                    continue
            if isinstance(v, (dict, list)):
                _find_token_fields(v, key, out, depth + 1)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:50]):
            _find_token_fields(v, "%s[%d]" % (prefix, i), out, depth + 1)
    return out

def parse_billing(body: str) -> UsageResult:
    """Parse the billing response. Raises UsageError with a reason; never invents numbers.

    Rules (keep these - tests pin them):
      * `creditUsagePercent` is the USED percent; remaining = 100 - used.
      * The API omits zero-valued fields, so a missing percent on an otherwise valid
        response (has currentPeriod / productUsage / billingPeriodEnd) means 0% used.
      * Values > 100 (over the pool, e.g. on-demand credits) clamp to 100% used / 0% left;
        the raw value is kept in raw_used_percent.
      * Present-but-non-numeric percent = schema change -> error (not a silent 0).
    """
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        raise UsageError("billing response was not JSON")
    if not isinstance(data, dict):
        raise UsageError("billing response was not a JSON object")
    if data.get("error") and "config" not in data:
        err = str(data.get("error"))
        if "personal team" in err.lower():
            raise UsageError("billing: this login is a team/business account (\"No personal "
                             "team\"); the weekly pool is not available for it")
        raise UsageError("billing error: %s" % err[:160])
    c = data.get("config", data)
    if not isinstance(c, dict):
        raise UsageError("billing response has no config object")
    raw = c.get("creditUsagePercent")
    if raw is None:
        if c.get("currentPeriod") or "productUsage" in c or "billingPeriodEnd" in c:
            raw = 0.0  # zero omitted by proto-JSON
        else:
            raise UsageError("billing response has no creditUsagePercent / currentPeriod "
                             "(format changed?)")
    raw_used = _num(raw, "creditUsagePercent")
    used = _clamp(raw_used)
    period = c.get("currentPeriod") if isinstance(c.get("currentPeriod"), dict) else {}
    reset = parse_time(period.get("end")) or parse_time(c.get("billingPeriodEnd"))
    start = parse_time(period.get("start")) or parse_time(c.get("billingPeriodStart"))
    products: List[Tuple[str, float]] = []
    details: List[str] = []
    for p in c.get("productUsage") or []:
        if isinstance(p, dict) and p.get("product"):
            try:
                pv = float(p.get("usagePercent") or 0)  # omitted = 0
            except (TypeError, ValueError):
                continue
            products.append((str(p["product"]), pv))
            details.append("%s: %.0f%% used" % (p["product"], pv))
    if raw_used > 100:
        details.append("Reported %.1f%% used (over the weekly pool)" % raw_used)
    return UsageResult(
        remaining_percent=100.0 - used, resets_at=reset, label="Grok billing (unofficial)",
        used_percent=used, raw_used_percent=raw_used, period_start=start,
        period_type=str(period.get("type") or ""), products=products,
        on_demand_cap=_val(c.get("onDemandCap")) if "onDemandCap" in c else None,
        on_demand_used=_val(c.get("onDemandUsed")) if "onDemandUsed" in c else None,
        token_fields=_find_token_fields(c), detail_lines=details)

# --------------------------------------------------------------------------- auth

def read_grok_auth(path: Path) -> Tuple[str, Optional[datetime]]:
    """Read the access token from the Grok CLI's own auth.json. READ-ONLY.

    Shape: {"<scope>": {"auth_mode": "oidc", "key": "<access token>", "refresh_token": ...,
            "expires_at": "...Z"}}. We only use `key` + `expires_at`; never the refresh token.
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            store = json.load(f)
    except FileNotFoundError:
        raise UsageError("Grok CLI auth file not found (%s) - install the Grok CLI and run "
                         "`grok login`" % path)
    except Exception as e:  # noqa: BLE001
        raise UsageError("cannot read %s: %s" % (path, e))
    if not isinstance(store, dict):
        raise UsageError("auth file is not a JSON object")
    entries = [(k, v) for k, v in store.items()
               if isinstance(v, dict) and isinstance(v.get("key"), str) and v["key"].strip()]

    def rank(item):
        k, v = item
        mode = v.get("auth_mode")
        if mode == "oidc":
            return 0 if "x.ai" in k else 1
        if mode == "web_login":
            return 9  # legacy entry the CLI itself ignores
        return 2
    entries = sorted((e for e in entries if rank(e) < 9), key=rank)
    if not entries:
        raise UsageError("no usable login in %s - run `grok login` once" % path)
    entry = entries[0][1]
    return entry["key"].strip(), parse_time(entry.get("expires_at"))

def find_grok_binary(cfg: Dict[str, Any]) -> Optional[str]:
    found = shutil.which("grok")
    if found:
        return found
    names = ["grok.exe", "grok"] if sys.platform == "win32" else ["grok"]
    for n in names:
        p = grok_home(cfg) / "bin" / n
        if p.is_file():
            return str(p)
    for extra in ("/opt/homebrew/bin/grok", "/usr/local/bin/grok"):
        if sys.platform != "win32" and os.path.isfile(extra):
            return extra
    return None

def refresh_argv(cfg: Dict[str, Any]) -> Optional[List[str]]:
    rc = cfg.get("grok_refresh_command")
    if not rc:
        return None
    if isinstance(rc, list):
        argv = [str(a) for a in rc]
    elif str(rc).strip() == "auto":
        b = find_grok_binary(cfg)
        return [b, "models"] if b else None
    else:
        argv = shlex.split(str(rc), posix=(os.name == "posix"))
    if argv:
        argv[0] = os.path.expanduser(argv[0])
    return argv or None

def run_refresh(cfg: Dict[str, Any]) -> None:
    """Ask the OFFICIAL Grok CLI to refresh its own token (it rewrites auth.json itself)."""
    argv = refresh_argv(cfg)
    if not argv:
        raise UsageError("token expired and no Grok CLI found to refresh it - run `grok` once")
    kwargs: Dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        out = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                             timeout=float(cfg.get("refresh_timeout_seconds") or 60), **kwargs)
    except subprocess.TimeoutExpired:
        raise UsageError("Grok CLI token refresh timed out - run `grok login`")
    except OSError as e:
        raise UsageError("cannot run Grok CLI for refresh: %s" % e)
    if out.returncode != 0:
        raise UsageError("Grok CLI refresh exited %d - you may need `grok login`" % out.returncode)

def _token_is_stale(expires_at: Optional[datetime], cfg: Dict[str, Any]) -> bool:
    if expires_at is None:
        return False
    skew = float(cfg.get("refresh_skew_seconds") or 0)
    return (expires_at - utcnow()).total_seconds() <= skew

# --------------------------------------------------------------------------- HTTP

def check_billing_url(url: str) -> str:
    """Only send the token to *.grok.com over HTTPS (or loopback for local tests)."""
    u = urllib.parse.urlsplit(url)
    host = (u.hostname or "").lower()
    if u.scheme == "https" and (host == "grok.com" or host.endswith(".grok.com")):
        return url
    if u.scheme == "http" and host in ("127.0.0.1", "localhost", "::1"):
        return url
    raise UsageError("grok_billing_url must be https://*.grok.com (or loopback for tests)")

class AuthRejected(Exception):
    def __init__(self, code: int):
        super().__init__("HTTP %d" % code)
        self.code = code

def billing_request(url: str, token: str, timeout: float) -> str:
    req = urllib.request.Request(check_billing_url(url), headers={
        "Authorization": "Bearer " + token,
        "X-XAI-Token-Auth": "xai-grok-cli",
        "Accept": "application/json",
        "User-Agent": "omnimonitor/%s" % __version__,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (checked above)
            return r.read(2_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AuthRejected(e.code)
        body = ""
        try:
            body = e.read(4000).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        if e.code == 412 and "personal team" in body.lower():
            raise UsageError("billing: team/business login (\"No personal team\") - weekly pool"
                             " not available for this account")
        raise UsageError("HTTP %d from billing endpoint" % e.code)
    except urllib.error.URLError as e:
        raise UsageError("network error: %s" % e.reason)
    except (TimeoutError, OSError) as e:
        raise UsageError("network error: %s" % e)

# --------------------------------------------------------------------------- sources

def fetch_demo(cfg: Dict[str, Any], now: Optional[datetime] = None) -> UsageResult:
    """DEMO MODE: fake, slowly changing numbers. NOT anyone's real usage."""
    now = now or utcnow()
    days_to_monday = (7 - now.weekday()) % 7 or 7
    reset = (now + timedelta(days=days_to_monday)).replace(hour=0, minute=0, second=0,
                                                           microsecond=0)
    start = reset - timedelta(days=7)
    frac = 1.0 - (reset - now).total_seconds() / (7 * 86400.0)
    used = _clamp(frac * 85.0 + random.uniform(-2, 2))
    return UsageResult(100.0 - used, reset, label="DEMO (fake data)", is_demo=True,
                       used_percent=used, raw_used_percent=used, period_start=start,
                       period_type="DEMO", products=[("GrokBuild", round(used, 1))],
                       detail_lines=["DEMO MODE - fake numbers, not your account"])

def fetch_grok_cli_billing(cfg: Dict[str, Any]) -> UsageResult:
    url = cfg.get("grok_billing_url") or DEFAULT_BILLING_URL
    timeout = float(cfg.get("timeout_seconds") or 15)
    manual = (cfg.get("token") or "").strip()
    can_refresh = not manual and refresh_argv(cfg) is not None
    refreshed = False
    if manual:
        token = manual
    else:
        path = grok_auth_file(cfg)
        token, exp = read_grok_auth(path)
        if _token_is_stale(exp, cfg):
            if not can_refresh:
                raise UsageError("Grok CLI token expired - run `grok` once (no CLI found to "
                                 "refresh automatically)")
            run_refresh(cfg)
            refreshed = True
            token, _ = read_grok_auth(path)
    try:
        body = billing_request(url, token, timeout)
    except AuthRejected as e:
        if can_refresh and not refreshed:
            run_refresh(cfg)
            token, _ = read_grok_auth(grok_auth_file(cfg))
            try:
                body = billing_request(url, token, timeout)
            except AuthRejected as e2:
                raise UsageError("HTTP %d after CLI refresh - run `grok login` once" % e2.code)
        elif manual:
            raise UsageError("HTTP %d: pasted token expired/invalid" % e.code)
        else:
            raise UsageError("HTTP %d: run `grok login` once" % e.code)
    return parse_billing(body)

def fetch_command(cfg: Dict[str, Any]) -> UsageResult:
    """Your own command; must print {"remaining_percent": 72, "resets_at": "..."}."""
    cmd = cfg.get("command")
    if not cmd:
        raise UsageError("source=command but 'command' is empty")
    argv = [str(a) for a in cmd] if isinstance(cmd, list) else shlex.split(
        str(cmd), posix=(os.name == "posix"))
    try:
        out = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                             timeout=float(cfg.get("timeout_seconds") or 15))
    except subprocess.TimeoutExpired:
        raise UsageError("command timed out")
    except OSError as e:
        raise UsageError("cannot run command: %s" % e)
    if out.returncode != 0:
        raise UsageError("command exited %d" % out.returncode)
    try:
        d = json.loads(out.stdout)
    except ValueError:
        raise UsageError("command did not print JSON")
    if not isinstance(d, dict):
        raise UsageError("command output is not a JSON object")
    if "remaining_percent" in d:
        rem = _clamp(_num(d["remaining_percent"], "remaining_percent"))
    else:
        rem = 100.0 - _clamp(_num(d.get("used_percent"), "used_percent"))
    return UsageResult(rem, parse_time(d.get("resets_at")), label="custom command",
                       used_percent=100.0 - rem)

SOURCES = {
    "demo": fetch_demo,
    "grok_cli_billing": fetch_grok_cli_billing,
    "command": fetch_command,
}

def fetch_usage(cfg: Dict[str, Any]) -> UsageResult:
    name = cfg.get("source") or "demo"
    fn = SOURCES.get(name)
    if fn is None:
        raise UsageError("unknown source %r (use: %s)" % (name, ", ".join(sorted(SOURCES))))
    return fn(cfg)

def fetch_usage_safely(cfg: Dict[str, Any]) -> Tuple[Optional[UsageResult], Optional[str]]:
    try:
        return fetch_usage(cfg), None
    except UsageError as e:
        return None, str(e)
    except Exception as e:  # noqa: BLE001 - never crash the tray
        return None, "%s: %s" % (type(e).__name__, e)
