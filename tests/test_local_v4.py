"""0.4 batch-1 local adapters: Grok unified.jsonl, Antigravity, DeepSeek Harness, Claude
transcripts, Kimi Work shareDir and the ≈5-hour block estimate. All fixtures synthetic."""
import json
import sqlite3

import pytest

from omnimonitor import adapters, blocks, catalog, config
from omnimonitor import local_v4 as v4
from omnimonitor.history import History


def _hist(tmp_path):
    return History(tmp_path / "h.sqlite")


# ------------------------------------------------------------------ Grok unified
def _uni(tmp_path, lines):
    d = tmp_path / "grok" / "logs"
    d.mkdir(parents=True)
    (d / "unified.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    return tmp_path / "grok"


def test_grok_unified_inference_done(tmp_path):
    home = _uni(tmp_path, [
        {"ts": "2026-10-01T10:00:00Z", "sid": "s1", "msg": "model changed",
         "ctx": {"model": "grok-code-fast-2"}},
        {"ts": "2026-10-01T10:00:05Z", "sid": "s1", "msg": "shell.turn.inference_done",
         "ctx": {"prompt_tokens": 5000, "cached_prompt_tokens": 4000,
                 "completion_tokens": 200, "reasoning_tokens": 50, "loop_index": 0}},
        {"ts": "2026-10-01T10:00:09Z", "sid": "s1", "msg": "shell.turn.inference_done",
         "ctx": {}},  # old build: no tokens -> skipped
        {"ts": "2026-10-01T10:00:10Z", "sid": "s1", "msg": "other", "ctx": {"x": 1}},
        "not a dict",
    ])
    h = _hist(tmp_path)
    sc = v4.GrokUnifiedScanner([home], h)
    assert sc.available()[0]
    st = sc.scan()
    assert st["new_events"] == 1
    e = h.events()[0]
    assert e["model"] == "grok-code-fast-2" and e["source"] == "grok_unified"
    assert (e["input_tokens"], e["output_tokens"], e["cache_read_tokens"],
            e["reasoning_tokens"]) == (5000, 250, 4000, 50)
    assert sc.scan()["new_events"] == 0  # incremental, no double count


# ------------------------------------------------------------------ protobuf / Antigravity
def _v(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _f_int(field, n):
    return _v(field << 3) + _v(n)


def _f_bytes(field, b):
    return _v(field << 3 | 2) + _v(len(b)) + b


def _ag_row(unc, out, cw, cr, think, rid, model, ts):
    usage = (_f_int(1, 7) + _f_int(2, unc) + _f_int(3, out) + _f_int(4, cw) + _f_int(5, cr)
             + _f_int(9, think) + _f_bytes(11, rid.encode()))
    tmsg = _f_bytes(4, _f_int(1, ts))
    gen = _f_bytes(4, usage) + _f_bytes(9, tmsg) + _f_bytes(19, model.encode())
    return _f_bytes(1, gen)


def test_pb_fields_roundtrip_and_bad():
    f = v4.pb_fields(_f_int(2, 300) + _f_bytes(3, b"hi"))
    assert f == {2: [300], 3: [b"hi"]}
    with pytest.raises(ValueError):
        v4.pb_fields(b"\x0b")  # wire type 3 (group) unsupported


def test_antigravity_sqlite(tmp_path):
    conv = tmp_path / "ag" / "conversations"
    conv.mkdir(parents=True)
    db = conv / "c1.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE gen_metadata(idx INTEGER, data BLOB)")
    rows = [(0, _ag_row(1000, 300, 200, 5000, 100, "r1", "gemini-3-pro", 1790000000)),
            (1, _ag_row(1000, 300, 200, 5000, 100, "r1", "gemini-3-pro", 1790000000)),  # dup
            (2, b"\xff\xff"),  # undecodable -> skipped
            (3, _ag_row(10, 5, 0, 0, 0, "r2", "claude-opus-5-medium", 1790000100))]
    con.executemany("INSERT INTO gen_metadata VALUES(?,?)", rows)
    con.commit()
    con.close()
    h = _hist(tmp_path)
    sc = v4.AntigravityScanner([tmp_path / "ag"], h)
    assert sc.scan()["new_events"] == 2
    evs = {e["model"]: e for e in h.events()}
    e = evs["gemini-3-pro"]
    assert (e["input_tokens"], e["output_tokens"], e["cache_read_tokens"],
            e["cache_creation_tokens"], e["reasoning_tokens"]) == (6200, 300, 5000, 200, 100)
    assert e["ts"] == 1790000000 and e["source"] == "antigravity"
    assert db.read_bytes()[:16].startswith(b"SQLite format 3")  # untouched


# ------------------------------------------------------------------ DeepSeek Harness
DSH_LINES = [
    {"type": "session", "version": 3, "id": "sess-A", "createdAt": 1790000000000,
     "cwd": "/home/u/proj-x"},
    {"type": "assistant/message", "seq": 3, "time": 1790000001000,
     "data": {"message": {"source": {"provider": "deepseek-official", "model": "deepseek-flash"}},
              "usage": {"inputTokens": 100, "outputTokens": 40, "cacheReadTokens": 900,
                        "cacheWriteTokens": 10, "reasoningTokens": 15}}},
    {"type": "assistant/chunk", "seq": 4, "time": 1790000001100, "data": {"usage": {}}},
    {"type": "tool/call", "seq": 5, "time": 1790000002000},
]


def _dsh_text():
    return "".join(json.dumps(x) + "\n" for x in DSH_LINES)


def test_dsh_parse_plain(tmp_path):
    d = tmp_path / "dsh" / "sessions" / "wd1" / "session-A"
    d.mkdir(parents=True)
    (d / "session.v3.jsonl").write_text(_dsh_text())
    (d / ".credentials.yaml").write_text("secret: no")
    h = _hist(tmp_path)
    sc = v4.DeepSeekHarnessScanner([tmp_path / "dsh" / "sessions"], h)
    assert [p.name for p in sc.files()] == ["session.v3.jsonl"]
    assert sc.scan()["new_events"] == 1
    e = h.events()[0]
    assert (e["input_tokens"], e["output_tokens"], e["cache_read_tokens"],
            e["cache_creation_tokens"], e["reasoning_tokens"]) == (1010, 40, 900, 10, 15)
    assert e["model"] == "deepseek-flash" and e["project"] == "proj-x"
    assert e["ts"] == pytest.approx(1790000001.0)


def test_dsh_multiframe_zstd(tmp_path):
    zstandard = pytest.importorskip("zstandard")
    text = _dsh_text().encode()
    half = text.index(b"\n") + 1
    c = zstandard.ZstdCompressor()
    raw = c.compress(text[:half]) + c.compress(text[half:]) + b"\x28\xb5\x2f\xfd torn"
    d = tmp_path / "s" / "w" / "session-A"
    d.mkdir(parents=True)
    (d / "session.v3.jsonl.zstd").write_bytes(raw)
    evs = v4.DeepSeekHarnessScanner([tmp_path / "s"], None).parse_file(
        d / "session.v3.jsonl.zstd")
    assert len(evs) == 1 and evs[0].session_id == "sess-A"


def test_dsh_names():
    ok = ["session.jsonl", "session.v3.jsonl", "session.v12.jsonl.zstd", "session.jsonl.zstd"]
    bad = ["session.vx.jsonl", "other.jsonl", "session.json", ".credentials.yaml"]
    assert all(v4._is_dsh_log(n) for n in ok)
    assert not any(v4._is_dsh_log(n) for n in bad)


def test_dsh_roots_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "h"))
    assert config.dsh_roots({}) == [tmp_path / "h" / "sessions"]


