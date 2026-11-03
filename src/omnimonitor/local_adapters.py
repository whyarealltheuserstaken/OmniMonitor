"""Local-log adapters added in 0.3.0: Gemini CLI, Qwen Code, Kimi (CLI / Code / Work),
opencode and the GitHub Copilot CLI OpenTelemetry file export.

All are read-only and opt-in. Formats were learned from the tools' own open-source code
and public docs (see docs/providers.md); no code was copied from other monitors.
Same normalisation as adapters.py:
    input_tokens  = full prompt INCLUDING cache reads/writes (and tool-use prompt tokens)
    output_tokens = full output INCLUDING reasoning/thoughts
Records we do not recognise are skipped, never guessed.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from omnimonitor.adapters import JsonlScanner, _sha, _ts
from omnimonitor.tokens import MAX_LINE_BYTES, TokenEvent, _int, project_name

MAX_JSON_FILE_BYTES = 64 * 1024 * 1024


def _walk(roots: Iterable[Path], accept) -> Iterator[Path]:
    seen = set()
    for root in roots:
        root = Path(root)
        if root.is_file():
            if accept(root.name) and root not in seen:
                seen.add(root)
                yield root
            continue
        if not root.is_dir():
            continue
        for dirpath, _dirs, files in os.walk(str(root)):
            for f in sorted(files):
                if accept(f):
                    p = Path(dirpath) / f
                    if p not in seen:
                        seen.add(p)
                        yield p


def _event(eid: str, model: Any, ts: Optional[float], session: str, inp: int, out: int,
           cr: int, cw: int, reasoning: int, source: str, project: str = "",
           cost: Optional[float] = None) -> List[TokenEvent]:
    if ts is None or -1 in (inp, out, cr, cw, reasoning):
        return []
    if inp == 0 and out == 0:
        return []
    m = str(model or "unknown").strip()[:120] or "unknown"
    return [TokenEvent(eid, m, ts, session[:200], inp, out, min(cr, inp), min(cw, inp),
                       min(reasoning, out), cost, source=source, project=project)]


# ================================================================ whole-file base

class WholeFileScanner(JsonlScanner):
    """For files that are rewritten in place (JSON documents, updated records).

    When a file's size or mtime changes it is parsed again from the start and the events
    are UPSERTED (same id -> latest numbers win), so rewrites never double count."""

    max_file_bytes = MAX_JSON_FILE_BYTES

    def fingerprint(self, path: Path) -> Tuple[int, float]:
        st = path.stat()
        return st.st_size, st.st_mtime

    def parse_file(self, path: Path) -> List[TokenEvent]:  # pragma: no cover - abstract
        raise NotImplementedError

    def scan(self, max_bytes: int = 256 * 1024 * 1024) -> Dict[str, int]:
        stats = {"files": 0, "new_events": 0, "errors": 0}
        budget = max_bytes
        for path in self.files():
            stats["files"] += 1
            try:
                size, mtime = self.fingerprint(path)
            except OSError:
                stats["errors"] += 1
                continue
            key = str(path)
            state = self.history.scan_state(key)
            if state and size == state[0] and abs(mtime - state[1]) < 1e-6:
                continue
            if size > self.max_file_bytes or budget <= 0:
                stats["errors"] += size > self.max_file_bytes
                continue
            budget -= size
            try:
                events = self.parse_file(path)
            except (OSError, ValueError, sqlite3.Error):
                stats["errors"] += 1
                continue
            stats["new_events"] += self.history.upsert_events(events)
            self.history.set_scan_ctx(key, size, mtime, size, {})
        return stats


def _read_json(path: Path) -> Any:
    with path.open("rb") as f:
        raw = f.read(MAX_JSON_FILE_BYTES + 1)
    if len(raw) > MAX_JSON_FILE_BYTES:
        raise ValueError("file too large")
    return json.loads(raw.decode("utf-8", "replace"))


def _json_lines(path: Path) -> Iterator[Tuple[int, Dict[str, Any]]]:
    with path.open("rb") as f:
        n = 0
        while True:
            raw = f.readline(MAX_LINE_BYTES)
            if not raw:
                return
            n += 1
            if not raw.endswith(b"\n") and len(raw) >= MAX_LINE_BYTES:
                f.readline()
                continue
            try:
                obj = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(obj, dict):
                yield n, obj


# ================================================================ Gemini CLI

def gemini_tokens(t: Any) -> Optional[Tuple[int, int, int, int]]:
    """Gemini CLI ``tokens`` -> (input, output, cached, thoughts).

    ``input`` is the prompt count (Gemini's promptTokenCount already includes cached
    tokens), ``tool`` is the tool-use prompt, ``thoughts`` is reasoning output."""
    if not isinstance(t, dict):
        return None
    inp, out = _int(t.get("input")), _int(t.get("output"))
    cached, thoughts, tool = _int(t.get("cached")), _int(t.get("thoughts")), _int(t.get("tool"))
    if -1 in (inp, out, cached, thoughts, tool):
        return None
    return inp + tool, out + thoughts, cached, thoughts


def _gemini_message(msg: Dict[str, Any], session: str, project: str,
                    key: str) -> List[TokenEvent]:
    if msg.get("type") != "gemini":
        return []
    tok = gemini_tokens(msg.get("tokens"))
    if tok is None:
        return []
    inp, out, cached, thoughts = tok
    mid = msg.get("id")
    eid = "gemini:%s:%s" % (session, mid) if mid else "gemini:sha1:" + _sha(key, json.dumps(
        msg, sort_keys=True)[:4096])
    return _event(eid, msg.get("model"), _ts(msg.get("timestamp")), session, inp, out,
                  cached, 0, thoughts, "gemini_cli", project)


def parse_gemini_file(path: Path) -> List[TokenEvent]:
    """``<GEMINI_CLI_HOME>/tmp/<project-hash>/chats/session-*.json`` (one JSON document with
    ``messages``) or the newer ``*.jsonl`` form (a header record, then one message record
    per line; a message may be re-written later with final counts - the last one wins)."""
    key = str(path)
    project = ""  # the folder is a hash of the project path: not a readable name
    session = path.stem
    out: Dict[str, TokenEvent] = {}
    if path.suffix == ".json":
        doc = _read_json(path)
        if not isinstance(doc, dict) or not isinstance(doc.get("messages"), list):
            return []
        session = str(doc.get("sessionId") or session)
        for m in doc["messages"]:
            if isinstance(m, dict):
                for e in _gemini_message(m, session, project, key):
                    out[e.event_id] = e
        return list(out.values())
    for _n, obj in _json_lines(path):
        if obj.get("sessionId") and "messages" not in obj and obj.get("type") != "gemini":
            session = str(obj["sessionId"])
            continue
        if isinstance(obj.get("messages"), list):  # a whole session written as one line
            session = str(obj.get("sessionId") or session)
            for m in obj["messages"]:
                if isinstance(m, dict):
                    for e in _gemini_message(m, session, project, key):
                        out[e.event_id] = e
            continue
        for e in _gemini_message(obj, session, project, key):
            out[e.event_id] = e
    return list(out.values())


class GeminiCliScanner(WholeFileScanner):
    name = "gemini_cli"
    label = "Gemini CLI"

    def roots(self):
        return [h / "tmp" for h in self.homes]

    def files(self):
        for p in _walk(self.roots(), lambda f: f.endswith((".json", ".jsonl"))):
            if p.parent.name == "chats":
                yield p

    def parse_file(self, path):
        return parse_gemini_file(path)


# ================================================================ Qwen Code

def parse_qwen_line(line: str, ctx: Dict[str, Any], file_key: str) -> List[TokenEvent]:
    """``~/.qwen/projects/<project>/chats/<session>.jsonl``: assistant records carry Gemini-
    style ``usageMetadata``. promptTokenCount includes cached tokens. Some versions count
    thoughts inside candidatesTokenCount (then total == prompt + candidates)."""
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return []
    if not isinstance(obj, dict):
        return []
    um = obj.get("usageMetadata")
    if not isinstance(um, dict):
        return []
    prompt, cand = _int(um.get("promptTokenCount")), _int(um.get("candidatesTokenCount"))
    thoughts, cached = _int(um.get("thoughtsTokenCount")), _int(um.get("cachedContentTokenCount"))
    total = um.get("totalTokenCount")
    if -1 in (prompt, cand, thoughts, cached):
        return []
    inside = thoughts > 0 and isinstance(total, (int, float)) and not isinstance(total, bool) \
        and total > 0 and int(total) == prompt + cand
    out = cand if inside else cand + thoughts
    path = Path(file_key)
    project = path.parent.parent.name if path.parent.name == "chats" else ""
    session = str(obj.get("sessionId") or path.stem)
    eid = "qwen:%s:%s" % (session, obj.get("uuid")) if obj.get("uuid") else \
        "qwen:sha1:" + _sha(file_key, line)
    return _event(eid, obj.get("model"), _ts(obj.get("timestamp")), session, prompt, out,
                  cached, 0, thoughts, "qwen_code", project_name(obj.get("cwd")) or project)


class QwenCodeScanner(JsonlScanner):
    name = "qwen_code"
    label = "Qwen Code"

    def roots(self):
        return [h / "projects" for h in self.homes]

    def files(self):
        for p in _walk(self.roots(), lambda f: f.endswith(".jsonl")):
            if p.parent.name == "chats":
                yield p

    def parse(self, line, ctx, key):
        return parse_qwen_line(line, ctx, key)


# ================================================================ Kimi

_KIMI_PLACEHOLDERS = ("__runtime_model__", "__kimi_env_model__")


def _kimi_model(v: Any) -> Optional[str]:
    if not isinstance(v, str):
        return None
    m = v.strip()
    if m.startswith("kimi-code/"):
        m = m[len("kimi-code/"):].strip()
    if not m or (m.startswith("__") and m.endswith("__")):
        return None
    return m[:120]


def _kimi_usage(u: Any) -> Optional[Tuple[int, int, int, int]]:
    """(input incl. cache, output, cache_read, cache_write) from snake or camel case keys."""
    if not isinstance(u, dict):
        return None
    def g(a, b):
        return _int(u.get(a) if a in u else u.get(b))
    other, out = g("input_other", "inputOther"), _int(u.get("output"))
    cr, cw = g("input_cache_read", "inputCacheRead"), g("input_cache_creation", "inputCacheCreation")
    if -1 in (other, out, cr, cw):
        return None
    return other + cr + cw, out, cr, cw


def _kimi_default_model(file_key: str) -> str:
    """Legacy Kimi CLI keeps the model in <home>/config.json (sessions/<grp>/<id>/wire.jsonl)."""
    try:
        cfg = Path(file_key).parent.parent.parent.parent / "config.json"
        if cfg.is_file() and cfg.stat().st_size < 1024 * 1024:
            m = _kimi_model(json.loads(cfg.read_text("utf-8", "replace")).get("model"))
            if m:
                return m
    except (OSError, ValueError, AttributeError):
        pass
    return "unknown"


def parse_kimi_line(line: str, ctx: Dict[str, Any], file_key: str) -> List[TokenEvent]:
    """``wire.jsonl`` from Kimi CLI (``StatusUpdate`` messages with ``token_usage``; the same
    message_id can be re-sent with growing counts - the scanner upserts, last wins) and Kimi
    Code / Kimi Work (``usage.record`` lines; only ``usageScope == "turn"`` counts, the model
    may come from the preceding ``llm.request``)."""
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return []
    if not isinstance(obj, dict):
        return []
    session = Path(file_key).parent.name or "unknown"
    t = obj.get("type")
    if t == "llm.request":
        m = _kimi_model(obj.get("model"))
        if m:
            ctx["model"] = m
        return []
    if t == "usage.record":
        if obj.get("usageScope") != "turn":
            return []
        u = _kimi_usage(obj.get("usage"))
        if u is None:
            return []
        model = _kimi_model(obj.get("model")) or ctx.get("model") or "unknown"
        tm = obj.get("time")
        ts = tm / 1000.0 if isinstance(tm, (int, float)) and not isinstance(tm, bool) and tm > 0 \
            else None
        eid = "kimi:%s:%s" % (session, _sha(file_key, line)[:16])
        return _event(eid, model, ts, session, u[0], u[1], u[2], u[3], 0, "kimi")
    msg = obj.get("message")
    if not isinstance(msg, dict) or msg.get("type") != "StatusUpdate":
        return []
    payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
    u = _kimi_usage(payload.get("token_usage"))
    if u is None:
        return []
    if "default_model" not in ctx:
        ctx["default_model"] = _kimi_default_model(file_key)
    mid = payload.get("message_id")
    eid = "kimi:%s:%s" % (session, mid) if mid else "kimi:sha1:" + _sha(file_key, line)
    return _event(eid, ctx["default_model"], _ts(obj.get("timestamp")), session,
                  u[0], u[1], u[2], u[3], 0, "kimi")


class KimiScanner(JsonlScanner):
    name = "kimi"
    label = "Kimi"
    replace = True  # StatusUpdate for one message can repeat with larger counts

    def roots(self):
        return self.homes

    def files(self):
        return _walk(self.homes, lambda f: f == "wire.jsonl")

    def parse(self, line, ctx, key):
        return parse_kimi_line(line, ctx, key)


# ================================================================ opencode

def _opencode_model(d: Dict[str, Any]) -> Any:
    if d.get("modelID"):
        return d["modelID"]
    m = d.get("model")
    if isinstance(m, dict):
        return m.get("modelID") or m.get("id")
    return m


def opencode_message(d: Any, session: str = "", fallback_id: str = "") -> List[TokenEvent]:
    """An opencode assistant message: ``tokens{input, output, reasoning, cache{read, write}}``
    (input EXCLUDES cache), ``cost`` in USD, ``time.created`` in ms, ``path.cwd``."""
    if not isinstance(d, dict) or d.get("role", "assistant") != "assistant":
        return []
    t = d.get("tokens")
    if not isinstance(t, dict):
        return []
    cache = t.get("cache") if isinstance(t.get("cache"), dict) else {}
    inp, out, rs = _int(t.get("input")), _int(t.get("output")), _int(t.get("reasoning"))
    cr, cw = _int(cache.get("read")), _int(cache.get("write"))
    if -1 in (inp, out, rs, cr, cw):
        return []
    tm = d.get("time") if isinstance(d.get("time"), dict) else {}
    ts = _ts(tm.get("completed") or tm.get("created"))
    cost = d.get("cost")
    cost = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) \
        and 0 < cost < 1e6 else None
    mid = str(d.get("id") or fallback_id)
    if not mid:
        return []
    sess = str(d.get("sessionID") or session or "unknown")
    path = d.get("path") if isinstance(d.get("path"), dict) else {}
    return _event("opencode:" + mid, _opencode_model(d), ts, sess, inp + cr + cw, out + rs,
                  cr, cw, rs, "opencode", project_name(path.get("cwd") or path.get("root")),
                  cost)


def _is_opencode_db(name: str) -> bool:
    if not name.endswith(".db"):
        return False
    stem = name[:-3]
    return stem == "opencode" or (stem.startswith("opencode-") and len(stem) > 9)


def parse_opencode_db(path: Path) -> List[TokenEvent]:
    """Opened READ-ONLY. Tries ``message(id, session_id, data)`` and the newer
    ``session_message(id, session_id, type, data)``; anything else is ignored."""
    uri = "file:%s?mode=ro" % path.resolve().as_posix()
    con = sqlite3.connect(uri, uri=True, timeout=2)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        out: List[TokenEvent] = []
        for table in ("message", "session_message"):
            if table not in tables:
                continue
            for mid, sid, data in con.execute(
                    "SELECT id, session_id, data FROM %s" % table):  # noqa: S608 - fixed names
                try:
                    d = json.loads(data) if isinstance(data, str) else None
                except ValueError:
                    continue
                if isinstance(d, dict) and "role" not in d and table == "session_message":
                    d = dict(d, role="assistant") if "tokens" in d else d
                out.extend(opencode_message(d, str(sid or ""), str(mid or "")))
        return out
    finally:
        con.close()


class OpencodeScanner(WholeFileScanner):
    name = "opencode"
    label = "opencode"

    def roots(self):
        return self.homes

    def files(self):
        for home in self.homes:
            msg = home / "storage" / "message"
            yield from _walk([msg], lambda f: f.endswith(".json"))
            if home.is_dir():
                for p in sorted(home.iterdir()):
                    if p.is_file() and _is_opencode_db(p.name):
                        yield p

    max_file_bytes = 2 * 1024 * 1024 * 1024  # SQLite rows are read one at a time

    def fingerprint(self, path):
        size, mtime = super().fingerprint(path)
        if path.suffix == ".db":  # writes land in the -wal file first
            wal = path.with_name(path.name + "-wal")
            try:
                st = wal.stat()
                size, mtime = size + st.st_size, max(mtime, st.st_mtime)
            except OSError:
                pass
        return size, mtime

    def parse_file(self, path):
        if path.suffix == ".db":
            return parse_opencode_db(path)
        return opencode_message(_read_json(path), "", path.stem)


# ================================================================ Copilot OpenTelemetry

_CR = ("gen_ai.usage.cache_read.input_tokens", "gen_ai.usage.cache_read_input_tokens")
_CW = ("gen_ai.usage.cache_creation.input_tokens", "gen_ai.usage.cache_write.input_tokens",
       "gen_ai.usage.cache_creation_input_tokens", "gen_ai.usage.cache_write_input_tokens")
_RS = ("gen_ai.usage.reasoning.output_tokens", "gen_ai.usage.reasoning_tokens")


def _otel_attrs(v: Any) -> Dict[str, Any]:
    """Flat dict, or the OTLP list form ``[{"key": k, "value": {"intValue": "3"}}]``."""
    if isinstance(v, dict):
        return v
    out: Dict[str, Any] = {}
    if isinstance(v, list):
        for kv in v:
            if isinstance(kv, dict) and isinstance(kv.get("key"), str):
                val = kv.get("value")
                if isinstance(val, dict) and val:
                    val = next(iter(val.values()))
                out[kv["key"]] = val
    return out


def _first(attrs: Dict[str, Any], keys: Tuple[str, ...]) -> int:
    for k in keys:
        if k in attrs:
            return _int(attrs[k])
    return 0


def _otel_time(v: Any) -> Optional[float]:
    if isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v):
        return float(v[0]) + float(v[1]) / 1e9 if v[0] > 0 else None
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 1e15:  # unix nanos
        return float(v) / 1e9
    if isinstance(v, str) and v.isdigit() and len(v) > 15:
        return int(v) / 1e9
    return _ts(v)


def parse_otel_line(line: str, ctx: Dict[str, Any], file_key: str) -> List[TokenEvent]:
    """One span per line (Copilot CLI file exporter). Only model-call spans (``chat`` /
    ``text_completion`` / ``generate_content``) are counted; ``invoke_agent`` spans are
    aggregates of their children and would double count. Per the GenAI semantic
    conventions ``input_tokens`` already includes cached input."""
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return []
    if not isinstance(obj, dict):
        return []
    attrs = _otel_attrs(obj.get("attributes"))
    op = attrs.get("gen_ai.operation.name")
    if op not in ("chat", "text_completion", "generate_content"):
        return []
    inp = _int(attrs.get("gen_ai.usage.input_tokens"))
    out = _int(attrs.get("gen_ai.usage.output_tokens"))
    cr, cw, rs = _first(attrs, _CR), _first(attrs, _CW), _first(attrs, _RS)
    model = attrs.get("gen_ai.response.model") or attrs.get("gen_ai.request.model")
    name = obj.get("name")
    if not model and isinstance(name, str) and name.startswith(op + " "):
        model = name[len(op) + 1:]
    sc = obj.get("spanContext") if isinstance(obj.get("spanContext"), dict) else {}
    trace = obj.get("traceId") or sc.get("traceId")
    span = obj.get("spanId") or sc.get("spanId")
    eid = "copilot:%s:%s" % (trace, span) if trace and span else "copilot:sha1:" + _sha(
        file_key, line)
    session = str(attrs.get("gen_ai.conversation.id") or trace or Path(file_key).stem)
    ts = _otel_time(obj.get("endTime") or obj.get("endTimeUnixNano") or obj.get("startTime"))
    return _event(eid, model, ts, session, inp, out, cr, cw, rs, "copilot_otel")


class CopilotOtelScanner(JsonlScanner):
    name = "copilot_otel"
    label = "Copilot CLI"

    def files(self):
        return _walk(self.homes, lambda f: f.endswith((".jsonl", ".json")))

    def parse(self, line, ctx, key):
        return parse_otel_line(line, ctx, key)
