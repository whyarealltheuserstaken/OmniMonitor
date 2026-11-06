"""Controller: polls billing + token logs, keeps a thread-safe snapshot. No GUI here."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from omnimonitor import accounts as accounts_mod
from omnimonitor import adapters, catalog
from omnimonitor import alerts as alerts_mod
from omnimonitor import config as config_mod
from omnimonitor import forecast as forecast_mod
from omnimonitor import net as net_mod
from omnimonitor import overview as overview_mod
from omnimonitor import settings as settings_mod
from omnimonitor import stats as stats_mod
from omnimonitor import status as status_mod
from omnimonitor.billing import UsageError, UsageResult, fetch_usage_safely
from omnimonitor.history import History
from omnimonitor.timeutil import utcnow
from omnimonitor.tokens import DEMO_TOOLS, demo_codex_limits, demo_events, demo_readings

log = logging.getLogger("omnimonitor.app")


class Controller:
    def __init__(self, cfg: Dict[str, Any], history_file: Optional[Path] = None,
                 clock: Callable[[], datetime] = utcnow):
        self.cfg = cfg
        self.clock = clock
        net_mod.configure(cfg)
        self.service_status: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._scan_lock = threading.Lock()  # web "save settings" and the poll loop both scan
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._listeners: List[Callable[[], None]] = []
        self.usage: Optional[UsageResult] = None
        self.error: Optional[str] = cfg.get("_config_error")
        self.last_ok: Optional[datetime] = None
        self.last_attempt: Optional[datetime] = None
        self.token_note = ""
        self._stats: Optional[Dict[str, Any]] = None
        self.history: Optional[History] = None
        self.history_file = history_file
        self.notifier: Optional[Callable[[str, str], None]] = None  # set by the tray
        self.sent_alerts: List[Dict[str, str]] = []
        self.token_source = config_mod.effective_token_source(cfg)
        self.readings: Dict[str, Dict[str, Any]] = {}
        self.http = None  # tests inject a fake HTTP client for account readers
        self._installer = None  # setup wizard; created lazily (tests inject one)
        self.state_file: Optional[Path] = None  # set by the tray: doctor compares against it
        self.started = clock()
        self._cfg_mtime = self._config_mtime()
        self._open_history()

    def _open_history(self) -> None:
        wants = self.token_source == "grok_sessions" or (
            self.token_source != "demo" and (config_mod.extra_token_sources(self.cfg)
                                             or config_mod.enabled_api_providers(self.cfg)))
        if wants and self.history is None:
            try:
                self.history = History(self.history_file or config_mod.history_path())
            except Exception as e:  # noqa: BLE001
                self.token_note = "history database unavailable: %s" % e
                if self.token_source == "grok_sessions":
                    self.token_source = "none"

    # ------------------------------------------------------------ config reload
    def _config_mtime(self) -> Optional[float]:
        p = self.cfg.get("_config_path")
        try:
            return Path(p).stat().st_mtime if p else None
        except OSError:
            return None

    def reload_config_if_changed(self) -> bool:
        """Pick up edits made while the app runs (e.g. `omnimonitor setup` in a terminal
        switching source from demo to grok_cli_billing). Never raises."""
        m = self._config_mtime()
        if m is None or m == self._cfg_mtime:
            return False
        self._cfg_mtime = m
        try:
            new = config_mod.load_config(Path(self.cfg["_config_path"]))
        except Exception:  # noqa: BLE001
            log.exception("config reload failed")
            return False
        if self.cfg.get("_forced_demo"):  # started with --demo: stay in demo
            new["source"], new["token_source"], new["_forced_demo"] = "demo", "demo", True
        self._apply_cfg(new)
        log.info("config reloaded from %s (source=%s)", new.get("_config_path"),
                 new.get("source"))
        return True

    def _apply_cfg(self, new: Dict[str, Any]) -> None:
        with self._lock:
            old_ts = self.token_source
            self.cfg.clear()
            self.cfg.update(new)
            net_mod.configure(self.cfg)
            self.token_source = config_mod.effective_token_source(self.cfg)
            if old_ts == "demo" and self.token_source != "demo":
                self.readings = {}
            if self.usage is not None and self.usage.is_demo and self.cfg.get("source") != "demo":
                self.usage = None
            self.error = self.cfg.get("_config_error")
        self._open_history()

    def tools(self) -> List[str]:
        """Token sources actually feeding the stats."""
        if self.token_source == "demo":
            return list(DEMO_TOOLS)
        out = ["grok_sessions"] if self.token_source == "grok_sessions" else []
        if self.history is not None:
            out += config_mod.extra_token_sources(self.cfg)
        return out

    # ------------------------------------------------------------------ polling
    def add_listener(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    def _notify(self) -> None:
        for fn in list(self._listeners):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass

    def refresh(self) -> None:
        """One poll. Never raises."""
        self.reload_config_if_changed()
        self.last_attempt = self.clock()
        t0 = time.monotonic()
        usage, err = fetch_usage_safely(self.cfg)
        dt = time.monotonic() - t0
        if usage is not None:
            log.info("poll source=%s ok: %.1f%% remaining%s (%.1fs)", self.cfg.get("source"),
                     usage.remaining_percent, " [DEMO]" if usage.is_demo else "", dt)
        else:
            log.warning("poll source=%s failed (%.1fs): %s", self.cfg.get("source"), dt, err)
        with self._lock:
            if usage is not None:
                self.usage, self.error, self.last_ok = usage, None, self.clock()
            else:
                self.error = err  # keep the last good value, but flag it as stale in the UI
        if usage is not None and not usage.is_demo and self.history is not None:
            try:
                self.history.add_billing_snapshot(
                    usage.used_percent, usage.raw_used_percent,
                    usage.period_start.isoformat() if usage.period_start else None,
                    usage.resets_at.isoformat() if usage.resets_at else None, usage.products)
            except Exception:  # noqa: BLE001
                pass
        self._scan_tokens()
        self._refresh_accounts()
        self._recompute()
        self._check_alerts()
        if self.token_note:
            log.info("token stats: %s", self.token_note)
        self._write_state()
        self._notify()

    def _write_state(self) -> None:
        """Small, secret-free JSON snapshot of what the running app uses, for `doctor`."""
        if self.state_file is None:
            return
        try:
            import os
            import sys

            from omnimonitor import __version__
            from omnimonitor.billing import find_grok_binary
            st = self.status_dict()
            st.pop("usage", None)
            u = self.usage
            doc = {"pid": os.getpid(), "version": __version__, "executable": sys.executable,
                   "started": self.started.isoformat(), "written": self.clock().isoformat(),
                   "remaining_percent": u.remaining_percent if u else None,
                   "usage_is_demo": bool(u and u.is_demo),
                   "token_note": self.token_note, "tools": self.tools(),
                   "grok_binary": find_grok_binary(self.cfg),
                   "grok_auth_file": str(config_mod.grok_auth_file(self.cfg)),
                   "grok_home": str(config_mod.grok_home(self.cfg)),
                   "path_env": os.environ.get("PATH", ""), **st}
            tmp = self.state_file.with_suffix(".tmp")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
            os.replace(str(tmp), str(self.state_file))
        except Exception:  # noqa: BLE001
            log.exception("could not write app state")

    def _refresh_accounts(self, force: bool = False) -> None:
        """Official account APIs for enabled providers (rate-limited, never raises)."""
        if self.token_source == "demo":
            self.readings = demo_readings(self.clock().timestamp())
            self.service_status = {}
            return
        self._refresh_status()
        ids = list(dict.fromkeys(
            config_mod.enabled_api_providers(self.cfg) + config_mod.enabled_web_providers(self.cfg)))
        if not ids:
            self.readings = {}
            return
        try:
            got = accounts_mod.refresh(self.cfg, self.history, ids, force=force, http=self.http,
                                       now=self.clock().timestamp())
            self.readings = {k: v.to_dict() for k, v in got.items()}
        except Exception as e:  # noqa: BLE001
            self.token_note = (self.token_note + "; " if self.token_note else "") + \
                "account readers failed: %s" % e.__class__.__name__

    def _refresh_status(self) -> None:
        """Opt-in official status-page badges (cached 5 min; never raises)."""
        try:
            ids = set(self.tools()) | set(config_mod.enabled_api_providers(self.cfg)) | set(
                config_mod.enabled_web_providers(self.cfg)) | {"grok"}
            fams = {catalog.get(i).family for i in ids if catalog.get(i)}
            self.service_status = status_mod.fetch_all(self.cfg, fams, http=self.http,
                                                       now=self.clock().timestamp())
        except Exception:  # noqa: BLE001
            self.service_status = {}

    def _scan_tokens(self) -> None:
        if self.history is None or self.token_source == "demo":
            return
        with self._scan_lock:
            self._scan_tokens_locked()

    def _scan_tokens_locked(self) -> None:
        notes = []
        for sc in adapters.scanners(self.cfg, self.history,
                                    include_grok=self.token_source == "grok_sessions"):
            name = getattr(sc, "label", "") or adapters.label(getattr(sc, "name", ""))
            ok, why = sc.available()
            if not ok:
                notes.append(why)
                continue
            try:
                res = sc.scan()
            except Exception as e:  # noqa: BLE001
                notes.append("could not read %s logs: %s" % (name or "Grok CLI", e))
                continue
            # Only shown when no token data exists at all: say what we saw, never guess.
            if getattr(sc, "name", "") == "grok_sessions" and isinstance(res, dict):
                root = getattr(sc, "grok_home", config_mod.grok_home(self.cfg)) / "sessions"
                if not res.get("files"):
                    notes.append("%s exists but has no */updates.jsonl session files yet "
                                 "(use the Grok CLI once, then wait for the next refresh)" % root)
                elif res.get("errors"):
                    notes.append("could not read %d of %d Grok session files in %s" % (
                        res["errors"], res["files"], root))
        if self.token_source == "grok_sessions" and any("Grok" in n for n in notes) and \
                "grok_unified" not in config_mod.extra_token_sources(self.cfg):
            uni = config_mod.grok_home(self.cfg) / "logs" / "unified.jsonl"
            notes.append("Grok token stats appear after you use the Grok CLI (it writes "
                         "~/.grok/sessions)" + (
                             "; %s exists - you can turn on the experimental 'Grok CLI unified "
                             "log' provider (don't use both: turns would count twice)" % uni
                             if uni.is_file() else ""))
        self.token_note = "; ".join(notes)

    def _check_alerts(self) -> None:
        u = self.usage
        if u is None or u.is_demo or self.history is None:
            self._check_provider_alerts()
            return
        try:
            billing = u.to_dict()
            fc = (self._stats or {}).get("forecast")
            state = alerts_mod.load_state(self.history)
            msgs, new = alerts_mod.decide(self.cfg, billing, fc, state)
            if new != state:
                alerts_mod.save_state(self.history, new)
            for m in msgs:
                alerts_mod.deliver(m["title"], m["body"], self.notifier)
                self.sent_alerts.append(m)
        except Exception:  # noqa: BLE001 - alerts must never break polling
            pass
        self._check_provider_alerts()

    def _check_provider_alerts(self) -> None:
        if self.token_source == "demo" or self.history is None:
            return
        try:
            ov = (self._stats or {}).get("providers") or {}
            key = alerts_mod.PROVIDER_META_KEY
            state = alerts_mod.load_state(self.history, key)
            msgs, new = alerts_mod.decide_providers(self.cfg, ov, state)
            if new != state:
                alerts_mod.save_state(self.history, new, key)
            for m in msgs:
                alerts_mod.deliver(m["title"], m["body"], self.notifier)
                self.sent_alerts.append(m)
        except Exception:  # noqa: BLE001
            pass

    def update_settings(self, changes: Any, web: bool = True) -> Dict[str, Any]:
        """Validate (allow-list), write to the config file, apply live. Never raises."""
        clean, errors = settings_mod.apply(self.cfg, changes, web=web)
        if errors:
            return {"ok": False, "errors": errors}
        if not clean:
            return {"ok": True, "changed": []}
        try:
            if self.cfg.get("_config_path"):
                config_mod.update_config_file(clean, Path(self.cfg["_config_path"]))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "errors": {"_": "could not save config: %s" % e}}
        with self._lock:
            self.cfg.update(clean)
            net_mod.configure(self.cfg)
            self.cfg["poll_interval_seconds"] = config_mod.clamp_poll(
                self.cfg.get("poll_interval_seconds"))
        self._open_history()
        if "extra_token_sources" in clean or "providers" in clean:
            self._scan_tokens()
            self._refresh_accounts()
        self._recompute()
        self._notify()
        return {"ok": True, "changed": sorted(clean)}

    def _recompute(self) -> None:
        now = self.clock()
        billing = self.usage.to_dict() if self.usage else None
        data_since = None
        snapshots: List[Dict[str, Any]] = []
        if self.token_source == "demo":
            events, turns = demo_events(now.timestamp())
            note = "DEMO - fake token history"
        elif self.history is not None:
            events, turns = self.history.events(), self.history.turns()
            try:
                snapshots = self.history.billing_snapshots(
                    now.timestamp() - forecast_mod.RECENT_WINDOW_H * 3600 - 60)
            except Exception:  # noqa: BLE001
                snapshots = []
            note = self.token_note
            try:
                data_since = float(self.history.meta("installed_at") or 0) or None
            except ValueError:
                data_since = None
        else:
            events, turns, note = [], [], self.token_note
        tools = self.tools()
        provider = self.token_source if self.token_source in ("demo", "grok_sessions") else (
            "local_logs" if tools else "none")
        st = stats_mod.compute(events, turns, billing, self.cfg, now, provider=provider,
                               provider_note=note, data_since=data_since,
                               snapshots=snapshots, tools=tools, readings=dict(self.readings))
        if self.token_source == "demo":
            codex = demo_codex_limits(now.timestamp())
        else:
            codex = adapters.codex_limits(self.history) if self.history is not None else None
        try:
            st["providers"] = overview_mod.build(
                events, self.cfg, now, tools=tools, readings=dict(self.readings),
                codex_limits=codex if "codex" in tools else None, billing=billing,
                is_demo=self.token_source == "demo")
        except Exception as e:  # noqa: BLE001 - a bad reading must not blank the page
            st["providers"] = {"error": "provider overview failed: %s" % e.__class__.__name__,
                               "cards": []}
        ss = dict(self.service_status)
        if isinstance(st.get("providers"), dict):
            st["providers"]["service_status"] = ss
            for c in st["providers"].get("cards") or []:
                fam = c.get("family")
                if fam in ss:
                    c["service"] = ss[fam]
        st["status"] = self.status_dict()
        with self._lock:
            self._stats = st

    # ------------------------------------------------------------ setup wizard
    @property
    def installer(self):
        if self._installer is None:
            from omnimonitor import deps
            self._installer = deps.Installer(log_path=config_mod.config_dir() / "setup-log.jsonl")
        return self._installer

    def setup_status(self) -> Dict[str, Any]:
        from omnimonitor import deps
        st = deps.status(self.cfg)
        st["history"] = self.installer.history(20)
        st["demo"] = self.cfg.get("source") == "demo"
        st["grok_login"] = grok_login_state(self.cfg)
        st["last_ok"] = self.last_ok.isoformat() if self.last_ok else None
        st["error"] = self.error
        return st

    def setup_use_real(self, body: Any = None) -> Dict[str, Any]:
        """Setup page: check the Grok login + billing, then switch source from demo to
        grok_cli_billing (same as `omnimonitor setup`). Never raises."""
        from omnimonitor.billing import fetch_grok_cli_billing
        real = dict(self.cfg)
        real["source"] = "grok_cli_billing"
        try:
            r = fetch_grok_cli_billing(real)
        except UsageError as e:
            log.warning("setup: real Grok usage check failed: %s", e)
            return {"ok": False, "error": str(e), "grok_login": grok_login_state(self.cfg)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        changes = {"source": "grok_cli_billing"}
        try:
            if self.cfg.get("_config_path"):
                config_mod.update_config_file(changes, Path(self.cfg["_config_path"]))
                self._cfg_mtime = self._config_mtime()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": "could not save config: %s" % e}
        new = dict(self.cfg)
        new.update(changes)
        new.pop("_forced_demo", None)
        if (new.get("token_source") or "auto") == "demo":
            new["token_source"] = "auto"
        self._apply_cfg(new)
        log.info("setup: switched to real Grok usage (%.1f%% remaining)", r.remaining_percent)
        self.poke()
        return {"ok": True, "remaining_percent": r.remaining_percent,
                "resets_at": r.resets_at.isoformat() if r.resets_at else None}

    def setup_prepare(self, body: Any) -> Dict[str, Any]:
        dep = body.get("dep") if isinstance(body, dict) else None
        if not isinstance(dep, str):
            return {"ok": False, "error": "dep required"}
        return self.installer.prepare(dep)

    def setup_run(self, body: Any) -> Dict[str, Any]:
        if not isinstance(body, dict):
            return {"ok": False, "error": "bad request"}
        res = self.installer.run(str(body.get("token") or ""), str(body.get("sha256") or ""),
                                 dry_run=bool(body.get("dry_run")))
        self.poke()
        return res

    def export_events(self) -> List[Dict[str, Any]]:
        if self.token_source == "demo":
            return demo_events(self.clock().timestamp())[0]
        if self.history is not None:
            return self.history.events()
        return []

    def export(self, kind: str, fmt: str) -> Tuple[bytes, str, str]:
        """-> (body, content type, download file name). Raises ValueError on bad kind."""
        from omnimonitor import export as export_mod

        evs = self.export_events()
        demo = self.token_source == "demo"
        prefix = "omnimonitor-%s" % ("DEMO-" if demo else "")
        if kind == "providers":
            ov = self.stats().get("providers") or {}
            if fmt == "json":
                return (export_mod.providers_json(ov).encode("utf-8"), "application/json",
                        prefix + "providers.json")
            if fmt == "csv":
                return (export_mod.to_csv(export_mod.provider_rows(ov)).encode("utf-8"),
                        "text/csv; charset=utf-8", prefix + "providers.csv")
            raise ValueError("unknown format")
        if fmt == "json":
            return (export_mod.to_json(evs, demo).encode("utf-8"), "application/json",
                    prefix + "export.json")
        if fmt == "csv":
            body = export_mod.to_csv(export_mod.rows(evs, kind))
            return body.encode("utf-8"), "text/csv; charset=utf-8", "%s%s.csv" % (prefix, kind)
        raise ValueError("unknown format")

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            s = self._stats
        if s is None:
            self._recompute()
            with self._lock:
                s = self._stats
        s = dict(s or {})
        s["status"] = self.status_dict()
        return s

    def status_dict(self) -> Dict[str, Any]:
        with self._lock:
            u = self.usage
            return {
                "source": self.cfg.get("source"),
                "token_source": self.token_source,
                "error": self.error,
                "last_ok": self.last_ok.isoformat() if self.last_ok else None,
                "last_attempt": self.last_attempt.isoformat() if self.last_attempt else None,
                "usage": u.to_dict() if u else None,
                "poll_interval_seconds": self.cfg.get("poll_interval_seconds"),
                "usage_page_url": self.cfg.get("usage_page_url"),
                "config_path": self.cfg.get("_config_path"),
            }

    def run_loop(self) -> None:
        while not self._stop.is_set():
            self.refresh()
            self._wake.wait(float(self.cfg.get("poll_interval_seconds") or 120))
            self._wake.clear()

    def refresh_now(self, body: Any = None) -> Dict[str, Any]:
        """Refresh button: poll now (synchronously) so the reply carries the fresh state."""
        self.refresh()
        return {"ok": self.error is None, "status": self.status_dict()}

    def poke(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def start_background(self) -> threading.Thread:
        t = threading.Thread(target=self.run_loop, name="omnimonitor-poll", daemon=True)
        t.start()
        return t


def grok_login_state(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Local-only check of the Grok CLI install + login (no network). Never raises."""
    from omnimonitor.billing import find_grok_binary, read_grok_auth
    binary = find_grok_binary(cfg)
    auth = config_mod.grok_auth_file(cfg)
    cmd = ('"%s" login' % binary if " " in binary else binary + " login") if binary \
        else "grok login"
    out: Dict[str, Any] = {"binary": binary, "auth_file": str(auth), "login_command": cmd}
    if not binary:
        out.update(state="not_installed", message="Grok CLI not found (looked on PATH and in "
                   "%s)" % (config_mod.grok_home(cfg) / "bin"))
        return out
    try:
        _tok, exp = read_grok_auth(auth)
    except UsageError as e:
        out.update(state="not_logged_in", message=str(e))
        return out
    if exp is not None and exp <= utcnow():
        out.update(state="expired", expires_at=exp.isoformat(),
                   message="login expired %s; the app asks the Grok CLI to refresh it, or run "
                           "`%s`" % (exp.isoformat(), cmd))
        return out
    out.update(state="ok", expires_at=exp.isoformat() if exp else None, message="logged in")
    return out


def wait_first(ctrl: Controller, timeout: float = 30.0) -> None:
    end = time.time() + timeout
    while ctrl.last_attempt is None and time.time() < end:
        time.sleep(0.05)
