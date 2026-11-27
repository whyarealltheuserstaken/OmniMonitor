"""Usage notifications. Pure decision logic + a best-effort desktop delivery.

Decisions are made only from REAL billing readings (never demo). "Already fired" state is
kept per billing period / limit window in history meta, so restarting the app doesn't repeat
alerts. Providers don't report a stable reset time, so windows are matched with a tolerance
(:func:`omnimonitor.timeutil.same_window`).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from omnimonitor.timeutil import fmt_local, same_window

META_KEY = "alerts_state"


def thresholds(cfg: Dict[str, Any]) -> List[float]:
    raw = cfg.get("notify_thresholds")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        raw = [raw]
    if isinstance(raw, str):
        raw = raw.split(",")
    out = set()
    for v in raw or []:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if 0 < f <= 100:
            out.add(f)
    return sorted(out)


def decide(cfg: Dict[str, Any], billing: Optional[Dict[str, Any]],
           forecast: Optional[Dict[str, Any]], state: Dict[str, Any]
           ) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """-> (notifications to show, new state). Pure."""
    if not cfg.get("notifications", True) or not billing or billing.get("is_demo"):
        return [], state
    used = billing.get("raw_used_percent", billing.get("used_percent"))
    if used is None:
        return [], state
    used = float(used)
    period = billing.get("resets_at") or ""
    new = dict(state)
    out: List[Dict[str, str]] = []
    if "period" not in state or not same_window(state.get("period"), period):
        if state.get("period") and period and cfg.get("notify_on_reset", True) \
                and used < float(state.get("last_used", 0)):
            out.append({"kind": "reset", "title": "Grok weekly usage reset",
                        "body": "Your weekly pool reset - %.0f%% used now." % used})
        new = {"period": period, "fired": [], "pace_fired": False}
    new["period"] = period or new.get("period") or ""
    fired = set(float(x) for x in new.get("fired", []))
    crossed = [t for t in thresholds(cfg) if used >= t and t not in fired]
    if crossed:
        t = max(crossed)
        out.append({"kind": "threshold", "title": "Grok usage at %.0f%%" % used,
                    "body": "You've used %.0f%% of this week's pool (alert at %.0f%%)."
                            % (used, t)})
        fired.update(crossed)
    new["fired"] = sorted(fired)
    if cfg.get("notify_on_pace") and not new.get("pace_fired") and forecast:
        avg = forecast.get("average") or {}
        if avg.get("runs_out_at") and used < 100:
            out.append({"kind": "pace", "title": "On pace to run out before reset",
                        "body": "ESTIMATE: at the current average pace the pool runs out "
                                "before it resets."})
            new["pace_fired"] = True
    new["last_used"] = used
    return out, new


PROVIDER_META_KEY = "alerts_state_providers"
#: Without a reset time, usage must fall this far under a level before it can alert again.
REARM_MARGIN = 5.0


def decide_providers(cfg: Dict[str, Any], ov: Dict[str, Any], state: Dict[str, Any]
                     ) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """Per-provider limit thresholds (non-Grok cards) and USD budget alerts. Pure.

    State is keyed by provider + window name and remembers that window's reset time, so each
    threshold fires once per window and again after the window resets. A window missing from
    one check keeps its state. Demo data never alerts."""
    if not cfg.get("notifications", True) or not ov or ov.get("is_demo"):
        return [], state
    from omnimonitor.overview import provider_thresholds, run_out_warning

    new: Dict[str, Any] = dict(state)
    out: List[Dict[str, str]] = []
    for c in ov.get("cards") or []:
        if c.get("id") == "grok" or c.get("demo"):
            continue  # the Grok pool has its own alerts (decide)
        ths = provider_thresholds(cfg, c["id"])
        for w in c.get("limits") or []:
            used = w.get("used_percent")
            if used is None or w.get("stale"):
                continue
            key = "%s:%s" % (c["id"], w.get("name"))
            reset = w.get("resets_at")
            before = new.get(key)
            alerted = set(before.get("fired") or []) if isinstance(before, dict) else set()
            entry = _window_state(new, key, reset)
            fired = set(float(x) for x in entry.get("fired", []))
            if not reset:  # no reset time to go by: re-arm levels usage has clearly left
                fired = {t for t in fired if used >= t - REARM_MARGIN}
            window = w.get("name") or "usage"
            if alerted and not fired and used < min(alerted) \
                    and cfg.get("notify_on_reset", True):
                out.append({"kind": "reset", "title": "%s: %s limit reset" % (c["name"], window),
                            "body": "%s's %s limit has reset: %.0f%% used now."
                                    % (c["name"], window, used)})
            warned = bool(entry.get("pace_fired"))
            runs_out = run_out_warning(w)
            if runs_out and not warned and cfg.get("notify_on_pace"):
                when = fmt_local(datetime.fromtimestamp(runs_out, timezone.utc))
                out.append({"kind": "pace", "title": "%s may run out early" % c["name"],
                            "body": "ESTIMATE: at the pace so far, the %s runs out around %s, "
                                    "before it resets." % (window, when)})
                warned = True
            crossed = [t for t in ths if used >= t and t not in fired]
            if crossed:
                out.append({"kind": "provider", "title": "%s at %.0f%%" % (c["name"], used),
                            "body": "%s: %.0f%% of the %s used (alert at %.0f%%)."
                                    % (c["name"], used, w.get("name") or "limit",
                                       max(crossed))})
                fired.update(crossed)
            new[key] = {"reset": reset or entry.get("reset"), "fired": sorted(fired),
                        "pace_fired": warned}
    b = ov.get("budgets") or {}
    alert = float(b.get("alert_percent") or 80)
    for period, row in (b.get("periods") or {}).items():
        pct = row.get("percent")
        if pct is None:
            continue
        key = "budget:%s:%s" % (period, row.get("starts_at"))
        fired = set(float(x) for x in state.get(key, []))
        for old in [k for k in new if k.startswith("budget:%s:" % period) and k != key]:
            del new[old]
        for t in (alert, 100.0):
            if pct >= t and t not in fired:
                fired.add(t)
                if t == 100.0 or pct < 100:
                    out.append({"kind": "budget",
                                "title": "%s budget %s" % (period.title(),
                                                           "exceeded" if pct >= 100 else
                                                           "at %.0f%%" % pct),
                                "body": "$%.2f of your $%.2f %s budget (local logs, "
                                        "recorded or estimated)."
                                        % (row["spent_usd"], row["limit_usd"], period)})
        new[key] = sorted(fired)
    return out, new


def _window_state(state: Dict[str, Any], key: str, reset: Any) -> Dict[str, Any]:
    """What already fired for the current window, or {} for a new one. Also takes over (and
    removes) the 0.6.0 entries, which were keyed ``provider:window:reset`` -> fired list."""
    entry = state.get(key)
    for old in [k for k, v in state.items() if k.startswith(key + ":") and isinstance(v, list)]:
        suffix = old[len(key) + 1:]
        try:
            old_reset = float(suffix) if suffix else None
        except ValueError:
            continue  # a different window whose name starts the same way
        if entry is None and same_window(old_reset, reset):
            entry = {"reset": old_reset, "fired": state[old]}
        del state[old]
    if isinstance(entry, dict) and same_window(entry.get("reset"), reset):
        return entry
    return {}


def load_state(history, key: str = META_KEY) -> Dict[str, Any]:
    if history is None:
        return {}
    try:
        return json.loads(history.meta(key) or "{}") or {}
    except (ValueError, TypeError):
        return {}


def save_state(history, state: Dict[str, Any], key: str = META_KEY) -> None:
    if history is not None:
        history.set_meta(key, json.dumps(state, sort_keys=True))


def deliver(title: str, body: str, tray_notify: Optional[Callable[[str, str], None]] = None
            ) -> bool:
    """Best effort: the tray's own notify, else osascript / notify-send. Never raises."""
    if tray_notify is not None:
        try:
            tray_notify(body, title)
            return True
        except Exception:  # noqa: BLE001
            pass
    try:
        if sys.platform == "darwin" and shutil.which("osascript"):
            subprocess.run(["osascript", "-e",
                            "display notification %s with title %s"
                            % (json.dumps(body), json.dumps(title))],
                           timeout=10, check=False, capture_output=True)
            return True
        if shutil.which("notify-send"):
            subprocess.run(["notify-send", "--app-name=OmniMonitor", title, body],
                           timeout=10, check=False, capture_output=True)
            return True
    except Exception:  # noqa: BLE001
        pass
    return False
