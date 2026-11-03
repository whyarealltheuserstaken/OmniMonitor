import os
import shutil

from omnimonitor.history import History
from omnimonitor.tokens import GrokSessionsScanner, demo_events, parse_update_line

from .conftest import FIXTURES


def make_home(tmp_path):
    d = tmp_path / ".grok" / "sessions" / "%2Fhome%2Fme%2Fproj" / "sess-1"
    d.mkdir(parents=True)
    shutil.copy(FIXTURES / "updates.jsonl", d / "updates.jsonl")
    return tmp_path / ".grok", d / "updates.jsonl"


def test_parse_non_turn_line_returns_timestamp_only():
    ts, evs, turn = parse_update_line(
        '{"params":{"update":{"sessionUpdate":"agent_message_chunk"},"_meta":{"agentTimestampMs":1000000000000}}}',
        "s")
    assert ts == 1e9 and evs == [] and turn is None


def test_parse_turn_completed():
    line = (FIXTURES / "updates.jsonl").read_text().splitlines()[2]
    ts, evs, turn = parse_update_line(line, "s1")
    assert len(evs) == 1
    e = evs[0]
    assert (e.input_tokens, e.output_tokens, e.cache_read_tokens) == (12000, 800, 9000)
    assert e.total == 12800  # input already includes cache reads
    assert abs(e.cost_usd - 0.015) < 1e-12
    assert turn["event_id"] == "e2"


def test_parse_top_level_update_and_cache_clamp():
    line = ('{"update":{"sessionUpdate":"turn_completed","usage":{"modelUsage":{"m":'
            '{"inputTokens":10,"cachedReadTokens":50,"outputTokens":1}}}},"timestamp":1762300000}')
    _ts, evs, _ = parse_update_line(line, "s")
    assert evs[0].cache_read_tokens == 10


def test_scanner_incremental_and_dedup(tmp_path):
    home, f = make_home(tmp_path)
    h = History(tmp_path / "h.sqlite3")
    sc = GrokSessionsScanner(home, h)
    assert sc.available()[0]
    sc.scan()
    evs = h.events()
    # e2 (1 model) + e4 (2 models); e5 implausible skipped; last line has no timestamp
    assert sorted((e["model"], e["input_tokens"]) for e in evs) == [
        ("grok-build", 5000), ("grok-build", 12000), ("grok-fast", 1000)]
    fast = [e for e in evs if e["model"] == "grok-fast"][0]
    assert fast["cache_read_tokens"] == 1000  # clamped to input
    turns = {round(t["duration_s"] or 0): t["duration_source"] for t in h.turns()}
    assert turns[90] == "derived from log timestamps"  # e0 -> e2
    assert turns[45] == "reported"                      # durationMs
    # rescanning unchanged file adds nothing
    assert sc.scan()["new_events"] == 0
    # appended data is picked up incrementally; a partial line waits
    with f.open("a") as fh:
        fh.write('{"params":{"update":{"sessionUpdate":"turn_completed","usage":{"modelUsage":'
                 '{"g":{"inputTokens":7,"outputTokens":3}}}},"_meta":{"agentTimestampMs":1762400000000,'
                 '"eventId":"e9"}}}\n{"partial":')
    assert sc.scan()["new_events"] == 1
    # truncation -> rescan from 0 without double counting
    f.write_text((FIXTURES / "updates.jsonl").read_text())
    os.utime(f, (1, 1))
    assert sc.scan()["new_events"] == 0
    assert len(h.events()) == 4


def test_scanner_unavailable(tmp_path):
    h = History(tmp_path / "h.sqlite3")
    ok, why = GrokSessionsScanner(tmp_path / "nothing", h).available()
    assert not ok and "no Grok CLI session logs" in why


def test_history_file_permissions(tmp_path):
    p = tmp_path / "x" / "h.sqlite3"
    History(p)
    if os.name == "posix":
        assert (p.stat().st_mode & 0o777) == 0o600


def test_demo_is_deterministic():
    a = demo_events(1_800_000_000.0)
    b = demo_events(1_800_000_000.0)
    assert a == b and len(a[0]) > 100
