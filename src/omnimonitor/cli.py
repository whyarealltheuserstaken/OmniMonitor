"""Command line entry point: `omnimonitor [command]`."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List, Optional

from omnimonitor import __version__
from omnimonitor import config as config_mod


def _load(args) -> dict:
    cfg = config_mod.load_config(Path(args.config).expanduser() if args.config else None)
    if getattr(args, "demo", False):
        cfg["source"] = "demo"
        cfg["token_source"] = "demo"
    return cfg


def cmd_run(args) -> int:
    cfg = _load(args)
    if not Path(cfg["_config_path"]).exists() and not args.config:
        try:
            config_mod.ensure_config_file()
        except OSError:
            pass
    from omnimonitor.app import Controller
    try:
        from omnimonitor.tray import TrayApp
        app = TrayApp(Controller(cfg))
    except Exception as e:  # noqa: BLE001
        print("Could not start the tray icon: %s\n"
              "On Linux you may need AppIndicator support (see README > Linux). You can still "
              "use `omnimonitor serve` for the stats page or `status` for text." % e,
              file=sys.stderr)
        return 2
    app.run()
    return 0


def cmd_status(args) -> int:
    from omnimonitor.app import Controller
    from omnimonitor.timeutil import fmt_local
    cfg = _load(args)
    ctrl = Controller(cfg)
    ctrl.refresh()
    st = ctrl.stats()
    if args.json:
        print(json.dumps(st, indent=2, default=str))
        return 0 if ctrl.usage else 1
    u = ctrl.usage
    if u:
        print("%s%.0f%% remaining (%.0f%% used)%s" % (
            "DEMO - FAKE DATA: " if u.is_demo else "", u.remaining_percent, u.used_percent or 0,
            "" if (u.raw_used_percent or 0) <= 100 else
            " [reported %.1f%%]" % u.raw_used_percent))
        print("Resets: %s" % fmt_local(u.resets_at))
        for line in u.detail_lines:
            print("  " + line)
    if ctrl.error:
        print("Error: %s" % ctrl.error)
    wk = st.get("week_tokens", {})
    print("Tokens this week: %s" % (wk.get("value") if wk.get("value") is not None
                                    else "not available (%s)" % wk.get("reason")))
    lim = st.get("weekly_limit", {})
    if lim.get("value"):
        print("Weekly token limit: %s" % lim["value"])
    elif lim.get("estimate"):
        print("Weekly token limit: ≈%s (ESTIMATE from %s %%)" % (
            lim["estimate"]["value"], lim["estimate"]["basis"]))
    else:
        print("Weekly token limit: not available (%s)" % lim.get("reason"))
    lt = st.get("lifetime_tokens", {})
    print("Lifetime tokens: %s" % (lt.get("value") if lt.get("value") is not None
                                   else "not available"))
    return 0 if ctrl.usage else 1


def cmd_serve(args) -> int:
    """Stats page only (no tray): useful on headless Linux / for testing."""
    from omnimonitor.app import Controller
    from omnimonitor.webui import for_controller
    cfg = _load(args)
    ctrl = Controller(cfg)
    srv = for_controller(ctrl, args.port if args.port is not None else
                         int(cfg.get("web_port") or 0)).start()
    ctrl.start_background()
    print(srv.url, flush=True)
    if args.open:
        import webbrowser
        webbrowser.open(srv.url)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        ctrl.stop()
        srv.stop()
    return 0


def cmd_doctor(args) -> int:
    from omnimonitor import doctor
    cfg = _load(args)
    if cfg.get("source") == "demo":
        cfg = dict(cfg)
        cfg["source"] = "grok_cli_billing"  # check the real path even while in demo mode
    if args.json:
        print(doctor.as_json(cfg))
        return 0
    return doctor.doctor(cfg, online=not args.offline)


def cmd_setup(args) -> int:
    from omnimonitor import doctor
    return doctor.setup(_load(args), assume_yes=args.yes)


def cmd_autostart(args, install: bool) -> int:
    from omnimonitor import autostart
    ok, msg = autostart.install() if install else autostart.uninstall()
    print(msg)
    return 0 if ok else 1


def cmd_config_path(args) -> int:
    print(_load(args)["_config_path"])
    return 0


def _ctrl(args, refresh: bool = False):
    from omnimonitor.app import Controller
    ctrl = Controller(_load(args))
    if refresh:
        ctrl.refresh()
    else:
        ctrl._scan_tokens()
        ctrl._recompute()
    return ctrl


def cmd_export(args) -> int:
    ctrl = _ctrl(args)
    body, _ctype, fname = ctrl.export(args.kind, args.format)
    if args.output == "-":
        sys.stdout.write(body.decode("utf-8"))
        return 0
    out = Path(args.output or fname).expanduser()
    out.write_bytes(body)
    print("Wrote %s%s" % (out, " (DEMO data)" if ctrl.token_source == "demo" else ""))
    return 0


def cmd_providers(args) -> int:
    from omnimonitor.adapters import describe
    rows = describe(_load(args))
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    for r in rows:
        print("%-14s %-8s %-9s %s" % (r["label"], "enabled" if r["enabled"] else "off",
                                      "found" if r["found"] else "not found",
                                      ", ".join(r["paths"])))
    print("\nEnable extras with: omnimonitor config set extra_token_sources "
          "'[\"claude_code\",\"codex\"]'")
    return 0


def _fmt(n) -> str:
    from omnimonitor.tray import fmt_tokens
    return fmt_tokens(n)


def cmd_breakdown(args) -> int:
    st = _ctrl(args).stats()
    br = (st.get("breakdowns") or {}).get(args.range)
    if not br:
        reason = (st.get("lifetime_tokens") or {}).get("reason") or "no data for that range"
        print("Breakdown: not available (%s)" % reason)
        return 1
    if args.json:
        print(json.dumps(br, indent=2, default=str))
        return 0
    if st.get("is_demo"):
        print("DEMO - FAKE DATA")
    print("%s: %s tokens" % (br["label"], _fmt(br["tokens"])))
    for title, key in (("By tool", "tools"), ("By model", "models"), ("By project", "projects")):
        print("\n" + title)
        for r in br[key]:
            cost = ("$%.2f est." % r["estimated_usd"]) if r["estimated_usd"] is not None else \
                ("$%.2f recorded" % r["recorded_usd"]) if r["recorded_usd"] is not None else \
                "cost n/a"
            hit = "%3.0f%% cache" % (100 * r["cache_hit"]) if r["cache_hit"] is not None \
                else "  n/a cache"
            print("  %-28s %9s %4.0f%%  %s  %s" % (r["name"][:28], _fmt(r["tokens"]),
                                                  100 * r["share"], hit, cost))
    return 0


def cmd_config(args) -> int:
    from omnimonitor import settings as settings_mod
    cfg = _load(args)
    if args.action == "get":
        keys = [args.key] if args.key else list(settings_mod.SCHEMA)
        for k in keys:
            if k not in settings_mod.SCHEMA:
                print("not a settable key: %s" % k, file=sys.stderr)
                return 2
            print("%s = %s" % (k, json.dumps(cfg.get(k))))
        return 0
    if args.action == "keys":
        for k, (_f, web, hlp) in settings_mod.SCHEMA.items():
            print("%-26s %s%s" % (k, hlp, "" if web else "  [CLI only]"))
        return 0
    if not args.key or args.value is None:
        print("usage: omnimonitor config set KEY VALUE", file=sys.stderr)
        return 2
    try:
        val = settings_mod.validate(args.key, settings_mod.parse_cli_value(args.value))
    except settings_mod.SettingError as e:
        print("Invalid %s: %s" % (args.key, e), file=sys.stderr)
        return 2
    path = config_mod.update_config_file({args.key: val}, Path(cfg["_config_path"]))
    print("Saved %s = %s in %s (restart the tray app to apply)" % (
        args.key, json.dumps(val), path))
    return 0


def cmd_notify_test(args) -> int:
    from omnimonitor.alerts import deliver
    ok = deliver("OmniMonitor test", "Notifications work.")
    print("Sent a test notification." if ok else
          "No notification backend found (osascript / notify-send). The tray app uses its own.")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="omnimonitor",
        description="OmniMonitor: unofficial tray app showing your Grok weekly usage. "
                    "Not affiliated with xAI or X.")
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    p.add_argument("--config", help="path to config.json (default: per-user config dir)")
    p.add_argument("--demo", action="store_true", help="force demo mode (fake, labeled data)")
    p.add_argument("--setup", action="store_true", help="same as the `setup` command")
    p.add_argument("--doctor", action="store_true", help="same as the `doctor` command")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("run", help="start the tray app (default)")
    s = sub.add_parser("status", help="print usage once and exit")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("serve", help="stats web page only, no tray (127.0.0.1)")
    s.add_argument("--port", type=int, default=None)
    s.add_argument("--open", action="store_true", help="open it in the browser")
    s = sub.add_parser("doctor", help="diagnose Grok CLI / login / billing")
    s.add_argument("--offline", action="store_true", help="skip the billing request")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("setup", help="first-run: check login and switch off demo mode")
    s.add_argument("-y", "--yes", action="store_true")
    sub.add_parser("install-autostart", help="start at login (launchd / Run key / XDG)")
    sub.add_parser("uninstall-autostart", help="remove start-at-login")
    sub.add_parser("config-path", help="print the config file path")
    s = sub.add_parser("export", help="export token data as CSV or JSON")
    s.add_argument("--format", choices=("csv", "json"), default="csv")
    s.add_argument("--kind", choices=("daily", "daily-models", "breakdown", "events"),
                   default="daily", help="CSV only")
    s.add_argument("-o", "--output", help="file path, or - for stdout")
    s = sub.add_parser("providers", help="which local token logs exist / are enabled")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("breakdown", help="tokens by tool, model and project")
    s.add_argument("--range", choices=("today", "week", "7d", "30d", "all"), default="7d")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("config", help="get/set validated settings")
    s.add_argument("action", choices=("get", "set", "keys"))
    s.add_argument("key", nargs="?")
    s.add_argument("value", nargs="?")
    sub.add_parser("notify-test", help="show a test desktop notification")
    for name in ("status", "serve", "doctor", "setup", "run", "export", "providers",
                 "breakdown", "config"):
        sub.choices[name].add_argument("--demo", action="store_true", default=argparse.SUPPRESS,
                                       help=argparse.SUPPRESS)
        sub.choices[name].add_argument("--config", default=argparse.SUPPRESS,
                                       help=argparse.SUPPRESS)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.setup:
        args.cmd, args.yes = "setup", False
    elif args.doctor:
        args.cmd, args.offline, args.json = "doctor", False, False
    cmd = args.cmd or "run"
    if cmd == "run":
        return cmd_run(args)
    if cmd == "status":
        return cmd_status(args)
    if cmd == "serve":
        return cmd_serve(args)
    if cmd == "doctor":
        return cmd_doctor(args)
    if cmd == "setup":
        return cmd_setup(args)
    if cmd == "install-autostart":
        return cmd_autostart(args, True)
    if cmd == "uninstall-autostart":
        return cmd_autostart(args, False)
    if cmd == "config-path":
        return cmd_config_path(args)
    handlers = {"export": cmd_export, "providers": cmd_providers, "breakdown": cmd_breakdown,
                "config": cmd_config, "notify-test": cmd_notify_test}
    if cmd in handlers:
        return handlers[cmd](args)
    return 2
