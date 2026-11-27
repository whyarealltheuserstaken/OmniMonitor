"""Real data for people without Grok: source "none", and surfaces that follow suites."""
import json

from omnimonitor import accounts, cli, overview, statusline
from omnimonitor import config as config_mod
from omnimonitor import settings as settings_mod
from omnimonitor.app import Controller
from omnimonitor.billing import fetch_usage_safely


def _cfg(tmp_path, **kw):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(dict({"source": "none"}, **kw)))
    return config_mod.load_config(p)


def test_source_none_is_not_an_error():
    assert fetch_usage_safely({"source": "none"}) == (None, None)


def test_source_none_is_not_demo_and_needs_no_grok(tmp_path):
    cfg = _cfg(tmp_path, grok_home=str(tmp_path / "no-grok"))
    assert config_mod.effective_token_source(cfg) == "none"
    ctrl = Controller(cfg, history_file=tmp_path / "h.sqlite3")
    ctrl.refresh()
    st = ctrl.stats()
    assert st["is_demo"] is False and ctrl.error is None
    assert not [c for c in st["providers"]["cards"] if c["id"] == "grok"]
    assert "Grok" not in ctrl.token_note


def test_source_none_still_reads_grok_session_logs_when_present(tmp_path):
    (tmp_path / "grok" / "sessions").mkdir(parents=True)
    cfg = _cfg(tmp_path, grok_home=str(tmp_path / "grok"))
    assert config_mod.effective_token_source(cfg) == "grok_sessions"


def test_source_none_keeps_history_for_account_sources(tmp_path):
    """A CLI-login source alone needs history: cached readings and alerts live there."""
    cfg = _cfg(tmp_path, grok_home=str(tmp_path / "no-grok"),
               providers={"claude_cli_plan": {"enabled": True}})
    ctrl = Controller(cfg, history_file=tmp_path / "h.sqlite3")
    assert ctrl.history is not None


def test_source_setting_is_cli_only_and_validated():
    assert settings_mod.validate("source", "none") == "none"
    for bad, web in (("command; rm", False), ("none", True)):
        try:
            settings_mod.validate("source", bad, web=web)
        except settings_mod.SettingError:
            continue
        raise AssertionError("accepted %r web=%s" % (bad, web))


def _reading_history(tmp_path):
    """A config + history that hold one cached Claude plan reading and no Grok at all."""
    cfg = _cfg(tmp_path, grok_home=str(tmp_path / "no-grok"),
               providers={"claude_cli_plan": {"enabled": True}})
    ctrl = Controller(cfg)
    now = ctrl.clock().timestamp()
    r = accounts.Reading("claude_cli_plan", True, now, attempted_at=now,
                         limits=[{"name": "5-hour", "used_percent": 89.0,
                                  "resets_at": now + 3 * 3600 + 120}])
    ctrl.history.set_meta(accounts.META_PREFIX + "claude_cli_plan", json.dumps(r.to_dict()))
    return cfg


def test_status_leads_with_suites_without_grok(tmp_path, capsys):
    cfg = _reading_history(tmp_path)
    assert cli.main(["--config", cfg["_config_path"], "status"]) == 0
    out = capsys.readouterr().out
    assert "Anthropic: 89% used" in out
    assert "5-hour 89% used" in out
    assert "Grok" not in out


def test_status_with_nothing_connected_says_how_to_connect(tmp_path, capsys):
    cfg = _cfg(tmp_path, grok_home=str(tmp_path / "no-grok"))
    assert cli.main(["--config", cfg["_config_path"], "status"]) == 1
    assert "omnimonitor setup" in capsys.readouterr().out


def test_statusline_shows_each_suite_from_cache(tmp_path, capsys):
    cfg = _reading_history(tmp_path)
    assert cli.main(["--config", cfg["_config_path"], "statusline"]) == 0
    out = capsys.readouterr().out
    assert "Anthropic 89%" in out and "resets in 3h" in out
    assert "Grok" not in out


def test_statusline_render_suites():
    p = statusline.parts(None, None, None, 1000.0, suites=[
        {"id": "openai", "name": "OpenAI",
         "headline": {"used_percent": 12.4, "resets_at": 1000.0 + 90 * 60}},
        {"id": "google", "name": "Google", "headline": None}])
    assert statusline.render(p, 1000.0) == "OpenAI 12% used, resets in 1h 30m"


def test_primary_remaining_is_exposed_for_hero_views(tmp_path):
    cards = [overview._card("claude_cli_plan", True,
                            limits=[{"name": "5-hour", "used_percent": 25.0}])]
    sv = overview.suite_view(cards)
    p = overview.primary({}, sv, {"periods": {}}, 0.0)
    assert p["remaining_percent"] == 75.0 and p["label"] == "Anthropic"


def test_deps_do_not_ask_for_the_grok_cli_when_grok_is_off(tmp_path):
    from omnimonitor import deps
    cfg = _cfg(tmp_path, grok_home=str(tmp_path / "no-grok"))
    assert not [d for d in deps.needed_deps(cfg) if "grok" in d.id]


def test_doctor_skips_grok_checks_when_grok_is_off(tmp_path):
    from omnimonitor import doctor
    cfg = _cfg(tmp_path, grok_home=str(tmp_path / "no-grok"))
    rows = doctor.checks(cfg, online=False)
    assert not [r for r in rows if r[1] in ("grok CLI", "auth", "grok login", "billing")]
    assert not [r for r in rows if r[0] == doctor.FAIL and "grok" in r[2].lower()]


def _static(name):
    from importlib import resources
    return resources.files("omnimonitor").joinpath("static", name).read_text("utf-8")


def test_hero_views_follow_the_tray_suite_not_grok():
    """The dashboard and mini view headline come from providers.primary."""
    for page, script in (("stats.html", "stats.js"), ("mini.html", "mini.js")):
        assert "Grok suite · weekly pool remaining" not in _static(page)
        assert 'id="hero-label"' in _static(page)
        assert "OM.hero(" in _static(script)
    assert "primary" in _static("common.js")
