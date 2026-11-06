"""One Grok-suite weekly pool, plus an honest CLI / Bot / Cursor breakdown.

The suite total is ``creditUsagePercent`` from
``GET https://cli-chat-proxy.grok.com/v1/billing?format=credits`` (already parsed by
:func:`omnimonitor.billing.parse_billing`). ``productUsage`` entries are shares of that
same pool, not separate limits. A product the payload does not list is **not** treated
as 0%.

Token and activity detail is filled only from local logs this app already reads:

* ``~/.grok/sessions/**/updates.jsonl`` — Grok CLI (``grok_sessions``)
* ``~/.grok/logs/unified.jsonl`` ``shell.turn.inference_done`` — the same CLI
  (experimental ``grok_unified``). If both sources have events, only session logs are
  counted here so the same turns are not added twice.

Grok Bot and Grok-in-Cursor have no token log in those files. Their pool % is shown
only when ``productUsage`` names them. This module does not call Cursor's session
endpoints and does not read ``~/.grokbot`` (no usage figures there).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Product ids are matched on letters and digits only, lowercased.
# GrokBuild is the CLI (Grok Build). Documented in docs/research.md and in public
# descriptions of this billing payload (GrokUsageBar, Pulse). GrokChat / GrokTasks are
# other shares of the same pool — not Grok Bot and not Cursor.
# Grok Bot's own weekly meter is a different call (Cursor sand usage status) and is
# not read here. A productUsage name that is literally GrokBot or Cursor is still shown,
# because that string is the API's, not a guess.
_CLI = {"grokbuild": "Grok Build", "grokcli": "Grok CLI"}
_BOT = {"grokbot": "Grok Bot"}
_CURSOR = {
    "cursor": "Cursor",
    "grokcursor": "Cursor",
    "cursorgrok": "Cursor",
    "grokincursor": "Cursor",
}
_OTHER = {
    "grokchat": "Grok Chat",
    "groktasks": "Grok Tasks",
    "imagine": "Imagine",
    "grokimagine": "Imagine",
    "voice": "Voice",
    "grokvoice": "Voice",
    "api": "API",
    "grokapi": "API",
    "other": "Other",
}

_BUCKETS = (
    ("cli", "Grok CLI",
     "Grok Build — the grok CLI. Pool % only if productUsage names it "
     "(usually GrokBuild)."),
    ("bot", "Grok Bot",
     "Pool % only if productUsage names Grok Bot. Not assumed from Grok Chat."),
    ("cursor", "Cursor (Grok)",
     "Grok-in-Cursor / a productUsage entry named Cursor. Not Cursor's own plan %."),
)

_CLI_TOKENS_MISSING = (
    "No Grok CLI token counts recorded. They come from ~/.grok/sessions/**/updates.jsonl "
    "(turn_completed usage) when Grok token stats are on. The experimental unified log "
    "~/.grok/logs/unified.jsonl (msg shell.turn.inference_done) is the same CLI and is "
    "used only when session logs have no usage events, so the two are not added together."
)
_BOT_TOKENS = (
    "not available: Grok Bot token counts are not in ~/.grok/sessions or "
    "~/.grok/logs/unified.jsonl (those are Grok CLI / Grok Build). ~/.grokbot holds "
    "daemon settings and logs without usage figures; transcript replicas are not a "
    "usage API and are not read. No public xAI API returns Grok Bot token totals."
)
_CURSOR_TOKENS = (
    "not available: no local log this app reads records Grok-in-Cursor token counts. "
    "~/.grok/sessions and unified.jsonl are the Grok CLI. Cursor's plan summary "
    "(opt-in cursor provider, cursor.com/api/usage-summary) is a different pool and "
    "is not a Grok token breakdown."
)
_BOT_PERCENT_SEPARATE = (
    "Third-party captures of this billing payload list shares such as GrokBuild, "
    "GrokChat and GrokTasks, and do not include Grok Bot. Grok Bot's own weekly meter "
    "is POST https://cursor.com/api/dashboard/get-sand-usage-status with a Cursor "
    "session cookie. OmniMonitor does not call that endpoint."
)
_CURSOR_PERCENT_SEPARATE = (
    "xAI usage inside Cursor is not a field of the Grok CLI billing payload unless "
    "productUsage names it. It is not written to ~/.grok session logs. The opt-in "
    "Cursor provider reads cursor.com/api/usage-summary, which is Cursor's plan, not "
    "this weekly pool."
)


def _key(name: str) -> str:
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def _fmt_pct(v: float) -> str:
    r = round(float(v), 1)
    if abs(r - round(r)) < 0.05:
        return "%d%%" % int(round(r))
    return "%.1f%%" % r


def _fmt_tok(n: Optional[float]) -> str:
    if n is None:
        return "n/a"
    n = float(n)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            return "%.1f%s" % (n / div, suf)
    return str(int(round(n)))


def _window(billing: Optional[Dict[str, Any]], now: datetime
            ) -> Tuple[float, float, str]:
    if billing and billing.get("period_start") and billing.get("resets_at"):
        from omnimonitor.timeutil import parse_time
        ps, pe = parse_time(billing["period_start"]), parse_time(billing["resets_at"])
        if ps and pe and ps < pe:
            return ps.timestamp(), pe.timestamp(), "current billing week"
    end = now.timestamp() + 1
    return end - 7 * 86400, end, "last 7 days (billing week unknown)"


def _products(billing: Optional[Dict[str, Any]]) -> Tuple[bool, List[Dict[str, Any]]]:
    """(field present, rows). Missing flag + empty list = field omitted."""
    if not billing:
        return False, []
    rows = []
    for p in billing.get("products") or []:
        if not isinstance(p, dict) or not p.get("product"):
            continue
        try:
            pct = float(p.get("used_percent") or 0)
        except (TypeError, ValueError):
            continue
        rows.append({"product": str(p["product"])[:80], "used_percent": pct})
    if "products_reported" in billing:
        return bool(billing.get("products_reported")), rows
    return bool(rows), rows


def _bucket_of(name: str) -> Optional[str]:
    k = _key(name)
    if k in _CLI:
        return "cli"
    if k in _BOT:
        return "bot"
    if k in _CURSOR:
        return "cursor"
    return None


def _token_detail(events: List[Dict[str, Any]], start: float, end: float,
                  source: str, window: str, note: str) -> Dict[str, Any]:
    evs = [e for e in events if start <= float(e.get("ts") or 0) < end]
    models: Dict[str, int] = {}
    sessions = set()
    inp = out = cache = reason = 0
    for e in evs:
        i = int(e.get("input_tokens") or 0)
        o = int(e.get("output_tokens") or 0)
        inp += i
        out += o
        cache += int(e.get("cache_read_tokens") or 0)
        reason += int(e.get("reasoning_tokens") or 0)
        name = str(e.get("model") or "unknown")[:120]
        models[name] = models.get(name, 0) + i + o
        if e.get("session_id"):
            sessions.add(e["session_id"])
    top = [{"name": n, "tokens": t} for n, t in sorted(models.items(), key=lambda kv: -kv[1])[:5]]
    older = len(events) - len(evs)
    extra = ""
    if not evs and events:
        extra = " 0 tokens in this window; the logs have older Grok CLI usage (%d events)." % len(events)
    elif older > 0:
        extra = " %d older events are outside this window and are not in the total." % older
    return {
        "value": inp + out,
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_tokens": cache,
        "reasoning_tokens": reason,
        "events": len(evs),
        "sessions": len(sessions),
        "models": top,
        "window": window,
        "source": source,
        "note": (note + extra).strip(),
        "reason": "",
    }


def _na_tokens(reason: str) -> Dict[str, Any]:
    return {"value": None, "reason": reason, "source": "", "note": "", "models": [],
            "events": None, "sessions": None, "window": ""}


def _cli_tokens(events: Iterable[Dict[str, Any]], provider: str,
                unified_on: bool, start: float, end: float, window: str
                ) -> Dict[str, Any]:
    evs = list(events or [])
    sess = [e for e in evs if (e.get("source") or "grok_sessions") == "grok_sessions"]
    uni = [e for e in evs if e.get("source") == "grok_unified"]
    if sess:
        note = ("This computer's Grok CLI session logs (~/.grok/sessions/**/updates.jsonl). "
                "Does not include Grok Bot, grok.com, or Cursor.")
        if uni:
            note += (" %d grok_unified events were not added — those lines are the same "
                     "Grok CLI turns and would double-count." % len(uni))
        return _token_detail(sess, start, end, "grok_sessions", window, note)
    if uni:
        note = ("Experimental Grok CLI unified log (~/.grok/logs/unified.jsonl, "
                "shell.turn.inference_done). Same CLI as session logs, not Grok Bot or "
                "Cursor. Used because session logs have no usage events.")
        if not unified_on:
            note += " The unified-log provider is not currently enabled; these are events already recorded."
        return _token_detail(uni, start, end, "grok_unified", window, note)
    if provider in ("grok_sessions", "demo", "local_logs") or unified_on:
        return _na_tokens(_CLI_TOKENS_MISSING)
    return _na_tokens(
        "not available: Grok CLI token stats are off (token_source is not grok_sessions, "
        "and the experimental unified log is not enabled).")


def _percent_reason(bucket: str, reported: bool, names: List[str]) -> str:
    listed = ", ".join(names) if names else "(none)"
    if not reported:
        return ("not available: the billing response omitted productUsage, so there is "
                "no per-product split. The suite total is the only percent in the payload.")
    if bucket == "cli":
        return ("not available: productUsage did not include a Grok CLI product "
                "(GrokBuild / Grok CLI). It listed: %s." % listed)
    if bucket == "bot":
        return ("not available: productUsage did not include Grok Bot. It listed: %s. "
                % listed) + _BOT_PERCENT_SEPARATE
    return ("not available: productUsage did not include Cursor (Grok-in-Cursor). "
            "It listed: %s. " % listed) + _CURSOR_PERCENT_SEPARATE


def build_grok_suite(billing: Optional[Dict[str, Any]], events: Iterable[Dict[str, Any]],
                     cfg: Optional[Dict[str, Any]], now: datetime, *,
                     provider: str = "", provider_note: str = "") -> Dict[str, Any]:
    """Dashboard / tray payload. Never invents a percent or a token count."""
    cfg = cfg or {}
    reported, products = _products(billing)
    by_bucket: Dict[str, List[Dict[str, Any]]] = {"cli": [], "bot": [], "cursor": []}
    other: List[Dict[str, Any]] = []
    for p in products:
        b = _bucket_of(p["product"])
        row = dict(p)
        if b:
            row["label"] = {"cli": _CLI, "bot": _BOT, "cursor": _CURSOR}[b].get(
                _key(p["product"]), p["product"])
            by_bucket[b].append(row)
        else:
            row["label"] = _OTHER.get(_key(p["product"]), p["product"])
            other.append(row)

    names = [p["product"] for p in products]
    start, end, window = _window(billing, now)
    unified_on = "grok_unified" in (cfg.get("extra_token_sources") or [])
    cli_tokens = _cli_tokens(events, provider, unified_on, start, end, window)
    is_demo = bool((billing or {}).get("is_demo") or provider == "demo")

    def bucket_pct(rows: List[Dict[str, Any]]) -> Optional[float]:
        if not rows:
            return None
        return round(sum(float(r["used_percent"]) for r in rows), 4)

    used = remaining = raw = None
    resets = period_type = ""
    if billing and billing.get("used_percent") is not None:
        try:
            used = float(billing["used_percent"])
            remaining = float(billing["remaining_percent"]) if billing.get(
                "remaining_percent") is not None else 100.0 - used
            raw = float(billing["raw_used_percent"]) if billing.get(
                "raw_used_percent") is not None else used
        except (TypeError, ValueError):
            used = remaining = raw = None
        resets = billing.get("resets_at") or ""
        period_type = str(billing.get("period_type") or "")

    honesty = [
        "One weekly pool. The suite total is creditUsagePercent (used and remaining). "
        "Product percents are shares of that same pool, not separate allowances.",
        "A productUsage entry with no usagePercent is 0% (proto-JSON omits zero). "
        "A product that is not listed is not available — it is not shown as 0%.",
        "Token counts are only this computer's Grok CLI. Grok Bot and Cursor (Grok) "
        "have no token log in the files OmniMonitor reads.",
    ]
    if is_demo:
        honesty.insert(0, "DEMO: every figure in this card is fake sample data.")
    if cli_tokens.get("source") == "grok_sessions" and "double-count" in (cli_tokens.get("note") or ""):
        honesty.append(cli_tokens["note"])
    gap_note = ""
    share_sum = None
    if reported and products and used is not None:
        share_sum = round(sum(float(p["used_percent"]) for p in products), 4)
        if abs(share_sum - float(billing.get("raw_used_percent", used))) > 0.6:
            gap_note = ("Product shares sum to %s used; the suite total is %s used. "
                        "The payload does not explain the difference, so nothing was "
                        "added to close the gap."
                        % (_fmt_pct(share_sum), _fmt_pct(float(billing.get(
                            "raw_used_percent", used)))))
            honesty.append(gap_note)
    if not billing or used is None:
        total_reason = "not available: no Grok billing reading yet."
    else:
        total_reason = ""
    if cli_tokens.get("value") is None and provider_note:
        note = " ".join(str(provider_note).split())
        low = note.lower()
        if note and not any(s in low for s in ("bearer ", "cookie", "authorization", "api_key")):
            cli_tokens = dict(cli_tokens)
            cli_tokens["reason"] = (cli_tokens.get("reason") or "").rstrip(".") + ". " + note[:400]

    buckets = []
    for bid, title, blurb in _BUCKETS:
        rows = by_bucket[bid]
        pct = bucket_pct(rows) if used is not None else None
        if bid == "cli":
            tokens = cli_tokens
        elif bid == "bot":
            tokens = _na_tokens(_BOT_TOKENS)
        else:
            tokens = _na_tokens(_CURSOR_TOKENS)
        if pct is not None:
            why = ""
        elif used is None:
            why = total_reason
        else:
            why = _percent_reason(bid, reported, names)
        buckets.append({
            "id": bid,
            "name": title,
            "blurb": blurb,
            "used_percent": pct,
            "percent_reason": why,
            "products": rows,
            "tokens": tokens,
        })

    return {
        "title": "Grok suite",
        "pool": "weekly",
        "source": "grok_cli_billing",
        "is_demo": is_demo,
        "used_percent": used,
        "remaining_percent": remaining,
        "raw_used_percent": raw,
        "total_reason": total_reason,
        "resets_at": resets or None,
        "period_type": period_type,
        "window": window,
        "products_reported": reported,
        "products": products,
        "buckets": buckets,
        "other_products": other,
        "share_sum": share_sum,
        "share_gap_note": gap_note,
        "honesty": honesty,
    }


def menu_lines(suite: Optional[Dict[str, Any]]) -> List[str]:
    """Short tray lines. Full reasons stay on the dashboard."""
    if not suite:
        return []
    if suite.get("remaining_percent") is None:
        head = "Grok suite: not available"
    else:
        head = "Grok suite: %s left" % _fmt_pct(suite["remaining_percent"])
        if suite.get("used_percent") is not None:
            head += " · %s used" % _fmt_pct(suite["used_percent"])
    lines = [head]
    for b in suite.get("buckets") or []:
        pct = b.get("used_percent")
        tok = b.get("tokens") or {}
        if pct is None:
            bit = "pool % not available"
        else:
            bit = "%s of pool" % _fmt_pct(pct)
        if b.get("id") == "cli" and tok.get("value") is not None:
            lines.append("%s: %s · %s tokens" % (b["name"], bit, _fmt_tok(tok["value"])))
        elif b.get("id") == "cli":
            lines.append("%s: %s · tokens not available" % (b["name"], bit))
        elif pct is None:
            why = "productUsage omitted" if not suite.get("products_reported") \
                else "not in productUsage"
            lines.append("%s: not available (%s)" % (b["name"], why))
        else:
            lines.append("%s: %s · tokens not available" % (b["name"], bit))
    others = suite.get("other_products") or []
    if others:
        bits = ["%s %s" % (o.get("label") or o["product"], _fmt_pct(o["used_percent"]))
                for o in others[:4]]
        lines.append("Also in pool: " + ", ".join(bits))
    return lines
