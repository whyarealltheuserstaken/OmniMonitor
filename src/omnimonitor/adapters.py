"""Token adapters for other AI coding tools' local logs (read-only, incremental, opt-in).

Each adapter turns one JSONL line into zero or more `TokenEvent`s. Formats are
undocumented internals of those tools (see docs/token-adapters.md); anything we don't
recognise is skipped, never guessed.

Normalisation (same as the Grok adapter, so totals are comparable):
    input_tokens  = full prompt size, INCLUDING cache reads and cache writes
    output_tokens = full output, INCLUDING reasoning
    total         = input_tokens + output_tokens
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from omnimonitor import config as config_mod
from omnimonitor.timeutil import parse_time
from omnimonitor.tokens import MAX_LINE_BYTES, GrokSessionsScanner, TokenEvent, _int, project_name

LABELS = {
    "grok_sessions": "Grok CLI",
    "claude_code": "Claude Code",
    "codex": "Codex",
}


def label(source: Optional[str]) -> str:
    return LABELS.get(source or "", source or "unknown")


def _ts(v: Any) -> Optional[float]:
    dt = parse_time(v)
    return dt.timestamp() if dt else None


def _sha(*parts: str) -> str:
    return hashlib.sha1("\0".join(parts).encode("utf-8", "replace")).hexdigest()


# --------------------------------------------------------------------------- Claude Code

def parse_claude_line(line: str, ctx: Dict[str, Any], file_key: str) -> List[TokenEvent]:
    """One line of ~/.claude/projects/<encoded-cwd>/<session>.jsonl.

    Only `type: "assistant"` lines carry `message.usage`. Claude's `input_tokens` EXCLUDES
    cache tokens, so we add cache reads + writes to get the full prompt size. The same
    message is often written on several lines (one per content block) with identical usage:
    dedupe on message.id + requestId.
    """
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return []
    if not isinstance(obj, dict):
        return []
    if isinstance(obj.get("cwd"), str):
        ctx["cwd"] = obj["cwd"]
    if obj.get("type") != "assistant":
        return []
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else None
    if not usage:
        return []
    model = str(msg.get("model") or "unknown")[:120]
    if model == "<synthetic>":
        return []  # client-side placeholder, not a model call
    ts = _ts(obj.get("timestamp"))
    if ts is None:
        return []
    inp = _int(usage.get("input_tokens"))
    out = _int(usage.get("output_tokens"))
    cr = _int(usage.get("cache_read_input_tokens"))
    cc = _int(usage.get("cache_creation_input_tokens"))
    if -1 in (inp, out, cr, cc) or inp == out == cr == cc == 0:
        return []
    mid, rid = msg.get("id"), obj.get("requestId")
    eid = "claude:%s:%s" % (mid, rid or "") if mid else "claude:sha1:" + _sha(file_key, line)
    session = str(obj.get("sessionId") or Path(file_key).stem)
    return [TokenEvent(eid, model, ts, session, inp + cr + cc, out, cr, cc, 0, None,
                       source="claude_code", project=project_name(ctx.get("cwd")))]


def iter_claude_files(homes: List[Path]) -> Iterator[Path]:
    seen = set()
    for home in homes:
        root = home / "projects"
        if not root.is_dir():
            continue
        for dirpath, _dirs, files in os.walk(str(root)):
            for f in files:
                if f.endswith(".jsonl"):
                    p = Path(dirpath) / f
                    if p not in seen:
                        seen.add(p)
                        yield p


# --------------------------------------------------------------------------- Codex

_CODEX_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens",
                 "reasoning_output_tokens", "total_tokens")


def _codex_usage(u: Any) -> Optional[Dict[str, int]]:
    if not isinstance(u, dict):
        return None
    out = {k: _int(u.get(k)) for k in _CODEX_FIELDS}
    if -1 in out.values():
        return None
    return out


def parse_codex_line(line: str, ctx: Dict[str, Any], file_key: str) -> List[TokenEvent]:
    """One line of ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl.

    `session_meta` / `turn_context` give session id, cwd and the active model.
    `event_msg` with `payload.type == "token_count"` carries `info.total_token_usage`
    (cumulative) and `info.last_token_usage` (the increment). Codex repeats identical
    token_count events, so an event only counts when the cumulative total advanced.
    `cached_input_tokens` is INSIDE `input_tokens`. Newer rollouts also write
    `token_usage_record` lines with the same numbers; we ignore those to avoid doubling.
    """
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return []
    if not isinstance(obj, dict):
        return []
    kind = obj.get("type")
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
    if kind == "session_meta":
        if payload.get("id"):
            ctx["session"] = str(payload["id"])
        if isinstance(payload.get("cwd"), str):
            ctx["cwd"] = payload["cwd"]
        return []
    if kind == "turn_context":
        if payload.get("model"):
            ctx["model"] = str(payload["model"])[:120]
        if isinstance(payload.get("cwd"), str):
            ctx["cwd"] = payload["cwd"]
        return []
    if kind != "event_msg" or payload.get("type") != "token_count":
        return []
    info = payload.get("info") if isinstance(payload.get("info"), dict) else None
    if not info:
        return []  # Codex writes info:null before the first response
    total = _codex_usage(info.get("total_token_usage"))
    last = _codex_usage(info.get("last_token_usage"))
    prev = ctx.get("prev_total")
    if total is not None and prev == total:
        return []  # repeated event, nothing new
    if last is not None and (total is None or prev != total):
        delta = last
    elif total is not None:
        p = prev or {}
        delta = {k: max(0, total[k] - int(p.get(k, 0))) for k in _CODEX_FIELDS}
    else:
        return []
    if total is not None:
        ctx["prev_total"] = total
    ts = _ts(obj.get("timestamp"))
    if ts is None:
        return []
    inp, cached, out = delta["input_tokens"], delta["cached_input_tokens"], delta["output_tokens"]
    if inp == out == 0:
        return []
    session = ctx.get("session") or Path(file_key).stem
    marker = str(total["total_tokens"]) if total else _sha(file_key, line)
    eid = "codex:%s:%s" % (session, marker)
    return [TokenEvent(eid, ctx.get("model") or "unknown", ts, session, inp, out,
                       min(cached, inp), 0, delta["reasoning_output_tokens"], None,
                       source="codex", project=project_name(ctx.get("cwd")))]


def iter_codex_files(homes: List[Path]) -> Iterator[Path]:
    for home in homes:
        seen_rel = set()
        for sub in ("sessions", "archived_sessions"):  # active copy wins over archived
            root = home / sub
            if not root.is_dir():
                continue
            for dirpath, _dirs, files in os.walk(str(root)):
                for f in files:
                    if f.startswith("rollout-") and f.endswith(".jsonl"):
                        p = Path(dirpath) / f
                        if f in seen_rel:
                            continue
                        seen_rel.add(f)
                        yield p


# --------------------------------------------------------------------------- scanner

class JsonlScanner:
    """Incremental read-only JSONL scanner; offsets + parser context live in History."""

    name = ""
    label = ""

    def __init__(self, homes: List[Path], history):
        self.homes = [Path(h) for h in homes]
        self.history = history

    # subclasses
    def files(self) -> Iterator[Path]:  # pragma: no cover - abstract
        raise NotImplementedError

    def parse(self, line: str, ctx: Dict[str, Any], key: str) -> List[TokenEvent]:
        raise NotImplementedError  # pragma: no cover

    def roots(self) -> List[Path]:
        return self.homes

    def available(self) -> Tuple[bool, str]:
        for _p in self.files():
            return True, ""
        return False, "no %s logs found under %s" % (
            self.label, ", ".join(str(r) for r in self.roots()))

    def scan(self, max_bytes: int = 256 * 1024 * 1024) -> Dict[str, int]:
        stats = {"files": 0, "new_events": 0, "errors": 0}
        budget = max_bytes
        for path in self.files():
            stats["files"] += 1
            try:
                st = path.stat()
            except OSError:
                stats["errors"] += 1
                continue
            key = str(path)
            state = self.history.scan_state(key)
            offset, ctx = 0, {}
            if state:
                size, mtime, offset, _ = state
                if st.st_size == size and abs(st.st_mtime - mtime) < 1e-6:
                    continue
                ctx = self.history.scan_ctx(key)
                if st.st_size < offset:  # rewritten -> start over; event ids dedupe
                    offset, ctx = 0, {}
            if budget <= 0:
                break
            events: List[TokenEvent] = []
            try:
                with path.open("rb") as f:
                    f.seek(offset)
                    while budget > 0:
                        raw = f.readline(MAX_LINE_BYTES)
                        if not raw:
                            break
                        if not raw.endswith(b"\n"):
                            if len(raw) >= MAX_LINE_BYTES:
                                offset += len(raw) + len(f.readline())
                                continue
                            break  # partial line still being written
                        offset += len(raw)
                        budget -= len(raw)
                        events.extend(self.parse(raw.decode("utf-8", "replace"), ctx, key))
            except OSError:
                stats["errors"] += 1
                continue
            stats["new_events"] += self.history.add_events(events)
            self.history.set_scan_ctx(key, st.st_size, st.st_mtime, offset, ctx)
        return stats


class ClaudeCodeScanner(JsonlScanner):
    name = "claude_code"
    label = "Claude Code"

    def roots(self):
        return [h / "projects" for h in self.homes]

    def files(self):
        return iter_claude_files(self.homes)

    def parse(self, line, ctx, key):
        return parse_claude_line(line, ctx, key)


class CodexScanner(JsonlScanner):
    name = "codex"
    label = "Codex"

    def roots(self):
        return [h / "sessions" for h in self.homes]

    def files(self):
        return iter_codex_files(self.homes)

    def parse(self, line, ctx, key):
        return parse_codex_line(line, ctx, key)


def scanners(cfg: Dict[str, Any], history, include_grok: bool = True) -> List[Any]:
    """Enabled scanners, Grok first."""
    out: List[Any] = []
    if include_grok:
        out.append(GrokSessionsScanner(config_mod.grok_home(cfg), history))
    extra = config_mod.extra_token_sources(cfg)
    if "claude_code" in extra:
        out.append(ClaudeCodeScanner(config_mod.claude_homes(cfg), history))
    if "codex" in extra:
        out.append(CodexScanner(config_mod.codex_homes(cfg), history))
    return out


def describe(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """For `omnimonitor providers` / doctor / settings: what exists on disk, what's on."""
    extra = config_mod.extra_token_sources(cfg)
    ts = config_mod.effective_token_source(cfg)
    rows = []
    g = GrokSessionsScanner(config_mod.grok_home(cfg), None)
    ok, why = g.available()
    rows.append({"name": "grok_sessions", "label": LABELS["grok_sessions"],
                 "enabled": ts == "grok_sessions", "found": ok,
                 "paths": [str(config_mod.grok_home(cfg) / "sessions")], "note": why})
    for cls, homes in ((ClaudeCodeScanner, config_mod.claude_homes(cfg)),
                       (CodexScanner, config_mod.codex_homes(cfg))):
        sc = cls(homes, None)
        ok, why = sc.available()
        rows.append({"name": sc.name, "label": sc.label, "enabled": sc.name in extra,
                     "found": ok, "paths": [str(r) for r in sc.roots()], "note": why})
    return rows
