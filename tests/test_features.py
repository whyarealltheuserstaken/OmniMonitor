"""Pricing, forecast, alerts, export, settings validation, history migration."""
import csv
import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from omnimonitor import alerts, export, forecast, pricing, settings
from omnimonitor.history import History

UTC = timezone.utc
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


# ------------------------------------------------------------------ pricing
def test_price_lookup_exact_then_longest_glob():
    p = pricing.clean_prices({"grok-*": {"input": 1, "output": 2},
                              "grok-code-*": {"input": 3, "output": 4},
                              "grok-code-fast-1": {"input": 5, "output": 6},
                              "bad": {"input": "x"}, "neg": {"input": -1, "output": 1}})
    assert set(p) == {"grok-*", "grok-code-*", "grok-code-fast-1"}
    assert pricing.price_for("grok-code-fast-1", p)["input"] == 5
    assert pricing.price_for("GROK-CODE-x", p)["input"] == 3
    assert pricing.price_for("grok-4", p)["input"] == 1
    assert pricing.price_for("claude", p) is None


def test_cost_splits_cache_and_reports_unpriced():
    prices = {"m": {"input": 10, "cached_input": 1, "cache_write": 20, "output": 100}}
    e = {"model": "m", "input_tokens": 1_000_000 + 2_000_000 + 500_000,
         "cache_read_tokens": 2_000_000, "cache_creation_tokens": 500_000,
         "output_tokens": 100_000}
    assert pricing.estimate_event_cost(e, prices) == pytest.approx(10 + 2 + 10 + 10)
    s = pricing.summarize_cost([e, {"model": "other", "input_tokens": 5, "output_tokens": 5,
                                    "cost_usd": 0.5}], prices)
    assert s["estimated_usd"] == pytest.approx(32)
    assert s["recorded_usd"] == 0.5 and s["unpriced_tokens"] == 10
    assert s["unpriced_models"] == ["other"]
    none = pricing.summarize_cost([{"model": "x", "input_tokens": 1, "output_tokens": 1}], {})
    assert none["estimated_usd"] is None and none["recorded_usd"] is None  # never invented


# ------------------------------------------------------------------ forecast
def bill(used, start_h_ago=48, reset_in_h=120, **kw):
    b = {"used_percent": used, "period_start": (NOW - timedelta(hours=start_h_ago)).isoformat(),
         "resets_at": (NOW + timedelta(hours=reset_in_h)).isoformat()}
    b.update(kw)
    return b


def test_average_pace_projection():
    f = forecast.pace(bill(40), NOW)
    a = f["average"]
    assert a["rate_per_hour"] == pytest.approx(40 / 48)
    assert a["percent_at_reset"] == pytest.approx(40 + 40 / 48 * 120)  # 140 %
    assert a["status"] == "ahead"  # even pace after 48/168 h is ~28.6 %
    runs_out = datetime.fromisoformat(a["runs_out_at"])
    assert runs_out == NOW + timedelta(hours=60 / (40 / 48))
    assert "ESTIMATE" in a["kind"]


def test_pace_not_available_cases():
    assert forecast.pace(None, NOW)["average"]["value"] is None
    assert "reset" in forecast.pace({"used_percent": 5}, NOW)["average"]["reason"]
    no_start = forecast.pace({"used_percent": 5, "resets_at": NOW.isoformat()}, NOW)
    assert "started" in no_start["average"]["reason"]
    early = forecast.pace(bill(1, start_h_ago=0.5), NOW)
    assert early["average"]["value"] is None and "early" in early["average"]["reason"]
    slow = forecast.pace(bill(10), NOW)["average"]
    assert slow["runs_out_at"] is None and slow["status"] == "behind"


