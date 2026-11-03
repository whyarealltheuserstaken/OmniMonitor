"""`setup` and `doctor`: find the Grok CLI, check the login, explain what to do next."""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Tuple

from omnimonitor import __version__
from omnimonitor import config as config_mod
from omnimonitor.billing import (
    UsageError,
    fetch_grok_cli_billing,
    find_grok_binary,
    read_grok_auth,
)
from omnimonitor.timeutil import fmt_local, utcnow
from omnimonitor.tokens import iter_update_files

OK, WARN, FAIL = "ok", "warn", "fail"


def checks(cfg: Dict[str, Any], online: bool = True) -> List[Tuple[str, str, str]]:
    out: List[Tuple[str, str, str]] = []
    out.append((OK, "version", "omnimonitor %s, Python %s, %s" % (
        __version__, sys.version.split()[0], sys.platform)))
    out.append((OK, "config", cfg.get("_config_path", "")))
    if cfg.get("_config_error"):
        out.append((FAIL, "config", cfg["_config_error"]))
    out.append((WARN if cfg.get("source") == "demo" else OK, "source",
                "%s%s" % (cfg.get("source"), " (FAKE demo numbers - run `omnimonitor setup`)"
                          if cfg.get("source") == "demo" else "")))
    try:
        import pystray  # noqa: F401
        out.append((OK, "tray backend", "pystray importable"))
    except Exception as e:  # noqa: BLE001
        out.append((FAIL, "tray backend", "pystray not usable: %s" % e))

    grok = find_grok_binary(cfg)
    out.append((OK if grok else WARN, "grok CLI", grok or
                "not found on PATH or in %s/bin - install the Grok CLI (see https://docs.x.ai) and run "
                "`grok login`" % config_mod.grok_home(cfg)))
    auth = config_mod.grok_auth_file(cfg)
    try:
        _tok, exp = read_grok_auth(auth)
        if exp and exp < utcnow():
            out.append((WARN, "login", "%s: token expired %s (it is refreshed by running "
                                       "`grok` - the app does this automatically)"
                        % (auth, fmt_local(exp))))
        else:
            out.append((OK, "login", "%s (expires %s)" % (auth, fmt_local(exp))))
    except UsageError as e:
        out.append((FAIL, "login", str(e)))

    sess = config_mod.grok_home(cfg) / "sessions"
    n = sum(1 for _ in iter_update_files(config_mod.grok_home(cfg))) if sess.is_dir() else 0
    out.append((OK if n else WARN, "token logs", "%d session log(s) under %s" % (n, sess) if n
                else "no Grok CLI session logs under %s - token stats will be 'not available'"
                % sess))

    if online:
        try:
            r = fetch_grok_cli_billing(cfg)
            out.append((OK, "billing", "%.0f%% remaining, resets %s" % (
                r.remaining_percent, fmt_local(r.resets_at))))
        except UsageError as e:
            out.append((FAIL, "billing", str(e)))
        except Exception as e:  # noqa: BLE001
            out.append((FAIL, "billing", "%s: %s" % (type(e).__name__, e)))
    return out


def print_checks(results) -> int:
    worst = 0
    for level, name, msg in results:
        mark = {"ok": "✔", "warn": "!", "fail": "✘"}[level]
        print("%s %-13s %s" % (mark, name, msg))
        worst = max(worst, {"ok": 0, "warn": 0, "fail": 1}[level])
    return worst


def doctor(cfg: Dict[str, Any], online: bool = True) -> int:
    return print_checks(checks(cfg, online=online))


def setup(cfg: Dict[str, Any], assume_yes: bool = False) -> int:
    """Check the Grok CLI login; if billing works, switch the config to real data."""
    print("OmniMonitor setup\n")
    real = dict(cfg)
    real["source"] = "grok_cli_billing"
    results = checks(real, online=True)
    rc = print_checks(results)
    billing_ok = any(n == "billing" and lvl == OK for lvl, n, _ in results)
    print()
    if not billing_ok:
        print("Not switching to real data yet. Fix the items marked ✘ above, e.g.:\n"
              "  1. install the Grok CLI and run `grok login` once\n"
              "  2. run `omnimonitor setup` again\n"
              "The tray keeps showing clearly labeled DEMO data until then.")
        return rc or 1
    if not assume_yes and sys.stdin.isatty():
        ans = input("Billing works. Use your real Grok usage from now on? [Y/n] ").strip().lower()
        if ans not in ("", "y", "yes"):
            print("Unchanged.")
            return 0
    new = {k: v for k, v in cfg.items() if not str(k).startswith("_")}
    new["source"] = "grok_cli_billing"
    path = config_mod.save_config(new)
    print("Saved %s (source = grok_cli_billing). Start the tray with `omnimonitor`." % path)
    print("Optional: `omnimonitor install-autostart` to start it at login.")
    return 0


def as_json(cfg: Dict[str, Any]) -> str:
    return json.dumps([{"level": a, "check": b, "message": c} for a, b, c in checks(cfg)],
                      indent=2)
