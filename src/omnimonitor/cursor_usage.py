"""Cursor dashboard usage parsers.

Unofficial endpoints, called only for the opt-in ``cursor`` provider:

* ``GET https://cursor.com/api/dashboard/v2/usage-status?scope=user``
  ``monthlyUsage.{usedCents,limitCents,periodStart,resetsAt}`` when
  ``availability`` is ``available``. Cents are dollars, never tokens.
* ``POST https://cursor.com/api/dashboard/get-filtered-usage-events``
  paginated body ``{page, pageSize, startDate, endDate}`` (millisecond strings).
  Each event's ``model`` and ``tokenUsage.{inputTokens,outputTokens,
  cacheWriteTokens,cacheReadTokens,totalCents}``.

A missing field stays missing. Nothing is filled in with zero unless the payload
actually sent a zero. Models whose name contains ``grok`` are "Grok in Cursor".
A model or kind is "Grok Bot" only when that label is written in the name.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

HttpFn = Callable[[str, str, Dict[str, str], Optional[bytes]], Tuple[int, Any]]

USAGE_STATUS_URL = "https://cursor.com/api/dashboard/v2/usage-status?scope=user"
USAGE_EVENTS_URL = "https://cursor.com/api/dashboard/get-filtered-usage-events"
PAGE_SIZE = 100
MAX_PAGES = 15


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != n or n in (float("inf"), float("-inf")):
        return None
    return n


def _int_token(value: Any) -> Optional[int]:
    """A token counter. Missing stays None. A present non-negative number is kept."""
    if value is None:
        return None
    n = _finite(value)
    if n is None or n < 0:
        return None
    return int(n)


def _cents(value: Any) -> Optional[float]:
    n = _finite(value)
    if n is None or n < 0:
        return None
    return n


def parse_usage_status(data: Any, now: datetime) -> Dict[str, Any]:
    """Unified-account dollars. ``available`` is False when the payload cannot be used.

    CodexBar uses this only when ``availability == "available"``, both cent fields
    are finite and non-negative, and ``now`` is inside ``[periodStart, resetsAt)``.
    The legacy usage-summary can report a false zero on these accounts, so a
    usable v2 payload is preferred by the caller.
    """
    if not isinstance(data, dict):
        return {"available": False, "reason": "usage-status response was not an object"}
    if data.get("availability") != "available":
        return {"available": False,
                "reason": "usage-status availability is not \"available\""}
    monthly = data.get("monthlyUsage")
    if not isinstance(monthly, dict):
        return {"available": False, "reason": "usage-status omitted monthlyUsage"}
    if "usedCents" not in monthly or "limitCents" not in monthly:
        missing = [k for k in ("usedCents", "limitCents") if k not in monthly]
        return {"available": False,
                "reason": "usage-status monthlyUsage omitted %s" % ", ".join(missing)}
    used = _cents(monthly.get("usedCents"))
    limit = _cents(monthly.get("limitCents"))
    if used is None or limit is None:
        return {"available": False,
                "reason": "usage-status usedCents/limitCents were not non-negative numbers"}
    start = _parse_iso(monthly.get("periodStart"))
    reset = _parse_iso(monthly.get("resetsAt"))
    if start is None or reset is None or start >= reset:
        return {"available": False,
                "reason": "usage-status periodStart/resetsAt were missing or not a range"}
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if not (start <= now < reset):
        return {"available": False,
                "reason": "usage-status period does not contain the current time"}
    return {
        "available": True,
        "reason": "",
        "used_cents": used,
        "limit_cents": limit,
        "used_usd": used / 100.0,
        "limit_usd": limit / 100.0,
        "period_start": start.timestamp(),
        "resets_at": reset.timestamp(),
    }


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    from omnimonitor.timeutil import parse_time
    return parse_time(value)


def _safe_model(name: Any) -> str:
    text = " ".join(str(name or "").split())
    if not text:
        return ""
    low = text.lower()
    if any(mark in low for mark in ("bearer", "cookie", "eyj", "sk-", "token=",
                                    "workoscursorsessiontoken")):
        return ""
    return text[:80]


def is_grok_model(model: str) -> bool:
    return "grok" in (model or "").lower()


def is_labeled_grok_bot(model: str, kind: str) -> bool:
    """True only when the event itself says Grok Bot. ``grok-4`` does not."""
    blob = "%s %s" % (model or "", kind or "")
    compact = "".join(ch for ch in blob.lower() if ch.isalnum())
    return "grokbot" in compact


def parse_event(raw: Any) -> Optional[Dict[str, Any]]:
    """One ``usageEventsDisplay`` row. None when it is not an object."""
    if not isinstance(raw, dict):
        return None
    ts = _event_millis(raw.get("timestamp"))
    model = _safe_model(raw.get("model"))
    kind = _safe_model(raw.get("kind"))
    usage = raw.get("tokenUsage") if isinstance(raw.get("tokenUsage"), dict) else None
    tokens = None
    cost = "omitted"
    if usage is not None:
        tokens = {
            "input": _int_token(usage["inputTokens"]) if "inputTokens" in usage else 0,
            "output": _int_token(usage["outputTokens"]) if "outputTokens" in usage else 0,
            "cache_write": _int_token(usage["cacheWriteTokens"]) if "cacheWriteTokens" in usage else 0,
            "cache_read": _int_token(usage["cacheReadTokens"]) if "cacheReadTokens" in usage else 0,
        }
        if any(v is None for v in tokens.values()):
            tokens = None
            cost = "invalid"
        elif "totalCents" not in usage or usage.get("totalCents") is None:
            cost = "omitted"
        else:
            cents = _cents(usage.get("totalCents"))
            cost = cents if cents is not None else "invalid"
    charged = _cents(raw.get("chargedCents")) if "chargedCents" in raw else None
    if "chargedCents" in raw and raw.get("chargedCents") is not None and charged is None:
        charged = "invalid"
    return {
        "timestamp_ms": ts,
        "model": model or "unknown",
        "kind": kind,
        "tokens": tokens,
        "cost_cents": cost,
        "charged_cents": charged,
        "grok": is_grok_model(model),
        "grok_bot": is_labeled_grok_bot(model, kind),
    }


def _event_millis(value: Any) -> Optional[int]:
    n = _finite(value)
    if n is None or n <= 0:
        return None
    return int(n)


def parse_events_page(data: Any) -> Dict[str, Any]:
    """One page. An empty object is an empty result, not an error.

    A negative ``totalUsageEventsCount`` is rejected. A page that is only the
    count (no ``usageEventsDisplay``) is an empty terminal page.
    """
    if data is None:
        return {"ok": False, "reason": "usage-events response was empty",
                "events": [], "total": None}
    if not isinstance(data, dict):
        return {"ok": False, "reason": "usage-events response was not an object",
                "events": [], "total": None}
    if not data:
        return {"ok": True, "reason": "", "events": [], "total": 0}
    if "totalUsageEventsCount" in data:
        total_n = _finite(data.get("totalUsageEventsCount"))
        if total_n is None or total_n < 0 or int(total_n) != total_n:
            return {"ok": False, "reason": "usage-events totalUsageEventsCount was not a count",
                    "events": [], "total": None}
        total: Optional[int] = int(total_n)
    else:
        total = None
    if "usageEventsDisplay" not in data:
        if set(data) == {"totalUsageEventsCount"}:
            return {"ok": True, "reason": "", "events": [], "total": total}
        return {"ok": False, "reason": "usage-events response omitted usageEventsDisplay",
                "events": [], "total": total}
    rows = data.get("usageEventsDisplay")
    if not isinstance(rows, list):
        return {"ok": False, "reason": "usageEventsDisplay was not a list",
                "events": [], "total": total}
    events = []
    for row in rows:
        ev = parse_event(row)
        if ev is not None:
            events.append(ev)
    return {"ok": True, "reason": "", "events": events, "total": total}


def _same_event(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return (a.get("timestamp_ms"), a.get("model"), a.get("kind"),
            a.get("tokens"), a.get("cost_cents"), a.get("charged_cents")) == (
        b.get("timestamp_ms"), b.get("model"), b.get("kind"),
        b.get("tokens"), b.get("cost_cents"), b.get("charged_cents"))


def _boundary_overlap(prev: List[Dict[str, Any]], cur: List[Dict[str, Any]]) -> int:
    limit = min(len(prev), len(cur))
    for count in range(limit, 0, -1):
        if all(_same_event(prev[-count + i], cur[i]) for i in range(count)):
            return count
    return 0


def reconcile_pages(pages: List[List[Dict[str, Any]]], total: Optional[int]
                    ) -> Dict[str, Any]:
    """Drop only the duplicate rows a reported total proves, at page boundaries."""
    raw = [ev for page in pages for ev in page]
    if total is None:
        return {"ok": True, "events": raw, "reason": ""}
    if len(raw) < total:
        return {"ok": False, "events": [],
                "reason": "usage-events pagination incomplete (%d of %d)" % (len(raw), total)}
    if len(raw) == total:
        return {"ok": True, "events": raw, "reason": ""}
    removals = len(raw) - total
    if not pages:
        return {"ok": False, "events": [], "reason": "usage-events pagination inconsistent"}
    reconciled = list(pages[0])
    for index in range(1, len(pages)):
        overlap = _boundary_overlap(pages[index - 1], pages[index])
        drop = min(overlap, removals)
        reconciled.extend(pages[index][drop:])
        removals -= drop
    if removals != 0 or len(reconciled) != total:
        return {"ok": False, "events": [],
                "reason": "usage-events pagination inconsistent (reported %d, received %d)"
                % (total, len(raw))}
    return {"ok": True, "events": reconciled, "reason": ""}


def _blank_totals() -> Dict[str, Any]:
    return {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "events": 0,
            "models": {}, "cost_cents": 0.0, "cost_ok": True, "saw_tokens": False}


def _add_tokens(bucket: Dict[str, Any], ev: Dict[str, Any]) -> None:
    tokens = ev.get("tokens")
    if not tokens:
        return
    bucket["saw_tokens"] = True
    bucket["events"] += 1
    for key in ("input", "output", "cache_write", "cache_read"):
        bucket[key] += int(tokens[key])
    name = ev.get("model") or "unknown"
    bucket["models"][name] = bucket["models"].get(name, 0) + int(tokens["input"]) + int(
        tokens["output"])
    cost = ev.get("cost_cents")
    if cost == "invalid" or cost == "omitted" or not isinstance(cost, (int, float)):
        bucket["cost_ok"] = False
        bucket["cost_cents"] = None
    elif bucket["cost_ok"]:
        bucket["cost_cents"] = (bucket["cost_cents"] or 0) + float(cost)


def _finish(bucket: Dict[str, Any], *, label: str) -> Dict[str, Any]:
    if not bucket["saw_tokens"]:
        return {"available": False, "value": None,
                "reason": "not available: %s events did not include tokenUsage" % label,
                "models": []}
    models = [{"name": n, "tokens": t}
              for n, t in sorted(bucket["models"].items(), key=lambda kv: (-kv[1], kv[0]))[:8]]
    cost = None
    cost_reason = ""
    if bucket["cost_ok"] and bucket["cost_cents"] is not None:
        cost = round(float(bucket["cost_cents"]) / 100.0, 4)
    else:
        cost_reason = "token cost not available: an event omitted or rejected totalCents"
    return {
        "available": True,
        "value": bucket["input"] + bucket["output"] + bucket["cache_write"] + bucket["cache_read"],
        "input_tokens": bucket["input"],
        "output_tokens": bucket["output"],
        "cache_write_tokens": bucket["cache_write"],
        "cache_read_tokens": bucket["cache_read"],
        "events": bucket["events"],
        "models": models,
        "cost_usd": cost,
        "cost_reason": cost_reason,
        "reason": "",
    }


def summarize_events(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Overall Cursor tokens, Grok-in-Cursor (name contains grok), and labeled Grok Bot."""
    overall = _blank_totals()
    grok = _blank_totals()
    bot = _blank_totals()
    names = []
    seen = set()
    for ev in events:
        if not ev.get("timestamp_ms"):
            continue
        model = ev.get("model") or ""
        if model and model not in seen and model != "unknown":
            seen.add(model)
            names.append(model)
        _add_tokens(overall, ev)
        if ev.get("grok"):
            _add_tokens(grok, ev)
        if ev.get("grok_bot"):
            _add_tokens(bot, ev)
    cursor = _finish(overall, label="Cursor")
    grok_out = _finish(grok, label="Grok-in-Cursor")
    if cursor.get("available") and not grok["saw_tokens"]:
        grok_out = {
            "available": True, "value": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_write_tokens": 0, "cache_read_tokens": 0, "events": 0, "models": [],
            "cost_usd": None, "cost_reason": "no grok-named model, so no Grok token cost",
            "reason": "", "note": "no model name contained grok",
        }
    bot_out = _finish(bot, label="Grok Bot")
    if not bot["saw_tokens"]:
        bot_out = {"available": False, "value": None, "models": [],
                   "reason": "not available: no event model or kind was labeled Grok Bot"}
    return {
        "models": sorted(seen),
        "cursor_tokens": cursor,
        "grok_in_cursor": grok_out,
        "grok_bot_labeled": bot_out,
    }


