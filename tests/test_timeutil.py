from datetime import datetime, timedelta, timezone

from omnimonitor.timeutil import fmt_duration, fmt_until, parse_time


def test_iso_variants():
    assert parse_time("2026-11-09T00:00:00Z") == datetime(2026, 11, 9, tzinfo=timezone.utc)
    assert parse_time("2026-11-09T00:00:00.123456789Z").microsecond == 123456
    assert parse_time("2026-11-09T00:00:00.1+00:00").microsecond == 100000
    assert parse_time("2026-11-09T00:00:00").tzinfo is not None


def test_epoch_seconds_and_ms():
    a = parse_time(1762300000)
    b = parse_time(1762300000000)
    assert a == b
    assert parse_time("1762300000") == a


def test_garbage():
    for v in (None, "", "soon", True, -1, float("nan"), {}):
        assert parse_time(v) is None


def test_formatters():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert fmt_until(now + timedelta(days=2, hours=3), now) == "in 2d 3h"
    assert fmt_until(now - timedelta(seconds=1), now) == "now"
    assert fmt_duration(3725) == "1h 2m"
    assert fmt_duration(None) == "not available"
