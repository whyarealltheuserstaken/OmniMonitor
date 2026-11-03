"""0.3.0 local-log adapters: Gemini CLI, Qwen Code, Kimi (CLI / Code / Work), opencode,
Copilot CLI OpenTelemetry, and Codex plan-limit capture. All fixtures are synthetic."""
import json
import os
import shutil
import sqlite3

from omnimonitor import adapters
from omnimonitor import local_adapters as la
from omnimonitor.history import History

from .conftest import FIXTURES


def lines(fn, name, key=None):
    ctx, out = {}, []
    key = key or "/x/" + name
    for line in (FIXTURES / name).read_text().splitlines():
        out.extend(fn(line, ctx, key))
    return out, ctx


# ------------------------------------------------------------------ Gemini CLI
def test_gemini_json_document():
    evs = {e.event_id: e for e in la.parse_gemini_file(FIXTURES / "gemini_session.json")}
    assert set(evs) == {"gemini:g-sess-1:m1", "gemini:g-sess-1:m2"}  # bad + non-gemini skipped
    e = evs["gemini:g-sess-1:m1"]
    assert (e.input_tokens, e.output_tokens) == (12000 + 40, 300 + 150)  # tool in, thoughts out
    assert (e.cache_read_tokens, e.reasoning_tokens) == (8000, 150)
    assert e.model == "gemini-2.5-pro" and e.source == "gemini_cli" and e.cost_usd is None


def test_gemini_jsonl_last_record_wins(tmp_path):
    evs = la.parse_gemini_file(FIXTURES / "gemini_session.jsonl")
    assert len(evs) == 1
    e = evs[0]
    assert e.event_id == "gemini:g-sess-2:m1"
    assert (e.input_tokens, e.output_tokens, e.cache_read_tokens) == (1000, 250, 400)


def test_gemini_scanner_rewrites_do_not_double_count(tmp_path):
    chats = tmp_path / "gem" / "tmp" / "abc123" / "chats"
    chats.mkdir(parents=True)
    f = chats / "session-1.json"
    shutil.copy(FIXTURES / "gemini_session.json", f)
    (tmp_path / "gem" / "tmp" / "abc123" / "logs.json").write_text("[]")  # not in chats/
    h = History(tmp_path / "h.db")
    sc = la.GeminiCliScanner([tmp_path / "gem"], h)
    assert sc.available()[0]
    sc.scan()
    assert len(h.events()) == 2
    assert sc.scan()["new_events"] == 0
    doc = json.loads(f.read_text())
    doc["messages"][1]["tokens"]["output"] = 999  # the CLI rewrites the whole file
    f.write_text(json.dumps(doc))
    os.utime(f, (1, 1))
    sc.scan()
    evs = sorted(h.events(), key=lambda e: e["ts"])
    assert len(evs) == 2 and evs[0]["output_tokens"] == 999 + 150


# ------------------------------------------------------------------ Qwen Code
def test_qwen_usage_metadata(tmp_path):
    key = str(tmp_path / "projects" / "proj" / "chats" / "q-sess.jsonl")
    evs, _ = lines(la.parse_qwen_line, "qwen_chat.jsonl", key)
    assert [e.event_id for e in evs] == ["qwen:q-sess:q2", "qwen:q-sess:q3"]
    a, b = evs
    assert (a.input_tokens, a.output_tokens, a.cache_read_tokens) == (9000, 500, 6000)
    assert b.output_tokens == 300  # thoughts already inside candidates (total == p + c)
    assert a.project == "qproj" and a.source == "qwen_code" and a.model == "qwen3-coder-plus"


def test_qwen_scanner(tmp_path):
    d = tmp_path / "qwen" / "projects" / "proj" / "chats"
    d.mkdir(parents=True)
    shutil.copy(FIXTURES / "qwen_chat.jsonl", d / "q-sess.jsonl")
    h = History(tmp_path / "h.db")
    sc = la.QwenCodeScanner([tmp_path / "qwen"], h)
    assert sc.scan()["new_events"] == 2 and sc.scan()["new_events"] == 0


