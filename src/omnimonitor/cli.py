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
    from omnimonitor import net
    net.configure(cfg)
    return cfg


def cmd_run(args) -> int:
    cfg = _load(args)
    if not Path(cfg["_config_path"]).exists() and not args.config:
        try:
            config_mod.ensure_config_file()
        except OSError:
            pass
    from omnimonitor import applog
    from omnimonitor.app import Controller
    logfile = applog.setup()
    try:
        from omnimonitor.tray import TrayApp
        applog.log.info("tray module loaded; creating icon")
        app = TrayApp(Controller(cfg))
    except Exception as e:  # noqa: BLE001
        applog.log.exception("could not start the tray icon")
        from omnimonitor.alerts import deliver
        deliver("OmniMonitor could not start", "%s. Log: %s" % (e, logfile or "n/a"))
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


def cmd_window(args) -> int:
    """Native app window (child process of the tray). URL arrives on stdin, never argv."""
    from omnimonitor import window
    if not window.webview_available():
        print("pywebview is not installed; the tray opens the browser instead")
        return 3
    if args.smoke:
        from omnimonitor.app import Controller
        from omnimonitor.webui import for_controller
        args.demo = True
        ctrl = Controller(_load(args))
        srv = for_controller(ctrl, 0).start()
        try:
            return window.run_window(srv.url, smoke=True, timeout_s=45, served=srv.stats_served)
        finally:
            srv.stop()
    url = window.read_url_from_stdin()
    if not url:
        print("expected a private 127.0.0.1 OmniMonitor URL on stdin")
        return 2
    return window.run_window(url)


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


def _status_word(st: str) -> str:
    return {"supported": "supported", "partial": "partial", "experimental": "experimental",
            "not_supported": "no"}.get(st, st)


def cmd_providers(args) -> int:
    from omnimonitor import accounts, apikeys, catalog
    from omnimonitor.adapters import describe
    cfg = _load(args)
    action = args.action or "list"
    if action in ("enable", "disable"):
        if args.id not in catalog.SWITCHABLE:
            print("unknown or non-switchable provider %r. Switchable: %s"
                  % (args.id, ", ".join(catalog.SWITCHABLE)), file=sys.stderr)
            return 2
        cur = dict(cfg.get("providers") or {})
        cur[args.id] = {"enabled": action == "enable"}
        path = config_mod.update_config_file({"providers": cur}, Path(cfg["_config_path"]))
        info = catalog.get(args.id)
        print("%s %s in %s (restart the tray app to apply)" % (
            info.name, "enabled" if action == "enable" else "disabled", path))
        if action == "enable" and info.kind == "api":
            key, name = accounts.key_for(args.id)
            if not key:
                print("It needs a key: export %s=... or run `omnimonitor secrets set %s`"
                      % (name, name))
        return 0
    if action == "primary":
        if args.id not in catalog.PRIMARY_CHOICES:
            print("primary must be one of: %s" % ", ".join(catalog.PRIMARY_CHOICES),
                  file=sys.stderr)
            return 2
        path = config_mod.update_config_file({"primary_provider": args.id},
                                             Path(cfg["_config_path"]))
        print("Tray icon now follows %s (saved in %s)" % (args.id, path))
        return 0
    if action == "check":
        from omnimonitor import doctor
        return doctor.print_checks(doctor.provider_checks(cfg, online=True))
    found = {r["name"]: r for r in describe(cfg)}
    enabled = set(catalog.enabled_ids(cfg))
    rows = []
    for p in catalog.CATALOG:
        r = {"id": p.id, "name": p.name, "family": p.family, "kind": p.kind,
             "status": p.status, "shows": p.shows, "docs": list(p.docs), "reason": p.reason,
             "enabled": (p.id in enabled) if p.id in catalog.SWITCHABLE else None}
        if p.id in found:
            r["found"], r["paths"] = found[p.id]["found"], found[p.id]["paths"]
            if p.id == "grok_sessions":
                r["enabled"] = found[p.id]["enabled"]
        if p.kind == "api":
            r["key"] = apikeys.where(accounts.key_for(p.id)[1]) or "not set"
        rows.append(r)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    groups = (("Local logs (read-only files on this computer)", "local_logs"),
              ("Account APIs (official, your key)", "api"),
              ("Grok weekly pool", "billing"), ("Not supported", "none"))
    for title, kind in groups:
        print("\n" + title)
        for r in (x for x in rows if x["kind"] == kind):
            if kind == "none":
                print("  %-24s %s" % (r["name"][:24], r["reason"]))
                continue
            on = "-" if r["enabled"] is None else ("ON " if r["enabled"] else "off")
            extra = ("found" if r.get("found") else "not found") if "found" in r else \
                ("key: %s" % r["key"] if "key" in r else "")
            print("  %-24s %-3s %-9s %-10s %s" % (r["name"][:24], on, _status_word(r["status"]),
                                                  extra, r["shows"][:60]))
    print("\nEnable one:   omnimonitor providers enable <id>   (ids: --json, or docs/providers.md)")
    print("Tray follows: omnimonitor providers primary <%s>" % "|".join(catalog.PRIMARY_CHOICES))
    print("Live check:   omnimonitor providers check")
    return 0


