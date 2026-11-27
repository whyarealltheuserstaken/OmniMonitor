"""Regression tests for the 0.6.0-beta pre-release review findings."""
import json
from importlib import resources
from pathlib import Path

from omnimonitor import (
    accounts,
    cli,
    cli_accounts,
    onboarding,
    redact,
    sessions,
    verify,
    web_accounts,
)
from omnimonitor import config as config_mod
from omnimonitor import settings as settings_mod
from omnimonitor.app import Controller


def _static(name):
    return resources.files("omnimonitor").joinpath("static", name).read_text("utf-8")


def _file(tmp_path, doc):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(doc))
    return p


def _isolated(tmp_path, **kw):
    none = str(tmp_path / "nothing")
    return _file(tmp_path, dict({"grok_home": none, "claude_home": none, "codex_home": none}, **kw))


# 1. the dashboard only drops a % figure that really is one of the limit bars
def test_claude_web_reports_every_window_and_marks_its_duplicates():
    sessions.save("claude_plan", "sessionKey=abc")

    def http(method, url, headers, body):
        if url.endswith("/organizations"):
            return 200, [{"uuid": "o1"}]
        return 200, {"five_hour": {"utilization": 20}, "seven_day": {"utilization": 70}}
    r = web_accounts.fetch("claude_plan", {}, http=http, now=1.0)
    assert [(w["name"], w["used_percent"]) for w in r.limits] == [
        ("5-hour session", 20.0), ("Weekly", 70.0)]
    assert all(m.get("in_limits") for m in r.metrics if m["unit"] == "%")


def test_plan_window_parsers_mark_duplicates(tmp_path):
    r = accounts.Reading("codex_cli_plan", True, 1.0)
    web_accounts.parse_plan_windows(
        {"rate_limit": {"primary_window": {"used_percent": 5}}}, r)
    assert r.metrics[0]["in_limits"] is True
    (tmp_path / ".credentials.json").write_text(json.dumps(
        {"claudeAiOauth": {"accessToken": "tok-123456"}}))
    r = cli_accounts.fetch("claude_cli_plan", {"claude_home": str(tmp_path)},
                           http=lambda *a: (200, {"five_hour": {"utilization": 3}}), now=1.0)
    assert r.metrics[0]["in_limits"] is True
    js = _static("providers.js")
    assert "m.in_limits" in js and 'm.unit === "%" && (c.limits || []).length' not in js
    assert "if (!want.limit) c.metrics" in js


# 2 + 3. setup actions are planned from the file, not from stale or forced-demo memory
def test_setup_action_keeps_a_config_edit_made_meanwhile(tmp_path):
    p = _isolated(tmp_path, source="none")
    ctrl = Controller(config_mod.load_config(p))
    doc = json.loads(p.read_text())
    doc["providers"] = {"openai_api": {"enabled": True}}
    p.write_text(json.dumps(doc))  # `providers enable openai_api` in a terminal
    assert ctrl.onboard({"action": "enable", "sources": ["codex"]})["ok"]
    saved = json.loads(p.read_text())["providers"]
    assert saved["openai_api"] == {"enabled": True} and saved["codex"] == {"enabled": True}
    assert ctrl.cfg["providers"]["openai_api"]["enabled"] is True


def test_setup_under_forced_demo_keeps_the_real_grok_pool(tmp_path):
    p = _isolated(tmp_path, source="grok_cli_billing")
    cfg = config_mod.load_config(p)
    cfg.update(source="demo", token_source="demo", _forced_demo=True)
    ctrl = Controller(cfg)
    assert ctrl.onboard({"action": "enable", "sources": ["codex"]})["ok"]
    saved = json.loads(p.read_text())
    assert saved["source"] == "grok_cli_billing" and "token_source" not in saved
    assert ctrl.cfg["source"] == "grok_cli_billing"


def test_cli_setup_demo_flag_does_not_rewrite_the_source(tmp_path, monkeypatch):
    monkeypatch.setattr("omnimonitor.billing.find_grok_binary", lambda cfg: None)
    logs = tmp_path / "claude" / "projects" / "p"
    logs.mkdir(parents=True)
    (logs / "s.jsonl").write_text("{}\n")
    p = _isolated(tmp_path, source="grok_cli_billing", claude_home=str(tmp_path / "claude"))
    cli.main(["--config", str(p), "--demo", "setup", "--yes"])
    saved = json.loads(p.read_text())
    assert saved["providers"]["claude_code"] == {"enabled": True}  # something was turned on
    assert saved["source"] == "grok_cli_billing"


# 4. a name that is both a source and a suite is not read as the suite
def test_layout_tells_the_editor_which_suite_names_are_also_sources():
    cfg = {"source": "none", "providers": {"deepseek": {"enabled": True},
                                           "codex": {"enabled": True}}}
    rows = {s["id"]: s for s in settings_mod.public_view(cfg)["_readonly"]["suites"]}
    assert rows["deepseek"]["shared_id"] is True and rows["openai"]["shared_id"] is False
    assert "shared_id" in _static("settings.js")


# 5. hiding Grok on the status line hides the saved Grok snapshot too
def test_statusline_respects_hidden_grok(tmp_path, capsys):
    p = _isolated(tmp_path, source="grok_cli_billing", token_source="grok_sessions")
    ctrl = Controller(config_mod.load_config(p))
    ctrl.history.add_billing_snapshot(42.0, 42.0, None, None, [])
    assert cli.main(["--config", str(p), "statusline"]) == 0
    assert "Grok 42% used" in capsys.readouterr().out
    for display in ({"hide": {"statusline": ["suite:xai"]}},
                    {"hide": {"statusline": ["grok"]}}, {"fields": {"statusline": []}}):
        doc = json.loads(p.read_text())
        doc["display"] = display
        p.write_text(json.dumps(doc))
        cli.main(["--config", str(p), "statusline"])
        assert "42%" not in capsys.readouterr().out, display