# ------------------------------------------------------------------ Kimi
def test_kimi_legacy_status_updates_use_config_model():
    f = FIXTURES / "kimi_home" / "sessions" / "grp1" / "s-legacy" / "wire.jsonl"
    ctx, evs = {}, []
    for line in f.read_text().splitlines():
        evs.extend(la.parse_kimi_line(line, ctx, str(f)))
    assert [e.event_id for e in evs] == ["kimi:s-legacy:k1", "kimi:s-legacy:k1",
                                         "kimi:s-legacy:k2"]
    last = evs[1]
    assert (last.input_tokens, last.output_tokens) == (150 + 900 + 50, 60)
    assert (last.cache_read_tokens, last.cache_creation_tokens) == (900, 50)
    assert last.model == "kimi-k2-turbo-preview"


def test_kimi_work_usage_records():
    f = FIXTURES / "kimi_home" / "sessions" / "grp2" / "s-work" / "wire.jsonl"
    ctx, evs = {}, []
    for line in f.read_text().splitlines():
        evs.extend(la.parse_kimi_line(line, ctx, str(f)))
    assert len(evs) == 2  # the session-scope record is a total, not counted
    assert evs[0].model == "kimi-for-coding" and evs[0].input_tokens == 2000
    assert evs[0].ts == 1791200001.0
    assert evs[1].model == "kimi-k2-thinking"  # placeholder model ignored


def test_kimi_scanner_replaces_repeated_status(tmp_path):
    home = tmp_path / "kimi"
    shutil.copytree(FIXTURES / "kimi_home", home)
    h = History(tmp_path / "h.db")
    sc = la.KimiScanner([home / "sessions"], h)
    sc.scan()
    evs = h.events()
    assert len(evs) == 4
    legacy = [e for e in evs if e["session_id"] == "s-legacy"]
    assert sorted(e["output_tokens"] for e in legacy) == [5, 60]  # last StatusUpdate wins
    assert sc.scan()["new_events"] == 0


# ------------------------------------------------------------------ opencode
def test_opencode_json_message():
    d = json.loads((FIXTURES / "opencode_message.json").read_text())
    (e,) = la.opencode_message(d)
    assert e.event_id == "opencode:msg_oc1" and e.session_id == "ses_1"
    assert e.input_tokens == 50 + 4000 + 100 and e.output_tokens == 320
    assert (e.cache_read_tokens, e.cache_creation_tokens, e.reasoning_tokens) == (4000, 100, 20)
    assert e.cost_usd == 0.0123 and e.project == "ocproj" and e.ts == 1791300004.0
    assert la.opencode_message(dict(d, role="user")) == []


def _make_db(path, rows, table="message"):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE %s (id TEXT, session_id TEXT, data TEXT)" % table)
    con.executemany("INSERT INTO %s VALUES (?, ?, ?)" % table, rows)
    con.commit()
    con.close()


def test_opencode_db_read_only_and_both_tables(tmp_path):
    d = json.loads((FIXTURES / "opencode_message.json").read_text())
    db = tmp_path / "opencode.db"
    newer = dict(d)
    newer.pop("role")
    newer.pop("id")
    newer.pop("sessionID")
    _make_db(db, [("m-a", "ses_db", json.dumps(dict(d, id="m-a"))),
                  ("m-b", "ses_db", "not json")])
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE session_message (id TEXT, session_id TEXT, type TEXT, data TEXT)")
    con.execute("INSERT INTO session_message VALUES ('m-c', 'ses_new', 'assistant', ?)",
                (json.dumps(newer),))
    con.commit()
    con.close()
    before = db.read_bytes()
    evs = {e.event_id: e for e in la.parse_opencode_db(db)}
    assert set(evs) == {"opencode:m-a", "opencode:m-c"}
    assert evs["opencode:m-c"].session_id == "ses_new"
    assert db.read_bytes() == before  # opened read-only