def cmd_secrets(args) -> int:
    from omnimonitor import apikeys
    if args.action == "list":
        print("API keys (values are never shown):")
        for n, src in apikeys.status().items():
            print("  %-22s %s" % (n, {"env": "set (environment)", "file": "set (secrets file)"}
                                  .get(src, "not set")))
        prob = apikeys.file_problem()
        print("Secrets file: %s%s" % (apikeys.secrets_path(), " - " + prob if prob else ""))
        return 1 if prob else 0
    if not args.name:
        print("usage: omnimonitor secrets %s NAME" % args.action, file=sys.stderr)
        return 2
    if args.action == "unset":
        ok = apikeys.unset_secret(args.name)
        print("Removed %s from the secrets file." % args.name if ok else
              "%s was not in the secrets file." % args.name)
        return 0
    import getpass
    try:
        value = getpass.getpass("Paste %s (input hidden): " % args.name) if sys.stdin.isatty() \
            else sys.stdin.readline()
        path = apikeys.set_secret(args.name, value)
    except (ValueError, EOFError, KeyboardInterrupt) as e:
        print("Not saved: %s" % (e or "cancelled"), file=sys.stderr)
        return 2
    print("Saved %s to %s (chmod 600). It is never shown in the UI." % (args.name, path))
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


def cmd_blocks(args) -> int:
    """≈ 5-hour blocks + burn rate from local logs (estimate)."""
    import datetime as _dt

    from omnimonitor import blocks as blocks_mod
    ctrl = _ctrl(args)
    evs = ctrl.history.events() if ctrl.history is not None else []
    src = None if args.source == "all" else (args.source,)
    bs = blocks_mod.blocks(evs, src)[-args.last:]
    if args.json:
        print(json.dumps(bs, indent=2))
        return 0
    print("≈ 5-hour blocks (ESTIMATE from local log timestamps; not the provider's real "
          "limit window)")
    if not bs:
        print("  no events for source %r" % args.source)
        return 0
    for b in bs:
        st = _dt.datetime.fromtimestamp(b["start"]).strftime("%Y-%m-%d %H:%M")
        line = "  %s  %9s tokens  %6s/min" % (st, _fmt(b["tokens"]), _fmt(int(b["tokens_per_min"])))
        if b["active"]:
            line += "  ACTIVE, %dm left, ≈%s projected" % (b["remaining_s"] // 60,
                                                         _fmt(b["projected_tokens"]))
        print(line)
    return 0


def cmd_statusline(args) -> int:
    """One line for a shell prompt or editor status bar (offline, no network)."""
    import time as _time

    from omnimonitor import blocks as blocks_mod
    from omnimonitor import statusline as sl
    ctrl = _ctrl(args)
    now = _time.time()
    hist = ctrl.history
    evs = hist.events() if hist is not None else []
    src = None if args.source == "all" else (args.source,)
    ov = ctrl.stats().get("providers") or {}
    p = sl.parts(hist.last_billing() if hist is not None else None, ov.get("today"),
                 blocks_mod.active_block(evs, src, now), now)
    if ctrl.token_source == "demo":
        p["demo"] = True
    if args.json:
        print(json.dumps(p))
    else:
        print(("DEMO " if p.get("demo") else "") + sl.render(p, now))
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
    if args.action == "export":
        return _config_export(cfg, args.key)
    if args.action == "import":
        return _config_import(cfg, args.key)
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


EXPORT_MARK = "omnimonitor_config_export"


def _config_export(cfg: dict, dest: Optional[str]) -> int:
    """Settable keys from the config FILE only (never env overrides, the token or keys)."""
    from omnimonitor import settings as settings_mod
    try:
        raw = json.loads(Path(cfg["_config_path"]).read_text("utf-8"))
    except (OSError, ValueError):
        raw = {}
    doc = {EXPORT_MARK: __version__}
    doc.update({k: raw[k] for k in settings_mod.SCHEMA if k in raw})
    if "source" in raw and raw["source"] in ("demo", "grok_cli_billing", "grok_billing"):
        doc["source"] = raw["source"]
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    if not dest or dest == "-":
        sys.stdout.write(text)
    else:
        Path(dest).expanduser().write_text(text, "utf-8")
        print("Exported %d setting(s) to %s (no tokens or API keys included)"
              % (len(doc) - 1, dest))
    return 0


