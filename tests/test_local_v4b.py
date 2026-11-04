"""0.4 batch-1b local adapters. All fixtures are synthetic (shaped after each tool's
open-source code or developer docs); no real user data."""
import json
import os
import sqlite3

from omnimonitor import adapters, catalog, config
from omnimonitor import local_v4b as v4b
from omnimonitor.history import History


def _hist(tmp_path):
    return History(tmp_path / "h.sqlite")


def _jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _one(h):
    ev = h.events()
    assert len(ev) == 1, ev
    e = ev[0]
    return (e["input_tokens"], e["output_tokens"], e["cache_read_tokens"],
            e["cache_creation_tokens"], e["reasoning_tokens"])


# ------------------------------------------------------------------ Pi / OpenClaw
PI_ROWS = [
    {"type": "session", "id": "sess-1", "timestamp": "2026-10-01T10:00:00Z", "cwd": "/w/proj"},
    {"type": "model_change", "provider": "anthropic", "modelId": "claude-sonnet-4-5"},
    {"type": "message", "id": "m1", "timestamp": "2026-10-01T10:00:01Z",
     "message": {"role": "user", "content": "hi"}},
    {"type": "message", "id": "m2", "timestamp": "2026-10-01T10:00:05Z",
     "message": {"role": "assistant", "provider": "anthropic", "model": "claude-sonnet-4-5",
                 "usage": {"input": 100, "output": 50, "cacheRead": 1000, "cacheWrite": 200,
                           "totalTokens": 1350, "cost": {"total": 0.01}}}},
]


def test_pi_session_and_fork_dedupe(tmp_path):
    root = tmp_path / "sessions"
    _jsonl(root / "a" / "s1.jsonl", PI_ROWS)
    _jsonl(root / "a" / "s2-fork.jsonl", PI_ROWS)  # a fork copies the parent's entries
    h = _hist(tmp_path)
    v4b.PiScanner([root], h).scan()
    assert _one(h) == (1300, 50, 1000, 200, 0)
    e = h.events()[0]
    assert e["model"] == "claude-sonnet-4-5" and e["project"] == "proj"
    assert abs(e["cost_usd"] - 0.01) < 1e-9
    assert v4b.PiScanner([root], h).scan()["new_events"] == 0


def test_openclaw_skips_mirror_and_handles_openai_usage(tmp_path):
    root = tmp_path / "agents"
    _jsonl(root / "main" / "sessions" / "x.jsonl", [
        {"type": "session", "id": "oc"},
        {"type": "message", "id": "a", "timestamp": "2026-10-01T10:00:00Z",
         "message": {"role": "assistant", "model": "gpt-5",
                     "usage": {"prompt_tokens": 900, "completion_tokens": 30,
                               "prompt_tokens_details": {"cached_tokens": 800}}}},
        {"type": "message", "id": "b", "timestamp": "2026-10-01T10:00:01Z",
         "message": {"role": "assistant", "provider": "openclaw", "model": "delivery-mirror",
                     "usage": {"input": 5, "output": 5}}},
    ])
    h = _hist(tmp_path)
    v4b.OpenClawScanner([root], h).scan()
    assert _one(h) == (900, 30, 800, 0, 0)
    assert h.events()[0]["source"] == "openclaw"


# ------------------------------------------------------------------ Goose
def _goose(path, accumulated=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    acc = (", accumulated_input_tokens INTEGER, accumulated_output_tokens INTEGER, "
           "accumulated_cache_read_tokens INTEGER, accumulated_cost REAL") if accumulated else ""
    con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, working_dir TEXT, created_at "
                "TIMESTAMP, updated_at TIMESTAMP, input_tokens INTEGER, output_tokens INTEGER, "
                "cache_read_tokens INTEGER, model_config_json TEXT" + acc + ")")
    if accumulated:
        con.execute("INSERT INTO sessions VALUES ('20261001_1','/w/g','2026-10-01 10:00:00',"
                    "'2026-10-01 11:00:00',10,1,0,?,5000,400,3000,0.2)",
                    (json.dumps({"model_name": "gpt-5"}),))
    else:
        con.execute("INSERT INTO sessions VALUES ('s','/w/g','2026-10-01 10:00:00',NULL,"
                    "700,70,NULL,NULL)")
    con.commit()
    con.close()


def test_goose_prefers_accumulated_and_upserts(tmp_path):
    db = tmp_path / "goose" / "sessions" / "sessions.db"
    _goose(db)
    h = _hist(tmp_path)
    sc = v4b.GooseScanner([db.parent], h)
    sc.scan()
    assert _one(h) == (5000, 400, 3000, 0, 0)
    e = h.events()[0]
    assert e["model"] == "gpt-5" and e["project"] == "g" and abs(e["cost_usd"] - 0.2) < 1e-9
    con = sqlite3.connect(db)
    con.execute("UPDATE sessions SET accumulated_output_tokens = 900")
    con.commit()
    con.close()
    os.utime(db, (1, 1))
    sc.scan()
    assert _one(h)[1] == 900  # upserted, not double-counted


def test_goose_old_schema(tmp_path):
    db = tmp_path / "s" / "sessions.db"
    _goose(db, accumulated=False)
    h = _hist(tmp_path)
    v4b.GooseScanner([db.parent], h).scan()
    assert _one(h) == (700, 70, 0, 0, 0)