def test_opencode_scanner_files_and_wal_fingerprint(tmp_path):
    home = tmp_path / "opencode"
    msg = home / "storage" / "message" / "ses_1"
    msg.mkdir(parents=True)
    shutil.copy(FIXTURES / "opencode_message.json", msg / "msg_oc1.json")
    db = home / "opencode-beta.db"
    d = json.loads((FIXTURES / "opencode_message.json").read_text())
    _make_db(db, [("m-db", "s", json.dumps(dict(d, id="m-db")))])
    (home / "opencode-.db").write_text("x")  # not a real channel name: ignored
    (home / "other.db").write_text("x")
    h = History(tmp_path / "h.db")
    sc = la.OpencodeScanner([home], h)
    names = sorted(p.name for p in sc.files())
    assert names == ["msg_oc1.json", "opencode-beta.db"]
    sc.scan()
    assert len(h.events()) == 2 and sc.scan()["new_events"] == 0
    fp = sc.fingerprint(db)
    (home / "opencode-beta.db-wal").write_bytes(b"\0" * 100)
    assert sc.fingerprint(db)[0] == fp[0] + 100  # WAL growth triggers a re-read
    assert len(h.events()) == 2


# ------------------------------------------------------------------ Copilot OTel
def test_copilot_otel_chat_spans_only():
    evs, _ = lines(la.parse_otel_line, "copilot_otel.jsonl")
    assert [e.event_id for e in evs] == ["copilot:t1:s1", "copilot:t1:s2"]  # agent span skipped
    a, b = evs
    assert (a.input_tokens, a.output_tokens, a.cache_read_tokens, a.reasoning_tokens) == (
        3000, 200, 1000, 50)
    assert a.model == "gpt-5" and a.session_id == "conv-1" and a.ts == 1791400005.5
    assert (b.input_tokens, b.output_tokens) == (500, 40)  # OTLP list-form attributes
    assert b.model == "claude-sonnet-4.5" and b.ts == 1791400009.0


def test_copilot_scanner_on_a_single_file(tmp_path):
    f = tmp_path / "otel.jsonl"
    shutil.copy(FIXTURES / "copilot_otel.jsonl", f)
    h = History(tmp_path / "h.db")
    sc = la.CopilotOtelScanner([tmp_path], h)
    assert sc.scan()["new_events"] == 2


# ------------------------------------------------------------------ Codex limits
def test_codex_rate_limits_snapshot(tmp_path):
    home = tmp_path / ".codex"
    d = home / "sessions" / "2026" / "10" / "02"
    d.mkdir(parents=True)
    shutil.copy(FIXTURES / "codex_rate_limits.jsonl", d / "rollout-rl.jsonl")
    h = History(tmp_path / "h.db")
    adapters.CodexScanner([home], h).scan()
    snap = adapters.codex_limits(h)
    assert snap["plan_type"] == "plus"
    w = snap["windows"]
    assert w["primary"] == {"used_percent": 20.0, "window_minutes": 300, "resets_at": 1791000000.0}
    assert w["secondary"]["used_percent"] == 45.0  # the 150 % line was rejected


def test_codex_rate_limits_resets_in_seconds_and_bad_values():
    snap = adapters.codex_rate_limits({"primary": {"used_percent": 10, "resets_in_seconds": 60}},
                                      1000.0)
    assert snap["windows"]["primary"]["resets_at"] == 1060.0
    assert adapters.codex_rate_limits({"primary": {"used_percent": True}}, 1.0) is None
    assert adapters.codex_rate_limits({"primary": {"used_percent": 5}}, None) is None
    assert adapters.codex_limits(None) is None


def test_describe_lists_new_adapters(tmp_path):
    names = {r["name"] for r in adapters.describe({"gemini_home": str(tmp_path)})}
    assert {"gemini_cli", "qwen_code", "kimi", "opencode", "copilot_otel"} <= names
