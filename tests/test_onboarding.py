"""First-run setup: every suite as Connected / Found / Sign in / Add key / Not available."""
import json
import time

import pytest

from omnimonitor import accounts, apikeys, cli, onboarding, sessions, webui
from omnimonitor import config as config_mod
from omnimonitor.app import Controller


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A computer with only Claude Code installed (logs + a login), nothing else."""
    claude = tmp_path / "claude"
    (claude / "projects" / "p").mkdir(parents=True)
    (claude / "projects" / "p" / "s.jsonl").write_text("{}\n")
    (claude / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "tok-SECRET-123456", "expiresAt": (time.time() + 3600) * 1000}}))
    empty = str(tmp_path / "nothing")
    path = tmp_path / "config.json"
    base = {k: empty for k in (
        "grok_home", "codex_home", "gemini_home", "qwen_home", "kimi_home", "opencode_home",
        "copilot_otel_path", "antigravity_home", "dsh_home", "pi_home", "openclaw_home",
        "goose_home", "hermes_home", "amp_home", "factory_home", "vscode_storage")}
    base["claude_home"] = str(claude)
    path.write_text(json.dumps(base))
    monkeypatch.setattr("omnimonitor.billing.find_grok_binary", lambda cfg: None)
    monkeypatch.setattr("omnimonitor.cursor_app_auth.default_db_path",
                        lambda: tmp_path / "no-cursor.vscdb")
    return path


def _by(status):
    return {s["id"]: s for s in status["suites"]}


def _src(status, pid):
    return next(r for s in status["suites"] for r in s["sources"] if r["id"] == pid)


def test_every_suite_is_listed_with_a_state_and_at_most_one_action(home):
    st = onboarding.status(config_mod.load_config(home))
    from omnimonitor import suites
    assert [s["id"] for s in st["suites"]] == [s.id for s in suites.SUITES if s.id != "other"]
    for s in st["suites"]:
        assert s["state"] in onboarding.STATES
        assert s["action"] is None or s["action"]["type"] in ("enable", "signin", "add_key")
    assert st["demo"] is True


def test_detects_installed_tools_and_cli_logins(home):
    st = onboarding.status(config_mod.load_config(home))
    assert _src(st, "claude_code")["state"] == "found"
    assert _src(st, "claude_cli_plan")["state"] == "found"
    anth = _by(st)["anthropic"]
    assert anth["state"] == "found"
    assert anth["action"] == {"type": "enable", "label": "Turn on 2 found",
                              "sources": ["claude_code", "claude_cli_plan"]}
    assert _src(st, "codex")["state"] == "not_available"
    assert _src(st, "codex_cli_plan")["state"] == "not_available"
    assert "codex" in _src(st, "codex_cli_plan")["detail"]
    assert "tok-SECRET" not in json.dumps(st)


def test_web_source_offers_sign_in_and_api_source_offers_a_key_command(home):
    st = onboarding.status(config_mod.load_config(home))
    assert _src(st, "chatgpt_web")["state"] == "sign_in"
    assert _by(st)["openai"]["action"] == {"type": "signin", "source": "chatgpt_web",
                                           "label": "Sign in"}
    ds = _by(st)["deepseek"]
    assert ds["state"] == "add_key"
    assert ds["action"]["command"] == "omnimonitor secrets set DEEPSEEK_API_KEY"


def test_each_source_says_whether_it_was_checked_on_real_data(home):
    st = onboarding.status(config_mod.load_config(home))
    assert _src(st, "claude_cli_plan")["verified"] == "live"
    assert _src(st, "deepseek")["verified"] == "fixtures"
    assert _src(st, "bedrock" if False else "dashscope")["verified"] == "none"


def test_without_a_keychain_sign_in_is_not_available(home, _fake_keyring):
    _fake_keyring.fail = True
    row = _src(onboarding.status(config_mod.load_config(home)), "chatgpt_web")
    assert row["state"] == "not_available" and "keychain" in row["detail"]


def test_enabled_source_with_an_error_needs_attention(home):
    cfg = config_mod.load_config(home)
    cfg.update(source="none", providers={"claude_cli_plan": {"enabled": True}})
    st = onboarding.status(cfg, readings={"claude_cli_plan": {
        "ok": False, "error": "HTTP 401: sign in again"}})
    assert _src(st, "claude_cli_plan")["state"] == "attention"
    assert _by(st)["anthropic"]["state"] == "attention"
    st = onboarding.status(cfg, readings={"claude_cli_plan": {"ok": True, "error": ""}})
    assert _src(st, "claude_cli_plan")["state"] == "connected"


def test_saved_session_and_saved_key_are_detected(home):
    sessions.save("claude_plan", "sessionKey=abc")
    apikeys.set_secret("DEEPSEEK_API_KEY", "sk-test-0000000000")
    st = onboarding.status(config_mod.load_config(home))
    assert _src(st, "claude_plan")["state"] == "found"
    assert _src(st, "deepseek")["state"] == "found"


def test_enable_leaves_demo_without_grok(home):
    plan = onboarding.plan(config_mod.load_config(home),
                           {"action": "enable", "sources": ["claude_code", "claude_cli_plan"]})
    assert plan.changes["source"] == "none"
    assert plan.changes["providers"]["claude_code"] == {"enabled": True}
    assert plan.signin is None


def test_enable_keeps_other_provider_options_and_real_source(home):
    cfg = config_mod.load_config(home)
    cfg.update(source="grok_cli_billing",
               providers={"cursor": {"enabled": True, "auth": "browser", "browser": "edge"}})
    changes = onboarding.plan(cfg, {"action": "enable", "sources": ["codex"]}).changes
    assert "source" not in changes
    assert changes["providers"]["cursor"]["browser"] == "edge"


def test_grok_pool_on_and_off(home):
    cfg = config_mod.load_config(home)
    on = onboarding.plan(cfg, {"action": "enable", "sources": ["grok"]}).changes
    assert on["source"] == "grok_cli_billing"
    cfg["source"] = "grok_cli_billing"
    off = onboarding.plan(cfg, {"action": "disable", "sources": ["grok"]}).changes
    assert off["source"] == "none"


def test_signin_enables_the_source_and_asks_for_a_window(home):
    plan = onboarding.plan(config_mod.load_config(home),
                           {"action": "signin", "source": "claude_plan"})
    assert plan.signin == "claude_plan"
    assert plan.changes["providers"]["claude_plan"] == {"enabled": True, "auth": "login"}


def test_cursor_app_is_only_used_when_asked_for(home):
    cfg = config_mod.load_config(home)
    plan = onboarding.plan(cfg, {"action": "cursor_app"})
    assert plan.changes["providers"]["cursor"] == {"enabled": True, "auth": "cursor_app"}
    web = onboarding.plan(cfg, {"action": "enable", "sources": ["cursor"]})
    assert web.changes["providers"]["cursor"]["auth"] == "login"


@pytest.mark.parametrize("bad", [
    None, [], {}, {"action": "run"}, {"action": "enable"},
    {"action": "enable", "sources": ["nope"]}, {"action": "enable", "sources": ["bedrock"]},
    {"action": "enable", "sources": "codex"}, {"action": "signin", "source": "codex"}])
def test_bad_requests_are_refused(home, bad):
    with pytest.raises(onboarding.OnboardingError):
        onboarding.plan(config_mod.load_config(home), bad)


def test_controller_applies_the_plan_and_rereads_at_once(home, monkeypatch):
    started = []
    monkeypatch.setattr("omnimonitor.session_login.start_login",
                        lambda pid, on_done=None: started.append((pid, on_done)) or "opening")
    ctrl = Controller(config_mod.load_config(home))
    res = ctrl.onboard({"action": "signin", "source": "claude_plan"})
    assert res["ok"] and started[0][0] == "claude_plan"
    assert json.loads(home.read_text())["source"] == "none"
    assert ctrl.token_source != "demo"
    # a cached "signed out" reading must not hide the new sign-in for 15 minutes
    now = ctrl.clock().timestamp()
    stale = accounts.Reading("claude_plan", False, now, error="signed out", attempted_at=now)
    ctrl.history.set_meta(accounts.META_PREFIX + "claude_plan", json.dumps(stale.to_dict()))
    started[0][1](True, "signed in")
    assert accounts.load(ctrl.history, "claude_plan") is None


def test_controller_reports_a_failed_sign_in_start(home, monkeypatch):
    from omnimonitor import session_login

    def boom(pid, on_done=None):
        raise session_login.LoginError("no window here")
    monkeypatch.setattr("omnimonitor.session_login.start_login", boom)
    res = Controller(config_mod.load_config(home)).onboard(
        {"action": "signin", "source": "claude_plan"})
    assert res == {"ok": False, "error": "no window here"}


def test_web_route_exists_and_needs_csrf(home):
    import urllib.error
    import urllib.request
    ctrl = Controller(config_mod.load_config(home))
    srv = webui.for_controller(ctrl, 0).start()
    try:
        req = urllib.request.Request(
            srv.url + "api/app/onboard", data=b'{"action":"real"}',
            headers={"Content-Type": "application/json"}, method="POST")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=5)
        assert e.value.code == 403
        req.add_header(webui.CSRF_HEADER, srv.csrf)
        assert json.load(urllib.request.urlopen(req, timeout=10))["ok"] is True
        view = json.load(urllib.request.urlopen(srv.url + "api/setup", timeout=10))
        assert view["onboarding"]["demo"] is False
    finally:
        srv.stop()


def test_cli_setup_yes_turns_on_what_was_found(home, capsys):
    assert cli.main(["--config", str(home), "setup", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "Anthropic" in out and "Sign in" in out and "tok-SECRET" not in out
    cfg = config_mod.load_config(home)
    assert cfg["source"] == "none"
    assert cfg["providers"]["claude_code"]["enabled"] is True
    assert cfg["providers"]["claude_cli_plan"]["enabled"] is True
    assert "chatgpt_web" not in cfg["providers"]  # sign-in is never done silently


def test_cli_setup_with_nothing_found_stays_in_demo(home, capsys, tmp_path):
    doc = json.loads(home.read_text())
    doc["claude_home"] = str(tmp_path / "nothing")
    home.write_text(json.dumps(doc))
    assert cli.main(["--config", str(home), "setup", "--yes"]) == 1
    assert config_mod.load_config(home)["source"] == "demo"


def test_first_run_opens_setup_while_in_demo(home, monkeypatch):
    from omnimonitor.tray import TrayApp
    monkeypatch.setattr("omnimonitor.alerts.deliver", lambda *a, **k: True)
    for source, page in (("demo", "setup"), ("none", "")):
        (config_mod.config_dir() / "first-run-done").unlink(missing_ok=True)
        opened = []
        app = TrayApp.__new__(TrayApp)
        app.ctrl = type("C", (), {"cfg": {"source": source}})()
        app.server = object()
        app._open = lambda rel="", view="full", o=opened: o.append(rel)
        assert app.first_run_hint() is True and opened == [page]


def test_setup_page_lists_suites():
    from importlib import resources

    def read(n):
        return resources.files("omnimonitor").joinpath("static", n).read_text("utf-8")
    assert 'id="suites"' in read("setup.html")
    assert '"onboard"' in read("setup.js")


def test_cli_setup_with_closed_stdin_changes_nothing(home, monkeypatch):
    """Windows reports NUL as a terminal: the question then ends in EOF. That means no."""
    import builtins
    import sys

    def eof(*_a):
        raise EOFError
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(builtins, "input", eof)
    assert cli.main(["--config", str(home), "setup"]) == 1
    assert config_mod.load_config(home)["source"] == "demo"