def _config_import(cfg: dict, src: Optional[str]) -> int:
    from omnimonitor import settings as settings_mod
    if not src:
        print("usage: omnimonitor config import FILE", file=sys.stderr)
        return 2
    try:
        doc = json.loads((sys.stdin.read() if src == "-" else
                          Path(src).expanduser().read_text("utf-8")))
    except (OSError, ValueError) as e:
        print("Cannot read %s: %s" % (src, e), file=sys.stderr)
        return 2
    if not isinstance(doc, dict) or EXPORT_MARK not in doc:
        print("Not an OmniMonitor settings export (missing %s)" % EXPORT_MARK, file=sys.stderr)
        return 2
    doc = {k: v for k, v in doc.items() if k != EXPORT_MARK}
    source = doc.pop("source", None)
    skipped = sorted(k for k in doc if k not in settings_mod.SCHEMA)
    clean, errors = settings_mod.apply(cfg, {k: v for k, v in doc.items()
                                             if k in settings_mod.SCHEMA}, web=False)
    if errors:
        for k, e in errors.items():
            print("Invalid %s: %s" % (k, e), file=sys.stderr)
        print("Nothing imported.", file=sys.stderr)
        return 2
    if source in ("demo", "grok_cli_billing", "grok_billing"):
        clean["source"] = source
    path = config_mod.update_config_file(clean, Path(cfg["_config_path"]))
    print("Imported %d setting(s) into %s%s" % (
        len(clean), path, "; ignored: %s" % ", ".join(skipped) if skipped else ""))
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
        description="OmniMonitor: local-first tray app that monitors AI usage, limits and "
                    "spend across many providers. Unofficial; not affiliated with any provider.")
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
    s = sub.add_parser("doctor", help="diagnose Grok CLI, login, billing and every provider")
    s.add_argument("--offline", action="store_true", help="skip the billing request")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("setup", help="first-run: check login and switch off demo mode")
    s.add_argument("-y", "--yes", action="store_true")
    sub.add_parser("install-autostart", help="start at login (launchd / Run key / XDG)")
    sub.add_parser("uninstall-autostart", help="remove start-at-login")
    sub.add_parser("config-path", help="print the config file path")
    s = sub.add_parser("export", help="export token data as CSV or JSON")
    s.add_argument("--format", choices=("csv", "json"), default="csv")
    s.add_argument("--kind", choices=("daily", "daily-models", "breakdown", "events", "providers"),
                   default="daily", help="CSV only")
    s.add_argument("-o", "--output", help="file path, or - for stdout")
    s = sub.add_parser("providers", help="provider matrix; enable/disable; tray primary; check")
    s.add_argument("action", nargs="?", choices=("list", "enable", "disable", "primary", "check"))
    s.add_argument("id", nargs="?")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("secrets", help="API keys for account providers (chmod-600 file)")
    s.add_argument("action", choices=("list", "set", "unset"))
    s.add_argument("name", nargs="?")
    s = sub.add_parser("breakdown", help="tokens by tool, model and project")
    s.add_argument("--range", choices=("today", "week", "7d", "30d", "all"), default="7d")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("blocks", help="≈ 5-hour blocks and burn rate (estimate)")
    s.add_argument("--source", default="claude_code", help="token source id, or 'all'")
    s.add_argument("--last", type=int, default=10)
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("statusline", help="one-line summary for prompts/status bars (offline)")
    s.add_argument("--source", default="all", help="token source for the ≈5h block, or 'all'")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("config", help="get/set validated settings")
    s.add_argument("action", choices=("get", "set", "keys", "export", "import"))
    s.add_argument("key", nargs="?")
    s.add_argument("value", nargs="?")
    sub.add_parser("notify-test", help="show a test desktop notification")
    s = sub.add_parser("net-check", help="test an HTTPS request with the app's CA bundle")
    s.add_argument("url", nargs="?", default="https://api.github.com/")
    s = sub.add_parser("deps", help="CLI tools for enabled providers; install one (official)")
    s.add_argument("action", nargs="?", choices=["list", "install"], default="list")
    s.add_argument("id", nargs="?")
    s.add_argument("--dry-run", action="store_true", help="download + verify, don't run")
    s.add_argument("--json", action="store_true")
    sub.add_parser("log-path", help="print the log file path")
    s = sub.add_parser("window", help="native app window (started by the tray; URL on stdin)")
    s.add_argument("--smoke", action="store_true",
                   help="open the demo dashboard, check it loaded, close (CI)")
    for name in ("status", "serve", "doctor", "setup", "run", "export", "providers",
                 "breakdown", "config", "window"):
        sub.choices[name].add_argument("--demo", action="store_true", default=argparse.SUPPRESS,
                                       help=argparse.SUPPRESS)
        sub.choices[name].add_argument("--config", default=argparse.SUPPRESS,
                                       help=argparse.SUPPRESS)
    return p


