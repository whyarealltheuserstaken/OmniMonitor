"""0.2.0: settings POST hardening, new pages, export, Controller.update_settings, CLI."""
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pytest

from omnimonitor import cli, stats
from omnimonitor import config as config_mod
from omnimonitor.app import Controller
from omnimonitor.icon import STYLES, render_icon
from omnimonitor.webui import for_controller


def req(url, method="GET", body=None, headers=None):
    r = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIMONITOR_HOME", str(tmp_path))
    monkeypatch.setenv("OMNIMONITOR_TOKEN", "ENV-SECRET")
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"source": "demo", "theme": "dark"}))
    cfg = config_mod.load_config(p)
    ctrl = Controller(cfg, tmp_path / "h.db")
    srv = for_controller(ctrl).start()
    yield ctrl, srv, p
    srv.stop()


def csrf(srv):
    code, body, _ = req(srv.url + "api/settings")
    assert code == 200
    return json.loads(body)


def post(srv, obj, token=None, ctype="application/json", origin=None):
    h = {"Content-Type": ctype}
    if token is not None:
        h["X-OmniMonitor-CSRF"] = token
    if origin:
        h["Origin"] = origin
    return req(srv.url + "api/settings", "POST", json.dumps(obj).encode(), h)


def test_settings_get_has_no_secrets(env):
    ctrl, srv, _ = env
    view = csrf(srv)
    assert "ENV-SECRET" not in json.dumps(view) and "token" not in view
    assert view["_csrf"] and view["theme"] == "dark"


def test_settings_post_guards(env):
    ctrl, srv, path = env
    tok = csrf(srv)["_csrf"]
    assert post(srv, {"theme": "light"})[0] == 403  # no CSRF header
    assert post(srv, {"theme": "light"}, token="wrong")[0] == 403
    assert post(srv, {"theme": "light"}, token=tok, origin="http://evil.example")[0] == 403
    assert post(srv, {"theme": "light"}, token=tok, ctype="text/plain")[0] == 415
    code, body, _ = post(srv, {"token": "x", "command": "sh"}, token=tok)
    assert code == 400 and set(json.loads(body)["errors"]) == {"token", "command"}
    code, _, _ = req(srv.url + "api/stats", "POST", b"{}", {
        "Content-Type": "application/json", "X-OmniMonitor-CSRF": tok})
    assert code == 404  # only api/settings accepts POST
    assert json.loads(path.read_text())["theme"] == "dark"  # nothing written so far


def test_settings_post_saves_without_leaking_env_token(env):
    ctrl, srv, path = env
    tok = csrf(srv)["_csrf"]
    port = srv.port
    code, body, _ = post(srv, {"theme": "light", "accent_color": "#123456"}, token=tok,
                         origin="http://127.0.0.1:%d" % port)
    assert code == 200 and json.loads(body) == {"ok": True,
                                                "changed": ["accent_color", "theme"]}
    saved = json.loads(path.read_text())
    assert saved["theme"] == "light" and "token" not in saved
    assert "ENV-SECRET" not in path.read_text()
    assert ctrl.stats()["personal"]["theme"] == "light"


def test_new_pages_and_export(env):
    ctrl, srv, _ = env
    for page, needle in (("settings", b"settings.js"), ("mini", b"mini.js"),
                         ("common.js", b"OM"), ("settings.js", b"X-OmniMonitor-CSRF")):
        code, body, _ = req(srv.url + page)
        assert code == 200 and needle in body, page
    code, body, h = req(srv.url + "api/export.csv?kind=daily-models")
    assert code == 200 and h["Content-Type"].startswith("text/csv")
    assert "DEMO" in h["Content-Disposition"] and body.startswith(b"date,tool,model")
    code, body, h = req(srv.url + "api/export.json")
    assert code == 200 and json.loads(body)["is_demo"] is True
    assert req(srv.url + "api/export.csv?kind=../../etc")[0] == 400