def test_recent_pace_from_real_snapshots_only():
    reset = (NOW + timedelta(hours=120)).isoformat()
    snaps = [{"ts": (NOW - timedelta(hours=2)).timestamp(), "used_percent": 30,
              "period_end": reset},
             {"ts": (NOW - timedelta(minutes=1)).timestamp(), "used_percent": 40,
              "period_end": reset},
             {"ts": (NOW - timedelta(hours=1)).timestamp(), "used_percent": 99,
              "period_end": "2020-01-01T00:00:00+00:00"}]  # other period: ignored
    r = forecast.pace(bill(40), NOW, snaps)["recent"]
    assert r["rate_per_hour"] == pytest.approx(10 / (119 / 60))
    one = forecast.pace(bill(40), NOW, snaps[:1])["recent"]
    assert one["value"] is None and "2 saved readings" in one["reason"]


def test_burn_rate_counts_real_events():
    ts = NOW.timestamp()
    evs = [{"ts": ts - 60, "input_tokens": 100, "output_tokens": 10},
           {"ts": ts - 7200, "input_tokens": 50, "output_tokens": 0},
           {"ts": ts - 86400 * 3, "input_tokens": 999, "output_tokens": 0}]
    b = forecast.burn_rate(evs, NOW, tz=UTC)
    assert b["last_hour"] == 110 and b["today"] == 160 and b["active_hours_today"] == 2
    assert b["per_active_hour_today"] == 80
    assert forecast.burn_rate([], NOW, tz=UTC)["per_active_hour_today"] is None


# ------------------------------------------------------------------ alerts
CFG = {"notifications": True, "notify_thresholds": [80, 95], "notify_on_reset": True,
       "notify_on_pace": True}


def test_threshold_fires_once_per_period_and_reset():
    b = bill(50)
    msgs, st = alerts.decide(CFG, b, None, {})
    assert msgs == []
    msgs, st = alerts.decide(CFG, bill(96), None, st)
    assert [m["kind"] for m in msgs] == ["threshold"] and "95" in msgs[0]["body"]
    msgs, st = alerts.decide(CFG, bill(97), None, st)
    assert msgs == []  # already fired for this period
    nb = bill(3, reset_in_h=288)
    msgs, st = alerts.decide(CFG, nb, None, st)
    assert [m["kind"] for m in msgs] == ["reset"]
    msgs, st = alerts.decide(CFG, bill(81, reset_in_h=288), None, st)
    assert [m["kind"] for m in msgs] == ["threshold"]


def test_alerts_never_for_demo_or_when_disabled():
    assert alerts.decide(CFG, bill(99, is_demo=True), None, {})[0] == []
    assert alerts.decide(dict(CFG, notifications=False), bill(99), None, {})[0] == []


def test_pace_alert_once():
    fc = forecast.pace(bill(40), NOW)
    msgs, st = alerts.decide(dict(CFG, notify_thresholds=[]), bill(40), fc, {})
    assert [m["kind"] for m in msgs] == ["pace"] and "ESTIMATE" in msgs[0]["body"]
    assert alerts.decide(dict(CFG, notify_thresholds=[]), bill(41), fc, st)[0] == []


def test_alert_state_persists(tmp_path):
    h = History(tmp_path / "h.db")
    alerts.save_state(h, {"period": "p", "fired": [80]})
    assert alerts.load_state(h) == {"period": "p", "fired": [80]}
    assert alerts.thresholds({"notify_thresholds": "95, 80, x, 0, 200"}) == [80, 95]


