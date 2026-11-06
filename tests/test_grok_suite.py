"""Grok suite total + CLI / Bot / Cursor breakdown. No invented figures."""

from datetime import datetime, timezone

from omnimonitor.grok_suite import build_grok_suite, menu_lines

NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)


def billing(**kw):
    base = {
        "used_percent": 27.0,
        "remaining_percent": 73.0,
        "raw_used_percent": 27.0,
        "period_start": "2026-11-02T00:00:00+00:00",
        "resets_at": "2026-11-09T00:00:00+00:00",
        "products_reported": True,
        "products": [],
    }
    base.update(kw)
    return base


def ev(ts, inp, out, source="grok_sessions", model="grok-4", sid="s1"):
    return {"ts": ts, "input_tokens": inp, "output_tokens": out, "model": model,
            "session_id": sid, "source": source, "cache_read_tokens": 10,
            "reasoning_tokens": 4}


def bucket(suite, bid):
    return next(b for b in suite["buckets"] if b["id"] == bid)


def test_suite_total_is_the_weekly_pool_not_the_sum_of_products():
    suite = build_grok_suite(billing(products=[
        {"product": "GrokBot", "used_percent": 20},
        {"product": "Cursor", "used_percent": 1},
    ]), [], {}, NOW, provider="grok_sessions")
    assert suite["used_percent"] == 27 and suite["remaining_percent"] == 73
    assert bucket(suite, "bot")["used_percent"] == 20
    assert bucket(suite, "cursor")["used_percent"] == 1
    cli = bucket(suite, "cli")
    assert cli["used_percent"] is None
    assert "GrokBuild" in cli["percent_reason"] or "did not include" in cli["percent_reason"]
    assert "not available" in cli["percent_reason"].lower()
    assert suite["share_gap_note"]  # 21 != 27, and we do not invent the missing 6
    assert "nothing was added" in suite["share_gap_note"]
    assert bucket(suite, "bot")["tokens"]["value"] is None
    assert "unified.jsonl" in bucket(suite, "bot")["tokens"]["reason"]
    assert bucket(suite, "cursor")["tokens"]["value"] is None
    assert "not available" in bucket(suite, "cursor")["tokens"]["reason"]


def test_grokbuild_is_cli_and_grokchat_is_not_bot():
    suite = build_grok_suite(billing(
        used_percent=25, remaining_percent=75, raw_used_percent=25,
        products=[
            {"product": "GrokBuild", "used_percent": 20},
            {"product": "GrokChat", "used_percent": 5},
        ]), [], {}, NOW, provider="grok_sessions")
    assert bucket(suite, "cli")["used_percent"] == 20
    assert bucket(suite, "bot")["used_percent"] is None
    assert "Grok Bot" in bucket(suite, "bot")["percent_reason"]
    assert "get-sand-usage-status" in bucket(suite, "bot")["percent_reason"]
    assert bucket(suite, "cursor")["used_percent"] is None
    other = suite["other_products"]
    assert other and other[0]["label"] == "Grok Chat" and other[0]["used_percent"] == 5
    assert not suite["share_gap_note"]


def test_omitted_product_usage_is_not_zero():
    suite = build_grok_suite(billing(products_reported=False, products=[]), [], {},
                             NOW, provider="grok_sessions")
    for bid in ("cli", "bot", "cursor"):
        b = bucket(suite, bid)
        assert b["used_percent"] is None
        assert "omitted productUsage" in b["percent_reason"]
    assert suite["used_percent"] == 27  # the total is still real


def test_listed_product_with_zero_percent_is_zero():
    suite = build_grok_suite(billing(
        used_percent=0, remaining_percent=100, raw_used_percent=0,
        products=[{"product": "GrokBuild", "used_percent": 0},
                  {"product": "GrokBot", "used_percent": 0}]),
        [], {}, NOW, provider="grok_sessions")
    assert bucket(suite, "cli")["used_percent"] == 0
    assert bucket(suite, "bot")["used_percent"] == 0
    assert bucket(suite, "cursor")["used_percent"] is None


def test_cli_tokens_from_sessions_not_unified_when_both_exist():
    ts = datetime(2026, 11, 4, 12, tzinfo=timezone.utc).timestamp()
    events = [ev(ts, 100, 40, "grok_sessions", sid="a"),
              ev(ts, 999, 999, "grok_unified", sid="a")]
    suite = build_grok_suite(
        billing(products=[{"product": "GrokBuild", "used_percent": 27}]),
        events, {"extra_token_sources": ["grok_unified"]}, NOW, provider="grok_sessions")
    tok = bucket(suite, "cli")["tokens"]
    assert tok["value"] == 140  # 100+40, not plus the unified duplicate
    assert tok["source"] == "grok_sessions"
    assert tok["input_tokens"] == 100 and tok["output_tokens"] == 40
    assert tok["events"] == 1 and tok["sessions"] == 1
    assert tok["models"][0]["name"] == "grok-4"
    assert "double-count" in tok["note"]
    assert any("double-count" in h for h in suite["honesty"])
    assert bucket(suite, "bot")["tokens"]["value"] is None


def test_unified_used_only_when_sessions_have_no_events():
    ts = datetime(2026, 11, 4, 12, tzinfo=timezone.utc).timestamp()
    suite = build_grok_suite(
        billing(products=[{"product": "GrokBuild", "used_percent": 27}]),
        [ev(ts, 50, 5, "grok_unified")], {"extra_token_sources": ["grok_unified"]},
        NOW, provider="grok_sessions")
    tok = bucket(suite, "cli")["tokens"]
    assert tok["value"] == 55 and tok["source"] == "grok_unified"
    assert "not Grok Bot" in tok["note"]


def test_no_events_is_not_available_not_zero():
    suite = build_grok_suite(
        billing(products=[{"product": "GrokBuild", "used_percent": 27}]),
        [], {}, NOW, provider="grok_sessions",
        provider_note="no Grok CLI session logs at ~/.grok/sessions")
    tok = bucket(suite, "cli")["tokens"]
    assert tok["value"] is None
    assert "not" in tok["reason"].lower() or "No Grok CLI" in tok["reason"]
    assert "updates.jsonl" in tok["reason"]
    assert "~/.grok/sessions" in tok["reason"]


def test_unknown_product_is_kept_and_not_assigned():
    suite = build_grok_suite(billing(products=[
        {"product": "GrokWidget", "used_percent": 3},
    ]), [], {}, NOW, provider="grok_sessions")
    assert suite["other_products"][0]["product"] == "GrokWidget"
    assert all(b["used_percent"] is None for b in suite["buckets"])


def test_no_billing_reading():
    suite = build_grok_suite(None, [], {}, NOW, provider="grok_sessions")
    assert suite["used_percent"] is None
    assert "no Grok billing" in suite["total_reason"]
    assert all(b["used_percent"] is None for b in suite["buckets"])


def test_menu_lines_do_not_invent():
    suite = build_grok_suite(billing(products=[
        {"product": "GrokBot", "used_percent": 20},
        {"product": "Cursor", "used_percent": 1},
        {"product": "GrokChat", "used_percent": 0},
    ]), [], {}, NOW, provider="grok_sessions")
    lines = menu_lines(suite)
    assert lines[0].startswith("Grok suite: 73% left")
    assert any(x.startswith("Grok CLI:") and "not available" in x for x in lines)
    assert any(x.startswith("Grok Bot: 20% of pool") and "tokens not available" in x for x in lines)
    assert any(x.startswith("Cursor (Grok): 1% of pool") for x in lines)
    assert any(x.startswith("Also in pool: Grok Chat") for x in lines)
