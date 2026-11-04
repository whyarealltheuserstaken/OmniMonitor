"""Local-log adapters added in 0.4 (batch 1b): Pi, OpenClaw, Goose, Hermes, Amp,
Factory Droid, Cline / Roo Code / Kilo Code.

Read-only and opt-in. Formats were checked against each tool's own source or developer
docs (links in docs/providers.md); records we don't recognise are skipped, never guessed.
SQLite stores are opened with ``mode=ro`` and never written.

Normalisation (same as everywhere):
    input_tokens  = full prompt INCLUDING cache reads/writes
    output_tokens = full output INCLUDING reasoning/thinking
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, List, Optional

from omnimonitor.adapters import _sha, _ts
from omnimonitor.local_adapters import WholeFileScanner, _event, _json_lines, _read_json, _walk
from omnimonitor.tokens import TokenEvent, _int, project_name


def _n(v: Any) -> int:
    """Non-negative int or 0 for missing; -1 (invalid) propagates to _event's guard."""
    return 0 if v is None else _int(v)


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)


def _cols(con: sqlite3.Connection, table: str) -> set:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}


# ================================================================ Pi / OpenClaw

def parse_pi_entries(entries, source: str, file_key: str, openclaw: bool = False
                     ) -> List[TokenEvent]:
    """Pi session JSONL entries (pi-mono coding-agent session format).

    ``{type:"session", id, cwd}`` header; ``{type:"message", id, timestamp,
    message:{role:"assistant", model, usage:{input, output, cacheRead, cacheWrite,
    cost:{total}}}}``. ``input`` excludes cache; reasoning is inside ``output``.
    Forked sessions copy the parent's entries, so the entry id is the dedup key."""
    sid, cwd, model = Path(file_key).stem, "", "unknown"
    out: List[TokenEvent] = []
    for _n_, e in entries:
        t = e.get("type")
        if t == "session":
            sid = str(e.get("id") or sid)
            cwd = str(e.get("cwd") or "")
            continue
        if t == "model_change" and isinstance(e.get("modelId"), str):
            model = e["modelId"]
            continue
        if t != "message":
            continue
        m = e.get("message") if isinstance(e.get("message"), dict) else {}
        if m.get("role") != "assistant" or not isinstance(m.get("usage"), dict):
            continue
        if openclaw and (m.get("provider") == "openclaw" or m.get("model") == "delivery-mirror"):
            continue  # OpenClaw mirrors delivered replies; they carry no new usage
        u = m["usage"]
        cr, cw = _n(u.get("cacheRead")), _n(u.get("cacheWrite"))
        if "input" in u or "output" in u:
            inp, outp = _n(u.get("input")), _n(u.get("output"))
            gross = -1 if -1 in (inp, cr, cw) else inp + cr + cw
        elif "prompt_tokens" in u:  # OpenAI-style usage (seen in some OpenClaw builds)
            p = _n(u.get("prompt_tokens"))
            det = u.get("prompt_tokens_details") if isinstance(u.get("prompt_tokens_details"),
                                                               dict) else {}
            cr = cr or _n(det.get("cached_tokens"))
            gross, outp = p, _n(u.get("completion_tokens"))
        else:
            continue
        cost = u.get("cost", {}).get("total") if isinstance(u.get("cost"), dict) else None
        cost = float(cost) if isinstance(cost, (int, float)) and cost >= 0 else None
        eid = f"{source}:" + str(e.get("id") or _sha(file_key, str(_n_)))
        out += _event(eid, m.get("model") or model, _ts(e.get("timestamp") or m.get("timestamp")),
                      sid, gross, outp, cr, cw, 0, source,
                      project_name(cwd) if cwd else "", cost)
    return out


class PiScanner(WholeFileScanner):
    """``~/.pi/agent/sessions/**/*.jsonl`` (also ``$PI_CODING_AGENT_DIR``/sessions,
    ``$PI_CODING_AGENT_SESSION_DIR``, and the ``~/.omp`` fork)."""

    name = "pi"
    label = "Pi coding agent"
    source_id = "pi"

    def files(self):
        return _walk(self.homes, lambda n: n.endswith(".jsonl"))

    def parse_file(self, path: Path) -> List[TokenEvent]:
        return parse_pi_entries(_json_lines(path), self.source_id, str(path),
                                openclaw=self.source_id == "openclaw")


