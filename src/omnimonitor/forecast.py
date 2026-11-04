"""Pace / forecast for the weekly pool. Pure functions, every output is labeled.

Only two real inputs exist: the used % and the period start/reset times from billing,
plus (optionally) earlier real billing snapshots stored in history. From them:

  average pace  = used% / hours elapsed this period           (needs period_start)
  projection    = used% + pace * hours left  -> "% at reset"   (linear, an ESTIMATE)
  runs out at   = when the projection hits 100 %, if before reset
  recent pace   = slope between the oldest and newest snapshot of the last N hours in
                  this period (needs >= 2 real snapshots, >= 30 min apart)

Anything that can't be derived is None with a reason.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from omnimonitor.timeutil import parse_time

MIN_ELAPSED_H = 1.0
RECENT_WINDOW_H = 6.0
MIN_RECENT_SPAN_H = 0.5


def _na(reason: str) -> Dict[str, Any]:
    return {"value": None, "reason": reason}


def _project(used: float, rate_per_h: float, now: datetime, reset: datetime
             ) -> Dict[str, Any]:
    hours_left = max(0.0, (reset - now).total_seconds() / 3600.0)
    at_reset = used + rate_per_h * hours_left
    out: Dict[str, Any] = {"rate_per_hour": rate_per_h, "percent_at_reset": at_reset,
                           "runs_out_at": None}
    if used >= 100:
        out["runs_out_at"] = now.isoformat()
    elif rate_per_h > 0 and at_reset > 100:
        out["runs_out_at"] = (now + timedelta(hours=(100.0 - used) / rate_per_h)).isoformat()
    return out


def pace(billing: Optional[Dict[str, Any]], now: datetime,
         snapshots: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    if not billing or billing.get("used_percent") is None:
        return {"average": _na("no usage percentage from billing"),
                "recent": _na("no usage percentage from billing"), "is_demo": False}
    used = float(billing.get("raw_used_percent", billing["used_percent"]) or 0.0)
    start = parse_time(billing.get("period_start"))
    reset = parse_time(billing.get("resets_at"))
    out: Dict[str, Any] = {"used_percent": used, "is_demo": bool(billing.get("is_demo"))}
    if reset is None:
        out["average"] = out["recent"] = _na("billing didn't report a reset time")
        return out
    out["hours_left"] = max(0.0, (reset - now).total_seconds() / 3600.0)
    if start is None or not start < reset:
        out["average"] = _na("billing didn't report when this week started")
    else:
        el_h = (now - start).total_seconds() / 3600.0
        total_h = (reset - start).total_seconds() / 3600.0
        if el_h < MIN_ELAPSED_H:
            out["average"] = _na("week just started - too early for a pace")
        else:
            a = _project(used, used / el_h, now, reset)
            a["even_pace_percent"] = min(100.0, 100.0 * el_h / total_h)
            a["status"] = "ahead" if used > a["even_pace_percent"] + 2 else (
                "behind" if used < a["even_pace_percent"] - 2 else "on pace")
            a["kind"] = "ESTIMATE (linear, average since week start)"
            out["average"] = a
    out["recent"] = recent_pace(snapshots or [], used, now, start, reset)
    return out


def recent_pace(snaps: List[Dict[str, Any]], used: float, now: datetime,
                start: Optional[datetime], reset: datetime) -> Dict[str, Any]:
    lo = now.timestamp() - RECENT_WINDOW_H * 3600
    if start is not None:
        lo = max(lo, start.timestamp())
    pts = [(float(s["ts"]), float(s.get("raw_used_percent", s.get("used_percent")) or 0))
           for s in snaps
           if s.get("ts") and s.get("used_percent") is not None and lo <= float(s["ts"])
           <= now.timestamp() and (s.get("period_end") is None or
                                   parse_time(s.get("period_end")) == reset)]
    if len(pts) < 2:
        return _na("needs at least 2 saved readings from the last %d h of this week"
                   % RECENT_WINDOW_H)
    pts.sort()
    span_h = (pts[-1][0] - pts[0][0]) / 3600.0
    if span_h < MIN_RECENT_SPAN_H:
        return _na("saved readings span less than 30 minutes")
    rate = max(0.0, (pts[-1][1] - pts[0][1]) / span_h)
    r = _project(used, rate, now, reset)
    r["window_hours"] = span_h
    r["kind"] = "ESTIMATE (linear, last %.1f h of saved readings)" % span_h
    return r


def burn_rate(events: List[Dict[str, Any]], now: datetime, tz=None) -> Dict[str, Any]:
    """Tokens in the last hour and average per active hour today - both real counts."""
    nts = now.timestamp()
    last_h = sum(int(e.get("input_tokens") or 0) + int(e.get("output_tokens") or 0)
                 for e in events if nts - 3600 <= e["ts"] <= nts)
    local = now.astimezone(tz) if tz else now.astimezone()
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    today = [e for e in events if midnight <= e["ts"] <= nts]
    hours = {int((e["ts"] - midnight) // 3600) for e in today}
    tok = sum(int(e.get("input_tokens") or 0) + int(e.get("output_tokens") or 0)
              for e in today)
    return {"last_hour": last_h, "today": tok, "active_hours_today": len(hours),
            "per_active_hour_today": (tok / len(hours)) if hours else None}
