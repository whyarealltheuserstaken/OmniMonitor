"""Pure stats functions (no I/O) - easy to test.

Every metric is either a real number computed from recorded data, an explicitly flagged
estimate, or `None` with a human-readable reason. Nothing is invented.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

NA_NO_PROVIDER = "Token stats are off (token_source = none)."
NA_NO_DATA = ("No token data recorded yet. Token counts come only from local CLI logs on "
              "this computer (Grok CLI, plus Claude Code / Codex if you enabled them).")
NA_LIMIT = ("xAI does not expose a weekly token limit - only a usage percentage. "
            "See README > Token stats.")


def event_total(e: Dict[str, Any]) -> int:
    # inputTokens is the full prompt (cache reads included); reasoning is part of output.
    return int(e.get("input_tokens") or 0) + int(e.get("output_tokens") or 0)


def daily_totals(events: Iterable[Dict[str, Any]], tz=None) -> Dict[date, int]:
    out: Dict[date, int] = defaultdict(int)
    for e in events:
        d = datetime.fromtimestamp(e["ts"], tz=tz).date() if tz else \
            datetime.fromtimestamp(e["ts"]).date()
        out[d] += event_total(e)
    return dict(out)


def active_days(daily: Dict[date, int]) -> Set[date]:
    return {d for d, v in daily.items() if v > 0}


def longest_streak(days: Set[date]) -> int:
    best = 0
    for d in days:
        if d - timedelta(days=1) in days:
            continue  # not a run start
        n = 1
        while d + timedelta(days=n) in days:
            n += 1
        best = max(best, n)
    return best


def current_streak(days: Set[date], today: date) -> int:
    """Consecutive active days ending today - or yesterday, so the streak isn't shown
    as broken just because you haven't used Grok yet today."""
    start = today if today in days else today - timedelta(days=1)
    n = 0
    while start - timedelta(days=n) in days:
        n += 1
    return n


def peak_day(daily: Dict[date, int]) -> Optional[Tuple[date, int]]:
    if not daily:
        return None
    d = max(daily, key=lambda k: (daily[k], k))
    return (d, daily[d]) if daily[d] > 0 else None


def tokens_between(events: Iterable[Dict[str, Any]], start_ts: float, end_ts: float) -> int:
    return sum(event_total(e) for e in events if start_ts <= e["ts"] < end_ts)


def estimate_weekly_limit(week_tokens: int, used_percent: Optional[float],
                          min_used_percent: float = 5.0) -> Tuple[Optional[int], str]:
    """≈Y = tokens_seen / (used% / 100). Flagged estimate; None + reason if not derivable."""
    if used_percent is None:
        return None, "no usage percentage from billing"
    if used_percent < max(min_used_percent, 0.01):
        return None, ("needs at least %.0f%% used this week to estimate (now %.1f%%)"
                      % (min_used_percent, used_percent))
    if used_percent > 100:
        return None, "over the weekly pool - ratio not meaningful"
    if week_tokens <= 0:
        return None, "no local token data this week"
    return int(round(week_tokens / (used_percent / 100.0))), ""


def heatmap(daily: Dict[date, int], today: date, weeks: int = 53) -> List[Dict[str, Any]]:
    """Calendar cells from the Sunday `weeks` weeks ago through today (GitHub style)."""
    weeks = max(4, min(int(weeks or 53), 106))
    start = today - timedelta(days=(today.weekday() + 1) % 7) - timedelta(weeks=weeks - 1)
    cells = []
    d = start
    while d <= today:
        cells.append({"date": d.isoformat(), "tokens": int(daily.get(d, 0))})
        d += timedelta(days=1)
    return cells


def _na(reason: str) -> Dict[str, Any]:
    return {"value": None, "reason": reason}


def is_grok(e: Dict[str, Any]) -> bool:
    return (e.get("source") or "grok_sessions") == "grok_sessions"


