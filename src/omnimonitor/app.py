"""Controller: polls billing + token logs, keeps a thread-safe snapshot. No GUI here."""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from omnimonitor import config as config_mod
from omnimonitor import stats as stats_mod
from omnimonitor.billing import UsageResult, fetch_usage_safely
from omnimonitor.history import History
from omnimonitor.timeutil import utcnow
from omnimonitor.tokens import GrokSessionsScanner, demo_events


class Controller:
    def __init__(self, cfg: Dict[str, Any], history_file: Optional[Path] = None,
                 clock: Callable[[], datetime] = utcnow):
        self.cfg = cfg
        self.clock = clock
        self._lock = threading.RLock()
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
        self.token_source = config_mod.effective_token_source(cfg)
        if self.token_source == "grok_sessions":
            try:
                self.history = History(history_file or config_mod.history_path())
            except Exception as e:  # noqa: BLE001
                self.token_note = "history database unavailable: %s" % e
                self.token_source = "none"

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
        self._recompute()
        self._notify()

    def _scan_tokens(self) -> None:
        if self.token_source != "grok_sessions" or self.history is None:
            return
        scanner = GrokSessionsScanner(config_mod.grok_home(self.cfg), self.history)
        ok, why = scanner.available()
        if not ok:
            self.token_note = why
            return
        try:
            scanner.scan()
            self.token_note = ""
        except Exception as e:  # noqa: BLE001
            self.token_note = "could not read Grok CLI session logs: %s" % e

    def _recompute(self) -> None:
        now = self.clock()
        billing = self.usage.to_dict() if self.usage else None
        data_since = None
        if self.token_source == "demo":
            events, turns = demo_events(now.timestamp())
            note = "DEMO - fake token history"
        elif self.token_source == "grok_sessions" and self.history is not None:
            events, turns = self.history.events(), self.history.turns()
            note = self.token_note
            try:
                data_since = float(self.history.meta("installed_at") or 0) or None
            except ValueError:
                data_since = None
        else:
            events, turns, note = [], [], self.token_note
        st = stats_mod.compute(events, turns, billing, self.cfg, now,
                               provider=self.token_source if self.token_source in (
                                   "demo", "grok_sessions") else "none",
                               provider_note=note, data_since=data_since)
        st["status"] = self.status_dict()
        with self._lock:
            self._stats = st

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