# ------------------------------------------------------------------ Claude transcripts
def _claude_line(mid, rid, out, ts="2026-10-01T10:00:00Z"):
    return json.dumps({"type": "assistant", "timestamp": ts, "requestId": rid, "sessionId": "cs",
                       "message": {"id": mid, "model": "claude-opus-5",
                                   "usage": {"input_tokens": 10, "output_tokens": out,
                                             "cache_read_input_tokens": 100,
                                             "cache_creation_input_tokens": 0}}}) + "\n"


def test_claude_transcripts_and_last_record_wins(tmp_path):
    home = tmp_path / "claude"
    (home / "transcripts" / "a").mkdir(parents=True)
    (home / "projects" / "p").mkdir(parents=True)
    (home / "transcripts" / "a" / "t.jsonl").write_text(
        _claude_line("m1", "r1", 1) + _claude_line("m1", "r1", 77))
    (home / "projects" / "p" / "s.jsonl").write_text(_claude_line("m2", "r2", 5))
    h = _hist(tmp_path)
    adapters.ClaudeCodeScanner([home], h).scan()
    outs = sorted(e["output_tokens"] for e in h.events())
    assert outs == [5, 77]


# ------------------------------------------------------------------ Kimi Work shareDir
def test_kimi_share_dir(monkeypatch, tmp_path):
    ad = tmp_path / "AppData"
    (ad / "kimi-desktop").mkdir(parents=True)
    share = tmp_path / "D" / "kimi"
    (ad / "kimi-desktop" / "daimon-storage.json").write_text(json.dumps({"shareDir": str(share)}))
    monkeypatch.setattr(config, "app_data_dir", lambda: ad)
    roots = config.kimi_session_roots({})
    assert share / "daimon" / "runtime" / "kimi-code" / "home" / "sessions" in roots
    (ad / "kimi-desktop" / "daimon-storage.json").write_text(json.dumps({"shareDir": "rel"}))
    roots = config.kimi_session_roots({})
    assert not any("rel" in str(r) for r in roots)


# ------------------------------------------------------------------ ≈ 5-hour blocks
def test_blocks_estimate():
    t0 = 1790000000 - (1790000000 % 3600)  # an hour boundary
    evs = [{"ts": t0 + 600, "source": "claude_code", "input_tokens": 1000, "output_tokens": 0},
           {"ts": t0 + 1800, "source": "claude_code", "input_tokens": 1000, "output_tokens": 0},
           {"ts": t0 + 1800, "source": "codex", "input_tokens": 9, "output_tokens": 9},
           {"ts": t0 + 6 * 3600, "source": "claude_code", "input_tokens": 50, "output_tokens": 50}]
    bs = blocks.blocks(evs, now=t0 + 6 * 3600 + 60)
    assert [b["start"] for b in bs] == [t0, t0 + 6 * 3600]
    assert bs[0]["tokens"] == 2000 and not bs[0]["active"] and bs[0]["estimate"]
    assert bs[0]["tokens_per_min"] == pytest.approx(2000 / 20)
    a = blocks.active_block(evs, now=t0 + 6 * 3600 + 60)
    assert a["active"] and a["remaining_s"] == 5 * 3600 - 60 and a["projected_tokens"] > 100
    assert len(blocks.blocks(evs, sources=None, now=t0)) == 2


# ------------------------------------------------------------------ catalog
def test_new_entries_are_experimental_and_off_by_default():
    for pid in ("grok_unified", "antigravity", "dsh"):
        p = catalog.get(pid)
        assert p.status == catalog.EXPERIMENTAL and p.kind == "local_logs"
        assert pid in catalog.SWITCHABLE and pid not in catalog.enabled_ids({})
    names = {cls.name for cls, _ in adapters._local_classes({})}
    assert {"grok_unified", "antigravity", "dsh"} <= names
