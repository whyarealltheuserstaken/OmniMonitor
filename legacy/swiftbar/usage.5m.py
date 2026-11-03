#!/usr/bin/env python3
# <xbar.title>Weekly Usage Remaining</xbar.title>
# <xbar.version>v0.2</xbar.version>
# <xbar.desc>Shows the remaining % of the X Subscription / SuperGrok weekly usage pool.</xbar.desc>
# <xbar.dependencies>python3 (stdlib only)</xbar.dependencies>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>false</swiftbar.hideSwiftBar>
"""
Weekly usage menu bar plugin (SwiftBar / xbar). The "5m" in the filename
makes SwiftBar re-run it every 5 minutes.

Everything about WHERE the number comes from lives in ONE function:
    fetch_usage(config) -> UsageResult
Swap or edit that function (or pick a different "source" in the config file)
without touching any of the display code.

Config: ~/.config/usage-menubar/config.json   (see config.example.json)
Env overrides: USAGE_MENUBAR_SOURCE, USAGE_MENUBAR_TOKEN

Security: this script NEVER reads browser cookies, the macOS keychain, or any
other app's credential files on its own. It only uses a token you put in the
config file / env var yourself, a token_file path you set yourself, or - only
if YOU set "grok_auth_file" - the Grok CLI's own auth.json (read-only; this
script never writes to it and never uses its refresh_token itself). Token
refresh is left to the official Grok CLI via "grok_refresh_command".

Python 3.9+ compatible (the python3 that ships with Apple's Command Line Tools).
"""

import json
import os
import random
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

CONFIG_PATH = os.path.expanduser("~/.config/usage-menubar/config.json")
DEFAULT_USAGE_PAGE = "https://grok.com/dashboard"

DEFAULT_CONFIG = {
    # "demo"             -> fake numbers, clearly labeled DEMO (works out of the box)
    # "grok_cli_billing" -> UNOFFICIAL, undocumented endpoint used by the Grok CLI's /usage panel
    # "command"          -> run your own command that prints JSON (see README)
    # "stub"             -> placeholder for an official API if xAI ever publishes one
    "source": "demo",
    "usage_page_url": DEFAULT_USAGE_PAGE,
    "token": "",          # only for grok_cli_billing; or set USAGE_MENUBAR_TOKEN
    "token_file": "",     # optional: path YOU choose to a file containing just the token
    "command": "",        # only for source=command
    # --- opt-in: no manual re-pasting (grok_cli_billing only) ---------------
    # Path to the Grok CLI's own credential file, e.g. "~/.grok/auth.json".
    # Empty = never read. Read-only; only the access token ("key") is used.
    "grok_auth_file": "",
    # Command that makes the official Grok CLI refresh its own token, e.g.
    # "~/.grok/bin/grok models". Run only when the token in grok_auth_file is
    # expired/near expiry or the endpoint returns 401/403. Empty = never run.
    "grok_refresh_command": "",
    "refresh_timeout_seconds": 60,
    "refresh_skew_seconds": 300,   # treat token as expired this many seconds early
    "timeout_seconds": 15,
    "use_sf_symbols": False,  # True -> SF Symbol gauge icon (macOS 13+) instead of emoji
}


class UsageResult(object):
    def __init__(self, remaining_percent, resets_at=None, label="", is_demo=False,
                 detail_lines=None):
        self.remaining_percent = remaining_percent  # float 0..100
        self.resets_at = resets_at                   # aware datetime or None
        self.label = label                           # short source name for the dropdown
        self.is_demo = is_demo
        self.detail_lines = detail_lines or []       # extra info lines for the dropdown


class UsageError(Exception):
    pass


# --------------------------------------------------------------------------- config

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                user = json.load(f)
            if isinstance(user, dict):
                cfg.update(user)
        except Exception as e:  # bad JSON should not crash the menu bar
            cfg["_config_error"] = "config.json unreadable: %s" % e
    if os.environ.get("USAGE_MENUBAR_SOURCE"):
        cfg["source"] = os.environ["USAGE_MENUBAR_SOURCE"]
    if os.environ.get("USAGE_MENUBAR_TOKEN"):
        cfg["token"] = os.environ["USAGE_MENUBAR_TOKEN"]
    return cfg


def _get_token(cfg):
    tok = (cfg.get("token") or "").strip()
    if not tok and (cfg.get("grok_auth_file") or "").strip():
        return _read_grok_auth_file(cfg)[0]
    if not tok and cfg.get("token_file"):
        path = os.path.expanduser(cfg["token_file"])
        try:
            with open(path, "r") as f:
                tok = f.read().strip()
        except Exception as e:
            raise UsageError("cannot read token_file %s: %s" % (path, e))
    if not tok:
        raise UsageError("no token set (config 'token' or env USAGE_MENUBAR_TOKEN)")
    return tok


