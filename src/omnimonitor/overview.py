"""Unified multi-provider view: per-provider cards, all-tools totals, limits + pace,
budgets and the primary provider for the tray. Pure (no I/O) - easy to test.

Numbers come only from recorded local events, readings returned by official APIs, or the
Grok billing endpoint. Anything else is ``None`` with a reason.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from omnimonitor import catalog, suites
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
    if elapsed < 0.02 or used_percent <= 0:
        return None  # nothing to project yet
    proj = used_percent / elapsed
    out = {"elapsed_percent": round(elapsed * 100, 1), "used_percent": round(used_percent, 1),
           "estimate": True, "projected_percent": round(proj, 1),
           "status": "over" if proj > 100 else ("high" if proj > 85 else "ok")}
    if proj > 100:
        out["runs_out_at"] = start + (resets_at - start) * elapsed * 100 / used_percent
    return out


#: Don't warn about running out from a sliver of a window or of usage.
WARN_MIN_ELAPSED, WARN_MIN_USED = 10.0, 25.0


def run_out_warning(window: Dict[str, Any]) -> Optional[float]:
    """When (unix time) this window is estimated to run out before it resets, or None."""
    p = window.get("pace") or {}
    if window.get("stale") or not p.get("runs_out_at") \
            or p["elapsed_percent"] < WARN_MIN_ELAPSED \
            or not WARN_MIN_USED <= p["used_percent"] < 100:
        return None
    return p["runs_out_at"]


def _window_pace(window: Dict[str, Any], now: float) -> Optional[Dict[str, Any]]:
    """Pace for a plan window whose length the provider told us."""
    reset, length = _ts(window.get("resets_at")), window.get("window_seconds")
    if reset is None or reset < now or isinstance(length, bool) \
            or not isinstance(length, (int, float)) or length <= 0:
        return None
    return pace(window.get("used_percent"), reset - length, reset, now)


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
        if r.get("limits"):
            c["limits"] = [{"name": w.get("name") or "limit", "used_percent": w["used_percent"],
                            "resets_at": w.get("resets_at"),
                            "stale": _past(w.get("resets_at"), nowts),
                            "pace": _window_pace(w, nowts)}
                           for w in r["limits"] if w.get("used_percent") is not None]
        elif r.get("limit_percent") is not None:
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
    from omnimonitor import display
    out["suites"] = display.ordered_suites(cfg, suite_view(cards, nowts))
    out["views"] = display.views(out, cfg, nowts)
    out["primary"] = primary(cfg, out["views"]["tray"]["suites"], out["budgets"], nowts)
    out["thresholds"] = {c["id"]: provider_thresholds(cfg, c["id"]) for c in cards}
    return out


def _past(resets_at: Any, now: float) -> bool:
    """True when a window's reset time has already passed (its % is no longer current)."""
    t = _ts(resets_at)
    return t is not None and t < now


def suite_view(cards: List[Dict[str, Any]], now: Optional[float] = None
               ) -> List[Dict[str, Any]]:
    """Group provider cards into suites (SUITES order, only suites that have a card).

    The headline is the non-stale limit with the highest % used across the suite's cards;
    without one it is None with a reason. Nothing is filled in."""
    by_suite: Dict[str, List[Dict[str, Any]]] = {}
    for c in cards:
        by_suite.setdefault(suites.suite_of(c["id"]), []).append(c)
    out = []
    for s in suites.SUITES:
        cs = by_suite.get(s.id)
        if not cs:
            continue
        best = None
        for c in cs:
            for w in c.get("limits") or []:
                if w.get("stale") or w.get("used_percent") is None:
                    continue
                reset = _ts(w.get("resets_at"))
                if now is not None and reset is not None and reset < now:
                    continue  # the window already reset: this % is no longer true
                if best is None or w["used_percent"] > best["used_percent"]:
                    wname = w.get("name") or "limit"
                    best = {"used_percent": float(w["used_percent"]),
                            "resets_at": w.get("resets_at"),
                            "label": c["name"] if c["name"].lower().endswith(wname.lower())
                            else "%s %s" % (c["name"], wname),
                            "source": c["id"]}
        today = [((c.get("ranges") or {}).get("today") or {}) for c in cs]
        usd = [t["usd"] for t in today if t.get("usd") is not None]
        soon = [t for c in cs for w in c.get("limits") or []
                for t in [run_out_warning(w)] if t and (now is None or t > now)]
        out.append({"id": s.id, "name": s.name, "usage_url": s.usage_url,
                    "card_ids": [c["id"] for c in cs], "headline": best,
                    "reason": "" if best else "no limit reading yet",
                    "runs_out_at": min(soon, default=None),
                    "today_tokens": sum(int(t.get("tokens") or 0) for t in today),
                    "today_usd": sum(usd) if usd else None,
                    "demo": any(c.get("demo") for c in cs)})
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


def _tray_name(choice: str) -> str:
    if choice == "tightest":
        return "Tightest limit"
    if choice == "budget":
        return "Budget"
    s = suites.get(choice)
    return s.name if s else choice