class OpenClawScanner(PiScanner):
    """``~/.openclaw/agents/**/*.jsonl`` (Pi-format transcripts; legacy ``~/.clawdbot``,
    ``~/.moltbot``)."""

    name = "openclaw"
    label = "OpenClaw"
    source_id = "openclaw"


# ================================================================ Goose

class GooseScanner(WholeFileScanner):
    """Goose ``sessions/sessions.db`` (block/goose ``session_manager.rs``).

    One row per session with running totals; we emit one event per session dated at
    ``created_at`` and upsert it as the totals grow. ``accumulated_*`` columns are
    preferred (they survive context compaction); plain columns are the fallback.
    Goose's ``input_tokens`` is the gross prompt, so cache is not added on top."""

    name = "goose"
    label = "Goose"
    max_file_bytes = 2 * 1024 ** 3

    def files(self):
        return _walk(self.homes, lambda n: n == "sessions.db")

    def parse_file(self, path: Path) -> List[TokenEvent]:
        con = _ro(path)
        try:
            cols = _cols(con, "sessions")
            if "id" not in cols:
                return []

            def pick(name):
                for c in ("accumulated_" + name, name):
                    if c in cols:
                        return f"COALESCE({c}, 0)"
                return "0"
            sel = ", ".join([
                "id",
                "working_dir" if "working_dir" in cols else "''",
                "created_at" if "created_at" in cols else "NULL",
                "updated_at" if "updated_at" in cols else "NULL",
                pick("input_tokens"), pick("output_tokens"),
                pick("cache_read_tokens"), pick("cache_write_tokens"),
                ("accumulated_cost" if "accumulated_cost" in cols else "NULL"),
                ("model_config_json" if "model_config_json" in cols else "NULL"),
            ])
            rows = con.execute(f"SELECT {sel} FROM sessions").fetchall()
        finally:
            con.close()
        out: List[TokenEvent] = []
        for sid, wd, created, updated, inp, outp, cr, cw, cost, mcj in rows:
            model = "unknown"
            try:
                mc = json.loads(mcj) if mcj else {}
                if isinstance(mc, dict):
                    model = mc.get("model_name") or mc.get("model") or model
            except ValueError:
                pass
            cost = float(cost) if isinstance(cost, (int, float)) and cost >= 0 else None
            out += _event("goose:" + str(sid), model, _ts(created) or _ts(updated), str(sid),
                          _n(inp), _n(outp), _n(cr), _n(cw), 0, self.name,
                          project_name(wd) if wd else "", cost)
        return out


# ================================================================ Hermes

class HermesScanner(WholeFileScanner):
    """Hermes Agent ``state.db`` (documented in the Hermes developer guide,
    "Session Storage"). Table ``sessions`` holds per-session totals: input_tokens,
    output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens,
    estimated/actual_cost_usd. ``input_tokens`` is the non-cached prompt (cache is stored
    separately), so input = input + cache read + cache write. Reasoning is stored
    separately from output and added to it. Named profiles live in
    ``profiles/<name>/state.db``; all are read."""

    name = "hermes"
    label = "Hermes Agent"
    max_file_bytes = 4 * 1024 ** 3

    def files(self):
        out = []
        for h in self.homes:
            if (h / "state.db").is_file():
                out.append(h / "state.db")
            prof = h / "profiles"
            if prof.is_dir():
                out += sorted(p / "state.db" for p in prof.iterdir() if (p / "state.db").is_file())
            if h.is_file() and h.name.endswith(".db"):
                out.append(h)
        return out

    def parse_file(self, path: Path) -> List[TokenEvent]:
        con = _ro(path)
        try:
            cols = _cols(con, "sessions")
            if not {"id", "input_tokens", "output_tokens"} <= cols:
                return []

            def c(name, default="0"):
                return f"COALESCE({name}, 0)" if name in cols else default
            cost = ("COALESCE(actual_cost_usd, estimated_cost_usd)"
                    if {"actual_cost_usd", "estimated_cost_usd"} <= cols else "NULL")
            rows = con.execute(
                f"SELECT id, {'model' if 'model' in cols else 'NULL'}, "
                f"{'started_at' if 'started_at' in cols else 'NULL'}, "
                f"{'cwd' if 'cwd' in cols else 'NULL'}, input_tokens, output_tokens, "
                f"{c('cache_read_tokens')}, {c('cache_write_tokens')}, "
                f"{c('reasoning_tokens')}, {cost} FROM sessions").fetchall()
        finally:
            con.close()
        out: List[TokenEvent] = []
        for sid, model, started, cwd, inp, outp, cr, cw, rs, cost in rows:
            inp, outp, cr, cw, rs = (_n(x) for x in (inp, outp, cr, cw, rs))
            if -1 in (inp, outp, cr, cw, rs):
                continue
            cost = float(cost) if isinstance(cost, (int, float)) and cost >= 0 else None
            out += _event("hermes:" + _sha(str(path.parent), str(sid)), model, _ts(started),
                          str(sid), inp + cr + cw, outp + rs, cr, cw, rs, self.name,
                          project_name(cwd) if cwd else "", cost)
        return out