def group(events: Iterable[Dict[str, Any]], key: str, prices: Dict[str, Any],
          limit: int = 12) -> List[Dict[str, Any]]:
    """Per-tool / model / project rows with real counts and labeled costs."""
    from omnimonitor.adapters import label as tool_label
    from omnimonitor.pricing import estimate_event_cost

    acc: Dict[str, Dict[str, Any]] = {}
    for e in events:
        k = e.get(key) or ("grok_sessions" if key == "source" else "")
        a = acc.setdefault(k, {"tokens": 0, "events": 0, "input": 0, "output": 0,
                               "cache_read": 0, "recorded_usd": None, "estimated_usd": None,
                               "unpriced_tokens": 0, "sessions": set()})
        t = event_total(e)
        a["tokens"] += t
        a["events"] += 1
        a["input"] += int(e.get("input_tokens") or 0)
        a["output"] += int(e.get("output_tokens") or 0)
        a["cache_read"] += int(e.get("cache_read_tokens") or 0)
        a["sessions"].add(e.get("session_id"))
        if e.get("cost_usd") is not None:
            a["recorded_usd"] = (a["recorded_usd"] or 0.0) + float(e["cost_usd"])
        c = estimate_event_cost(e, prices)
        if c is None:
            a["unpriced_tokens"] += t
        else:
            a["estimated_usd"] = (a["estimated_usd"] or 0.0) + c
    total = sum(a["tokens"] for a in acc.values()) or 1
    rows = []
    for k, a in sorted(acc.items(), key=lambda kv: -kv[1]["tokens"])[:limit]:
        name = tool_label(k) if key == "source" else (k or "(unknown)")
        rows.append({"key": k, "name": name, "tokens": a["tokens"], "share": a["tokens"] / total,
                     "events": a["events"], "sessions": len(a["sessions"]),
                     "input": a["input"], "output": a["output"],
                     "cache_hit": (a["cache_read"] / a["input"]) if a["input"] else None,
                     "recorded_usd": a["recorded_usd"], "estimated_usd": a["estimated_usd"],
                     "unpriced_tokens": a["unpriced_tokens"]})
    return rows


def ranges(now: datetime, week: Optional[Tuple[float, float, str]], tz=None
           ) -> List[Tuple[str, str, float, float]]:
    """(id, label, start_ts, end_ts)."""
    local = now.astimezone(tz) if tz else now.astimezone()
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    end = now.timestamp() + 1
    out = [("today", "Today", midnight, end)]
    if week:
        out.append(("week", "This billing week", week[0], week[1]))
    out += [("7d", "Last 7 days", end - 7 * 86400, end),
            ("30d", "Last 30 days", end - 30 * 86400, end),
            ("all", "All time", 0.0, end)]
    return out