def _read_grok_auth_file(cfg):
    """Read the access token from the Grok CLI's own auth.json (path set by the user).

    Documented location: ~/.grok/auth.json (or $GROK_HOME/auth.json, or $GROK_AUTH_PATH).
    Shape (JSON object keyed by scope, e.g. "https://auth.x.ai::<client_id>"):
      {"<scope>": {"auth_mode": "oidc", "key": "<access token>",
                   "refresh_token": "...", "expires_at": "2026-...Z", ...}}
    Returns (token, expires_at_datetime_or_None). Never writes the file.
    """
    path = os.path.expanduser(cfg["grok_auth_file"].strip())
    try:
        with open(path, "r") as f:
            store = json.load(f)
    except Exception as e:
        raise UsageError("cannot read grok_auth_file %s: %s" % (path, e))
    if not isinstance(store, dict):
        raise UsageError("grok_auth_file is not a JSON object")
    entries = [(k, v) for k, v in store.items()
               if isinstance(v, dict) and isinstance(v.get("key"), str) and v["key"].strip()]
    # Prefer an xAI OAuth/OIDC session (the kind `grok login` creates), then anything else
    # except the legacy web-login entry (the CLI itself ignores those).
    def rank(item):
        k, v = item
        mode = v.get("auth_mode")
        if mode == "oidc":
            return 0 if "x.ai" in k else 1
        if mode == "web_login":
            return 9
        return 2
    entries = [e for e in entries if rank(e) < 9]
    if not entries:
        raise UsageError("no usable login in %s - run `grok login` once" % path)
    entries.sort(key=rank)
    entry = entries[0][1]
    return entry["key"].strip(), parse_time(entry.get("expires_at"))


def _token_is_stale(expires_at, cfg):
    if expires_at is None:
        return False
    skew = float(cfg.get("refresh_skew_seconds") or 0)
    return (expires_at - datetime.now(timezone.utc)).total_seconds() <= skew


def _run_refresh_command(cfg):
    """Ask the official Grok CLI to refresh its own token (it rewrites auth.json)."""
    cmd = (cfg.get("grok_refresh_command") or "").strip()
    if not cmd:
        return False
    env = dict(os.environ)
    # SwiftBar runs plugins with a minimal PATH; add the CLI's default install dir.
    env["PATH"] = env.get("PATH", "/usr/bin:/bin") + ":" + os.path.expanduser("~/.grok/bin") \
        + ":/opt/homebrew/bin:/usr/local/bin"
    try:
        out = subprocess.run(cmd, shell=True, capture_output=True, text=True, env=env,
                             stdin=subprocess.DEVNULL,
                             timeout=float(cfg.get("refresh_timeout_seconds") or 60))
    except subprocess.TimeoutExpired:
        raise UsageError("grok_refresh_command timed out")
    if out.returncode != 0:
        raise UsageError("grok_refresh_command exited %d: %s - you may need `grok login`"
                         % (out.returncode, (out.stderr or out.stdout).strip()[:120]))
    return True


# --------------------------------------------------------------------------- helpers

