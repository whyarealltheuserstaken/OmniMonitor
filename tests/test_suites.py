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


# --------------------------------------------------------------- suite view (Task 2)
from datetime import datetime, timezone  # noqa: E402

from omnimonitor import overview  # noqa: E402

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
T = NOW.timestamp()


def _c(pid, limits=(), today=0, usd=None, demo=False):
    c = overview._card(pid, True, demo=demo, limits=list(limits))
    c["ranges"] = {"today": {"tokens": today, "usd": usd}}
    return c


def test_suite_view_groups_cards():
    sv = overview.suite_view([_c("grok"), _c("cursor"), _c("claude_code")])
    by = {s["id"]: s for s in sv}
    assert by["xai"]["card_ids"] == ["grok", "cursor"]
    assert [s["id"] for s in sv] == ["anthropic", "xai"]  # SUITES order, only suites with cards


def test_suite_headline_is_tightest_non_stale():
    sv = overview.suite_view([
        _c("grok", [{"name": "weekly pool", "used_percent": 20.0, "resets_at": T + 9}]),
        _c("cursor", [{"name": "plan", "used_percent": 40.0, "resets_at": T + 9},
                      {"name": "old", "used_percent": 99.0, "resets_at": T - 9, "stale": True}]),
    ])
    h = sv[0]["headline"]
    assert h["used_percent"] == 40.0 and h["source"] == "cursor"
    assert h["label"] == "Cursor (individual plans) plan"


def test_suite_view_no_limits_has_reason():
    s = overview.suite_view([_c("claude_code", today=1200, usd=0.5)])[0]
    assert s["headline"] is None and s["reason"] == "no limit reading yet"
    assert s["today_tokens"] == 1200 and s["today_usd"] == 0.5


def test_build_includes_suites_in_demo():
    ov = overview.build([], {}, NOW, tools=["claude_code"], readings={},
                        billing={"used_percent": 30.0, "is_demo": True}, is_demo=True,
                        tz=timezone.utc)
    ids = [s["id"] for s in ov["suites"]]
    assert "xai" in ids and "anthropic" in ids
    assert next(s for s in ov["suites"] if s["id"] == "xai")["demo"] is True
