"""An alert fires once per limit window, even though providers don't report a stable reset
time. Claude's usage endpoint returns a reset time that differs by up to a second on every
read (seen on a real account, 2026-11-27)."""
from omnimonitor import alerts

CFG = {"notifications": True, "notify_thresholds": [80, 95], "notify_on_reset": False}
RESET = 1795989599.769


def ov(used, reset=RESET, name="Weekly", pid="claude_cli_plan"):
    return {"is_demo": False, "budgets": {},
            "cards": [{"id": pid, "name": "Claude plan", "limits": [
                {"name": name, "used_percent": used, "resets_at": reset, "stale": False}]}]}


def test_jittering_reset_time_does_not_repeat_the_alert():
    msgs, st = alerts.decide_providers(CFG, ov(85), {})
    assert len(msgs) == 1
    for jitter in (0.721, -0.5, 0.221):
        msgs, st = alerts.decide_providers(CFG, ov(86, RESET + jitter), st)
        assert msgs == []


def test_next_window_alerts_again():
    _, st = alerts.decide_providers(CFG, ov(85), {})
    msgs, _ = alerts.decide_providers(CFG, ov(82, RESET + 7 * 86400), st)
    assert [m["kind"] for m in msgs] == ["provider"]


def test_a_check_without_the_window_keeps_what_already_fired():
    _, st = alerts.decide_providers(CFG, ov(85), {})
    _, st = alerts.decide_providers(CFG, {"is_demo": False, "cards": [], "budgets": {}}, st)
    msgs, _ = alerts.decide_providers(CFG, ov(86), st)
    assert msgs == []


def test_state_saved_by_0_6_0_does_not_alert_again():
    old = {"claude_cli_plan:Weekly:%s" % RESET: [80.0]}
    msgs, st = alerts.decide_providers(CFG, ov(86, RESET + 0.4), old)
    assert msgs == []
    assert not any(k.startswith("claude_cli_plan:Weekly:") for k in st)


def test_window_without_a_reset_time_rearms_when_usage_drops():
    _, st = alerts.decide_providers(CFG, ov(85, None), {})
    assert alerts.decide_providers(CFG, ov(86, None), st)[0] == []
    _, st = alerts.decide_providers(CFG, ov(3, None), st)
    assert len(alerts.decide_providers(CFG, ov(81, None), st)[0]) == 1


def test_old_budget_periods_are_dropped_from_state():
    def budget(start, pct):
        return {"is_demo": False, "cards": [], "budgets": {"alert_percent": 80, "periods": {
            "daily": {"percent": pct, "starts_at": start, "spent_usd": 9.0, "limit_usd": 10.0}}}}
    msgs, st = alerts.decide_providers(CFG, budget(1000.0, 90), {})
    assert len(msgs) == 1
    assert alerts.decide_providers(CFG, budget(1000.0, 91), st)[0] == []
    _, st = alerts.decide_providers(CFG, budget(87400.0, 10), st)
    assert [k for k in st if k.startswith("budget:daily:")] == ["budget:daily:87400.0"]


def test_grok_pool_alert_survives_a_jittering_reset_time():
    def bill(used, reset):
        return {"used_percent": used, "resets_at": reset}
    msgs, st = alerts.decide(CFG, bill(85, "2026-11-30T00:00:00.100+00:00"), None, {})
    assert len(msgs) == 1
    msgs, _ = alerts.decide(CFG, bill(86, "2026-11-30T00:00:00.900+00:00"), None, st)
    assert msgs == []


def test_recent_pace_uses_snapshots_whose_reset_time_jitters():
    from datetime import datetime, timedelta, timezone

    from omnimonitor import forecast
    now = datetime(2026, 11, 27, 12, tzinfo=timezone.utc)
    reset = now + timedelta(days=2)
    snaps = [{"ts": now.timestamp() - 3600 * h, "used_percent": 50 - 5 * h,
              "period_end": (reset + timedelta(milliseconds=300 * h)).isoformat()}
             for h in (2, 1)]
    got = forecast.recent_pace(snaps, 50.0, now, None, reset)
    assert got.get("rate_per_hour") == 5.0
