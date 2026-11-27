"""Run-out estimates for every plan window, "limit reset" notices and webhook alerts."""
import json
from datetime import datetime, timezone

import pytest

from omnimonitor import alerts, apikeys, cli_accounts, cli_logins, outbound, overview
from omnimonitor import config as config_mod
from omnimonitor.accounts import Reading
from omnimonitor.web_accounts import parse_plan_windows

NOW = datetime(2026, 11, 27, 12, tzinfo=timezone.utc)
TS = NOW.timestamp()
CFG = {"notifications": True, "notify_thresholds": [80], "notify_on_reset": True,
       "notify_on_pace": True}


def reading(used, hours_left, window=18000, pid="claude_cli_plan"):
    r = Reading(pid, True, TS)
    r.limits.append({"name": "5-hour", "used_percent": used,
                     "resets_at": TS + hours_left * 3600, "window_seconds": window})
    return {pid: r.to_dict()}


def build(readings, cfg=None):
    return overview.build([], dict(config_mod.DEFAULT_CONFIG, **(cfg or {})), NOW, tools=[],
                          readings=readings)


def test_claude_windows_carry_their_length(monkeypatch):
    monkeypatch.setattr(cli_logins, "claude",
                        lambda cfg, now: type("L", (), {"token": "t"})())
    data = {"five_hour": {"utilization": 10, "resets_at": "2026-11-27T14:00:00Z"},
            "seven_day": {"utilization": 20, "resets_at": "2026-11-30T14:00:00Z"}}
    r = cli_accounts.fetch_claude({}, lambda *a: (200, data), TS)
    assert [w["window_seconds"] for w in r.limits] == [18000, 604800]


def test_chatgpt_windows_carry_their_length():
    r = Reading("codex_cli_plan", True, TS)
    parse_plan_windows({"rate_limit": {"primary_window": {
        "used_percent": 10, "limit_window_seconds": 18000, "reset_at": TS + 3600}}}, r)
    assert r.limits[0]["window_seconds"] == 18000


def test_plan_window_gets_a_run_out_estimate():
    # 3 of 5 hours gone, 90 % used -> 100 % after 20 more minutes, before the reset
    ov = build(reading(90, 2))
    pace = ov["cards"][0]["limits"][0]["pace"]
    assert pace["runs_out_at"] == pytest.approx(TS + 1200, abs=1)
    line = overview.suite_menu_lines(ov, now=TS)[0]
    assert "runs out in 0h 20m (est.)" in line


@pytest.mark.parametrize("used,hours_left", [(20, 4.9), (30, 1)])
def test_no_run_out_claim_when_too_early_or_on_track(used, hours_left):
    ov = build(reading(used, hours_left))
    assert "runs out" not in overview.suite_menu_lines(ov, now=TS)[0]
    assert [m for m in alerts.decide_providers(CFG, ov, {})[0] if m["kind"] == "pace"] == []


def test_run_out_alert_fires_once_per_window_and_only_when_asked():
    ov = build(reading(70, 3))  # 2 h gone, 70 % used
    msgs, st = alerts.decide_providers(CFG, ov, {})
    assert [m["kind"] for m in msgs] == ["pace"] and "ESTIMATE" in msgs[0]["body"]
    assert alerts.decide_providers(CFG, ov, st)[0] == []
    assert alerts.decide_providers(dict(CFG, notify_on_pace=False), ov, {})[0] == []


def test_reset_notice_only_after_a_window_that_alerted():
    _, st = alerts.decide_providers(CFG, build(reading(85, 0.1)), {})
    msgs, st = alerts.decide_providers(CFG, build(reading(2, 4.9)), st)
    assert [m["kind"] for m in msgs] == ["reset"] and "reset" in msgs[0]["title"]
    assert alerts.decide_providers(CFG, build(reading(3, 4.8)), st)[0] == []
    _, quiet = alerts.decide_providers(CFG, build(reading(40, 0.1)), {})
    assert [m for m in alerts.decide_providers(CFG, build(reading(2, 4.9)), quiet)[0]
            if m["kind"] == "reset"] == []


def test_reset_notice_can_be_turned_off():
    cfg = dict(CFG, notify_on_reset=False, notify_on_pace=False)
    _, st = alerts.decide_providers(cfg, build(reading(85, 0.1)), {})
    assert alerts.decide_providers(cfg, build(reading(2, 4.9)), st)[0] == []


# ----------------------------------------------------------------------------- webhook
class Opener:
    def __init__(self, status=200, boom=None):
        self.requests, self.status, self.boom = [], status, boom

    def open(self, req, timeout):
        self.requests.append(req)
        if self.boom:
            raise self.boom
        response = type("R", (), {"status": self.status})()
        response.__enter__ = None

        class Ctx:
            def __enter__(_):
                return response

            def __exit__(*_):
                return False
        return Ctx()