def compute(events: List[Dict[str, Any]], turns: List[Dict[str, Any]],
            billing: Optional[Dict[str, Any]], cfg: Dict[str, Any], now: datetime,
            provider: str, provider_note: str = "", data_since: Optional[float] = None,
            tz=None, snapshots: Optional[List[Dict[str, Any]]] = None,
            tools: Optional[List[str]] = None) -> Dict[str, Any]:
    """Build the JSON the stats page / tray menu render."""
    now_local = now.astimezone(tz) if tz else now.astimezone()
    today = now_local.date()
    is_demo = provider == "demo"
    out: Dict[str, Any] = {"provider": provider, "provider_note": provider_note,
                           "is_demo": is_demo, "generated_at": now.isoformat(),
                           "billing": billing}
    if provider == "none":
        reason = NA_NO_PROVIDER
    elif not events:
        reason = provider_note or NA_NO_DATA
    else:
        reason = ""
    daily = daily_totals(events, tz=tz)
    days = active_days(daily)
    out["heatmap"] = heatmap(daily, today, cfg.get("heatmap_weeks", 53))
    out["data_since"] = datetime.fromtimestamp(min(e["ts"] for e in events)).date().isoformat() \
        if events else None
    out["tracking_since"] = datetime.fromtimestamp(data_since).date().isoformat() \
        if data_since else None

    if reason:
        for k in ("lifetime_tokens", "peak_day", "longest_task", "longest_streak",
                  "current_streak", "week_tokens"):
            out[k] = _na(reason)
    else:
        out["lifetime_tokens"] = {"value": sum(daily.values())}
        pk = peak_day(daily)
        out["peak_day"] = {"value": pk[1], "date": pk[0].isoformat()} if pk else _na(
            "no token usage recorded")
        out["longest_streak"] = {"value": longest_streak(days), "unit": "days"}
        out["current_streak"] = {"value": current_streak(days, today), "unit": "days"}
        durs = [t for t in turns if t.get("duration_s")]
        if durs:
            best = max(durs, key=lambda t: t["duration_s"])
            out["longest_task"] = {"value": best["duration_s"], "unit": "seconds",
                                   "source": best.get("duration_source") or ""}
        else:
            out["longest_task"] = _na("the session logs don't contain usable turn timings")

        # ---- this week
        start_ts = end_ts = None
        label = ""
        if billing and billing.get("period_start") and billing.get("resets_at"):
            from omnimonitor.timeutil import parse_time
            ps, pe = parse_time(billing["period_start"]), parse_time(billing["resets_at"])
            if ps and pe and ps < pe:
                start_ts, end_ts, label = ps.timestamp(), pe.timestamp(), "current billing week"
        if start_ts is None:
            end_ts = now.timestamp() + 1
            start_ts = end_ts - 7 * 86400
            label = "last 7 days (billing week unknown)"
        wk = tokens_between(events, start_ts, end_ts)
        grok_wk = tokens_between([e for e in events if is_grok(e)], start_ts, end_ts)
        out["week_tokens"] = {"value": wk, "window": label, "grok_value": grok_wk}

    # ---- weekly limit (Y)
    limit: Dict[str, Any] = {"value": None, "reason": NA_LIMIT, "estimate": None}
    if billing:
        for k, v in (billing.get("token_fields") or {}).items():
            if "limit" in k.lower() or "cap" in k.lower() or "max" in k.lower():
                limit = {"value": int(v), "source": "billing field %s" % k, "estimate": None}
                break
    if limit["value"] is None and cfg.get("estimate_weekly_limit", True) and not reason \
            and billing and billing.get("used_percent") is not None:
        # The weekly pool is shared by Grok chat + Grok Build. Local logs only see the CLI,
        # so compare against the GrokBuild share when billing reports one.
        pct, basis = billing.get("raw_used_percent", billing.get("used_percent")), "overall"
        for p in billing.get("products") or []:
            if str(p.get("product")).lower() == "grokbuild" and p.get("used_percent"):
                pct, basis = p["used_percent"], "Grok Build share"
                break
        # Only Grok CLI tokens count against the Grok pool - never Claude Code / Codex.
        est, why = estimate_weekly_limit(out["week_tokens"]["grok_value"], pct,
                                         float(cfg.get("estimate_min_used_percent", 5.0)))
        if est:
            limit["estimate"] = {
                "value": est, "basis": basis, "used_percent": pct,
                "caveat": ("ESTIMATE: local Grok CLI tokens this week ÷ %s %% used. Credits are "
                           "weighted by model and caching, and usage outside this computer's "
                           "Grok CLI isn't in the logs, so this can be far off." % basis)}
        else:
            limit["estimate_unavailable"] = why
    out["weekly_limit"] = limit

    # ---- insights (only things we actually have)
    insights: List[Dict[str, str]] = []
    top_models: List[Dict[str, Any]] = []
    if events and not reason:
        by_model: Dict[str, int] = defaultdict(int)
        for e in events:
            by_model[e.get("model") or "unknown"] += event_total(e)
        total = sum(by_model.values()) or 1
        top_models = [{"name": m, "tokens": v, "share": v / total}
                      for m, v in sorted(by_model.items(), key=lambda kv: -kv[1])[:5]]
        sessions = {e.get("session_id") for e in events}
        insights.append({"label": "Sessions recorded", "value": "%d" % len(sessions)})
        insights.append({"label": "Turns recorded", "value": "%d" % len(turns or [])})
        inp = sum(int(e.get("input_tokens") or 0) for e in events)
        cr = sum(int(e.get("cache_read_tokens") or 0) for e in events)
        if inp:
            insights.append({"label": "Prompt tokens served from cache",
                             "value": "%.0f%%" % (100.0 * cr / inp)})
        outp = sum(int(e.get("output_tokens") or 0) for e in events)
        rs = sum(int(e.get("reasoning_tokens") or 0) for e in events)
        if outp and rs:
            insights.append({"label": "Reasoning share of output",
                             "value": "%.0f%%" % (100.0 * rs / outp)})
        costs = [e["cost_usd"] for e in events if e.get("cost_usd") is not None]
        if costs:
            insights.append({"label": "Cost recorded in logs (API-equivalent)",
                             "value": "$%.2f" % sum(costs)})
        if top_models:
            insights.append({"label": "Most used model",
                             "value": "%s · %.0f%%" % (top_models[0]["name"],
                                                       100 * top_models[0]["share"])})
    out["top_models"] = top_models
    out["top_plugins"] = []  # not present in any known local data -> section hidden
    out["insights"] = insights
    _extras(out, events, billing, cfg, now, tz, snapshots, tools, bool(reason), is_demo)
    return out


