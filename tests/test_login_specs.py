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
