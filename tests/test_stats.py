from datetime import date, datetime, timedelta, timezone

from omnimonitor import stats

D = date(2026, 11, 3)


def days(*offsets):
    return {D - timedelta(days=o) for o in offsets}


def test_longest_streak():
    assert stats.longest_streak(set()) == 0
    assert stats.longest_streak(days(0)) == 1
    assert stats.longest_streak(days(0, 1, 2, 5, 6)) == 3
    assert stats.longest_streak(days(10, 11, 12, 13, 0)) == 4


def test_current_streak_counts_from_today_or_yesterday():
    assert stats.current_streak(days(0, 1, 2), D) == 3
    assert stats.current_streak(days(1, 2), D) == 2  # not broken just because today is empty
    assert stats.current_streak(days(2, 3), D) == 0
    assert stats.current_streak(set(), D) == 0


def test_peak_day():
    assert stats.peak_day({}) is None
    assert stats.peak_day({D: 5, D - timedelta(days=1): 9}) == (D - timedelta(days=1), 9)
    assert stats.peak_day({D: 0}) is None


def test_estimate_rules():
    assert stats.estimate_weekly_limit(1000, None)[0] is None
    v, why = stats.estimate_weekly_limit(1000, 2.0, 5.0)
    assert v is None and "5%" in why
    assert stats.estimate_weekly_limit(1000, 120.0)[0] is None
    assert stats.estimate_weekly_limit(0, 50.0)[0] is None
    assert stats.estimate_weekly_limit(1000, 25.0)[0] == 4000


def test_heatmap_starts_on_sunday_and_ends_today():
    cells = stats.heatmap({D: 7}, D, 53)
    first = date.fromisoformat(cells[0]["date"])
    assert first.weekday() == 6  # Sunday
    assert cells[-1] == {"date": D.isoformat(), "tokens": 7}


UTC = timezone.utc
NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)


def ev(dt, inp, out, model="m", sid="s"):
    return {"ts": dt.timestamp(), "input_tokens": inp, "output_tokens": out, "model": model,
            "session_id": sid, "cache_read_tokens": 0, "reasoning_tokens": 0, "cost_usd": None}


BILLING = {"used_percent": 20.0, "raw_used_percent": 20.0,
           "period_start": "2026-11-02T00:00:00+00:00", "resets_at": "2026-11-09T00:00:00+00:00",
           "products": [{"product": "GrokBuild", "used_percent": 10.0}], "token_fields": {}}


def test_compute_no_data_is_not_available_never_zero():
    out = stats.compute([], [], BILLING, {}, NOW, provider="grok_sessions", tz=UTC)
    for k in ("lifetime_tokens", "peak_day", "longest_streak", "current_streak", "week_tokens",
              "longest_task"):
        assert out[k]["value"] is None and out[k]["reason"]
    assert out["weekly_limit"]["value"] is None and out["weekly_limit"]["estimate"] is None
    assert out["insights"] == [] and out["top_plugins"] == []


def test_compute_none_provider():
    out = stats.compute([], [], None, {}, NOW, provider="none", tz=UTC)
    assert "off" in out["lifetime_tokens"]["reason"]


def test_compute_week_uses_billing_window_and_grokbuild_share():
    events = [ev(datetime(2026, 11, 1, 23, tzinfo=UTC), 1000, 0),   # before week
              ev(datetime(2026, 11, 3, 10, tzinfo=UTC), 800, 200),
              ev(datetime(2026, 11, 4, 10, tzinfo=UTC), 900, 100, model="x")]
    turns = [{"ts": 1, "duration_s": 30.0, "duration_source": "reported"},
             {"ts": 2, "duration_s": 90.0, "duration_source": "derived from log timestamps"}]
    out = stats.compute(events, turns, BILLING, {}, NOW, provider="grok_sessions", tz=UTC)
    assert out["lifetime_tokens"]["value"] == 3000
    assert out["week_tokens"] == {"value": 2000, "window": "current billing week",
                                  "grok_value": 2000}
    est = out["weekly_limit"]["estimate"]
    assert est["value"] == 20000 and est["basis"] == "Grok Build share"
    assert "ESTIMATE" in est["caveat"]
    assert out["weekly_limit"]["value"] is None
    assert out["longest_task"]["value"] == 90.0
    assert out["longest_streak"]["value"] == 2  # Nov 3-4
    assert out["current_streak"]["value"] == 2  # ends yesterday (Nov 4): still counts
    assert out["peak_day"]["value"] == 1000


def test_compute_estimate_can_be_disabled_and_threshold():
    events = [ev(NOW - timedelta(hours=1), 100, 0)]
    out = stats.compute(events, [], BILLING, {"estimate_weekly_limit": False}, NOW,
                        provider="grok_sessions", tz=UTC)
    assert out["weekly_limit"]["estimate"] is None
    low = dict(BILLING, products=[{"product": "GrokBuild", "used_percent": 1.0}])
    out = stats.compute(events, [], low, {}, NOW, provider="grok_sessions", tz=UTC)
    assert out["weekly_limit"]["estimate"] is None
    assert "at least" in out["weekly_limit"]["estimate_unavailable"]


def test_compute_rolling_window_without_billing():
    events = [ev(NOW - timedelta(days=8), 5, 0), ev(NOW - timedelta(days=1), 7, 0)]
    out = stats.compute(events, [], None, {}, NOW, provider="grok_sessions", tz=UTC)
    assert out["week_tokens"]["value"] == 7
    assert "last 7 days" in out["week_tokens"]["window"]


def test_real_limit_field_wins_over_estimate():
    b = dict(BILLING, token_fields={"weeklyTokenLimit": 5_000_000})
    out = stats.compute([ev(NOW, 10, 0)], [], b, {}, NOW, provider="grok_sessions", tz=UTC)
    assert out["weekly_limit"]["value"] == 5_000_000