# ------------------------------------------------------------------ export
EVS = [{"ts": NOW.timestamp(), "model": "=cmd|' /C calc'!A0", "source": "codex",
        "project": "@evil", "input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 4,
        "cache_creation_tokens": 0, "reasoning_tokens": 1, "cost_usd": None,
        "session_id": "s"},
       {"ts": NOW.timestamp() + 10, "model": "m", "source": "grok_sessions", "project": "p",
        "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.25}]


def test_export_csv_neutralizes_formulas():
    text = export.to_csv(export.rows(EVS, "breakdown"))
    rows = list(csv.DictReader(io.StringIO(text)))
    assert {r["model"] for r in rows} == {"'=cmd|' /C calc'!A0", "m"}
    assert "'@evil" in {r["project"] for r in rows}
    assert {r["tool"] for r in rows} == {"Codex", "Grok CLI"}


def test_export_daily_and_json():
    d = export.rows(EVS, "daily", tz=UTC)
    assert d == [dict(date="2026-10-05", events=2, input_tokens=11, cache_read_tokens=4,
                      cache_creation_tokens=0, output_tokens=6, reasoning_tokens=1,
                      total_tokens=17, recorded_cost_usd=0.25)]
    doc = json.loads(export.to_json(EVS, is_demo=True, tz=UTC))
    assert doc["is_demo"] and "DEMO" in doc["note"] and len(doc["daily_models"]) == 2
    assert len(export.rows(EVS, "events")) == 2
    with pytest.raises(ValueError):
        export.rows(EVS, "nope")


# ------------------------------------------------------------------ settings
def test_settings_validation():
    clean, errs = settings.apply({}, {"theme": "light", "accent_color": "#AABBCC",
                                      "notify_thresholds": "95,50", "display_name": " Hi\x07 ",
                                      "hidden_cards": ["cost"], "poll_interval_seconds": "90",
                                      "prices": '{"m": {"input": 1, "output": 2}}'}, web=True)
    assert errs == {}
    assert clean["accent_color"] == "#aabbcc" and clean["notify_thresholds"] == [50, 95]
    assert clean["display_name"] == "Hi" and clean["poll_interval_seconds"] == 90
    assert clean["prices"] == {"m": {"input": 1.0, "output": 2.0}}
    _, errs = settings.apply({}, {"theme": "neon", "accent_color": "red",
                                  "poll_interval_seconds": 5, "hidden_cards": ["x"],
                                  "prices": {"m": {"input": 1}}}, web=True)
    assert set(errs) == {"theme", "accent_color", "poll_interval_seconds", "hidden_cards",
                         "prices"}


@pytest.mark.parametrize("key", ["token", "command", "grok_refresh_command",
                                 "grok_billing_url", "usage_page_url", "codex_home",
                                 "token_source", "source"])
def test_web_cannot_set_sensitive_keys(key):
    _, errs = settings.apply({}, {key: "x"}, web=True)
    assert key in errs


def test_public_view_has_no_secrets():
    v = settings.public_view({"token": "SECRET", "command": "rm", "theme": "dark",
                              "grok_billing_url": "http://x"})
    blob = json.dumps(v)
    assert "SECRET" not in blob and "rm" not in v and "grok_billing_url" not in v
    assert v["theme"] == "dark" and "hideable_cards" in v["_readonly"]
    assert settings.parse_cli_value("[1,2]") == [1, 2]
    assert settings.parse_cli_value("dark") == "dark"


# ------------------------------------------------------------------ history migration
def test_history_migrates_0_1_database(tmp_path):
    p = tmp_path / "old.db"
    db = sqlite3.connect(str(p))
    db.executescript("""
    CREATE TABLE token_events (event_id TEXT NOT NULL, model TEXT NOT NULL, ts REAL NOT NULL,
        session_id TEXT, input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
        cache_read_tokens INTEGER DEFAULT 0, cache_creation_tokens INTEGER DEFAULT 0,
        reasoning_tokens INTEGER DEFAULT 0, cost_usd REAL, source TEXT,
        PRIMARY KEY (event_id, model));
    CREATE TABLE scan_state (path TEXT PRIMARY KEY, size INTEGER, mtime REAL,
        offset INTEGER, turn_start_ms REAL);
    INSERT INTO token_events(event_id, model, ts, input_tokens, output_tokens)
        VALUES ('e1', 'grok-x', 1000, 5, 6);
    INSERT INTO scan_state VALUES ('/f', 10, 1.0, 10, NULL);
    """)
    db.commit()
    db.close()
    h = History(p)
    (e,) = h.events()
    assert e["source"] == "grok_sessions" and e["project"] in ("", None)
    assert h.scan_state("/f")[2] == 10 and h.scan_ctx("/f") == {}
    h.set_scan_ctx("/f", 10, 1.0, 10, {"model": "m"})
    assert h.scan_ctx("/f") == {"model": "m"}
