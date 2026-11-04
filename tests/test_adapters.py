import json
import shutil

from omnimonitor import adapters
from omnimonitor.adapters import (
    ClaudeCodeScanner,
    CodexScanner,
    parse_claude_line,
    parse_codex_line,
)
from omnimonitor.history import History
from omnimonitor.tokens import grok_project, project_name

from .conftest import FIXTURES


def parse_all(fn, name):
    ctx, out = {}, []
    for line in (FIXTURES / name).read_text().splitlines():
        out.extend(fn(line, ctx, "/x/" + name))
    return out, ctx


def test_claude_normalizes_cache_and_skips_synthetic():
    evs, _ = parse_all(parse_claude_line, "claude_session.jsonl")
    # 3 assistant lines with usage, one is <synthetic>; msg_1 appears twice (dedupe by id)
    assert [e.event_id for e in evs] == ["claude:msg_1:req_1", "claude:msg_1:req_1",
                                         "claude:msg_2:req_2"]
    e = evs[0]
    assert e.input_tokens == 10 + 2000 + 30000  # input includes cache reads + writes
    assert (e.cache_read_tokens, e.cache_creation_tokens, e.output_tokens) == (30000, 2000, 400)
    assert e.total == 32410
    assert e.source == "claude_code" and e.project == "alpha" and e.session_id == "c-sess-1"
    assert e.model == "claude-sonnet-4-5"


def test_claude_scanner_dedupes_and_is_incremental(tmp_path):
    home = tmp_path / ".claude"
    d = home / "projects" / "-home-me-code-alpha"
    d.mkdir(parents=True)
    f = d / "c-sess-1.jsonl"
    shutil.copy(FIXTURES / "claude_session.jsonl", f)
    h = History(tmp_path / "h.db")
    sc = ClaudeCodeScanner([home], h)
    assert sc.available() == (True, "")
    assert sc.scan()["new_events"] == 2
    assert sc.scan()["new_events"] == 0  # unchanged file is skipped
    with f.open("a") as fh:
        fh.write(json.dumps({"type": "assistant", "requestId": "r3",
                             "timestamp": "2026-10-01T11:00:00Z",
                             "message": {"id": "m3", "model": "claude-opus-4-1",
                                         "usage": {"input_tokens": 5, "output_tokens": 7}}})
                 + "\n")
        fh.write('{"type":"assistant","partial')  # half-written line: not consumed yet
    assert sc.scan()["new_events"] == 1
    evs = h.events()
    assert len(evs) == 3 and {e["source"] for e in evs} == {"claude_code"}
    # cwd context persisted across scans -> new event still gets the project
    assert [e["project"] for e in evs if e["model"] == "claude-opus-4-1"] == ["alpha"]


def test_codex_parsing_rules():
    evs, ctx = parse_all(parse_codex_line, "codex_rollout.jsonl")
    # info:null skipped, repeated total skipped, token_usage_record ignored,
    # last event has no last_token_usage -> delta from cumulative totals
    assert len(evs) == 2
    a, b = evs
    assert (a.input_tokens, a.cache_read_tokens, a.output_tokens, a.reasoning_tokens) == (
        5000, 3000, 200, 50)
    assert (b.input_tokens, b.cache_read_tokens, b.output_tokens, b.reasoning_tokens) == (
        7000, 6000, 300, 70)
    assert a.model == "gpt-5-codex" and a.project == "beta" and a.session_id == "cx-sess-1"
    assert a.event_id == "codex:cx-sess-1:5200" and b.event_id == "codex:cx-sess-1:12500"
    assert a.source == "codex" and a.total == 5200


def test_codex_unknown_model_is_labeled_unknown():
    line = json.dumps({"timestamp": "2026-10-02T09:00:10Z", "type": "event_msg",
                       "payload": {"type": "token_count", "info": {"last_token_usage": {
                           "input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5,
                           "reasoning_output_tokens": 0, "total_tokens": 15}}}})
    (e,) = parse_codex_line(line, {}, "/x/rollout-a.jsonl")
    assert e.model == "unknown" and e.total == 15


def test_codex_scanner_resumes_with_saved_context(tmp_path):
    home = tmp_path / ".codex"
    d = home / "sessions" / "2026" / "10" / "02"
    d.mkdir(parents=True)
    lines = (FIXTURES / "codex_rollout.jsonl").read_text().splitlines(keepends=True)
    f = d / "rollout-2026-10-02T09-00-00-cx.jsonl"
    f.write_text("".join(lines[:5]))
    h = History(tmp_path / "h.db")
    sc = CodexScanner([home], h)
    assert sc.scan()["new_events"] == 1
    with f.open("a") as fh:
        fh.write("".join(lines[5:]))
    assert sc.scan()["new_events"] == 1  # prev total + model came from the saved context
    evs = sorted(h.events(), key=lambda e: e["ts"])
    assert [e["input_tokens"] for e in evs] == [5000, 7000]
    assert {e["model"] for e in evs} == {"gpt-5-codex"}
    # archived copy of the same rollout is not double-counted
    arch = home / "archived_sessions"
    arch.mkdir()
    shutil.copy(f, arch / f.name)
    assert len(list(adapters.iter_codex_files([home]))) == 1


def test_truncated_file_restarts_and_dedupes(tmp_path):
    home = tmp_path / ".codex"
    d = home / "sessions"
    d.mkdir(parents=True)
    f = d / "rollout-x.jsonl"
    shutil.copy(FIXTURES / "codex_rollout.jsonl", f)
    h = History(tmp_path / "h.db")
    sc = CodexScanner([home], h)
    assert sc.scan()["new_events"] == 2
    f.write_text((FIXTURES / "codex_rollout.jsonl").read_text().splitlines()[0] + "\n")
    assert sc.scan()["new_events"] == 0


def test_missing_dirs_report_reason(tmp_path):
    ok, why = ClaudeCodeScanner([tmp_path / "nope"], None).available()
    assert not ok and "Claude Code" in why


def test_scanners_and_describe_follow_config(tmp_path):
    cfg = {"token_source": "auto", "source": "grok_cli_billing", "grok_home": str(tmp_path / "g"),
           "claude_home": str(tmp_path / "c"), "codex_home": str(tmp_path / "x"),
           "extra_token_sources": ["codex"]}
    names = [s.name for s in adapters.scanners(cfg, None)]
    assert names == ["grok_sessions", "codex"]
    rows = {r["name"]: r for r in adapters.describe(cfg)}
    assert rows["codex"]["enabled"] and not rows["claude_code"]["enabled"]
    assert not rows["codex"]["found"] and rows["codex"]["note"]


def test_project_names_are_basenames_only():
    assert project_name("/home/me/secret/client-x") == "client-x"
    assert project_name(None) == ""
    assert grok_project(__import__("pathlib").Path(
        "/h/.grok/sessions/%2Fhome%2Fme%2Fproj/sess-1/updates.jsonl")) == "proj"