def millis_string(when: Optional[float]) -> str:
    """Unix milliseconds as a string. Negative instants clamp to the epoch."""
    if when is None:
        return "0"
    ms = int(when * 1000)
    if ms < 0:
        ms = 0
    return str(ms)


def fetch_filtered_events(http: HttpFn, headers: Dict[str, str], start: Optional[float],
                          end: Optional[float], *, page_size: int = PAGE_SIZE,
                          max_pages: int = MAX_PAGES) -> Dict[str, Any]:
    """Page the events endpoint. Incomplete pagination does not return a partial total."""
    hdr = dict(headers)
    hdr["Content-Type"] = "application/json"
    hdr.setdefault("Origin", "https://cursor.com")
    hdr.setdefault("Accept", "application/json")
    pages: List[List[Dict[str, Any]]] = []
    expected: Optional[int] = None
    completed = False
    for page in range(1, max_pages + 1):
        body = json.dumps({
            "page": page,
            "pageSize": page_size,
            "startDate": millis_string(start),
            "endDate": millis_string(end),
        }).encode("utf-8")
        try:
            status, payload = http("POST", USAGE_EVENTS_URL, hdr, body)
        except Exception as e:  # noqa: BLE001 - caller turns this into "not available"
            return {"ok": False, "reason": "usage-events request failed (%s)" % type(e).__name__,
                    "events": [], "models": []}
        if status in (401, 403):
            return {"ok": False, "reason": "signed out — sign in again",
                    "events": [], "models": []}
        if status != 200:
            return {"ok": False,
                    "reason": "get-filtered-usage-events HTTP %s" % status,
                    "events": [], "models": []}
        parsed = parse_events_page(payload)
        if not parsed["ok"]:
            return {"ok": False, "reason": parsed["reason"], "events": [], "models": []}
        if expected is None:
            expected = parsed["total"]
        elif parsed["total"] is not None and parsed["total"] != expected:
            return {"ok": False, "reason": "usage-events page totals disagreed",
                    "events": [], "models": []}
        rows = parsed["events"]
        if not rows:
            completed = True
            break
        pages.append(rows)
        if len(rows) < page_size:
            completed = True
            break
    if not completed:
        return {"ok": False,
                "reason": "usage-events pagination stopped at the page cap before the window ended",
                "events": [], "models": _model_names(pages)}
    reconciled = reconcile_pages(pages, expected)
    if not reconciled["ok"]:
        return {"ok": False, "reason": reconciled["reason"], "events": [],
                "models": _model_names(pages)}
    summary = summarize_events(reconciled["events"])
    summary["ok"] = True
    summary["reason"] = ""
    summary["events"] = reconciled["events"]
    return summary


def _model_names(pages: List[List[Dict[str, Any]]]) -> List[str]:
    have = set()
    for page in pages:
        for ev in page:
            name = ev.get("model") or ""
            if name and name != "unknown":
                have.add(name)
    return sorted(have)
