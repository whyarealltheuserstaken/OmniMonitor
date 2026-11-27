"""First-run setup: what is on this computer, and the one thing to do for each suite.

Used by the Setup page and ``omnimonitor setup``. Detection is local only (files that
exist, logins the CLIs hold, saved sessions and keys); nothing here calls the network, and
no token, key or cookie value ever leaves the functions that read it.

API keys are never typed into the web page: "Add key" shows the command to run.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from omnimonitor import catalog, suites
from omnimonitor import config as config_mod

#: Most important first: a suite shows the first of these that any of its sources has.
STATES = ("attention", "connected", "found", "sign_in", "add_key", "not_available")
LABELS = {"attention": "Needs attention", "connected": "Connected",
          "found": "Found - not turned on", "sign_in": "Sign in", "add_key": "Add key",
          "not_available": "Not available"}
#: Never switched on by "turn on everything found": grok_unified counts the same turns as
#: grok_sessions; the others need their own deliberate choice.
NOT_IN_BULK = ("grok_unified",)
CLI_COMMANDS = {"claude_cli_plan": "claude", "codex_cli_plan": "codex login",
                "gemini_cli_quota": "gemini"}


class OnboardingError(ValueError):
    pass


@dataclass
class Plan:
    changes: Dict[str, Any] = field(default_factory=dict)  # config keys to write
    signin: Optional[str] = None  # open this source's sign-in window afterwards
    sources: List[str] = field(default_factory=list)  # re-read these at once


def _row(pid: str, state: str, detail: str = "", enabled: bool = False,
         **extra: Any) -> Dict[str, Any]:
    info = catalog.get(pid)
    return dict({"id": pid, "name": info.name, "kind": info.kind, "state": state,
                 "detail": detail, "enabled": enabled, "verified": catalog.verified(pid),
                 "status": info.status}, **extra)


def _on_state(pid: str, readings: Dict[str, Any]) -> Dict[str, str]:
    """State of a switched-on account source, from its last reading."""
    r = readings.get(pid) or {}
    if r.get("error"):
        return {"state": "attention", "detail": str(r["error"])[:200]}
    return {"state": "connected", "detail": "" if r else "turned on - not read yet"}


def _grok_pool(cfg, readings) -> Dict[str, Any]:
    from omnimonitor.app import grok_login_state
    if cfg.get("source") == "command":  # never offer to replace the user's own source
        return _row("grok", "connected", "using your own command (config \"command\")")
    login = grok_login_state(cfg)
    on = cfg.get("source") == "grok_cli_billing"
    if login["state"] == "ok":
        return _row("grok", "connected" if on else "found", "Grok CLI is logged in", on)
    if on:
        return _row("grok", "attention", login["message"], on)
    return _row("grok", "not_available", "needs the Grok CLI: %s" % login["message"])


def _local(pid: str, found: Dict[str, Any], on: bool) -> Dict[str, Any]:
    if found.get("found"):
        return _row(pid, "connected" if on else "found",
                    "logs found on this computer", on)
    return _row(pid, "not_available",
                ("turned on, but " if on else "") + "no logs found on this computer", on)


def _cli(pid: str, cfg, readings, on: bool) -> Dict[str, Any]:
    from omnimonitor import cli_logins
    reader = {"claude_cli_plan": cli_logins.claude, "codex_cli_plan": cli_logins.codex,
              "gemini_cli_quota": cli_logins.gemini}[pid]
    try:
        reader(cfg)
    except cli_logins.LoginExpired as e:
        return _row(pid, "attention" if on else "not_available", str(e), on)
    except cli_logins.LoginMissing:
        return _row(pid, "attention" if on else "not_available",
                    "sign in with the CLI once: run `%s`" % CLI_COMMANDS[pid], on)
    if on:
        return _row(pid, enabled=True, **_on_state(pid, readings))
    return _row(pid, "found", "uses the CLI's own login (read-only)")


def _api(pid: str, readings, on: bool) -> Dict[str, Any]:
    from omnimonitor import accounts
    key, name = accounts.key_for(pid)
    if not key:
        command = "omnimonitor secrets set %s" % name
        return _row(pid, "add_key", "run `%s` in a terminal (or set %s)" % (command, name), on,
                    command=command)
    if on:
        return _row(pid, enabled=True, **_on_state(pid, readings))
    return _row(pid, "found", "an API key is saved")


def _web(pid: str, cfg, readings, on: bool) -> Dict[str, Any]:
    from omnimonitor import cursor_app_auth, login_specs, sessions, vault
    opts = (cfg.get("providers") or {}).get(pid)
    auth = opts.get("auth") if isinstance(opts, dict) else None
    extra: Dict[str, Any] = {}
    if pid == "cursor" and cursor_app_auth.default_db_path().is_file() \
            and not (on and auth == "cursor_app"):
        extra["cursor_app"] = True
    if on and auth == "cursor_app":
        return _row(pid, enabled=True, **_on_state(pid, readings), **extra)
    can_sign_in = pid in login_specs.SPECS and vault.available()
    if sessions.has(pid):
        if not on:
            return _row(pid, "found", "a saved sign-in exists", **extra)
        got = _on_state(pid, readings)
        return _row(pid, enabled=True, can_sign_in=can_sign_in, **got, **extra)
    if can_sign_in:
        return _row(pid, "sign_in", "sign in on the provider's own page", on,
                    can_sign_in=True, **extra)
    return _row(pid, "not_available", sessions.NO_VAULT_MESSAGE, on, **extra)


def source_rows(cfg: Dict[str, Any], readings: Optional[Dict[str, Any]] = None
                ) -> List[Dict[str, Any]]:
    from omnimonitor.adapters import describe
    readings = readings or {}
    found = {r["name"]: r for r in describe(cfg)}
    enabled = set(catalog.enabled_ids(cfg))
    demo = (cfg.get("source") or "demo") == "demo"
    rows = []
    for p in catalog.CATALOG:
        on = p.id in enabled and not demo
        try:
            if p.id == "grok":
                row = _grok_pool(cfg, readings)
            elif p.id == "grok_sessions":
                row = _local(p.id, found.get(p.id, {}),
                             not demo and bool(found.get(p.id, {}).get("enabled")))
            elif p.kind == "local_logs":
                row = _local(p.id, found.get(p.id, {}), on)
            elif p.kind == "cli_login":
                row = _cli(p.id, cfg, readings, on)
            elif p.kind == "api":
                row = _api(p.id, readings, on)
            elif p.kind == "web_session":
                row = _web(p.id, cfg, readings, on)
            else:
                row = _row(p.id, "not_available", p.reason)
        except Exception as e:  # noqa: BLE001 - one unreadable source must not blank setup
            row = _row(p.id, "not_available", "could not check (%s)" % type(e).__name__, on)
        rows.append(row)
    return rows


def _action(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The single most useful next step for a suite."""
    bulk = [r["id"] for r in rows if r["state"] == "found" and r["id"] not in NOT_IN_BULK]
    if bulk:
        return {"type": "enable", "label": "Turn on %d found" % len(bulk), "sources": bulk}
    connected = any(r["state"] == "connected" for r in rows)
    for r in rows:  # an expired sign-in comes before offering new ones
        if r["state"] == "attention" and r.get("can_sign_in"):
            return {"type": "signin", "source": r["id"], "label": "Sign in again"}
    if connected:
        return None
    for r in rows:
        if r["state"] == "sign_in":
            return {"type": "signin", "source": r["id"], "label": "Sign in"}
    for r in rows:
        if r["state"] == "add_key":
            return {"type": "add_key", "source": r["id"], "label": "Add key",
                    "command": r["command"]}
    return None


