"""Controller and tray bugs: config reload race, missing alert history, silent first run."""
from types import SimpleNamespace

from omnimonitor import alerts, config as config_mod
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
