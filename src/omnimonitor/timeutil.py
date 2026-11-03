"""Small, dependency-free time helpers (Python 3.9+)."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Optional


def parse_time(value: Any) -> Optional[datetime]:
    """Parse ISO-8601 (with Z / any fractional digits) or epoch seconds/ms.

    Returns an aware UTC-or-offset datetime, or None if it cannot be parsed.
    """
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v != v or v <= 0:  # NaN / non-positive
            return None
        if v >= 1e11:  # milliseconds (1e11 s would be year 5138)
            v /= 1000.0
        try:
            return datetime.fromtimestamp(v, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return parse_time(float(s))
    s = s.replace("Z", "+00:00").replace("z", "+00:00")
    # Python 3.9's fromisoformat only accepts 3 or 6 fractional digits: normalise to 6.
    m = re.match(r"^(.*T\d\d:\d\d:\d\d)(\.\d+)?(.*)$", s)
    if m:
        frac = m.group(2) or ""
        if frac:
            frac = "." + (frac[1:] + "000000")[:6]
        s = m.group(1) + frac + m.group(3)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def local_day(ts: float, tz=None) -> date:
    """Calendar day of a unix timestamp in the local (or given) timezone."""
    return datetime.fromtimestamp(ts, tz=tz).date() if tz else datetime.fromtimestamp(ts).date()


def fmt_local(dt: Optional[datetime]) -> str:
    """Cross-platform friendly local time ("Tue Nov 3, 4:05 PM PST"). No %-d (POSIX-only)."""
    if dt is None:
        return "unknown"
    loc = dt.astimezone()
    hour = loc.hour % 12 or 12
    ampm = "AM" if loc.hour < 12 else "PM"
    return ("%s %s %d, %d:%02d %s %s" % (
        loc.strftime("%a"), loc.strftime("%b"), loc.day, hour, loc.minute, ampm,
        loc.tzname() or "")).strip()


def fmt_until(dt: Optional[datetime], now: Optional[datetime] = None) -> str:
    if dt is None:
        return ""
    now = now or utcnow()
    secs = (dt - now).total_seconds()
    if secs <= 0:
        return "now"
    d, rem = divmod(int(secs), 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return ("in %dd %dh" % (d, h)) if d else ("in %dh %dm" % (h, m))


def fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "not available"
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return "%dh %dm" % (h, m)
    if m:
        return "%dm %ds" % (m, sec)
    return "%ds" % sec
