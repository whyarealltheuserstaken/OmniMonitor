"""Unified multi-provider view: per-provider cards, all-tools totals, limits + pace,
budgets and the primary provider for the tray. Pure (no I/O) - easy to test.

Numbers come only from recorded local events, readings returned by official APIs, or the
Grok billing endpoint. Anything else is ``None`` with a reason.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from omnimonitor import catalog
from omnimonitor.pricing import DEMO_PRICES, clean_prices, estimate_event_cost

DEMO_BUDGETS = {"daily": 1.0, "weekly": 5.0, "monthly": 20.0}  # DEMO ONLY

BUDGET_PERIODS = ("daily", "weekly", "monthly")


def _total(e: Dict[str, Any]) -> int:
    return int(e.get("input_tokens") or 0) + int(e.get("output_tokens") or 0)


def _local_starts(now: datetime, tz=None) -> Dict[str, float]:
    loc = now.astimezone(tz) if tz else now.astimezone()
    mid = loc.replace(hour=0, minute=0, second=0, microsecond=0)
    return {"daily": mid.timestamp(),
            "weekly": (mid - timedelta(days=mid.weekday())).timestamp(),
            "monthly": mid.replace(day=1).timestamp()}


def _local_resets(now: datetime, tz=None) -> Dict[str, float]:
    loc = now.astimezone(tz) if tz else now.astimezone()
    mid = loc.replace(hour=0, minute=0, second=0, microsecond=0)
    first = mid.replace(day=1)
    return {"daily": (mid + timedelta(days=1)).timestamp(),
            "weekly": (mid + timedelta(days=7 - mid.weekday())).timestamp(),
            "monthly": (first + timedelta(days=32)).replace(day=1).timestamp()}


def spend(events: List[Dict[str, Any]], prices: Dict[str, Any]) -> Dict[str, Any]:
    """Recorded cost where the tool wrote one, else your price-table estimate."""
    usd, priced, unpriced, recorded, estimated = None, 0, 0, 0.0, 0.0
    for e in events:
        c = e.get("cost_usd")
        if c is not None:
            usd, recorded = (usd or 0.0) + float(c), recorded + float(c)
            priced += _total(e)
            continue
        est = estimate_event_cost(e, prices)
        if est is None:
            unpriced += _total(e)
        else:
            usd, estimated = (usd or 0.0) + est, estimated + est
            priced += _total(e)
    return {"usd": usd, "recorded_usd": recorded if recorded else None,
            "estimated_usd": estimated if estimated else None,
            "priced_tokens": priced, "unpriced_tokens": unpriced}


def pace(used_percent: Optional[float], start: Optional[float], resets_at: Optional[float],
         now: float) -> Optional[Dict[str, Any]]:
    """Used % vs the share of the window that has elapsed. ESTIMATE, flagged as such."""
    if used_percent is None or not start or not resets_at or resets_at <= start:
        return None
    elapsed = max(0.0, min(1.0, (now - start) / (resets_at - start)))
    out = {"elapsed_percent": round(elapsed * 100, 1), "used_percent": round(used_percent, 1),
           "estimate": True}
    if elapsed >= 0.02 and used_percent > 0:
        proj = used_percent / elapsed
        out["projected_percent"] = round(proj, 1)
        out["status"] = "over" if proj > 100 else ("high" if proj > 85 else "ok")
        if proj > 100:
            out["runs_out_at"] = start + (resets_at - start) * elapsed * 100 / used_percent
    return out


def codex_windows(snap: Optional[Dict[str, Any]], now: float) -> List[Dict[str, Any]]:
    out = []
    names = {"primary": "short window", "secondary": "weekly window"}
    for key in ("primary", "secondary"):
        w = ((snap or {}).get("windows") or {}).get(key)
        if not w:
            continue
        reset, mins = w.get("resets_at"), w.get("window_minutes")
        if mins:
            names[key] = ("%dh window" % (mins // 60)) if mins < 1440 else (
                "weekly window" if 9000 < mins < 11000 else "%dd window" % (mins // 1440))
        start = reset - mins * 60 if reset and mins else None
        stale = bool(reset and reset < now)
        out.append({"name": names[key], "key": key, "used_percent": w["used_percent"],
                    "resets_at": reset, "window_minutes": mins, "stale": stale,
                    "pace": None if stale else pace(w["used_percent"], start, reset, now)})
    return out


def _codex_primary(snap, now) -> Optional[Tuple[float, Optional[float], str]]:
    wins = [w for w in codex_windows(snap, now) if not w["stale"]]
    if not wins:
        return None
    w = next((x for x in wins if x["key"] == "secondary"), wins[0])
    return w["used_percent"], w["resets_at"], "Codex " + w["name"]


def budgets(events: List[Dict[str, Any]], cfg: Dict[str, Any], now: datetime, tz=None
            ) -> Dict[str, Any]:
    limits = cfg.get("budgets") if isinstance(cfg.get("budgets"), dict) else {}
    prices = clean_prices(cfg.get("prices"))
    starts, resets = _local_starts(now, tz), _local_resets(now, tz)
    alert = float(cfg.get("budget_alert_percent") or 80)
    out: Dict[str, Any] = {"alert_percent": alert, "periods": {},
                           "basis": "recorded cost from local logs, else estimates from your "
                                    "price table; API account spend is not added"}
    for p in BUDGET_PERIODS:
        lim = limits.get(p)
        lim = float(lim) if isinstance(lim, (int, float)) and not isinstance(lim, bool) else 0
        evs = [e for e in events if e["ts"] >= starts[p]]
        sp = spend(evs, prices)
        row: Dict[str, Any] = {"limit_usd": lim or None, "spent_usd": sp["usd"],
                               "unpriced_tokens": sp["unpriced_tokens"],
                               "starts_at": starts[p], "resets_at": resets[p],
                               "percent": None, "note": ""}
        if sp["usd"] is None:
            row["note"] = ("not available: no recorded costs and no matching prices"
                           if evs else "no usage this period")
        elif sp["unpriced_tokens"]:
            row["note"] = "%s tokens have no price, so this is a lower bound" % format(
                sp["unpriced_tokens"], ",")
        if lim and sp["usd"] is not None:
            row["percent"] = round(sp["usd"] / lim * 100, 1)
            row["state"] = "over" if row["percent"] >= 100 else (
                "warn" if row["percent"] >= alert else "ok")
        out["periods"][p] = row
    return out


def _range_summary(evs: List[Dict[str, Any]], prices) -> Dict[str, Any]:
    sp = spend(evs, prices)
    return {"tokens": sum(_total(e) for e in evs), "events": len(evs), "usd": sp["usd"],
            "recorded_usd": sp["recorded_usd"], "estimated_usd": sp["estimated_usd"],
            "unpriced_tokens": sp["unpriced_tokens"]}


def build(events: List[Dict[str, Any]], cfg: Dict[str, Any], now: datetime, *,
          tools: List[str], readings: Dict[str, Dict[str, Any]],
          codex_limits: Optional[Dict[str, Any]] = None,
          billing: Optional[Dict[str, Any]] = None, is_demo: bool = False,
          tz=None) -> Dict[str, Any]:
    nowts = now.timestamp()
    demo_prices = False
    if is_demo:  # DEMO ONLY: fake prices + budgets so the demo shows every card
        cfg = dict(cfg)
        if not clean_prices(cfg.get("prices")):
            cfg["prices"], demo_prices = dict(DEMO_PRICES), True
        if not any((cfg.get("budgets") or {}).values()):
            cfg["budgets"] = dict(DEMO_BUDGETS)
    prices = clean_prices(cfg.get("prices"))
    loc = now.astimezone(tz) if tz else now.astimezone()
    mid = loc.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    rng = {"today": mid, "7d": nowts - 7 * 86400, "30d": nowts - 30 * 86400}
    by_src: Dict[str, List[Dict[str, Any]]] = {}
    for e in events:
        by_src.setdefault(e.get("source") or "", []).append(e)

    cards: List[Dict[str, Any]] = []
    if billing is not None:
        used = billing.get("used_percent")
        reset = _ts(billing.get("resets_at"))
        start = _ts(billing.get("period_start"))
        cards.append(_card("grok", True, demo=bool(billing.get("is_demo")), limits=[{
            "name": "weekly pool", "used_percent": used, "resets_at": reset,
            "stale": False, "pace": pace(used, start, reset, nowts)}] if used is not None
            else []))
    for pid in tools:
        evs = by_src.get(pid, [])
        c = _card(pid, True, demo=is_demo)
        c["ranges"] = {k: _range_summary([e for e in evs if e["ts"] >= a], prices)
                       for k, a in rng.items()}
        models: Dict[str, int] = {}
        for e in evs:
            if e["ts"] >= rng["30d"]:
                m = str(e.get("model") or "unknown")
                models[m] = models.get(m, 0) + _total(e)
        c["top_models"] = [{"model": m, "tokens": t} for m, t in
                           sorted(models.items(), key=lambda x: -x[1])[:5]]
        c["last_seen"] = max((e["ts"] for e in evs), default=None)
        if pid == "codex" and codex_limits:
            c["limits"] = codex_windows(codex_limits, nowts)
            c["plan"] = codex_limits.get("plan_type")
            c["limits_observed_at"] = codex_limits.get("observed_at")
        cards.append(c)
    for pid, r in readings.items():
        c = _card(pid, True, demo=bool(r.get("demo")))
        c.update({"metrics": r.get("metrics") or [], "error": r.get("error") or "",
                  "note": r.get("note") or "", "fetched_at": r.get("fetched_at"),
                  "period": r.get("period") or "", "ok": bool(r.get("ok"))})
        if r.get("limit_percent") is not None:
            c["limits"] = [{"name": r.get("period") or "limit",
                            "used_percent": r["limit_percent"], "resets_at": r.get("resets_at"),
                            "stale": False,
                            "pace": pace(r["limit_percent"], r.get("period_start"),
                                         r.get("resets_at"), nowts)}]
        cards.append(c)

    all_tools = {k: _range_summary([e for e in events if e["ts"] >= a], prices)
                 for k, a in rng.items()}
    today_by_tool = sorted(((c["id"], c["ranges"]["today"]["tokens"]) for c in cards
                            if c.get("ranges")), key=lambda x: -x[1])
    out = {"cards": cards, "all_tools": all_tools,
           "today": {"tokens": all_tools["today"]["tokens"], "usd": all_tools["today"]["usd"],
                     "top_tool": catalog.name(today_by_tool[0][0])
                     if today_by_tool and today_by_tool[0][1] else None},
           "budgets": budgets(events, cfg, now, tz),
           "catalog_counts": catalog.counts(), "is_demo": is_demo,
           "demo_prices": demo_prices}
    out["primary"] = primary(cfg, billing, readings, codex_limits, out["budgets"], nowts)
    out["thresholds"] = {c["id"]: provider_thresholds(cfg, c["id"]) for c in cards}
    return out


def _ts(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str) and v:
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _card(pid: str, enabled: bool, demo: bool = False, limits=None) -> Dict[str, Any]:
    info = catalog.get(pid)
    return {"id": pid, "name": catalog.name(pid), "family": info.family if info else "",
            "kind": info.kind if info else "", "status": info.status if info else "",
            "confidence": info.confidence if info else "", "reset": info.reset if info else "",
            "docs": list(info.docs) if info else [], "enabled": enabled, "demo": demo,
            "limits": limits or [], "ranges": None, "metrics": []}


def primary(cfg: Dict[str, Any], billing, readings, codex_snap, budget_view, now: float
            ) -> Dict[str, Any]:
    """The provider whose % drives the tray icon. ``available`` False -> the tray falls back
    to the Grok pool and says so; it never invents a value."""
    pid = cfg.get("primary_provider") or "grok"
    if pid not in catalog.PRIMARY_CHOICES:
        pid = "grok"
    out: Dict[str, Any] = {"id": pid, "name": "Budget" if pid == "budget" else catalog.name(pid),
                           "available": False, "used_percent": None, "resets_at": None,
                           "label": "", "reason": ""}
    used = reset = None
    label = out["name"]
    if pid == "grok":
        if billing and billing.get("used_percent") is not None:
            used, reset = billing["used_percent"], _ts(billing.get("resets_at"))
            label = "Grok weekly"
    elif pid == "codex":
        got = _codex_primary(codex_snap, now)
        if got:
            used, reset, label = got
        else:
            out["reason"] = "no current Codex plan-limit reading in the logs yet"
    elif pid == "budget":
        rows = [(p, r) for p, r in (budget_view.get("periods") or {}).items()
                if r.get("percent") is not None]
        if rows:
            p, r = max(rows, key=lambda x: x[1]["percent"])
            used, reset, label = min(100.0, r["percent"]), r["resets_at"], "%s budget" % p
        else:
            out["reason"] = "no budget set, or spend not available"
    else:
        r = readings.get(pid) or {}
        if r.get("limit_percent") is not None:
            used, reset, label = r["limit_percent"], r.get("resets_at"), out["name"]
        else:
            out["reason"] = r.get("error") or ("enable %s and set its key" % out["name"]
                                               if not r else "this account has no % limit")
    if used is not None:
        out.update(available=True, used_percent=float(used),
                   remaining_percent=max(0.0, 100.0 - float(used)), resets_at=reset,
                   label=label)
    return out


def provider_thresholds(cfg: Dict[str, Any], pid: str) -> List[float]:
    raw = (cfg.get("provider_thresholds") or {}).get(pid)
    if raw is None:
        raw = cfg.get("notify_thresholds") or []
    out = []
    for v in raw if isinstance(raw, list) else []:
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v <= 100:
            out.append(float(v))
    return sorted(set(out))


def tooltip(ov: Dict[str, Any], billing_line: str = "") -> str:
    """Compact 'today' tooltip (OS tooltips are short: keep under ~120 chars)."""
    parts = []
    p = ov.get("primary") or {}
    if p.get("available"):
        parts.append("%s %.0f%% used" % (p["label"], p["used_percent"]))
    elif billing_line:
        parts.append(billing_line)
    t = ov.get("today") or {}
    if t.get("tokens"):
        parts.append("today %s tok" % _short(t["tokens"]))
        if t.get("usd") is not None:
            parts.append("$%.2f" % t["usd"])
    head = "DEMO · OmniMonitor" if ov.get("is_demo") else "OmniMonitor"
    return (head + " - " + " · ".join(parts))[:127] if parts else head


def _short(n: int) -> str:
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if n >= div:
            return ("%.1f" % (n / div)).rstrip("0").rstrip(".") + suf
    return str(int(n))


def tray_value(ov: Dict[str, Any], billing: Optional[Dict[str, Any]]
               ) -> Tuple[Optional[float], str]:
    """(remaining % for the icon or None, label). Primary provider first, else the Grok
    pool (labelled as a fallback), else None (the icon shows '?')."""
    p = (ov or {}).get("primary") or {}
    if p.get("available"):
        return p["remaining_percent"], p["label"]
    if billing and billing.get("remaining_percent") is not None:
        lab = "Grok weekly"
        if p.get("id") not in (None, "grok"):
            lab += " (primary %s not available)" % p.get("name")
        return float(billing["remaining_percent"]), lab
    return None, p.get("label") or "no data"


def menu_lines(ov: Dict[str, Any], limit: int = 8) -> List[str]:
    """Short per-provider lines for the tray menu."""
    out = []
    for c in (ov or {}).get("cards") or []:
        if c["id"] == "grok":
            continue
        bits = []
        for w in (c.get("limits") or [])[:2]:
            if w.get("used_percent") is not None and not w.get("stale"):
                bits.append("%.0f%% %s" % (w["used_percent"], w.get("name") or "used"))
        t = (c.get("ranges") or {}).get("today") or {}
        if t.get("tokens"):
            bits.append("today %s tok" % _short(t["tokens"]))
        if not bits:
            m = next((m for m in c.get("metrics") or [] if m.get("kind") in ("balance", "spend")),
                     None)
            if m:
                bits.append("%s %s%s" % (m["label"], _money(m["value"], m.get("unit")), ""))
            elif c.get("error"):
                bits.append("⚠ " + c["error"][:40])
        if bits:
            out.append(("%s%s: %s" % ("DEMO " if c.get("demo") else "", c["name"],
                                      " · ".join(bits)))[:80])
        if len(out) >= limit:
            break
    return out


def _money(v: Any, unit: Optional[str]) -> str:
    if unit == "USD":
        return "$%.2f" % v
    return "%s %s" % (round(v, 2), unit or "")