def _extras(out, events, billing, cfg, now, tz, snapshots, tools, no_data, is_demo) -> None:
    """0.2.0 additions: breakdowns, cost, forecast, burn rate, personalization."""
    from omnimonitor import forecast as fc
    from omnimonitor.adapters import label as tool_label
    from omnimonitor.pricing import DEMO_PRICES, clean_prices, summarize_cost

    prices = clean_prices(cfg.get("prices"))
    prices_demo = False
    if is_demo and not prices:
        prices, prices_demo = dict(DEMO_PRICES), True
    out["tools"] = [{"key": t, "name": tool_label(t)} for t in (tools or [])]
    week = None
    if billing and billing.get("period_start") and billing.get("resets_at"):
        from omnimonitor.timeutil import parse_time
        ps, pe = parse_time(billing["period_start"]), parse_time(billing["resets_at"])
        if ps and pe and ps < pe:
            week = (ps.timestamp(), pe.timestamp(), "This billing week")
    br: Dict[str, Any] = {}
    cost: Dict[str, Any] = {}
    if events and not no_data:
        for rid, rlabel, a, b in ranges(now, week, tz):
            evs = [e for e in events if a <= e["ts"] < b]
            br[rid] = {"label": rlabel, "tokens": sum(event_total(e) for e in evs),
                       "tools": group(evs, "source", prices),
                       "models": group(evs, "model", prices),
                       "projects": group(evs, "project", prices)}
            cost[rid] = summarize_cost(evs, prices)
            cost[rid]["label"] = rlabel
    out["breakdowns"] = br
    out["cost"] = {"ranges": cost, "priced_models": len(prices), "demo_prices": prices_demo,
                   "note": ("DEMO prices" if prices_demo else
                            "Estimates use YOUR price table (config \"prices\"). "
                            "Recorded = cost the CLI wrote in its own logs.")
                   if prices else "No price table set - add one in Settings to estimate cost."}
    out["forecast"] = fc.pace(billing, now, snapshots)
    out["burn"] = fc.burn_rate(events, now, tz) if events and not no_data else None
    out["personal"] = {"display_name": str(cfg.get("display_name") or "")[:40],
                       "theme": cfg.get("theme") if cfg.get("theme") in (
                           "dark", "light", "auto") else "dark",
                       "accent_color": cfg.get("accent_color") or "#2a8bd8",
                       "hidden_cards": [c for c in (cfg.get("hidden_cards") or [])
                                        if isinstance(c, str)]}