@pytest.mark.parametrize("url,check", [
    ("https://discord.com/api/webhooks/1/abc",
     lambda r: json.loads(r.data) == {"content": "**T**\nB",
                                      "allowed_mentions": {"parse": []}}),
    ("https://hooks.slack.com/services/a/b/c",
     lambda r: json.loads(r.data) == {"text": "T\nB"}),
    ("https://ntfy.sh/my-topic",
     lambda r: r.data == b"B" and r.get_header("Title") == "T"),
    ("ntfy+https://push.example.org/topic",
     lambda r: r.full_url == "https://push.example.org/topic" and r.data == b"B"),
    ("https://example.org/hook",
     lambda r: json.loads(r.data) == {"app": "OmniMonitor", "title": "T", "body": "B"}),
])
def test_webhook_formats(url, check):
    op = Opener()
    assert outbound.send("T", "B", url=url, opener=op) == (True, "sent")
    assert check(op.requests[0]) and op.requests[0].get_method() == "POST"


@pytest.mark.parametrize("url", ["http://example.org/h", "file:///etc/passwd",
                                 "https://user:pw@example.org/h", "not a url", "https://?q=1"])
def test_webhook_refuses_unsafe_urls(url):
    op = Opener()
    ok, why = outbound.send("T", "B", url=url, opener=op)
    assert not ok and not op.requests and url not in why


def test_webhook_errors_never_repeat_the_url():
    secret = "https://example.org/hook/SECRET123"
    for op in (Opener(boom=OSError("failed for " + secret)), Opener(status=500)):
        ok, why = outbound.send("T", "B", url=secret, opener=op)
        assert not ok and "SECRET123" not in why


def test_webhook_titles_cannot_inject_headers():
    op = Opener()
    outbound.send("A\r\nX-Evil: 1 é", "B", url="https://ntfy.sh/t", opener=op)
    assert "\n" not in op.requests[0].get_header("Title")
    assert op.requests[0].get_header("Title").isascii()


def test_webhook_url_is_a_secret_and_off_by_default(monkeypatch):
    assert outbound.NAME in apikeys.allowed_names()
    monkeypatch.delenv(outbound.NAME, raising=False)
    assert outbound.configured_url() == ""
    assert outbound.send("T", "B", opener=Opener())[0] is False


def test_webhook_does_not_follow_redirects():
    assert outbound._NoRedirect().redirect_request() is None


def test_controller_alerts_also_go_to_the_webhook(monkeypatch):
    from omnimonitor.app import Controller
    sent = []
    monkeypatch.setattr(alerts, "deliver", lambda *a: sent.append("desktop"))
    monkeypatch.setattr(outbound, "configured_url", lambda: "https://example.org/h")
    monkeypatch.setattr(outbound, "send", lambda t, b, **k: sent.append(t) or (True, "sent"))
    ctrl = Controller(dict(config_mod.DEFAULT_CONFIG))
    ctrl._send_alert({"kind": "provider", "title": "Claude at 85%", "body": "b"})
    assert sent == ["desktop", "Claude at 85%"] and ctrl.sent_alerts[-1]["title"]


def test_no_reset_time_and_usage_hovering_at_a_level_does_not_flap():
    def ov(used):
        return {"is_demo": False, "budgets": {}, "cards": [{"id": "x", "name": "X", "limits": [
            {"name": "limit", "used_percent": used, "resets_at": None}]}]}
    cfg = dict(CFG, notify_on_pace=False)
    msgs, st = alerts.decide_providers(cfg, ov(80.4), {})
    assert len(msgs) == 1
    for used in (79.8, 80.1, 79.2, 80.6):
        msgs, st = alerts.decide_providers(cfg, ov(used), st)
        assert msgs == []
    msgs, st = alerts.decide_providers(cfg, ov(10), st)
    assert [m["kind"] for m in msgs] == ["reset"]


@pytest.mark.parametrize("used,hours_left", [(0, 4.5), (3, 4.99)])
def test_no_pace_at_all_when_nothing_can_be_projected(used, hours_left):
    """The dashboard printed "NaN% at reset" for a pace without a projection."""
    assert build(reading(used, hours_left))["cards"][0]["limits"][0]["pace"] is None


def test_secrets_set_says_where_the_value_really_went(monkeypatch, capsys, tmp_path):
    import io

    from omnimonitor import cli, vault
    monkeypatch.setenv("OMNIMONITOR_SECRETS_FILE", str(tmp_path / "secrets.json"))
    monkeypatch.setenv(outbound.NAME, "https://example.org/from-env")
    monkeypatch.setattr(vault, "available", lambda: False)
    monkeypatch.setattr("sys.stdin", io.StringIO("https://example.org/saved\n"))
    assert cli.main(["secrets", "set", outbound.NAME]) == 0
    out = capsys.readouterr().out
    assert "plain text" in out and "encrypted" not in out and "example.org" not in out
