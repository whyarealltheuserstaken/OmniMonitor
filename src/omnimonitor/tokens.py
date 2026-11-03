"""Token-stats providers.

The Grok billing endpoint reports only a weekly *percentage*; it does not expose token
counts or a weekly token limit. The only real token numbers available locally are the
Grok CLI's own session logs:

    <GROK_HOME>/sessions/<encoded-cwd>/<session-id>/updates.jsonl

Each `turn_completed` update carries per-model usage
(`usage.modelUsage.{model}.inputTokens / outputTokens / cachedReadTokens / ...`).
These files are undocumented internals and may change - parsing is defensive and any
line we don't understand is skipped (never guessed).

What this does NOT see: Grok usage outside the CLI on this computer (grok.com, the apps,
other machines, IDE integrations). The weekly % from billing does include those.

Providers:
    grok_sessions  real numbers from the local CLI logs (read-only, incremental)
    demo           FAKE, deterministic, labeled DEMO
    none           nothing (everything "not available")
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from omnimonitor.timeutil import parse_time

MAX_PLAUSIBLE_TOKENS = 10 ** 12  # anything above is treated as corrupt and skipped
MAX_LINE_BYTES = 8 * 1024 * 1024
DURATION_KEYS = ("durationMs", "turnDurationMs", "elapsedMs", "wallTimeMs", "duration_ms")


@dataclass
class TokenEvent:
    event_id: str
    model: str
    ts: float
    session_id: str
    input_tokens: int = 0         # full prompt size (includes cached reads)
    output_tokens: int = 0        # includes reasoning
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: Optional[float] = None
    source: str = "grok_sessions"

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class TurnRecord:
    event_id: str
    session_id: str
    ts: float
    duration_s: Optional[float]
    duration_source: str  # "reported" | "derived from log timestamps" | ""


def _int(v: Any) -> int:
    if isinstance(v, bool) or v is None:
        return 0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(f) or f < 0 or f > MAX_PLAUSIBLE_TOKENS:
        return -1  # sentinel: implausible
    return int(f)


def _event_ts(obj: Dict[str, Any], params: Dict[str, Any]) -> Optional[float]:
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    for v in (meta.get("agentTimestampMs"), meta.get("timestampMs"), obj.get("timestamp"),
              params.get("timestamp"), obj.get("ts")):
        dt = parse_time(v)
        if dt is not None:
            return dt.timestamp()
    return None


def _get_update(obj: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    params = obj.get("params") if isinstance(obj.get("params"), dict) else {}
    upd = params.get("update") if isinstance(params.get("update"), dict) else None
    if upd is None and isinstance(obj.get("update"), dict):
        upd = obj["update"]
    return (upd or {}), params


def parse_update_line(line: str, session_id: str, path_hint: str = ""
                      ) -> Tuple[Optional[float], List[TokenEvent], Optional[Dict[str, Any]]]:
    """Parse one updates.jsonl line.

    Returns (timestamp_or_None, token_events, turn_info_or_None). Lines that aren't
    turn_completed return their timestamp only (used to derive turn duration).
    """
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return None, [], None
    if not isinstance(obj, dict):
        return None, [], None
    upd, params = _get_update(obj)
    ts = _event_ts(obj, params)
    if upd.get("sessionUpdate") != "turn_completed":
        return ts, [], None
    usage = upd.get("usage") if isinstance(upd.get("usage"), dict) else {}
    model_usage = usage.get("modelUsage") if isinstance(usage.get("modelUsage"), dict) else {}
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    base_id = str(meta.get("eventId") or upd.get("eventId") or obj.get("id") or "") or (
        "sha1:" + hashlib.sha1((path_hint + "\0" + line).encode("utf-8", "replace")).hexdigest())
    if ts is None:
        return None, [], None  # can't place it on a day -> skip rather than guess
    events: List[TokenEvent] = []
    for model, mu in model_usage.items():
        if not isinstance(mu, dict):
            continue
        inp = _int(mu.get("inputTokens"))
        out = _int(mu.get("outputTokens"))
        cr = _int(mu.get("cachedReadTokens", mu.get("cacheReadTokens")))
        cc = _int(mu.get("cacheCreationTokens"))
        rs = _int(mu.get("reasoningTokens"))
        if -1 in (inp, out, cr, cc, rs):
            continue  # implausible -> skip this model entry
        cr = min(cr, inp) if inp else cr
        cost = None
        ticks = mu.get("costUsdTicks")
        if isinstance(ticks, (int, float)) and not isinstance(ticks, bool) and ticks >= 0:
            cost = float(ticks) / 1e10
        if inp == out == cr == cc == 0:
            continue
        events.append(TokenEvent(base_id, str(model)[:120], ts, session_id, inp, out, cr, cc,
                                 rs, cost))
    dur = None
    for k in DURATION_KEYS:
        for src in (upd, usage, meta):
            v = src.get(k) if isinstance(src, dict) else None
            if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v < 7 * 86400e3:
                dur = float(v) / 1000.0
                break
        if dur is not None:
            break
    return ts, events, {"event_id": base_id, "ts": ts, "duration_s": dur}


def iter_update_files(grok_home: Path) -> Iterator[Path]:
    root = grok_home / "sessions"
    if not root.is_dir():
        return
    for dirpath, _dirs, files in os.walk(str(root)):
        if "updates.jsonl" in files:
            yield Path(dirpath) / "updates.jsonl"


class GrokSessionsScanner:
    """Incremental, read-only scanner. Offsets are kept in History.scan_state."""

    name = "grok_sessions"
    label = "Grok CLI session logs on this computer"

    def __init__(self, grok_home: Path, history):
        self.grok_home = Path(grok_home)
        self.history = history

    def available(self) -> Tuple[bool, str]:
        if not (self.grok_home / "sessions").is_dir():
            return False, ("no Grok CLI session logs at %s (install/use the Grok CLI, or "
                           "token stats stay unavailable)" % (self.grok_home / "sessions"))
        return True, ""

    def scan(self, max_bytes: int = 256 * 1024 * 1024) -> Dict[str, int]:
        stats = {"files": 0, "new_events": 0, "errors": 0}
        budget = max_bytes
        for path in iter_update_files(self.grok_home):
            stats["files"] += 1
            try:
                st = path.stat()
            except OSError:
                stats["errors"] += 1
                continue
            key = str(path)
            state = self.history.scan_state(key)
            offset, turn_start = 0, None
            if state:
                size, mtime, offset, turn_start = state
                if st.st_size == size and abs(st.st_mtime - mtime) < 1e-6:
                    continue
                if st.st_size < offset:  # truncated / rewritten -> rescan; dedup by event id
                    offset, turn_start = 0, None
            if budget <= 0:
                break
            session_id = path.parent.name
            events: List[TokenEvent] = []
            turns: List[TurnRecord] = []
            try:
                with path.open("rb") as f:  # read-only
                    f.seek(offset)
                    while budget > 0:
                        raw = f.readline(MAX_LINE_BYTES)
                        if not raw:
                            break
                        if not raw.endswith(b"\n"):
                            if len(raw) >= MAX_LINE_BYTES:  # absurd line: skip it
                                offset += len(raw)
                                rest = f.readline()
                                offset += len(rest)
                                continue
                            break  # partial line still being written; resume later
                        offset += len(raw)
                        budget -= len(raw)
                        ts, evs, turn = parse_update_line(raw.decode("utf-8", "replace"),
                                                          session_id, key)
                        if turn is None:
                            if ts is not None and turn_start is None:
                                turn_start = ts * 1000.0
                            continue
                        events.extend(evs)
                        dur, src = turn["duration_s"], "reported"
                        if dur is None and turn_start is not None:
                            d = turn["ts"] - turn_start / 1000.0
                            dur, src = (d, "derived from log timestamps") if 0 < d < 7 * 86400 \
                                else (None, "")
                        elif dur is None:
                            src = ""
                        turns.append(TurnRecord(turn["event_id"], session_id, turn["ts"], dur,
                                                src))
                        turn_start = None
            except OSError:
                stats["errors"] += 1
                continue
            stats["new_events"] += self.history.add_events(events)
            self.history.add_turns(turns)
            self.history.set_scan_state(key, st.st_size, st.st_mtime, offset, turn_start)
        return stats


def demo_events(now_ts: float, days: int = 365, seed: int = 7
                ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """FAKE history for demo mode / screenshots. Deterministic. NOT real usage."""
    rng = random.Random(seed)
    events, turns = [], []
    models = [("demo-model-a", 0.6), ("demo-model-b", 0.25), ("demo-model-c", 0.15)]
    day0 = now_ts - days * 86400
    for d in range(days):
        recent = d >= days - 6  # keep the last week active so the demo shows a streak
        if rng.random() < 0.45 and not recent:
            continue
        burst = rng.random() ** 2
        n = 1 + int(burst * 12)
        for _i in range(n):
            ts = day0 + d * 86400 + rng.uniform(0, 23) * 3600
            if ts > now_ts:
                ts = now_ts - rng.uniform(60, 6 * 3600)
            m = rng.choices([m for m, _ in models], [w for _, w in models])[0]
            inp = int(rng.lognormvariate(11.5, 1.0))
            out = int(inp * rng.uniform(0.02, 0.12))
            events.append({"ts": ts, "session_id": "demo-%d" % d, "model": m,
                           "input_tokens": inp, "output_tokens": out,
                           "cache_read_tokens": int(inp * 0.7), "cache_creation_tokens": 0,
                           "reasoning_tokens": int(out * 0.4), "cost_usd": None})
            turns.append({"ts": ts, "session_id": "demo-%d" % d,
                          "duration_s": rng.lognormvariate(4.5, 1.1),
                          "duration_source": "demo"})
    return events, turns