def status(cfg: Dict[str, Any], readings: Optional[Dict[str, Any]] = None,
           login_errors: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    rows = source_rows(cfg, readings)
    for r in rows:
        why = (login_errors or {}).get(r["id"])
        if why:
            r["detail"] = "last sign-in: %s" % why
    out = []
    for s in suites.SUITES:
        mine = [r for r in rows if suites.suite_of(r["id"]) == s.id]
        if s.id == "other" or not mine:
            continue
        state = min((r["state"] for r in mine), key=STATES.index)
        out.append({"id": s.id, "name": s.name, "state": state, "label": LABELS[state],
                    "action": _action(mine), "sources": mine})
    return {"suites": out, "demo": (cfg.get("source") or "demo") == "demo"}


def _leave_demo(cfg: Dict[str, Any], plan: Plan) -> None:
    if (cfg.get("source") or "demo") == "demo":
        plan.changes.setdefault("source", "none")
    if cfg.get("token_source") == "demo":
        plan.changes["token_source"] = "auto"


def _switch(cfg: Dict[str, Any], plan: Plan, pid: str, on: bool) -> None:
    if pid == "grok":
        plan.changes["source"] = "grok_cli_billing" if on else "none"
        return
    if pid == "grok_sessions":
        plan.changes["token_source"] = "auto" if on else "none"
        return
    providers = plan.changes.setdefault(
        "providers", {k: dict(v) for k, v in (cfg.get("providers") or {}).items()
                      if isinstance(v, dict)})
    entry = providers.setdefault(pid, {})
    entry["enabled"] = on
    if on and pid in catalog.WEB_SESSION_IDS and entry.get("auth") in (None, "", "off"):
        entry["auth"] = "login"


def plan(cfg: Dict[str, Any], body: Any) -> Plan:
    """Validate a setup request and return what to write. Pure apart from reading ``cfg``.

    ``{"action": "enable"|"disable", "sources": [...]}``, ``{"action": "signin",
    "source": pid}``, ``{"action": "cursor_app"}`` or ``{"action": "real"}``."""
    if not isinstance(body, dict):
        raise OnboardingError("bad request")
    action, out = body.get("action"), Plan()
    switchable = set(catalog.SWITCHABLE) | {"grok", "grok_sessions"}
    if action in ("enable", "disable"):
        ids = body.get("sources")
        if not isinstance(ids, list) or not ids or not all(
                isinstance(i, str) and i in switchable for i in ids):
            raise OnboardingError("unknown source")
        for pid in ids:
            _switch(cfg, out, pid, action == "enable")
        if action == "enable":
            _leave_demo(cfg, out)
            out.sources = list(ids)
    elif action == "signin":
        pid = body.get("source")
        from omnimonitor import login_specs
        if not isinstance(pid, str) or pid not in login_specs.SPECS:
            raise OnboardingError("that source has no sign-in")
        _switch(cfg, out, pid, True)
        out.changes["providers"][pid]["auth"] = "login"
        _leave_demo(cfg, out)
        out.signin, out.sources = pid, [pid]
    elif action == "cursor_app":
        _switch(cfg, out, "cursor", True)
        out.changes["providers"]["cursor"]["auth"] = "cursor_app"
        _leave_demo(cfg, out)
        out.sources = ["cursor"]
    elif action == "real":
        _leave_demo(cfg, out)
    else:
        raise OnboardingError("unknown action")
    return out


def render(st: Dict[str, Any]) -> List[str]:
    """Plain-text version of the Setup page."""
    out = []
    for s in st["suites"]:
        out.append("%-16s %s" % (s["name"], s["label"]))
        for r in s["sources"]:
            if r["kind"] == "none":
                continue
            note = " [not live-verified]" if r["verified"] == "fixtures" else ""
            out.append("    %-40s %-22s %s%s" % (r["name"][:40], LABELS[r["state"]],
                                                 r["detail"], note))
    return out


def cli_setup(cfg: Dict[str, Any], assume_yes: bool = False) -> int:
    """``omnimonitor setup``: show every suite, turn on what is already here, then check
    the Grok pool the same careful way as before (only switched on if billing answers)."""
    from pathlib import Path

    from omnimonitor import doctor
    if cfg.get("_forced_demo") and cfg.get("_config_path"):
        cfg = config_mod.load_config(Path(cfg["_config_path"]))  # --demo must not be saved
    st = status(cfg)
    print("OmniMonitor setup\n")
    for line in render(st):
        print(line)
    rows = [r for s in st["suites"] for r in s["sources"]]
    found = [r["id"] for r in rows if r["state"] == "found"
             and r["id"] not in NOT_IN_BULK + ("grok",)]
    path = Path(cfg["_config_path"]) if cfg.get("_config_path") else None
    changed = False
    if found:
        print("\nFound on this computer: %s" % ", ".join(catalog.name(i) for i in found))
        yes = assume_yes
        if not yes and sys.stdin.isatty():
            try:
                yes = input("Turn these on and use real data? [Y/n] ").strip().lower() in (
                    "", "y", "yes")
            except EOFError:  # no one to answer (Windows calls NUL a terminal)
                print()
        if yes:
            p = plan(cfg, {"action": "enable", "sources": found})
            config_mod.update_config_file(p.changes, path)
            cfg.update(p.changes)
            changed = True
            print("Saved. Start the app with `omnimonitor`, or run `omnimonitor status`.")
    if any(r["id"] == "grok" and r["state"] in ("found", "connected") for r in rows):
        print("\nGrok weekly pool:")
        changed = doctor.setup(cfg, assume_yes=assume_yes) == 0 or changed
    print("\nMore: sign in to websites on the app's Setup page (or `omnimonitor login "
          "<id>`), add API keys with `omnimonitor secrets set NAME`.")
    if changed or (cfg.get("source") or "demo") != "demo":
        return 0
    print("Nothing was turned on; the app keeps showing clearly labeled DEMO data.")
    return 1
