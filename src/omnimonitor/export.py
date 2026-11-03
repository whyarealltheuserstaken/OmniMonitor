"""CSV / JSON export of recorded token data. Only real rows (or DEMO rows, labeled)."""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List

from omnimonitor import __version__
from omnimonitor.adapters import label

FORMULA_CHARS = ("=", "+", "-", "@", "\t", "\r")


def _cell(v: Any) -> Any:
    """Neutralise spreadsheet formula injection in text cells (model/project names)."""
    if isinstance(v, str) and v.startswith(FORMULA_CHARS):
        return "'" + v
    return v


def _day(ts: float, tz=None) -> str:
    return (datetime.fromtimestamp(ts, tz=tz) if tz else datetime.fromtimestamp(ts)
            ).date().isoformat()


def rows(events: List[Dict[str, Any]], kind: str = "daily", tz=None) -> List[Dict[str, Any]]:
    """kind: daily (per day) | daily-models (per day+tool+model) | breakdown (per
    tool+model+project) | events (one row per recorded API call)."""
    if kind == "events":
        return [{"time": datetime.fromtimestamp(e["ts"], tz=tz).isoformat() if tz else
                 datetime.fromtimestamp(e["ts"]).isoformat(),
                 "tool": label(e.get("source")), "model": e.get("model") or "unknown",
                 "project": e.get("project") or "", "session_id": e.get("session_id") or "",
                 "input_tokens": int(e.get("input_tokens") or 0),
                 "cache_read_tokens": int(e.get("cache_read_tokens") or 0),
                 "cache_creation_tokens": int(e.get("cache_creation_tokens") or 0),
                 "output_tokens": int(e.get("output_tokens") or 0),
                 "reasoning_tokens": int(e.get("reasoning_tokens") or 0),
                 "recorded_cost_usd": e.get("cost_usd")}
                for e in events]
    if kind == "daily":
        def key(e):
            return (_day(e["ts"], tz),)
        names = ("date",)
    elif kind == "daily-models":
        def key(e):
            return (_day(e["ts"], tz), label(e.get("source")), e.get("model") or "unknown")
        names = ("date", "tool", "model")
    elif kind == "breakdown":
        def key(e):
            return (label(e.get("source")), e.get("model") or "unknown",
                    e.get("project") or "")
        names = ("tool", "model", "project")
    else:
        raise ValueError("unknown export kind: %s" % kind)
    agg: Dict[tuple, Dict[str, Any]] = defaultdict(lambda: {
        "events": 0, "input_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0,
        "output_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0,
        "recorded_cost_usd": None})
    for e in events:
        a = agg[key(e)]
        a["events"] += 1
        for f in ("input_tokens", "cache_read_tokens", "cache_creation_tokens",
                  "output_tokens", "reasoning_tokens"):
            a[f] += int(e.get(f) or 0)
        a["total_tokens"] += int(e.get("input_tokens") or 0) + int(e.get("output_tokens") or 0)
        if e.get("cost_usd") is not None:
            a["recorded_cost_usd"] = (a["recorded_cost_usd"] or 0.0) + float(e["cost_usd"])
    out = []
    for k in sorted(agg):
        r = dict(zip(names, k))
        r.update(agg[k])
        out.append(r)
    return out


def to_csv(rs: List[Dict[str, Any]]) -> str:
    buf = io.StringIO()
    if not rs:
        return ""
    w = csv.DictWriter(buf, fieldnames=list(rs[0].keys()), lineterminator="\n")
    w.writeheader()
    for r in rs:
        w.writerow({k: ("" if v is None else _cell(v)) for k, v in r.items()})
    return buf.getvalue()


def to_json(events: List[Dict[str, Any]], is_demo: bool, tz=None) -> str:
    doc = {"generator": "OmniMonitor %s" % __version__,
           "exported_at": datetime.now().astimezone().isoformat(),
           "is_demo": is_demo,
           "note": ("DEMO - fake data" if is_demo else
                    "Token counts read from local CLI logs. input_tokens includes cache "
                    "reads/writes; output_tokens includes reasoning."),
           "daily": rows(events, "daily", tz),
           "daily_models": rows(events, "daily-models", tz),
           "breakdown": rows(events, "breakdown", tz)}
    return json.dumps(doc, indent=2)