def parse_time(value):
    """Parse ISO-8601 (with Z / any fractional digits) or epoch seconds/ms."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e12:
            v /= 1000.0
        return datetime.fromtimestamp(v, tz=timezone.utc)
    s = str(value).strip()
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return parse_time(float(s))
    s = s.replace("Z", "+00:00")
    # Python 3.9's fromisoformat only accepts 3 or 6 fractional digits: normalise to 6.
    m = re.match(r"^(.*T\d\d:\d\d:\d\d)(\.\d+)?(.*)$", s)
    if m:
        frac = m.group(2) or ""
        if frac:
            frac = "." + (frac[1:] + "000000")[:6]
        s = m.group(1) + frac + m.group(3)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _clamp_pct(x, what, clamp=False):
    try:
        v = float(x)
    except (TypeError, ValueError):
        raise UsageError("%s missing or not a number: %r" % (what, x))
    if clamp:
        return min(100.0, max(0.0, v))
    if v < 0 or v > 100:
        raise UsageError("%s out of range: %r" % (what, x))
    return v


# --------------------------------------------------------------------------- sources

def _fetch_demo(cfg):
    """DEMO MODE: fake, slowly changing numbers. NOT your real usage."""
    now = datetime.now(timezone.utc)
    # Pretend the week resets next Monday 00:00 UTC, and usage grows through the week.
    days_to_monday = (7 - now.weekday()) % 7 or 7
    reset = (now + timedelta(days=days_to_monday)).replace(hour=0, minute=0, second=0,
                                                           microsecond=0)
    week_fraction = 1.0 - (reset - now).total_seconds() / (7 * 86400.0)
    used = min(100.0, max(0.0, week_fraction * 85.0 + random.uniform(-2, 2)))
    return UsageResult(100.0 - used, reset, label="DEMO (fake data)", is_demo=True,
                       detail_lines=["This is mock data, not your account."])


def _fetch_grok_cli_billing(cfg):
    """UNOFFICIAL / UNDOCUMENTED. Same endpoint the Grok CLI's /usage panel calls.

    GET https://cli-chat-proxy.grok.com/v1/billing?format=credits
      Authorization: Bearer <token from `grok login`>
      X-XAI-Token-Auth: xai-grok-cli
    Response (observed by third parties, may change without notice):
      {"config": {"creditUsagePercent": 28.0,             # USED percent
                  "currentPeriod": {"type": "USAGE_PERIOD_TYPE_WEEKLY",
                                    "start": "...", "end": "..."},
                  "productUsage": [{"product": "GrokBuild", "usagePercent": 20.0}, ...],
                  "billingPeriodEnd": "...", ...}}
    remaining = 100 - creditUsagePercent; reset = currentPeriod.end
    """
    url = cfg.get("grok_billing_url") or "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
    use_auth_file = not (cfg.get("token") or "").strip() and bool(
        (cfg.get("grok_auth_file") or "").strip())
    can_refresh = use_auth_file and bool((cfg.get("grok_refresh_command") or "").strip())
    refreshed = False
    if use_auth_file:
        token, expires_at = _read_grok_auth_file(cfg)
        if _token_is_stale(expires_at, cfg) and can_refresh:
            _run_refresh_command(cfg)
            refreshed = True
            token, expires_at = _read_grok_auth_file(cfg)
    else:
        token = _get_token(cfg)
    try:
        body = _billing_request(url, token, cfg)
    except _AuthRejected as e:
        if can_refresh and not refreshed:
            _run_refresh_command(cfg)
            token, _ = _read_grok_auth_file(cfg)
            try:
                body = _billing_request(url, token, cfg)
            except _AuthRejected as e2:
                raise UsageError("HTTP %d after CLI refresh - run `grok login` once" % e2.code)
        elif use_auth_file:
            raise UsageError("HTTP %d: token in grok_auth_file expired - run `grok` once"
                             " (or set grok_refresh_command)" % e.code)
        else:
            raise UsageError("HTTP %d: token expired/invalid - get a fresh one" % e.code)
    return _parse_billing(body)


class _AuthRejected(Exception):
    def __init__(self, code):
        Exception.__init__(self, "HTTP %d" % code)
        self.code = code


def _billing_request(url, token, cfg):
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "X-XAI-Token-Auth": "xai-grok-cli",
        "Accept": "application/json",
        "User-Agent": "usage-menubar/0.1",
    })
    try:
        with urllib.request.urlopen(req, timeout=float(cfg.get("timeout_seconds") or 15)) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise _AuthRejected(e.code)
        raise UsageError("HTTP %d from billing endpoint" % e.code)
    except urllib.error.URLError as e:
        raise UsageError("network error: %s" % e.reason)


def _parse_billing(body):
    try:
        data = json.loads(body)
    except ValueError:
        raise UsageError("response was not JSON")
    c = data.get("config", data) if isinstance(data, dict) else {}
    raw = c.get("creditUsagePercent")
    if raw is None and isinstance(c, dict) and (c.get("currentPeriod") or "productUsage" in c
                                                 or "billingPeriodEnd" in c):
        # The API appears to omit zero-valued fields, so a missing percent on an otherwise
        # valid response means 0% used.
        raw = 0.0
    used = _clamp_pct(raw, "creditUsagePercent", clamp=True)
    period = c.get("currentPeriod") or {}
    reset = parse_time(period.get("end")) or parse_time(c.get("billingPeriodEnd"))
    details = []
    for p in c.get("productUsage") or []:
        if isinstance(p, dict) and p.get("product"):
            details.append("%s: %.0f%% used" % (p["product"], float(p.get("usagePercent") or 0)))
    return UsageResult(100.0 - used, reset, label="Grok billing (unofficial)",
                       detail_lines=details)


def _fetch_command(cfg):
    """Run a command YOU configured; it must print JSON like
    {"remaining_percent": 72, "resets_at": "2026-11-01T00:00:00Z"}
    (or "used_percent" instead of remaining_percent)."""
    cmd = (cfg.get("command") or "").strip()
    if not cmd:
        raise UsageError("source=command but 'command' is empty in config")
    try:
        out = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                             timeout=float(cfg.get("timeout_seconds") or 15))
    except subprocess.TimeoutExpired:
        raise UsageError("command timed out")
    if out.returncode != 0:
        raise UsageError("command exited %d: %s" % (out.returncode, out.stderr.strip()[:120]))
    try:
        d = json.loads(out.stdout)
    except ValueError:
        raise UsageError("command did not print JSON")
    if "remaining_percent" in d:
        rem = _clamp_pct(d["remaining_percent"], "remaining_percent")
    else:
        rem = 100.0 - _clamp_pct(d.get("used_percent"), "used_percent")
    return UsageResult(rem, parse_time(d.get("resets_at")), label="custom command")


def _fetch_stub(cfg):
    # TODO: xAI does not (as of Oct 2026) publish an official, documented API for
    # the X Subscription / SuperGrok weekly pool. If one appears, implement it here:
    #   1. call the endpoint with the user's own key from cfg["token"]
    #   2. return UsageResult(remaining_percent, resets_at_datetime, label="official API")
    raise UsageError("no official API yet - set source to demo, grok_cli_billing or command")


SOURCES = {
    "demo": _fetch_demo,
    "grok_cli_billing": _fetch_grok_cli_billing,
    "command": _fetch_command,
    "stub": _fetch_stub,
}


def fetch_usage(cfg):
    """THE pluggable data source. Returns UsageResult or raises UsageError."""
    name = cfg.get("source") or "demo"
    fn = SOURCES.get(name)
    if fn is None:
        raise UsageError("unknown source %r (use: %s)" % (name, ", ".join(sorted(SOURCES))))
    return fn(cfg)


# --------------------------------------------------------------------------- display

def gauge_emoji(pct):
    if pct is None:
        return "⚠️"
    if pct >= 50:
        return "🟢"
    if pct >= 20:
        return "🟡"
    return "🔴"


def gauge_sf_symbol(pct):
    if pct is None:
        return "exclamationmark.triangle"
    for threshold, name in ((84, "100percent"), (59, "67percent"), (42, "50percent"),
                            (17, "33percent")):
        if pct >= threshold:
            return "gauge.with.dots.needle." + name
    return "gauge.with.dots.needle.0percent"


def fmt_local(dt):
    if dt is None:
        return "unknown"
    local = dt.astimezone()
    return local.strftime("%a %b %-d, %-I:%M %p ") + (local.tzname() or "")


def fmt_until(dt):
    if dt is None:
        return ""
    secs = (dt - datetime.now(timezone.utc)).total_seconds()
    if secs <= 0:
        return " (now)"
    d, rem = divmod(int(secs), 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return " (in %dd %dh)" % (d, h) if d else " (in %dh %dm)" % (h, m)


def _esc(s):
    # '|' separates SwiftBar params; keep text safe.
    return str(s).replace("|", "/").replace("\n", " ")


def render(cfg, result, error):
    lines = []
    use_sf = bool(cfg.get("use_sf_symbols"))
    if result is not None:
        pct = result.remaining_percent
        text = "%d%%" % round(pct)
        if result.is_demo:
            text += " DEMO"
        if use_sf:
            lines.append("%s | sfimage=%s" % (text, gauge_sf_symbol(pct)))
        else:
            lines.append("%s %s" % (gauge_emoji(pct), text))
    else:
        lines.append("⚠️ --%" if not use_sf else "--% | sfimage=exclamationmark.triangle")
    lines.append("---")
    if result is not None:
        lines.append("Weekly usage remaining: %.1f%%" % result.remaining_percent)
        lines.append("Resets: %s%s" % (fmt_local(result.resets_at), fmt_until(result.resets_at)))
        for d in result.detail_lines:
            lines.append("%s | size=12" % _esc(d))
        lines.append("Source: %s | size=12" % _esc(result.label))
        if result.is_demo:
            lines.append("DEMO MODE - fake numbers, not your account | color=orange size=12")
    if error:
        lines.append("Error: %s | color=red" % _esc(error))
    if cfg.get("_config_error"):
        lines.append("%s | color=red" % _esc(cfg["_config_error"]))
    lines.append("Last updated: %s" % datetime.now().strftime("%-I:%M:%S %p"))
    lines.append("---")
    lines.append("Refresh Now | refresh=true")
    lines.append("Open usage page | href=%s" % (cfg.get("usage_page_url") or DEFAULT_USAGE_PAGE))
    lines.append("Edit config… | bash=/usr/bin/open param1=-t param2=%s terminal=false"
                 % CONFIG_PATH)
    lines.append("Quit | bash=/usr/bin/pkill param1=-x param2=SwiftBar terminal=false")
    return "\n".join(lines)


def get_usage_safely(cfg):
    try:
        return fetch_usage(cfg), None
    except UsageError as e:
        return None, str(e)
    except Exception as e:  # never crash the menu bar
        return None, "%s: %s" % (type(e).__name__, e)


def main():
    cfg = load_config()
    result, error = get_usage_safely(cfg)
    print(render(cfg, result, error))


if __name__ == "__main__":
    main()
