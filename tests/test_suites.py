"""Suites: every provider belongs to one suite; the tray follows a suite, not Grok."""
from omnimonitor import catalog, suites


def test_every_catalog_provider_has_a_suite():
    for p in catalog.CATALOG:
        assert suites.suite_of(p.id) in suites.SUITE_IDS, p.id


def test_known_mappings():
    expect = {"claude_plan": "anthropic", "claude_code": "anthropic", "codex": "openai",
              "gemini_cli": "google", "grok": "xai", "cursor": "xai", "deepseek": "deepseek",
              "qwen_code": "qwen", "moonshot": "kimi", "github_copilot": "github",
              "openrouter": "gateways", "cline": "agents", "bedrock": "other"}
    for pid, sid in expect.items():
        assert suites.suite_of(pid) == sid, pid


def test_members_and_get():
    assert "grok" in suites.members("xai") and "cursor" in suites.members("xai")
    assert suites.get("xai").name == "Grok & Cursor"
    assert suites.get("nope") is None


def test_map_legacy_primary():
    assert suites.map_legacy_primary("grok") == "xai"
    assert suites.map_legacy_primary("codex") == "openai"
    assert suites.map_legacy_primary("budget") == "budget"
    assert suites.map_legacy_primary("anthropic") == "anthropic"
    assert suites.map_legacy_primary("nope") == "tightest"
    assert "tightest" in suites.TRAY_CHOICES and "budget" in suites.TRAY_CHOICES


def test_verified():
    assert catalog.verified("claude_code") == "live"
    assert catalog.verified("claude_plan") == "fixtures"
    assert catalog.verified("bedrock") == "none"
