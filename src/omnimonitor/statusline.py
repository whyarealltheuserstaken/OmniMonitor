"""One-line summary for shell prompts / editor status lines (`omnimonitor statusline`).

Offline and fast: the last saved Grok billing snapshot (no network call), an incremental
scan of local logs, and the active ≈5-hour block. Estimates are marked with ≈; anything
not recorded is left out rather than guessed."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional


def fmt_tokens(n: float) -> str:
    n = float(n or 0)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(n) >= div:
            return ("%.1f" % (n / div)).rstrip("0").rstrip(".") + suf
    return "%d" % n


def fmt_span(seconds: float) -> str:
    s = max(0, int(seconds))
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    if d:
        return "%dd %dh" % (d, h)
    if h:
        return "%dh %dm" % (h, m)
    return "%dm" % m


def _iso_ts(v: Any) -> Optional[float]:
    if not isinstance(v, str) or not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parts(last_billing: Optional[Dict[str, Any]], today: Optional[Dict[str, Any]],
          block: Optional[Dict[str, Any]], now: float) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if last_billing and last_billing.get("used_percent") is not None:
        g = {"used_percent": round(float(last_billing["used_percent"]), 1),
             "as_of": last_billing.get("ts")}
        reset = _iso_ts(last_billing.get("period_end"))
        if reset and reset > now:
            g["resets_in_s"] = int(reset - now)
        out["grok"] = g
    if block:
        out["block"] = {"tokens": block.get("tokens", 0), "cost_usd": block.get("cost_usd"),
                        "remaining_s": int(block.get("remaining_s") or 0), "estimate": True}
    if today:
        out["today"] = {"tokens": today.get("tokens", 0), "usd": today.get("usd")}
    return out


def render(p: Dict[str, Any], now: float) -> str:
    bits = []
    g = p.get("grok")
    if g:
        s = "Grok %.0f%% used" % g["used_percent"]
        if g.get("resets_in_s") is not None:
            s += ", resets in %s" % fmt_span(g["resets_in_s"])
        age = now - float(g.get("as_of") or now)
        if age > 3600:
            s += " (as of %s ago)" % fmt_span(age)
        bits.append(s)
    b = p.get("block")
    if b:
        s = "≈5h block %s tok" % fmt_tokens(b["tokens"])
        if b.get("cost_usd") is not None:
            s += " $%.2f" % b["cost_usd"]
        bits.append(s + ", %s left" % fmt_span(b["remaining_s"]))
    t = p.get("today")
    if t and t.get("tokens"):
        s = "today %s tok" % fmt_tokens(t["tokens"])
        if t.get("usd") is not None:
            s += " $%.2f" % t["usd"]
        bits.append(s)
    return " | ".join(bits) or "OmniMonitor: no data yet"
