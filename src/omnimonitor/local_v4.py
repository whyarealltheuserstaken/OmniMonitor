"""Local-log adapters added in 0.4: Grok unified.jsonl, Antigravity, DeepSeek Harness.

Same rules as local_adapters.py: read-only, opt-in, formats learned from public docs and
from inspecting the tools' own files (see docs/providers.md); records we don't
recognise are skipped and never guessed. Everything here is marked *experimental*:
the formats are undocumented by the vendors and may change.

Normalisation (same as everywhere):
    input_tokens  = full prompt INCLUDING cache reads/writes
    output_tokens = full output INCLUDING reasoning/thinking
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from omnimonitor.adapters import JsonlScanner, _sha, _ts
from omnimonitor.local_adapters import WholeFileScanner, _event, _walk
from omnimonitor.tokens import MAX_LINE_BYTES, TokenEvent, _int, project_name

# ================================================================ Grok unified.jsonl

class GrokUnifiedScanner(JsonlScanner):
    """``~/.grok/logs/unified.jsonl`` (experimental).

    Lines are ``{ts, sid, msg, ctx}``. ``msg == "shell.turn.inference_done"`` carries
    ``ctx.prompt_tokens`` (gross, includes cached), ``cached_prompt_tokens``,
    ``completion_tokens`` and ``reasoning_tokens``. The model is not on those lines; it is
    taken from the latest ``ctx.model`` seen for the same session. Builds before roughly
    June 2026 log no token counts, so there is nothing to read from them.
    Reasoning is reported separately from completion (xAI API convention), so output =
    completion + reasoning.
    """

    name = "grok_unified"
    label = "Grok CLI (unified log)"

    def roots(self):
        return [h / "logs" for h in self.homes]

    def files(self):
        return _walk(self.roots(), lambda n: n.startswith("unified") and n.endswith(".jsonl"))

    def parse(self, line, ctx, key):
        try:
            obj = json.loads(line)
        except ValueError:
            return []
        if not isinstance(obj, dict):
            return []
        c = obj.get("ctx") if isinstance(obj.get("ctx"), dict) else {}
        sid = str(obj.get("sid") or "")
        models = ctx.setdefault("models", {})
        if isinstance(c.get("model"), str) and c["model"].strip():
            models[sid] = c["model"].strip()[:120]
        if obj.get("msg") != "shell.turn.inference_done":
            return []
        ts = _ts(obj.get("ts"))
        p = _int(c.get("prompt_tokens"))
        cr = _int(c.get("cached_prompt_tokens") or 0)
        co = _int(c.get("completion_tokens") or 0)
        rs = _int(c.get("reasoning_tokens") or 0)
        if -1 in (p, cr, co, rs):
            return []
        eid = "grokuni:" + _sha(sid, str(obj.get("ts")), str(c.get("loop_index", "")),
                                str(p), str(co))
        return _event(eid, models.get(sid, "unknown"), ts, sid or Path(key).stem, p, co + rs,
                      cr, 0, rs, self.name)


# ================================================================ protobuf (minimal)

def _varint(buf: bytes, i: int) -> Tuple[int, int]:
    n = shift = 0
    while True:
        if i >= len(buf) or shift > 63:
            raise ValueError("bad varint")
        b = buf[i]
        i += 1
        n |= (b & 0x7F) << shift
        if not b & 0x80:
            return n, i
        shift += 7


def pb_fields(buf: bytes) -> Dict[int, List[Any]]:
    """Decode one protobuf message level: field number -> list of int / bytes values.
    Unknown wire types raise ValueError (the blob is then skipped)."""
    out: Dict[int, List[Any]] = {}
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        field, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(buf, i)
        elif wt == 1:
            v, i = int.from_bytes(buf[i:i + 8], "little"), i + 8
        elif wt == 2:
            ln, i = _varint(buf, i)
            if i + ln > len(buf):
                raise ValueError("truncated")
            v, i = buf[i:i + ln], i + ln
        elif wt == 5:
            v, i = int.from_bytes(buf[i:i + 4], "little"), i + 4
        else:
            raise ValueError("wire type %d" % wt)
        out.setdefault(field, []).append(v)
    return out


def _pb_int(f: Dict[int, List[Any]], n: int) -> int:
    v = f.get(n)
    return v[0] if v and isinstance(v[0], int) else 0


def _pb_msg(f: Dict[int, List[Any]], n: int) -> Optional[Dict[int, List[Any]]]:
    v = f.get(n)
    if not v or not isinstance(v[0], bytes):
        return None
    try:
        return pb_fields(v[0])
    except ValueError:
        return None


def _pb_str(f: Dict[int, List[Any]], n: int) -> str:
    v = f.get(n)
    if v and isinstance(v[0], bytes):
        try:
            return v[0].decode("utf-8")
        except UnicodeDecodeError:
            return ""
    return ""


# ================================================================ Antigravity

class AntigravityScanner(WholeFileScanner):
    """Antigravity per-conversation SQLite files, ``<root>/conversations/*.db`` (experimental).

    Table ``gen_metadata(idx, data BLOB)``; ``data`` is protobuf. Field numbers are not
    documented by Google; they were worked out by inspecting files:
      data.1 = generation record; .1.4 = usage {2 uncached input, 3 output, 4 cache write,
      5 cache read, 9 thinking, 11 response id}; .1.19 = model id.
    Opened read-only (``mode=ro``); never written. Rows that don't decode are skipped.
    """

    name = "antigravity"
    label = "Antigravity"

    def roots(self):
        return [h / "conversations" for h in self.homes]

    def files(self):
        return _walk(self.roots(), lambda n: n.endswith(".db"))

    def parse_file(self, path: Path) -> List[TokenEvent]:
        uri = path.resolve().as_uri() + "?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=2)
        try:
            rows = con.execute("SELECT idx, data FROM gen_metadata").fetchall()
        finally:
            con.close()
        mtime = path.stat().st_mtime
        out: List[TokenEvent] = []
        seen = set()
        for idx, data in rows:
            if not isinstance(data, (bytes, bytearray)):
                continue
            try:
                top = pb_fields(bytes(data))
            except ValueError:
                continue
            gen = _pb_msg(top, 1) or top
            usage = _pb_msg(gen, 4)
            if not usage:
                continue
            unc, out_t = _pb_int(usage, 2), _pb_int(usage, 3)
            cw, cr, think = _pb_int(usage, 4), _pb_int(usage, 5), _pb_int(usage, 9)
            rid = _pb_str(usage, 11)
            if rid and rid in seen:
                continue
            seen.add(rid)
            model = _pb_str(gen, 19) or "unknown"
            ts = None
            tmsg = _pb_msg(gen, 9)
            if tmsg:
                tsm = _pb_msg(tmsg, 4)
                if tsm and 1_500_000_000 < _pb_int(tsm, 1) < 4_000_000_000:
                    ts = float(_pb_int(tsm, 1))
            eid = "antigravity:" + (rid or _sha(str(path), str(idx)))
            out += _event(eid, model, ts or mtime, path.stem, unc + cr + cw,
                          max(out_t, think), cr, cw, think, self.name)
        return out


# ================================================================ DeepSeek Harness

def zstd_available() -> bool:
    return _zstd_reader() is not None


def _zstd_reader():
    """Returns a function bytes -> bytes that decodes every concatenated frame (one per
    harness flush), keeping everything before a torn final frame; or None."""
    try:  # Python 3.14+
        from compression import zstd as _z  # type: ignore

        new, err = _z.ZstdDecompressor, _z.ZstdError
    except ImportError:
        try:
            import zstandard  # type: ignore
        except ImportError:
            return None
        new, err = (lambda: zstandard.ZstdDecompressor().decompressobj()), zstandard.ZstdError

    def dec(raw: bytes) -> bytes:
        chunks = []
        while raw:
            d = new()
            try:
                chunks.append(d.decompress(raw))
            except err:
                break  # torn / corrupt frame: keep the frames before it
            if not d.eof:
                break
            raw = d.unused_data
        return b"".join(chunks)

    return dec


def _is_dsh_log(name: str) -> bool:
    if not name.startswith("session"):
        return False
    base = name[:-5] if name.endswith(".zstd") else name
    if not base.endswith(".jsonl"):
        return False
    mid = base[len("session"):-len(".jsonl")]
    return mid == "" or (mid.startswith(".v") and mid[2:].isdigit())


class DeepSeekHarnessScanner(WholeFileScanner):
    """DeepSeek Harness (``dsh``) session logs (experimental).

    ``$DSH_HOME/sessions/*/session-*/session[.vN].jsonl[.zstd]``; ``.zstd`` files are one
    zstd frame per flush. ``assistant/message`` records carry ``data.usage`` with
    ``inputTokens`` (uncached), ``cacheReadTokens``, ``cacheWriteTokens``, ``outputTokens``
    and ``reasoningTokens`` (a subset of output). The model comes from
    ``data.message.source.model``. ``.credentials.yaml`` and other files are never read.
    """

    name = "dsh"
    label = "DeepSeek Harness"

    def files(self):
        return _walk(self.homes, _is_dsh_log)

    def available(self):
        ok, why = super().available()
        if ok and not zstd_available() and any(
                str(p).endswith(".zstd") for p in self.files()):
            return True, "compressed logs need the 'zstandard' Python package (or Python 3.14)"
        return ok, why

    def parse_file(self, path: Path) -> List[TokenEvent]:
        with path.open("rb") as f:
            raw = f.read(self.max_file_bytes + 1)
        if len(raw) > self.max_file_bytes:
            raise ValueError("too large")
        if path.name.endswith(".zstd"):
            dec = _zstd_reader()
            if dec is None:
                return []
            raw = dec(raw)
        return parse_dsh_text(raw.decode("utf-8", "replace"), str(path))


def parse_dsh_text(text: str, key: str) -> List[TokenEvent]:
    session, cwd, model = Path(key).parent.name, "", ""
    out: List[TokenEvent] = []
    for n, line in enumerate(text.splitlines()):
        if len(line) > MAX_LINE_BYTES:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        typ = obj.get("type")
        data = obj.get("data") if isinstance(obj.get("data"), dict) else {}
        if typ == "session":
            session = str(obj.get("id") or session)
            cwd = obj.get("cwd") if isinstance(obj.get("cwd"), str) else cwd
            continue
        if typ == "session/title-llm-request":
            r = data.get("route") if isinstance(data.get("route"), dict) else {}
            model = model or str(r.get("model") or "")
            continue
        if typ != "assistant/message" or not isinstance(data.get("usage"), dict):
            continue
        u = data["usage"]
        msg = data.get("message") if isinstance(data.get("message"), dict) else {}
        src = msg.get("source") if isinstance(msg.get("source"), dict) else {}
        m = str(src.get("model") or model or "unknown")
        inp, cr, cw = _int(u.get("inputTokens") or 0), _int(u.get("cacheReadTokens") or 0), \
            _int(u.get("cacheWriteTokens") or 0)
        o, rs = _int(u.get("outputTokens") or 0), _int(u.get("reasoningTokens") or 0)
        if -1 in (inp, cr, cw, o, rs):
            continue
        t = obj.get("time")
        ts = (t / 1000.0 if t > 1e11 else float(t)) if isinstance(t, (int, float)) else _ts(t)
        eid = "dsh:%s:%s" % (session, obj.get("seq", "l%d" % n))
        out += _event(eid, m, ts, session, inp + cr + cw, o, cr, cw, rs, "dsh",
                      project=project_name(cwd))
    return out