# ================================================================ Amp (experimental)

class AmpScanner(WholeFileScanner):
    """Amp thread files ``~/.local/share/amp/threads/T-*.json`` (experimental).

    ``usageLedger.events[]`` = ``{id, timestamp, model, tokens:{input, output},
    toMessageId}``; cache tokens come from the matching message's ``usage``
    (``cacheReadInputTokens``, ``cacheCreationInputTokens``). Ledger "credits" are not
    dollars and are not shown as cost. Without a ledger, assistant ``usage`` is used."""

    name = "amp"
    label = "Amp"

    def files(self):
        return _walk(self.homes, lambda n: n.endswith(".json"))

    def parse_file(self, path: Path) -> List[TokenEvent]:
        doc = _read_json(path)
        if not isinstance(doc, dict):
            return []
        tid = str(doc.get("id") or path.stem)
        msgs = [m for m in doc.get("messages") or [] if isinstance(m, dict)]
        by_id = {}
        for i, m in enumerate(msgs):
            mid = (m.get("meta") or {}).get("messageId") if isinstance(m.get("meta"), dict) else None
            by_id[str(m.get("messageId", mid if mid is not None else i))] = m
        ledger = doc.get("usageLedger") if isinstance(doc.get("usageLedger"), dict) else {}
        events = [e for e in ledger.get("events") or [] if isinstance(e, dict)]
        out: List[TokenEvent] = []
        if events:
            for e in events:
                tok = e.get("tokens") if isinstance(e.get("tokens"), dict) else {}
                mu = (by_id.get(str(e.get("toMessageId"))) or {}).get("usage") or {}
                cr, cw = _n(mu.get("cacheReadInputTokens")), _n(mu.get("cacheCreationInputTokens"))
                inp = _n(tok.get("input"))
                gross = -1 if -1 in (inp, cr, cw) else inp + cr + cw
                out += _event("amp:" + str(e.get("id") or _sha(tid, json.dumps(e, sort_keys=True))),
                              e.get("model"), _ts(e.get("timestamp")), tid, gross,
                              _n(tok.get("output")), cr, cw, 0, self.name)
            return out
        for i, m in enumerate(msgs):
            u = m.get("usage")
            if m.get("role") != "assistant" or not isinstance(u, dict):
                continue
            cr, cw = _n(u.get("cacheReadInputTokens")), _n(u.get("cacheCreationInputTokens"))
            inp = _n(u.get("inputTokens"))
            gross = -1 if -1 in (inp, cr, cw) else inp + cr + cw
            out += _event(f"amp:{tid}:{i}", u.get("model"), _ts(u.get("timestamp")) or
                          _ts(doc.get("created")), tid, gross, _n(u.get("outputTokens")),
                          cr, cw, 0, self.name)
        return out


# ================================================================ Factory Droid (experimental)