def test_controller_update_settings_rejects_and_applies(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{}")
    ctrl = Controller(config_mod.load_config(p), tmp_path / "h.db")
    assert ctrl.update_settings({"grok_billing_url": "http://evil"})["ok"] is False
    assert ctrl.update_settings({"poll_interval_seconds": 120})["ok"] is True
    assert json.loads(p.read_text()) == {"poll_interval_seconds": 120}
    # CLI-only keys work when not from the web
    assert ctrl.update_settings({"token_source": "none"}, web=False)["ok"] is True


def test_demo_stats_have_new_sections(tmp_path):
    ctrl = Controller({"source": "demo", "token_source": "auto"}, tmp_path / "h.db")
    ctrl.refresh()
    st = ctrl.stats()
    assert st["is_demo"] and ctrl.sent_alerts == []  # never alert on demo data
    bd = st["breakdowns"]
    assert set(bd) == {"today", "week", "7d", "30d", "all"}
    tools = {t["name"] for t in bd["all"]["tools"]}
    assert tools == {"Grok CLI", "Claude Code", "Codex"}
    assert sum(t["tokens"] for t in bd["all"]["tools"]) == bd["all"]["tokens"]
    assert st["cost"]["demo_prices"] is True
    assert st["burn"]["today"] >= 0


def test_weekly_limit_estimate_uses_grok_tokens_only():
    now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    ts = now.timestamp() - 3600
    evs = [{"ts": ts, "model": "g", "source": "grok_sessions", "input_tokens": 1000,
            "output_tokens": 0, "cost_usd": None},
           {"ts": ts, "model": "c", "source": "claude_code", "input_tokens": 9000,
            "output_tokens": 0, "cost_usd": None}]
    billing = {"used_percent": 50, "period_start": "2026-10-01T00:00:00+00:00",
               "resets_at": "2026-10-08T00:00:00+00:00"}
    st = stats.compute(evs, [], billing, {"estimate_weekly_limit": True,
                                          "estimate_min_used_percent": 10}, now,
                       "grok_sessions", tz=timezone.utc)
    assert st["week_tokens"]["value"] == 10000 and st["week_tokens"]["grok_value"] == 1000
    assert st["weekly_limit"]["estimate"]["value"] == 2000  # 1000 Grok tokens / 50 %


@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("show", ["remaining", "used"])
def test_icon_styles(style, show):
    img = render_icon(37, size=64, style=style, show=show)
    assert img.size == (64, 64) and img.mode == "RGBA"
    assert render_icon(None, size=32, style=style).size == (32, 32)


def test_cli_config_set_get_and_export(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIMONITOR_HOME", str(tmp_path))
    monkeypatch.setenv("OMNIMONITOR_TOKEN", "ENV-SECRET")
    assert cli.main(["config", "set", "notify_thresholds", "[50, 90]"]) == 0
    assert cli.main(["config", "set", "theme", "neon"]) == 2
    capsys.readouterr()
    assert cli.main(["config", "get", "notify_thresholds"]) == 0
    assert "[50, 90]" in capsys.readouterr().out
    cfgfile = next(tmp_path.rglob("config.json"))
    assert "ENV-SECRET" not in cfgfile.read_text()
    assert cli.main(["config", "set", "token", "x"]) == 2
    out = tmp_path / "e.csv"
    assert cli.main(["export", "--demo", "--kind", "breakdown", "-o", str(out)]) == 0
    assert out.read_text().startswith("tool,model,project")
    capsys.readouterr()
    assert cli.main(["breakdown", "--demo", "--range", "7d", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["tools"]
    assert cli.main(["providers", "--json"]) == 0
    names = [r["name"] for r in json.loads(capsys.readouterr().out)]
    assert names == ["grok_sessions", "claude_code", "codex"]


def test_setup_writes_only_source_not_env_token(tmp_path, monkeypatch):
    import json as _json

    from omnimonitor import config as cm
    from omnimonitor import doctor
    p = tmp_path / "config.json"
    p.write_text(_json.dumps({"source": "demo", "theme": "light"}), encoding="utf-8")
    monkeypatch.setenv("OMNIMONITOR_TOKEN", "env-secret-token")
    cfg = cm.load_config(p)
    assert cfg.get("token") == "env-secret-token"
    monkeypatch.setattr(doctor, "checks", lambda c, online=True: [(doctor.OK, "billing", "ok")])
    monkeypatch.setattr(doctor, "print_checks", lambda r: 0)
    assert doctor.setup(cfg, assume_yes=True) == 0
    saved = _json.loads(p.read_text(encoding="utf-8"))
    assert saved["source"] == "grok_cli_billing"
    assert saved["theme"] == "light"
    assert "env-secret-token" not in p.read_text(encoding="utf-8")