# 6. the layout editor lists Grok CLI logs exactly when they are being read
def test_layout_lists_grok_logs_when_they_are_in_use(tmp_path):
    (tmp_path / "grok" / "sessions").mkdir(parents=True)

    def listed(cfg):
        return [x["id"] for s in settings_mod.public_view(cfg)["_readonly"]["suites"]
                for x in s["sources"]]
    assert "grok_sessions" in listed({"source": "none", "grok_home": str(tmp_path / "grok")})
    assert "grok_sessions" not in listed({"source": "grok_cli_billing", "token_source": "none"})


# 7. the Cursor app method can be turned back on
def test_cursor_app_button_returns_after_turning_cursor_off(tmp_path, monkeypatch):
    db = tmp_path / "state.vscdb"
    db.write_text("x")
    monkeypatch.setattr("omnimonitor.cursor_app_auth.default_db_path", lambda: db)
    cfg = {"source": "none", "providers": {"cursor": {"enabled": False, "auth": "cursor_app"}}}
    row = next(r for r in onboarding.source_rows(cfg) if r["id"] == "cursor")
    assert row["cursor_app"] is True


# 8. "turn on N found" never replaces a custom Grok source
def test_custom_grok_command_is_left_alone(monkeypatch):
    monkeypatch.setattr("omnimonitor.app.grok_login_state",
                        lambda cfg: {"state": "ok", "message": "logged in"})
    row = next(r for r in onboarding.source_rows({"source": "command"}) if r["id"] == "grok")
    assert row["state"] == "connected" and "your own command" in row["detail"]
    assert row["enabled"] is False  # no "turn off" that would replace the command


# 9. a failed sign-in says why and keeps the last good reading
def test_failed_sign_in_is_shown_and_keeps_the_reading(tmp_path, monkeypatch):
    done = []
    monkeypatch.setattr("omnimonitor.session_login.start_login",
                        lambda pid, on_done=None: done.append(on_done) or "opening")
    p = _isolated(tmp_path, source="none")
    ctrl = Controller(config_mod.load_config(p))
    assert ctrl.onboard({"action": "signin", "source": "claude_plan"})["ok"]
    now = ctrl.clock().timestamp()
    good = accounts.Reading("claude_plan", True, now, attempted_at=now)
    ctrl.history.set_meta(accounts.META_PREFIX + "claude_plan", json.dumps(good.to_dict()))
    done[0](False, "the sign-in window is not available on this system")
    assert accounts.load(ctrl.history, "claude_plan") is not None
    rows = [r for s in ctrl.setup_status()["onboarding"]["suites"] for r in s["sources"]]
    row = next(r for r in rows if r["id"] == "claude_plan")
    assert "not available on this system" in row["detail"]
    done[0](True, "signed in")
    assert accounts.load(ctrl.history, "claude_plan") is None


# race: a read that started before a sign-in must not put "signed out" back
def test_a_read_already_in_flight_cannot_restore_a_forgotten_reading(tmp_path):
    p = _isolated(tmp_path, source="none", providers={"claude_plan": {"enabled": True}})
    ctrl = Controller(config_mod.load_config(p))
    started = ctrl.clock().timestamp() - 5
    ctrl.forget_readings(["claude_plan"])
    old = accounts.Reading("claude_plan", False, started, error="signed out",
                           attempted_at=started)
    ctrl.history.set_meta(accounts.META_PREFIX + "claude_plan", json.dumps(old.to_dict()))
    assert accounts.load(ctrl.history, "claude_plan") is None


# 10. verify output never carries the home folder (it names the Windows user)
def test_verify_hides_the_home_folder(tmp_path, monkeypatch):
    home = str(Path.home())
    assert redact.scrub({"error": "missing %s/.grok/auth.json" % home})["error"] \
        == "missing ~/.grok/auth.json"
    from omnimonitor import billing
    monkeypatch.setattr(billing, "fetch_usage_safely",
                        lambda c: (None, "no login at %s" % home))
    res = verify.run({"source": "grok_cli_billing"}, ["grok"], out_dir=tmp_path)
    assert home not in res[0]["error"]
    assert home not in json.loads((tmp_path / res[0]["saved"]).read_text())["error"]


# 11. a typo in a hand-edited layout must not block every later Settings save
def test_settings_page_gets_only_valid_stored_layout_entries():
    view = settings_mod.public_view({
        "display": {"order": ["openai", "typo"], "hide": {"menu": ["typo"], "toaster": []},
                    "fields": {"menu": ["limit", "nope"]}},
        "suite_colors": {"openai": "#10a37f", "typo": "#000000", "google": "red"},
        "source_poll_seconds": {"deepseek": 3600, "typo": 600, "openrouter": 5}})
    for key in ("display", "suite_colors", "source_poll_seconds"):
        settings_mod.validate(key, view[key], web=True)  # round-trips
    assert view["display"] == {"order": ["openai"], "fields": {"menu": ["limit"]}}
    assert view["suite_colors"] == {"openai": "#10a37f"}
    assert view["source_poll_seconds"] == {"deepseek": 3600}
