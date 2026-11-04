"""Native-app behaviour: in-app actions, mini<->full switching, diagnostics, menubar icon."""
import io
import json
import urllib.error
import urllib.request

import pytest

from omnimonitor import appactions, window
from omnimonitor import config as config_mod
from omnimonitor.app import Controller
from omnimonitor.icon import render_menubar
from omnimonitor.tokens import parse_update_line
from omnimonitor.webui import CSRF_HEADER, for_controller


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIMONITOR_HOME", str(tmp_path))
    monkeypatch.setenv("OMNIMONITOR_TOKEN", "xai-SUPERSECRETTOKEN123456")
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"source": "demo"}))
    ctrl = Controller(config_mod.load_config(p), tmp_path / "h.db")
    srv = for_controller(ctrl).start()
    yield ctrl, srv
    srv.stop()


def _post(srv, action, body, csrf=True):
    h = {"Content-Type": "application/json"}
    if csrf:
        h[CSRF_HEADER] = srv.csrf
    req = urllib.request.Request(srv.url + "api/app/" + action, data=json.dumps(body).encode(),
                                 headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None


def _get(srv, path):
    with urllib.request.urlopen(srv.url + path, timeout=5) as r:
        return r.status, r.read()


def test_api_app_lists_actions_and_needs_csrf(env):
    _ctrl, srv = env
    code, body = _get(srv, "api/app")
    meta = json.loads(body)
    assert code == 200 and meta["_csrf"] == srv.csrf
    assert {"export", "open-url", "diagnostics"} <= set(meta["actions"])
    assert _post(srv, "diagnostics", {}, csrf=False)[0] == 403
    assert _post(srv, "nope", {})[0] == 404


def test_export_saves_file_and_reveals(env, tmp_path):
    ctrl, _ = env
    shown = []
    r = appactions.save_export(ctrl, "daily", "csv", folder=tmp_path, reveal=shown.append)
    assert r["ok"] and shown == [r["path"]]
    assert open(r["path"], "rb").read()[:4]  # non-empty file on disk
    r2 = appactions.save_export(ctrl, "daily", "csv", folder=tmp_path, reveal=shown.append)
    assert r2["path"] != r["path"]  # never overwrites
    assert appactions.save_export(ctrl, "../x", "csv", folder=tmp_path)["ok"] is False


def test_open_url_https_only():
    opened = []
    assert appactions.open_external({"url": "https://x.ai/docs"}, opened.append)["ok"]
    for bad in ("http://x.ai", "javascript:alert(1)", "file:///etc/passwd", "https://a b", 5):
        assert not appactions.open_external({"url": bad}, opened.append)["ok"]
    assert opened == ["https://x.ai/docs"]


def test_redact():
    t = appactions.redact('url http://127.0.0.1:1/s/abcdefghijklmnopqrstu/ key xai-abcdefghijk '
                          'api_key="zzz" token: qqq')
    assert "abcdefghijklmnop" not in t and "xai-abc" not in t
    assert "zzz" not in t and "qqq" not in t and "/s/<secret>/" in t


def test_diagnostics_secret_free(env):
    ctrl, srv = env
    code, d = _post(srv, "diagnostics", {})
    assert code == 200 and d["ok"]
    text = d["text"]
    assert "OmniMonitor" in text and "doctor" in text
    assert "SUPERSECRET" not in text and srv.url not in text
    assert srv.url.split("/s/")[1].strip("/") not in text


def test_mini_to_full_keeps_tray_and_server_alive(env):
    """Smoke: the page asks for mini, then full. The window child is replaced, never the
    tray-side process, and the stats server keeps answering throughout."""
    ctrl, srv = env
    procs = []

    class P:
        def __init__(self):
            self.stdin, self.rc, self.pid = io.BytesIO(), None, len(procs) + 100
            self.stdin.close = lambda: None

        def poll(self):
            return self.rc

        def terminate(self):
            self.rc = -15

        def wait(self, timeout=None):
            return self.rc

    def popen(*a, **k):
        procs.append(P())
        return procs[-1]
    browser = []
    wl = window.WindowLauncher(popen=popen, browser=browser.append, available=lambda: True)
    srv.app_actions.update(appactions.window_actions(wl, lambda: srv.url,
                                                     lambda k: k == "mini_on_top",
                                                     browser=browser.append))
    code, r = _post(srv, "window", {"view": "mini"})
    assert code == 200 and r == {"ok": True, "view": "mini", "mode": "window"}
    url, opts = procs[0].stdin.getvalue().decode().splitlines()
    assert url.endswith("mini?app=1") and json.loads(opts) == {"view": "mini", "on_top": True}
    assert _get(srv, "api/stats")[0] == 200
    code, r = _post(srv, "window", {"view": "full"})
    assert code == 200 and r["mode"] == "window" and wl.view == "full"
    assert procs[0].rc is not None and procs[1].rc is None  # mini closed, full open
    assert procs[1].stdin.getvalue().decode().splitlines()[0].endswith("?app=1")
    assert _get(srv, "api/stats")[0] == 200 and browser == []
    assert ctrl.status_dict()  # controller still alive
    _post(srv, "browser", {})
    assert browser == [srv.url]  # browser only when explicitly asked
    wl.close()


def test_read_request_from_stdin():
    s = io.StringIO("http://127.0.0.1:1/s/x/mini?app=1\n{\"view\": \"mini\", \"on_top\": true}\n")
    assert window.read_request_from_stdin(s) == ("http://127.0.0.1:1/s/x/mini?app=1",
                                                 {"view": "mini", "on_top": True})
    s = io.StringIO("http://127.0.0.1:1/s/x/\n")
    url, opts = window.read_request_from_stdin(s)
    assert url == "http://127.0.0.1:1/s/x/" and not opts.get("on_top")


@pytest.mark.parametrize("rem,err,template", [(80, False, True), (30, False, True),
                                              (10, False, False), (None, True, True)])
def test_menubar_icon_44px_template(rem, err, template):
    img, tpl = render_menubar(rem, error=err)
    assert img.height == 44 and img.mode == "RGBA" and tpl is template
    if tpl:  # template images: pure black + alpha only
        r, g, b, a = (ch.tobytes() for ch in img.split())
        assert {(r[i], g[i], b[i]) for i in range(len(a)) if a[i]} == {(0, 0, 0)}
    small, _ = render_menubar(50, text=False)
    assert small.height == 44 and small.width <= 44


def test_prompt_id_dedup_and_toplevel_usage_fallback():
    a = ('{"update":{"sessionUpdate":"turn_completed","prompt_id":"p1","usage":{"inputTokens":5,'
         '"outputTokens":2}},"timestamp":1762300000}')
    b = a.replace("1762300000", "1762309999")  # forked copy, rewritten timestamp
    _, ea, _ = parse_update_line(a, "s")
    _, eb, _ = parse_update_line(b, "s2")
    assert len(ea) == 1 and ea[0].input_tokens == 5 and ea[0].output_tokens == 2
    assert ea[0].event_id == eb[0].event_id
