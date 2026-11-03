import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from omnimonitor import autostart, cli
from omnimonitor.app import Controller
from omnimonitor.icon import color_for, render_icon

from .conftest import FIXTURES


class Mock(BaseHTTPRequestHandler):
    status = 200
    seen = []

    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        Mock.seen.append({k.lower(): v for k, v in self.headers.items()})
        body = (FIXTURES / "billing_weekly.json").read_bytes()
        if self.headers.get("Authorization") != "Bearer GOOD":
            self.send_response(401)
            self.end_headers()
            return
        self.send_response(Mock.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


def mock_server():
    s = HTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def real_cfg(tmp_path, port, key="GOOD"):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"https://auth.x.ai::c": {"auth_mode": "oidc", "key": key,
                                                          "expires_at": "2099-01-01T00:00:00Z"}}))
    return {"source": "grok_cli_billing", "token_source": "grok_sessions",
            "grok_auth_file": str(auth), "grok_home": str(tmp_path / "grokhome"),
            "grok_billing_url": "http://127.0.0.1:%d/v1/billing?format=credits" % port,
            "grok_refresh_command": "", "timeout_seconds": 5, "poll_interval_seconds": 60}


def test_controller_against_mock_billing(tmp_path):
    s = mock_server()
    try:
        ctrl = Controller(real_cfg(tmp_path, s.server_address[1]), tmp_path / "h.sqlite3")
        ctrl.refresh()
        assert ctrl.error is None and ctrl.usage.remaining_percent == 71.5
        h = Mock.seen[-1]
        assert h["x-xai-token-auth"] == "xai-grok-cli"
        st = ctrl.stats()
        assert st["lifetime_tokens"]["value"] is None  # no logs -> not available, not 0
        assert "no Grok CLI session logs" in st["lifetime_tokens"]["reason"]
        assert ctrl.history.last_billing()["used_percent"] == 28.5
    finally:
        s.shutdown()


def test_controller_error_keeps_last_good(tmp_path):
    s = mock_server()
    try:
        cfg = real_cfg(tmp_path, s.server_address[1])
        ctrl = Controller(cfg, tmp_path / "h.sqlite3")
        ctrl.refresh()
        cfg["grok_auth_file"] = str(tmp_path / "missing.json")
        ctrl.refresh()
        assert ctrl.usage.remaining_percent == 71.5 and "not found" in ctrl.error
    finally:
        s.shutdown()


def test_rejected_token_without_refresh(tmp_path):
    s = mock_server()
    try:
        ctrl = Controller(real_cfg(tmp_path, s.server_address[1], key="BAD"), tmp_path / "h.db")
        ctrl.refresh()
        assert ctrl.usage is None and "401" in ctrl.error
    finally:
        s.shutdown()


def test_demo_controller_labeled(tmp_path):
    ctrl = Controller({"source": "demo", "token_source": "auto"}, tmp_path / "h.db")
    ctrl.refresh()
    st = ctrl.stats()
    assert st["is_demo"] and ctrl.usage.is_demo
    assert ctrl.history is None  # demo never touches history / Grok files


def test_icon_render_variants():
    assert color_for(80) != color_for(30) != color_for(5)
    for args in ((100,), (0,), (55.4,), (None,)):
        img = render_icon(*args, size=64)
        assert img.size == (64, 64)
    assert render_icon(10, error=True, demo=True).mode == "RGBA"


def test_cli_status_demo(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIMONITOR_HOME", str(tmp_path))
    rc = cli.main(["status", "--demo"])
    out = capsys.readouterr().out
    assert rc == 0 and "DEMO" in out


def test_cli_config_path(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIMONITOR_HOME", str(tmp_path))
    assert cli.main(["config-path"]) == 0
    assert str(tmp_path) in capsys.readouterr().out


def test_autostart_files():
    entry = autostart.desktop_entry(["/opt/my app/omnimonitor"])
    assert 'Exec="/opt/my app/omnimonitor"' in entry
    plist = autostart.mac_plist(["/usr/local/bin/omnimonitor"])
    assert b"RunAtLoad" in plist and b"com.omnimonitor.tray" in plist


def test_linux_autostart_roundtrip(tmp_path, monkeypatch):
    import sys
    if sys.platform in ("darwin", "win32"):
        return
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    ok, _ = autostart.install()
    assert ok and autostart.linux_desktop_path().exists()
    ok, _ = autostart.uninstall()
    assert ok and not autostart.linux_desktop_path().exists()


def test_controller_real_billing_with_claude_logs_and_alert(tmp_path):
    import shutil
    from datetime import datetime, timezone
    home = tmp_path / ".claude" / "projects" / "-x-alpha"
    home.mkdir(parents=True)
    shutil.copy(FIXTURES / "claude_session.jsonl", home / "s.jsonl")
    s = mock_server()
    try:
        cfg = real_cfg(tmp_path, s.server_address[1])
        cfg.update({"extra_token_sources": ["claude_code"], "claude_home": str(tmp_path / ".claude"),
                    "notifications": True, "notify_thresholds": [25], "notify_on_pace": False})
        got = []
        ctrl = Controller(cfg, tmp_path / "h.sqlite3",
                          clock=lambda: datetime(2026, 11, 4, 12, tzinfo=timezone.utc))
        ctrl.notifier = lambda body, title: got.append((title, body))
        ctrl.refresh()
        ctrl.refresh()
        assert [m["kind"] for m in ctrl.sent_alerts] == ["threshold"]  # once, not twice
        assert got and "28%" in got[0][0]
        assert "claude_code" in ctrl.tools()
        st = ctrl.stats()
        tools = {t["name"]: t["tokens"] for t in st["breakdowns"]["all"]["tools"]}
        assert tools == {"Claude Code": 32410 + 32150}
        assert st["weekly_limit"]["estimate"] is None  # Claude tokens never count for Grok
        assert st["forecast"]["average"]["status"] in ("ahead", "behind", "on pace")
    finally:
        s.shutdown()
