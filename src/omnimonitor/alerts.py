"""Usage notifications. Pure decision logic + a best-effort desktop delivery.

Decisions are made only from REAL billing readings (never demo). "Already fired" state is
kept per billing period in history meta, so restarting the app doesn't repeat alerts.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

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
    if state.get("period") != period:
        if state.get("period") and period and cfg.get("notify_on_reset", True) \
                and used < float(state.get("last_used", 0)):
            out.append({"kind": "reset", "title": "Grok weekly usage reset",
                        "body": "Your weekly pool reset - %.0f%% used now." % used})
        new = {"period": period, "fired": [], "pace_fired": False}
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


def load_state(history) -> Dict[str, Any]:
    if history is None:
        return {}
    try:
        return json.loads(history.meta(META_KEY) or "{}") or {}
    except (ValueError, TypeError):
        return {}


def save_state(history, state: Dict[str, Any]) -> None:
    if history is not None:
        history.set_meta(META_KEY, json.dumps(state, sort_keys=True))


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