def primary(cfg: Dict[str, Any], suite_list: List[Dict[str, Any]],
            budget_view: Dict[str, Any], now: float) -> Dict[str, Any]:
    """What drives the tray icon: ``tray_suite`` (a suite, ``tightest`` or ``budget``).

    A chosen suite without a reading falls back to the tightest suite, labelled so; with
    nothing anywhere ``available`` stays False. No value is ever invented."""
    choice = cfg.get("tray_suite") or "tightest"
    if choice not in suites.TRAY_CHOICES:
        choice = "tightest"
    out: Dict[str, Any] = {"id": choice, "name": _tray_name(choice), "available": False,
                           "used_percent": None, "resets_at": None, "label": "",
                           "reason": "", "detail": ""}
    used = reset = None
    label = out["name"]
    with_data = [s for s in suite_list if s.get("headline")]
    tightest = max(with_data, key=lambda s: s["headline"]["used_percent"], default=None)
    if choice == "budget":
        rows = [(p, r) for p, r in (budget_view.get("periods") or {}).items()
                if r.get("percent") is not None]
        if rows:
            p, r = max(rows, key=lambda x: x[1]["percent"])
            used, reset, label = min(100.0, r["percent"]), r["resets_at"], "%s budget" % p
        else:
            out["reason"] = "no budget set, or spend not available"
    else:
        pick = tightest if choice == "tightest" else next(
            (s for s in with_data if s["id"] == choice), None)
        if pick is None and choice != "tightest":
            out["reason"] = "no limit reading for %s yet" % out["name"]
            pick = tightest
            if pick is not None:
                label = "%s (%s not available)" % (pick["name"], out["name"])
        elif pick is not None:
            label = pick["name"]
        if pick is not None:
            h = pick["headline"]
            used, reset, out["detail"] = h["used_percent"], h.get("resets_at"), h["label"]
        elif not out["reason"]:
            out["reason"] = "no limit readings yet"
    if used is not None:
        out.update(available=True, used_percent=float(used),
                   remaining_percent=max(0.0, 100.0 - float(used)), resets_at=reset,
                   label=label)
    return out


def provider_thresholds(cfg: Dict[str, Any], pid: str) -> List[float]:
    """Alert levels for a source: its own, else its suite's, else the global list."""
    per = cfg.get("provider_thresholds") or {}
    raw = per.get(pid)
    if raw is None:
        raw = per.get(suites.suite_of(pid))
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
    """(remaining % for the icon or None, label) from ``tray_suite``; None -> the icon
    shows '?'. ``billing`` is accepted for compatibility and not used: no provider is a
    special fallback."""
    p = (ov or {}).get("primary") or {}
    if p.get("available"):
        return p["remaining_percent"], p["label"]
    return None, p.get("label") or "no data"


def _menu_view(ov: Optional[Dict[str, Any]]):
    """(the tray menu's view or None, the numbers it should show)."""
    from omnimonitor.display import FIELDS
    shown = ((ov or {}).get("views") or {}).get("menu")
    return shown, set(shown["fields"] if shown else FIELDS)


def menu_lines(ov: Dict[str, Any], limit: int = 8) -> List[str]:
    """Short per-provider lines for the tray menu."""
    out = []
    shown, want = _menu_view(ov)
    cards = {c["id"]: c for c in (ov or {}).get("cards") or []}
    ids = list(cards) if shown is None else shown["card_ids"]
    for c in (cards[i] for i in ids if i in cards):
        if c["id"] == "grok":
            continue
        bits = []
        for w in (c.get("limits") or [])[:2]:
            if "limit" in want and w.get("used_percent") is not None and not w.get("stale"):
                bits.append("%.0f%% %s" % (w["used_percent"], w.get("name") or "used"))
        t = (c.get("ranges") or {}).get("today") or {}
        if "tokens" in want and t.get("tokens"):
            bits.append("today %s tok" % _short(t["tokens"]))
        if not bits:
            m = next((m for m in c.get("metrics") or [] if m.get("kind") in ("balance", "spend")),
                     None)
            if m and "money" in want:
                bits.append("%s %s" % (m["label"], money(m["value"], m.get("unit"))))
            elif c.get("error"):
                bits.append("⚠ " + c["error"][:40])
        if bits:
            out.append(("%s%s: %s" % ("DEMO " if c.get("demo") else "", c["name"],
                                      " · ".join(bits)))[:80])
        if len(out) >= limit:
            break
    return out


def until(resets_at: Any, now: float) -> str:
    """"in 2d 3h" for a unix or ISO reset time; "" when unknown."""
    ts = _ts(resets_at)
    if not ts:
        return ""
    secs = ts - now
    if secs <= 0:
        return "now"
    d, rem = divmod(int(secs), 86400)
    h, m = rem // 3600, (rem % 3600) // 60
    return ("in %dd %dh" % (d, h)) if d else ("in %dh %dm" % (h, m))


def suite_menu_lines(ov: Dict[str, Any], now: Optional[float] = None,
                     limit: int = 10) -> List[str]:
    """One short tray-menu line per suite: headline % used, reset countdown, today."""
    import time
    now = time.time() if now is None else now
    out = []
    shown, want = _menu_view(ov)
    rows = (ov or {}).get("suites") if shown is None else shown["suites"]
    for s in rows or []:
        bits = []
        h = s.get("headline")
        if h:
            if "limit" in want:
                bits.append("%.0f%% used" % h["used_percent"])
            if "reset" in want and until(h.get("resets_at"), now):
                bits.append("resets " + until(h["resets_at"], now))
        if "limit" in want and s.get("runs_out_at"):
            bits.append("runs out %s (est.)" % until(s["runs_out_at"], now))
        if "tokens" in want and s.get("today_tokens"):
            bits.append("today %s tok" % _short(s["today_tokens"]))
        if "cost" in want and s.get("today_tokens") and s.get("today_usd") is not None:
            bits.append("$%.2f" % s["today_usd"])
        m = s.get("money")
        if "money" in want and m:
            bits.append("%s %s" % (m["label"], money(m["value"], m.get("unit"))))
        if not bits:
            continue
        out.append(("%s%s: %s" % ("DEMO " if s.get("demo") else "", s["name"],
                                  " · ".join(bits)))[:80])
        if len(out) >= limit:
            break
    return out


def money(v: Any, unit: Optional[str]) -> str:
    if unit == "USD":
        return "$%.2f" % v
    return "%s %s" % (round(v, 2), unit or "")
