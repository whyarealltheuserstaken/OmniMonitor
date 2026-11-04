"""Henry's bug: wizard installed Grok + login, but the running app stayed on demo numbers."""
import json
import os
import time
from datetime import timedelta
from pathlib import Path

from omnimonitor import app as app_mod
from omnimonitor import billing, doctor
from omnimonitor import config as config_mod
from omnimonitor.billing import UsageError, UsageResult
from omnimonitor.timeutil import utcnow


def _cfg(tmp_path, **kw):
    p = tmp_path / "config.json"
    config_mod.save_config(dict({"source": "demo", "grok_home": str(tmp_path / "grok")}, **kw), p)
    return config_mod.load_config(p)


def _ctrl(tmp_path, cfg):
    return app_mod.Controller(cfg, history_file=tmp_path / "h.sqlite")


def _real(pct=80.0):
    return UsageResult(remaining_percent=pct, resets_at=utcnow() + timedelta(days=3))


def test_running_app_reloads_config_written_by_cli_setup(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    c = _ctrl(tmp_path, cfg)
    c.refresh()
    assert c.usage.is_demo and c.token_source == "demo"
    seen = []
    monkeypatch.setattr(app_mod, "fetch_usage_safely", lambda cf: (seen.append(cf["source"]) or
                                                                     (_real(), None)))
    time.sleep(0.01)
    config_mod.update_config_file({"source": "grok_cli_billing"}, Path(cfg["_config_path"]))
    os.utime(cfg["_config_path"], None)
    c._cfg_mtime = -1  # coarse mtime filesystems
    c.refresh()
    assert seen == ["grok_cli_billing"]
    assert c.token_source == "grok_sessions"
    assert not c.usage.is_demo and c.usage.remaining_percent == 80.0


def test_forced_demo_survives_reload(tmp_path):
    cfg = _cfg(tmp_path, source="grok_cli_billing")
    cfg.update(source="demo", token_source="demo", _forced_demo=True)
    c = _ctrl(tmp_path, cfg)
    c._cfg_mtime = -1
    assert c.reload_config_if_changed()
    assert c.cfg["source"] == "demo" and c.token_source == "demo"


def test_setup_use_real_switches_live_only_after_billing_ok(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    c = _ctrl(tmp_path, cfg)
    c.refresh()

    def bad(_c):
        raise UsageError("not logged in: run `grok login`")
    monkeypatch.setattr(billing, "fetch_grok_cli_billing", bad)
    r = c.setup_use_real({})
    assert not r["ok"] and "grok login" in r["error"]
    assert c.cfg["source"] == "demo"
    assert json.loads(open(cfg["_config_path"]).read())["source"] == "demo"

    monkeypatch.setattr(billing, "fetch_grok_cli_billing", lambda _c: _real(80))
    r = c.setup_use_real({})
    assert r["ok"] and r["remaining_percent"] == 80
    assert c.cfg["source"] == "grok_cli_billing" and c.token_source == "grok_sessions"
    assert c.usage is None  # demo numbers dropped
    assert json.loads(open(cfg["_config_path"]).read())["source"] == "grok_cli_billing"


def test_grok_login_state(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(billing.shutil, "which", lambda _n: None)
    monkeypatch.setattr(billing.os.path, "isfile", lambda _p: False)
    assert app_mod.grok_login_state(cfg)["state"] == "not_installed"
    monkeypatch.setattr(billing, "find_grok_binary", lambda _c: "/Users/h/.grok/bin/grok")
    g = app_mod.grok_login_state(cfg)
    assert g["state"] == "not_logged_in" and g["login_command"] == "/Users/h/.grok/bin/grok login"
    st = _ctrl(tmp_path, cfg).setup_status()
    assert st["grok_login"]["state"] == "not_logged_in" and st["demo"] is True


def test_poll_is_logged_and_state_file_feeds_doctor(tmp_path, monkeypatch, caplog):
    cfg = _cfg(tmp_path, source="grok_cli_billing")
    c = _ctrl(tmp_path, cfg)
    c.state_file = tmp_path / "app-state.json"
    monkeypatch.setattr(app_mod, "fetch_usage_safely",
                        lambda cf: (None, "not logged in: run `grok login`"))
    with caplog.at_level("INFO", logger="omnimonitor"):
        c.refresh()
    assert any("poll source=grok_cli_billing failed" in r.getMessage() for r in caplog.records)
    st = json.loads(c.state_file.read_text())
    assert st["pid"] == os.getpid() and st["source"] == "grok_cli_billing"
    assert "grok login" in st["error"]
    rows = doctor.running_app_checks(cfg, state_path=c.state_file)
    names = {r[1]: r for r in rows}
    assert names["running app"][0] == doctor.OK
    assert names["app error"][0] == doctor.FAIL
    # doctor vs app mismatch: config says demo but app runs grok_cli_billing
    rows = doctor.running_app_checks(dict(cfg, _configured_source="demo"),
                                     state_path=c.state_file)
    assert any(r[1] == "app vs config" and r[0] == doctor.FAIL for r in rows)

    monkeypatch.setattr(app_mod, "fetch_usage_safely", lambda cf: (_real(80), None))
    with caplog.at_level("INFO", logger="omnimonitor"):
        c.refresh_now({})
    assert any("80.0% remaining" in r.getMessage() for r in caplog.records)


def test_doctor_reports_configured_demo_source(tmp_path):
    cfg = _cfg(tmp_path)
    cfg = dict(cfg, _configured_source="demo", source="grok_cli_billing")
    rows = doctor.checks(cfg, online=False)
    src = [r for r in rows if r[1] == "source"][0]
    assert src[0] == doctor.WARN and src[2].startswith("demo")


def test_token_note_explains_missing_sessions_and_unified(tmp_path):
    cfg = _cfg(tmp_path, source="grok_cli_billing")
    (tmp_path / "grok" / "logs").mkdir(parents=True)
    (tmp_path / "grok" / "logs" / "unified.jsonl").write_text("{}\n")
    c = _ctrl(tmp_path, cfg)
    c._scan_tokens()
    assert "~/.grok/sessions" in c.token_note and "unified" in c.token_note


def test_web_use_real_route_needs_csrf(tmp_path):
    import urllib.error
    import urllib.request

    from omnimonitor.webui import for_controller
    c = _ctrl(tmp_path, _cfg(tmp_path))
    srv = for_controller(c, 0).start()
    try:
        req = urllib.request.Request(srv.url + "api/setup/use-real", data=b"{}",
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("expected 403")
        except urllib.error.HTTPError as e:
            assert e.code == 403
    finally:
        srv.stop()
