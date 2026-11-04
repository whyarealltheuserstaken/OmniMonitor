"""Controller: polls billing + token logs, keeps a thread-safe snapshot. No GUI here."""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from omnimonitor import accounts as accounts_mod
from omnimonitor import adapters
from omnimonitor import alerts as alerts_mod
from omnimonitor import config as config_mod
from omnimonitor import forecast as forecast_mod
from omnimonitor import overview as overview_mod
from omnimonitor import settings as settings_mod
from omnimonitor import stats as stats_mod
from omnimonitor.billing import UsageResult, fetch_usage_safely
from omnimonitor.history import History
from omnimonitor.timeutil import utcnow
from omnimonitor.tokens import DEMO_TOOLS, demo_codex_limits, demo_events, demo_readings


class Controller:
    def __init__(self, cfg: Dict[str, Any], history_file: Optional[Path] = None,
                 clock: Callable[[], datetime] = utcnow):
        self.cfg = cfg
        self.clock = clock
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
        self.last_attempt = self.clock()
        usage, err = fetch_usage_safely(self.cfg)
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
        self._notify()

    def _refresh_accounts(self, force: bool = False) -> None:
        """Official account APIs for enabled providers (rate-limited, never raises)."""
        if self.token_source == "demo":
            self.readings = demo_readings(self.clock().timestamp())
            return
        ids = config_mod.enabled_api_providers(self.cfg)
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
                sc.scan()
            except Exception as e:  # noqa: BLE001
                notes.append("could not read %s logs: %s" % (name or "Grok CLI", e))
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
                               snapshots=snapshots, tools=tools)
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
        st["status"] = self.status_dict()
        with self._lock:
            self._stats = st

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

    def poke(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def start_background(self) -> threading.Thread:
        t = threading.Thread(target=self.run_loop, name="grok-usage-poll", daemon=True)
        t.start()
        return t


def wait_first(ctrl: Controller, timeout: float = 30.0) -> None:
    end = time.time() + timeout
    while ctrl.last_attempt is None and time.time() < end:
        time.sleep(0.05)
