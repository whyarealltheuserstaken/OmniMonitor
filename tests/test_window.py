import io
import subprocess

import pytest

from omnimonitor import window
from omnimonitor.window import WindowLauncher, valid_local_url

URL = "http://127.0.0.1:5123/s/abcDEF_-123/"


class FakeProc:
    def __init__(self, rc=None):
        self.rc = rc
        self.stdin = io.BytesIO()
        self.stdin.close = lambda: setattr(self, "closed", True)
        self.terminated = False

    def poll(self):
        return self.rc

    def terminate(self):
        self.terminated = True


@pytest.mark.parametrize("u,ok", [
    (URL, True), (URL + "settings", True), ("http://localhost:1/s/x/mini", True),
    ("https://evil.example/s/x/", False), ("http://127.0.0.1:5123/", False),
    ("file:///etc/passwd", False), ("http://127.0.0.1:5123/s/x/\njs", False), ("", False),
])
def test_valid_local_url(u, ok):
    assert valid_local_url(u) is ok


def test_url_goes_over_stdin_not_argv():
    calls, procs = [], []

    def popen(cmd, **kw):
        calls.append((cmd, kw))
        procs.append(FakeProc())
        return procs[-1]
    opened = []
    wl = WindowLauncher(popen=popen, browser=opened.append, available=lambda: True, grace_s=0)
    assert wl.open(URL) == "window"
    cmd, kw = calls[0]
    assert cmd[-1] == "window" and URL not in " ".join(cmd)
    assert kw["stdin"] is subprocess.PIPE
    assert procs[0].stdin.getvalue() == (URL + "\n").encode()
    assert opened == []


def test_falls_back_to_browser_without_pywebview():
    opened = []
    wl = WindowLauncher(popen=lambda *a, **k: pytest.fail("no spawn"),
                        browser=opened.append, available=lambda: False)
    assert wl.open(URL) == "browser" and opened == [URL]


def test_falls_back_when_child_dies(monkeypatch):
    opened = []
    wl = WindowLauncher(popen=lambda *a, **k: FakeProc(rc=3), browser=opened.append,
                        available=lambda: True, grace_s=0.5)
    wl.open(URL)
    import time
    for _ in range(50):
        if opened:
            break
        time.sleep(0.05)
    assert opened == [URL] and wl.last_mode == "browser"


def test_falls_back_when_spawn_fails():
    opened = []

    def boom(*a, **k):
        raise OSError("nope")
    wl = WindowLauncher(popen=boom, browser=opened.append, available=lambda: True)
    assert wl.open(URL) == "browser" and opened == [URL]


def test_refuses_remote_url():
    wl = WindowLauncher(popen=None, browser=None, available=lambda: True)
    with pytest.raises(ValueError):
        wl.open("https://example.com/")


def test_reopen_terminates_previous():
    procs = []

    def popen(*a, **k):
        procs.append(FakeProc())
        return procs[-1]
    wl = WindowLauncher(popen=popen, browser=lambda u: None, available=lambda: True, grace_s=0)
    wl.open(URL)
    wl.open(URL)
    assert procs[0].terminated and not procs[1].terminated


def test_read_url_from_stdin():
    assert window.read_url_from_stdin(io.StringIO(URL + "\n")) == URL
    assert window.read_url_from_stdin(io.StringIO("https://x.y/\n")) is None


def test_child_command_frozen(monkeypatch):
    monkeypatch.setattr(window.sys, "frozen", True, raising=False)
    assert window.child_command() == [window.sys.executable, "window"]


def test_env_disables_window(monkeypatch):
    monkeypatch.setenv("OMNIMONITOR_NO_WINDOW", "1")
    assert window.webview_available() is False


def test_stats_served_event_set_by_api(tmp_path, monkeypatch):
    """Window smoke relies on the server flagging that the page's script fetched api/stats."""
    import urllib.request

    from omnimonitor.webui import StatsServer

    srv = StatsServer(lambda: {"ok": True}).start()
    try:
        assert not srv.stats_served.is_set()
        with urllib.request.urlopen(srv.url, timeout=5) as r:
            r.read()
        assert not srv.stats_served.is_set()
        with urllib.request.urlopen(srv.url + "api/stats", timeout=5) as r:
            r.read()
        assert srv.stats_served.is_set()
    finally:
        srv.stop()
