"""One spec per sign-in: strict cookie filtering and signed-in detection."""
from omnimonitor import browser_cookies, login_specs


def test_foreign_domain_dropped():
    spec = login_specs.SPECS["claude_plan"]
    kept = login_specs.filter_cookies(spec, [("sessionKey", "sk-ant-good", ".claude.ai"),
                                             ("sessionKey", "sk-ant-evil", "evil.com"),
                                             ("other", "x", "claude.ai")])
    assert kept == [("sessionKey", "sk-ant-good")]


def test_empty_domain_only_required_names():
    spec = login_specs.SPECS["cursor"]
    kept = login_specs.filter_cookies(spec, [("WorkosCursorSessionToken", "a", ""),
                                             ("wos-session", "b", "")])
    assert kept == [("WorkosCursorSessionToken", "a")]


def test_signed_in_rejects_login_page():
    s = login_specs.SPECS["claude_plan"].signed_in
    assert not s("https://claude.ai/login")
    assert not s("https://claude.ai/login?returnTo=/new")
    assert s("https://claude.ai/new")
    assert not s("https://evil.com/claude.ai/new")
    assert not s("http://claude.ai/new")  # https only
    c = login_specs.SPECS["chatgpt_web"].signed_in
    assert not c("https://chatgpt.com/auth/login") and c("https://chatgpt.com/")
    assert not c("https://auth.openai.com/log-in")


def test_browser_filter_matches_specs():
    for pid, spec in login_specs.SPECS.items():
        assert browser_cookies.PROVIDER_COOKIE_FILTER[pid] == (spec.hosts, spec.cookie_names)
    assert set(login_specs.ids()) >= {"claude_plan", "chatgpt_web", "cursor"}


def test_one_required_cookie_is_enough():
    spec = login_specs.SPECS["cursor"]
    assert login_specs.enough(spec, [("WorkosCursorSessionToken", "a")])  # was: needed all 3
    assert not login_specs.enough(spec, [("wos-session", "b")])
    assert login_specs.enough(login_specs.SPECS["chatgpt_web"], [("oai-did", "x")])


# ------------------------------------------------ sign-in in a child process (Task 4)
import threading  # noqa: E402

import pytest  # noqa: E402

from omnimonitor import session_login, sessions, vault  # noqa: E402


class _Proc:
    def __init__(self, code):
        self.code = code

    def wait(self, timeout=None):
        return self.code


def _start(code, pid="claude_plan"):
    calls, done, got = [], threading.Event(), {}

    def popen(args, **kw):
        calls.append(args)
        return _Proc(code)

    def on_done(ok, msg):
        got.update(ok=ok, msg=msg)
        done.set()
    assert session_login.start_login(pid, on_done=on_done, popen=popen) == "opening sign-in window"
    assert done.wait(5)
    return calls, got


def test_start_login_spawns_child():
    calls, got = _start(0)
    assert calls[0][-2:] == ["login", "claude_plan"]
    assert got == {"ok": True, "msg": "signed in"}


def test_exit_codes_map_to_messages():
    assert _start(3)[1] == {"ok": False, "msg": "closed before sign-in finished"}
    assert _start(4)[1]["msg"] == sessions.NO_VAULT_MESSAGE
    assert _start(5)[1]["msg"] == "the sign-in window is not available on this system"
    assert _start(99)[1]["msg"] == "sign-in failed"


def test_login_refused_without_vault():
    vault.set_backend(False)
    with pytest.raises(session_login.LoginError) as e:
        session_login.start_login("claude_plan", popen=lambda *a, **k: _Proc(0))
    assert str(e.value) == sessions.NO_VAULT_MESSAGE


class _C:
    def __init__(self, name, value, domain):
        self.name, self.value, self.domain = name, value, domain


def test_capture_builds_header_from_strict_filter():
    spec = session_login.login_specs.SPECS["claude_plan"]
    header = session_login.capture_header(spec, [_C("sessionKey", "sk-ant-ok", ".claude.ai"),
                                                 _C("sessionKey", "sk-ant-evil", "evil.com"),
                                                 _C("ga", "1", "claude.ai")])
    assert header == "sessionKey=sk-ant-ok"
    assert session_login.capture_header(spec, [_C("ga", "1", "claude.ai")]) == ""


def test_cli_login_runs_child_flow(monkeypatch):
    from omnimonitor import cli
    seen = []
    monkeypatch.setattr(session_login, "run_login", lambda pid: seen.append(pid) or 0)
    assert cli.main(["login", "cursor"]) == 0 and seen == ["cursor"]
    assert cli.main(["login", "nope"]) == 2