# ------------------------------------------------------------------ Hermes
def _hermes(path, model="anthropic/claude-sonnet-4.6"):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, model TEXT, "
                "started_at REAL, input_tokens INTEGER, output_tokens INTEGER, "
                "cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens "
                "INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL)")
    con.execute("INSERT INTO sessions VALUES ('s1','cli',?,1790000000.0,100,40,900,50,10,"
                "0.03,NULL)", (model,))
    con.commit()
    con.close()


def test_hermes_default_and_profiles(tmp_path):
    home = tmp_path / ".hermes"
    _hermes(home / "state.db")
    _hermes(home / "profiles" / "coder" / "state.db", model="x/y")
    h = _hist(tmp_path)
    v4b.HermesScanner([home], h).scan()
    ev = sorted(h.events(), key=lambda e: e["model"])
    assert len(ev) == 2  # same session id in two profiles is two sessions
    e = ev[0]
    assert (e["input_tokens"], e["output_tokens"], e["cache_read_tokens"],
            e["reasoning_tokens"]) == (1050, 50, 900, 10)
    assert abs(e["cost_usd"] - 0.03) < 1e-9


def test_hermes_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hh"))
    assert config.hermes_roots({})[0] == tmp_path / "hh"


# ------------------------------------------------------------------ Amp
def test_amp_ledger_with_cache(tmp_path):
    d = tmp_path / "threads"
    d.mkdir()
    (d / "T-1.json").write_text(json.dumps({
        "id": "T-1",
        "messages": [{"role": "user", "messageId": 0},
                     {"role": "assistant", "messageId": 1,
                      "usage": {"cacheReadInputTokens": 700, "cacheCreationInputTokens": 20}}],
        "usageLedger": {"events": [{"id": "ev1", "timestamp": "2026-10-01T10:00:00Z",
                                    "model": "claude-sonnet-4-5", "credits": 3,
                                    "tokens": {"input": 80, "output": 15},
                                    "toMessageId": 1}]}}))
    (d / "T-2.json").write_text(json.dumps({"id": "T-2", "created": 1790000000000, "messages": [
        {"role": "assistant", "usage": {"model": "gpt-5", "inputTokens": 10, "outputTokens": 2,
                                        "timestamp": "2026-10-01T10:00:00Z"}}]}))
    h = _hist(tmp_path)
    v4b.AmpScanner([d], h).scan()
    ev = {e["session_id"]: e for e in h.events()}
    a = ev["T-1"]
    assert (a["input_tokens"], a["output_tokens"], a["cache_read_tokens"]) == (800, 15, 700)
    assert a["cost_usd"] is None  # credits are not dollars
    assert ev["T-2"]["input_tokens"] == 10


# ------------------------------------------------------------------ Droid
def test_droid_settings(tmp_path):
    d = tmp_path / "sessions" / "-w-p"
    d.mkdir(parents=True)
    (d / "abc.settings.json").write_text(json.dumps({
        "model": "claude-opus-4-1", "providerLockTimestamp": "2026-10-01T10:00:00Z",
        "tokenUsage": {"inputTokens": 100, "outputTokens": 20, "thinkingTokens": 5,
                       "cacheReadTokens": 900, "cacheCreationTokens": 10}}))
    _jsonl(d / "abc.jsonl", [{"type": "session_start", "cwd": "/w/p"}])
    h = _hist(tmp_path)
    v4b.DroidScanner([tmp_path / "sessions"], h).scan()
    assert _one(h) == (1010, 25, 900, 10, 5)
    assert h.events()[0]["project"] == "p"


# ------------------------------------------------------------------ Cline / Roo / Kilo
def _task(root, ext, tokens_in, model=True):
    t = root / ext / "tasks" / "1790000000000"
    t.mkdir(parents=True)
    info = {"request": "x", "tokensIn": tokens_in, "tokensOut": 40, "cacheReads": 500,
            "cacheWrites": 100, "cost": 0.02}
    (t / "ui_messages.json").write_text(json.dumps([
        {"ts": 1790000000000, "type": "say", "say": "text", "text": "hi"},
        {"ts": 1790000001000, "type": "say", "say": "api_req_started", "text": json.dumps(info)},
        {"ts": 1790000002000, "type": "say", "say": "api_req_started", "text": "{bad"},
    ]))
    if model:
        (t / "api_conversation_history.json").write_text(json.dumps([
            {"role": "user", "content": [{"type": "text", "text":
                                          "<environment_details><model>claude-sonnet-4-5</model>"
                                          "</environment_details>"}]}]))


def test_cline_adds_cache(tmp_path):
    _task(tmp_path, v4b._EXT["cline"], 60)
    h = _hist(tmp_path)
    v4b.ClineScanner([tmp_path], h).scan()
    assert _one(h) == (660, 40, 500, 100, 0)
    assert h.events()[0]["model"] == "claude-sonnet-4-5"


def test_roo_cache_inclusive(tmp_path):
    _task(tmp_path, v4b._EXT["roo"], 660, model=False)
    h = _hist(tmp_path)
    v4b.RooScanner([tmp_path], h).scan()
    assert _one(h)[0] == 660
    assert h.events()[0]["model"] == "unknown"


# ------------------------------------------------------------------ wiring
def test_catalog_and_registration():
    names = {c.name for c, _ in adapters._local_classes({})}
    for pid in ("pi", "openclaw", "goose", "hermes", "amp", "droid", "cline", "roo_code",
                "kilo_code"):
        assert pid in names
        assert catalog.BY_ID[pid].kind == "local_logs"
        assert pid in catalog.SWITCHABLE
    for pid in ("zed", "kiro", "devin_cli", "lmstudio", "minimax_agent"):
        assert catalog.BY_ID[pid].status == catalog.NOT_SUPPORTED