def _safe_console() -> None:
    """Legacy Windows consoles / redirected output use a code page (e.g. cp1252) that can't
    encode ≈ or ✔. Replace such characters instead of crashing."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _clean_argv(argv: List[str]) -> List[str]:
    """Finder / LaunchServices may pass -psn_0_12345 to GUI apps; argparse would reject it."""
    return [a for a in argv if not a.startswith("-psn_")]


def cmd_deps(args, input_fn=input) -> int:
    """List CLI tools needed by enabled providers; optionally install one (official script,
    shown with its SHA256, confirmed by typing 'yes', run as the current user)."""
    import getpass

    from omnimonitor import config as config_mod
    from omnimonitor import deps
    cfg = _load(args)
    if getattr(args, "action", None) != "install":
        st = deps.status(cfg)
        if args.json:
            print(json.dumps(st, indent=2))
            return 0
        if not st["deps"]:
            print("No enabled provider needs a command-line tool.")
        for r in st["deps"]:
            print("%-10s %-9s %s" % (r["id"], "ok" if r["installed"] else "MISSING",
                                     r["path"] or (r["installer"] or r["manual"] or r["docs"])))
        return 1 if st["missing"] else 0
    inst = deps.Installer(log_path=config_mod.config_dir() / "setup-log.jsonl")
    plan = inst.prepare(args.id or "")
    if not plan.get("ok"):
        print("error: " + plan.get("error", "?"), file=sys.stderr)
        if plan.get("manual"):
            print("manual install: " + plan["manual"], file=sys.stderr)
        return 1
    print("Source:  " + plan["url"])
    print("Command: " + plan["command_str"])
    print("SHA256:  " + plan["sha256"])
    print("--- first lines ---\n" + plan["preview"] + "\n---")
    if not args.dry_run:
        try:
            ans = input_fn("Run this installer as %s? type 'yes': " % getpass.getuser())
        except EOFError:
            ans = ""
        if ans.strip().lower() != "yes":
            inst.cancel(plan["token"])
            print("Not run.")
            return 1
    res = inst.run(plan["token"], plan["sha256"], dry_run=args.dry_run)
    if res.get("output"):
        print(res["output"])
    print("ok" if res.get("ok") else "failed: " + str(res.get("error") or "rc %s" % res.get("rc")))
    return 0 if res.get("ok") else 1


def cmd_net_check(args) -> int:
    """HTTPS round trip with the app's TLS settings (used by the release smoke test)."""
    import urllib.error
    import urllib.request

    from omnimonitor.net import ca_source
    kind, detail = ca_source()
    print("CA bundle: %s (%s)" % (kind, detail))
    url = args.url
    if not url.startswith("https://"):
        print("https URLs only")
        return 2
    req = urllib.request.Request(url, headers={"User-Agent": "omnimonitor/" + __version__})
    try:
        from omnimonitor.net import build_opener, proxy_url
        if proxy_url():
            print("Proxy: %s" % proxy_url())
        with build_opener().open(req, timeout=15) as r:
            print("HTTPS OK: %s -> %s" % (url, r.status))
            return 0
    except urllib.error.HTTPError as e:
        # an HTTP status (e.g. 403 rate limit) means TLS + certificate verification succeeded
        print("HTTPS OK (TLS verified; server answered HTTP %s): %s" % (e.code, url))
        return 0
    except Exception as e:  # noqa: BLE001
        print("HTTPS FAILED: %s: %s" % (url, e))
        return 1


def main(argv: Optional[List[str]] = None) -> int:
    _safe_console()
    args = build_parser().parse_args(_clean_argv(sys.argv[1:] if argv is None else argv))
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
    if cmd == "net-check":
        return cmd_net_check(args)
    if cmd == "window":
        return cmd_window(args)
    if cmd == "log-path":
        from omnimonitor.applog import log_path
        print(log_path())
        return 0
    handlers = {"export": cmd_export, "providers": cmd_providers, "breakdown": cmd_breakdown, "blocks": cmd_blocks, "statusline": cmd_statusline,
                "config": cmd_config, "notify-test": cmd_notify_test, "secrets": cmd_secrets, "deps": cmd_deps}
    if cmd in handlers:
        return handlers[cmd](args)
    return 2