class DroidScanner(WholeFileScanner):
    """Factory Droid ``~/.factory/sessions/**/<id>.settings.json`` (experimental).

    ``tokenUsage`` holds session totals {inputTokens, outputTokens, thinkingTokens,
    cacheReadTokens, cacheCreationTokens}; one event per session, upserted. The sibling
    ``<id>.jsonl`` ``session_start`` line gives the working directory."""

    name = "droid"
    label = "Factory Droid"

    def files(self):
        return _walk(self.homes, lambda n: n.endswith(".settings.json"))

    def parse_file(self, path: Path) -> List[TokenEvent]:
        doc = _read_json(path)
        if not isinstance(doc, dict) or not isinstance(doc.get("tokenUsage"), dict):
            return []
        sid = path.name[: -len(".settings.json")]
        u = doc["tokenUsage"]
        inp, outp = _n(u.get("inputTokens")), _n(u.get("outputTokens"))
        th, cr, cw = (_n(u.get(k)) for k in ("thinkingTokens", "cacheReadTokens",
                                              "cacheCreationTokens"))
        if -1 in (inp, outp, th, cr, cw):
            return []
        cwd = ""
        side = path.with_name(sid + ".jsonl")
        if side.is_file():
            try:
                for _i, e in _json_lines(side):
                    if e.get("type") == "session_start":
                        cwd = str(e.get("cwd") or "")
                        break
            except OSError:
                pass
        ts = _ts(doc.get("providerLockTimestamp")) or path.stat().st_mtime
        return _event("droid:" + sid, doc.get("model"), ts, sid, inp + cr + cw, outp + th,
                      cr, cw, th, self.name, project_name(cwd) if cwd else "")


# ================================================================ Cline / Roo / Kilo

_EXT = {"cline": "saoudrizwan.claude-dev", "roo": "rooveterinaryinc.roo-cline",
        "kilo": "kilocode.kilo-code"}
_MODEL_RE = re.compile(r"<model>\s*([^<\s][^<]{0,118}?)\s*</model>")


def _task_model(task_dir: Path) -> str:
    p = task_dir / "api_conversation_history.json"
    try:
        if p.is_file() and p.stat().st_size < 64 * 1024 * 1024:
            found = _MODEL_RE.findall(p.read_text("utf-8", "replace"))
            if found:
                return found[-1]
    except OSError:
        pass
    return "unknown"


class ClineFamilyScanner(WholeFileScanner):
    """``<editor>/User/globalStorage/<extension>/tasks/<id>/ui_messages.json``
    (experimental). Usage is on ``say == "api_req_started"`` entries whose ``text`` is a
    JSON string ``{tokensIn, tokensOut, cacheReads, cacheWrites, cost}``; ``ts`` is ms.
    Cline reports ``tokensIn`` without cache; Roo/Kilo builds may include it, so cache is
    only added when ``tokensIn`` is smaller than the cache total. The model is read from
    the ``<model>`` tag in the task's environment details and may be missing."""

    ext = ""

    def files(self):
        roots = [h / self.ext / "tasks" for h in self.homes]
        return _walk(roots, lambda n: n == "ui_messages.json")

    def parse_file(self, path: Path) -> List[TokenEvent]:
        doc = _read_json(path)
        if not isinstance(doc, list):
            return []
        tid = path.parent.name
        model: Optional[str] = None
        out: List[TokenEvent] = []
        for m in doc:
            if not isinstance(m, dict) or m.get("say") != "api_req_started":
                continue
            try:
                info = json.loads(m.get("text") or "")
            except (TypeError, ValueError):
                continue
            if not isinstance(info, dict):
                continue
            ti, to = _n(info.get("tokensIn")), _n(info.get("tokensOut"))
            cr, cw = _n(info.get("cacheReads")), _n(info.get("cacheWrites"))
            if -1 in (ti, to, cr, cw):
                continue
            gross = ti + cr + cw if (self.name == "cline" or ti < cr + cw) else ti
            if model is None:
                model = _task_model(path.parent)
            ts = m.get("ts")
            ts = ts / 1000.0 if isinstance(ts, (int, float)) and ts > 1e11 else _ts(ts)
            cost = info.get("cost")
            cost = float(cost) if isinstance(cost, (int, float)) and cost >= 0 else None
            out += _event(f"{self.name}:{tid}:{m.get('ts')}", model, ts, tid, gross, to,
                          cr, cw, 0, self.name, "", cost)
        return out


class ClineScanner(ClineFamilyScanner):
    name, label, ext = "cline", "Cline", _EXT["cline"]


class RooScanner(ClineFamilyScanner):
    name, label, ext = "roo_code", "Roo Code", _EXT["roo"]


class KiloScanner(ClineFamilyScanner):
    name, label, ext = "kilo_code", "Kilo Code", _EXT["kilo"]


