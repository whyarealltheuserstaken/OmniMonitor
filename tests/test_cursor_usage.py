"""Fixtures for Cursor usage-status dollars and filtered usage events."""

import json
from datetime import datetime, timezone

from omnimonitor.cursor_usage import (
    fetch_filtered_events,
    parse_event,
    parse_events_page,
    parse_usage_status,
    reconcile_pages,
    summarize_events,
)

NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)

STATUS = {
    "availability": "available",
    "monthlyUsage": {
        "usedCents": 250,
        "limitCents": 2000,
        "periodStart": "2026-11-01T00:00:00Z",
        "resetsAt": "2026-12-01T00:00:00Z",
    },
}

EVENTS = {
    "totalUsageEventsCount": 2,
    "usageEventsDisplay": [
        {
            "timestamp": "1700000000000",
            "model": "grok-4",
            "kind": "USAGE_EVENT_KIND_CHAT",
            "tokenUsage": {
                "inputTokens": 10, "outputTokens": 4,
                "cacheReadTokens": 2, "cacheWriteTokens": 1, "totalCents": 5,
            },
            "chargedCents": 4,
        },
        {
            "timestamp": "1700000001000",
            "model": "gpt-5",
            "kind": "USAGE_EVENT_KIND_CHAT",
            "tokenUsage": {"inputTokens": 3, "outputTokens": 1, "totalCents": 1},
        },
    ],
}


def test_usage_status_is_dollars_and_missing_fields_are_not_zero():
    parsed = parse_usage_status(STATUS, NOW)
    assert parsed["available"]
    assert parsed["used_usd"] == 2.5
    assert parsed["limit_usd"] == 20.0
    assert parsed["used_cents"] == 250
    outside = parse_usage_status(STATUS, datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert not outside["available"] and "period" in outside["reason"]
    missing = dict(STATUS)
    missing["monthlyUsage"] = {"limitCents": 10, "periodStart": STATUS["monthlyUsage"]["periodStart"],
                               "resetsAt": STATUS["monthlyUsage"]["resetsAt"]}
    no_used = parse_usage_status(missing, NOW)
    assert not no_used["available"] and "usedCents" in no_used["reason"]
    assert "0" not in no_used["reason"]
    unavailable = parse_usage_status({"availability": "unavailable", "monthlyUsage": STATUS["monthlyUsage"]}, NOW)
    assert not unavailable["available"]


def test_events_split_grok_models_and_do_not_call_them_grok_bot():
    page = parse_events_page(EVENTS)
    assert page["ok"] and page["total"] == 2
    summary = summarize_events(page["events"])
    assert summary["models"] == ["gpt-5", "grok-4"]
    grok = summary["grok_in_cursor"]
    assert grok["available"] and grok["value"] == 17
    assert grok["input_tokens"] == 10 and grok["output_tokens"] == 4
    assert grok["cache_read_tokens"] == 2 and grok["cache_write_tokens"] == 1
    assert grok["cost_usd"] == 0.05
    overall = summary["cursor_tokens"]
    assert overall["value"] == 21
    assert overall["cost_usd"] == 0.06
    assert not summary["grok_bot_labeled"]["available"]
    assert "Grok Bot" in summary["grok_bot_labeled"]["reason"]


def test_labeled_grok_bot_is_the_only_bot_attribution():
    raw = {
        "timestamp": "1700000000000",
        "model": "grok-bot",
        "kind": "chat",
        "tokenUsage": {"inputTokens": 8, "outputTokens": 1, "totalCents": 2},
    }
    ev = parse_event(raw)
    assert ev["grok"] and ev["grok_bot"]
    plain = parse_event(dict(raw, model="grok-4", kind="sand"))
    assert plain["grok"] and not plain["grok_bot"]
    summary = summarize_events([ev])
    assert summary["grok_bot_labeled"]["available"]
    assert summary["grok_bot_labeled"]["value"] == 9
    assert summary["grok_in_cursor"]["value"] == 9


def test_empty_page_and_bad_count():
    assert parse_events_page({})["total"] == 0
    assert parse_events_page({"totalUsageEventsCount": 0})["events"] == []
    bad = parse_events_page({"totalUsageEventsCount": -1, "usageEventsDisplay": []})
    assert not bad["ok"]
    omitted = parse_events_page({"usageEventsDisplay": [{
        "timestamp": "1700000000000", "model": "grok-4",
        "tokenUsage": {"inputTokens": 1, "outputTokens": 1},
    }]})
    summary = summarize_events(omitted["events"])
    assert summary["grok_in_cursor"]["value"] == 2
    assert summary["grok_in_cursor"]["cost_usd"] is None


def test_pagination_drops_only_proven_boundary_duplicates():
    row = parse_event({
        "timestamp": "1700000000000", "model": "grok-4",
        "tokenUsage": {"inputTokens": 1, "outputTokens": 1, "totalCents": 1},
    })
    other = parse_event({
        "timestamp": "1700000001000", "model": "gpt-5",
        "tokenUsage": {"inputTokens": 4, "outputTokens": 0, "totalCents": 1},
    })
    reconciled = reconcile_pages([[row, other], [other]], 2)
    assert reconciled["ok"]
    assert len(reconciled["events"]) == 2
    inconsistent = reconcile_pages([[row], [other]], 1)
    assert not inconsistent["ok"]
    assert inconsistent["events"] == []


def test_fetch_sends_origin_and_millis_and_stops(monkeypatch):
    seen = []

    def http(method, url, headers, body):
        seen.append((method, url, headers.get("Origin"), json.loads(body)))
        if len(seen) == 1:
            return 200, EVENTS
        return 200, {"totalUsageEventsCount": 2, "usageEventsDisplay": []}

    result = fetch_filtered_events(http, {"Cookie": "WorkosCursorSessionToken=SECRET",
                                          "Origin": "https://cursor.com"}, 10, 20, page_size=100)
    assert result["ok"]
    assert seen[0][0] == "POST"
    assert seen[0][1].endswith("/api/dashboard/get-filtered-usage-events")
    assert seen[0][2] == "https://cursor.com"
    assert seen[0][3]["startDate"] == "10000"
    assert seen[0][3]["page"] == 1
    assert result["grok_in_cursor"]["value"] == 17
    blob = json.dumps({k: v for k, v in result.items() if k != "events"})
    assert "SECRET" not in blob
