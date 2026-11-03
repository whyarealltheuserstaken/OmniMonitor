import json
import os
import urllib.error
import urllib.request

import pytest

from omnimonitor import config as config_mod
from omnimonitor.webui import StatsServer


def test_defaults_are_demo_and_poll_clamped(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"poll_interval_seconds": 5}))
    cfg = config_mod.load_config(p)
    assert cfg["source"] == "demo" and cfg["poll_interval_seconds"] == 60
    assert config_mod.effective_token_source(cfg) == "demo"
    cfg["source"] = "grok_cli_billing"
    assert config_mod.effective_token_source(cfg) == "grok_sessions"


def test_broken_config_does_not_crash(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{nope")
    cfg = config_mod.load_config(p)
    assert cfg["source"] == "demo" and "unreadable" in cfg["_config_error"]


def test_save_is_private(tmp_path):
    p = config_mod.save_config({"source": "demo", "_secret": 1}, tmp_path / "d" / "c.json")
    assert "_secret" not in json.loads(p.read_text())
    if os.name == "posix":
        assert (p.stat().st_mode & 0o777) == 0o600


@pytest.fixture
def server():
    s = StatsServer(lambda: {"hello": "world"}).start()
    yield s
    s.stop()


def get(url, host=None):
    req = urllib.request.Request(url)
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def test_server_binds_loopback_only(server):
    assert server.httpd.server_address[0] == "127.0.0.1"


def test_server_requires_secret(server):
    code, _, _ = get("http://127.0.0.1:%d/" % server.port)
    assert code == 404
    code, _, _ = get("http://127.0.0.1:%d/s/wrong/api/stats" % server.port)
    assert code == 404
    code, body, headers = get(server.url + "api/stats")
    assert code == 200 and json.loads(body) == {"hello": "world"}
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert "Access-Control-Allow-Origin" not in headers


def test_server_rejects_foreign_host_header(server):
    code, _, _ = get(server.url + "api/stats", host="evil.example:%d" % server.port)
    assert code == 421


def test_server_serves_page(server):
    code, body, _ = get(server.url)
    assert code == 200 and b"Token activity" in body
    code, body, _ = get(server.url + "stats.js")
    assert code == 200 and b"api/stats" in body
