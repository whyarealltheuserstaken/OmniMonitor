"""≈ 5-hour usage blocks and burn rate (an ESTIMATE, not Anthropic's real limit window).

Claude plans meter usage in rolling 5-hour sessions that start with your first message.
Anthropic does not publish the exact rule or expose the window, so this reconstructs
blocks from local log timestamps the common way: a block starts at the hour (UTC) of the
first event after the previous block ended, and lasts 5 hours. Gaps of 5h+ end a block.
Everything returned is labelled ``estimate``.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Optional

BLOCK_S = 5 * 3600


def blocks(events: Iterable[Dict[str, Any]], sources: Optional[Iterable[str]] = ("claude_code",),
           now: Optional[float] = None) -> List[Dict[str, Any]]:
    now = time.time() if now is None else now
    want = set(sources) if sources is not None else None
    evs = sorted((e for e in events if want is None or e.get("source") in want),
                 key=lambda e: e["ts"])
    out: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for e in evs:
        ts = float(e["ts"])
        if cur is None or ts >= cur["end"]:
            start = ts - (ts % 3600)
            cur = {"start": start, "end": start + BLOCK_S, "first": ts, "last": ts,
                   "tokens": 0, "events": 0, "cost_usd": None, "estimate": True}
            out.append(cur)
        cur["last"] = ts
        cur["events"] += 1
        cur["tokens"] += int(e.get("input_tokens") or 0) + int(e.get("output_tokens") or 0)
        if e.get("cost_usd") is not None:
            cur["cost_usd"] = (cur["cost_usd"] or 0.0) + float(e["cost_usd"])
    for b in out:
        b["active"] = b["start"] <= now < b["end"]
        span = max(60.0, (min(now, b["end"]) if b["active"] else b["last"]) - b["first"])
        b["tokens_per_min"] = b["tokens"] / (span / 60.0)
        if b["active"]:
            b["remaining_s"] = b["end"] - now
            b["projected_tokens"] = int(b["tokens"] + b["tokens_per_min"] * b["remaining_s"] / 60)
    return out


def active_block(events, sources=("claude_code",), now=None) -> Optional[Dict[str, Any]]:
    bs = blocks(events, sources, now)
    return bs[-1] if bs and bs[-1]["active"] else None
