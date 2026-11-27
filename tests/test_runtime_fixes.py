"""Controller and tray bugs: config reload race, missing alert history, silent first run."""
from types import SimpleNamespace

from omnimonitor import alerts
from omnimonitor import config as config_mod
from omnimonitor.app import Controller


class Watched(dict):
    """Records the "source" a reader on another thread could see after each change."""

    def __init__(self, *a):
        super().__init__(*a)
        self.seen = []

    def _note(self):
        self.seen.append(dict.get(self, "source"))

    def clear(self):
        super().clear()
        self._note()

    def update(self, *a, **k):
        super().update(*a, **k)
        self._note()

    def pop(self, *a):
        out = super().pop(*a)
        self._note()
        return out

    def __delitem__(self, key):
        super().__delitem__(key)
        self._note()


def test_config_reload_never_exposes_a_half_empty_config(tmp_path):
    cfg = Watched(dict(config_mod.DEFAULT_CONFIG, source="none", stale_key=1))
    ctrl = Controller(cfg, history_file=tmp_path / "h.sqlite3")
    cfg.seen.clear()
    ctrl._apply_cfg(dict(config_mod.DEFAULT_CONFIG, source="none"))
    assert cfg.seen and set(cfg.seen) == {"none"}
    assert "stale_key" not in cfg


def test_grok_pool_without_token_logs_still_gets_alert_history(tmp_path):
    cfg = dict(config_mod.DEFAULT_CONFIG, source="grok_cli_billing", token_source="none")
    assert Controller(cfg, history_file=tmp_path / "h.sqlite3").history is not None


def test_first_run_notice_goes_through_the_tray_icon(monkeypatch):
    from omnimonitor.tray import TrayApp
    sent = []
    monkeypatch.setattr(alerts, "deliver",
                        lambda title, body, tray_notify=None: sent.append(tray_notify))
    monkeypatch.setenv("OMNIMONITOR_NO_BROWSER", "1")

    def notify(body, title):
        return None

    app = SimpleNamespace(server=None, ctrl=SimpleNamespace(cfg={}), _notify_os=notify)
    assert TrayApp.first_run_hint(app) is True
    assert sent == [notify]


def test_two_refreshes_at_once_send_an_alert_once(tmp_path, monkeypatch):
    import threading
    import time

    ctrl = Controller(dict(config_mod.DEFAULT_CONFIG, source="grok_cli_billing",
                           token_source="none", notify_thresholds=[80]),
                      history_file=tmp_path / "h.sqlite3")
    ctrl._stats = {"providers": {"is_demo": False, "budgets": {}, "cards": [
        {"id": "codex_cli_plan", "name": "ChatGPT", "limits": [
            {"name": "Weekly", "used_percent": 90.0, "resets_at": 2e9}]}]}}
    real_load = alerts.load_state

    def slow_load(*a):
        state = real_load(*a)
        time.sleep(0.2)  # the other thread reads before this one saves
        return state

    sent = []
    monkeypatch.setattr(alerts, "load_state", slow_load)
    monkeypatch.setattr(ctrl, "_send_alert", sent.append)
    threads = [threading.Thread(target=ctrl._check_alerts) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(sent) == 1


def test_rejected_posts_deliver_their_status_instead_of_resetting_the_connection():
    """Clients send headers, then the body. Replying and closing before the body arrives
    resets the connection on Windows, and the client never sees the status."""
    import socket
    import time

    from omnimonitor.webui import StatsServer
    srv = StatsServer(lambda: {}, save_settings=lambda body: {"ok": True}).start()
    try:
        path = "/s/%s/api/settings" % srv.secret
        host = "127.0.0.1:%d" % srv.port
        crlf = chr(13) + chr(10)
        got = []
        for target, sent_host, extra in ((path, host, []), (path + "-nope", host, []),
                                         (path, "evil", []),
                                         (path, host, ["Origin: http://evil"])):
            head = ["POST %s HTTP/1.1" % target, "Host: " + sent_host,
                    "Content-Length: 2"] + extra
            with socket.create_connection(("127.0.0.1", srv.port), timeout=5) as sock:
                sock.sendall((crlf.join(head) + crlf + crlf).encode())
                time.sleep(0.3)
                try:
                    sock.sendall(b"{}")
                    time.sleep(0.1)
                    got.append(sock.recv(4096).split(b" ")[1].decode())
                except OSError as e:
                    got.append(type(e).__name__)
        assert got == ["403", "404", "421", "403"]
    finally:
        srv.stop()
