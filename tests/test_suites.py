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


# ------------------------------------------------------------- tray_suite (Task 3)
import json  # noqa: E402

from omnimonitor import config as config_mod  # noqa: E402
from omnimonitor import settings as settings_mod  # noqa: E402


def _ov(cards, cfg, budgets=None):
    sv = overview.suite_view(cards)
    return {"suites": sv,
            "primary": overview.primary(cfg, sv, budgets or {"periods": {}}, T)}


def _lim(pct, stale=False):
    return [{"name": "weekly", "used_percent": pct, "resets_at": T + 99, "stale": stale}]


def test_migrate_primary_provider(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"primary_provider": "codex"}))
    cfg = config_mod.load_config(p)
    assert cfg["tray_suite"] == "openai" and "primary_provider" not in cfg


def test_migrate_unknown_primary_and_default(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"primary_provider": "gone_provider"}))
    assert config_mod.load_config(p)["tray_suite"] == "tightest"
    p.write_text(json.dumps({"primary_provider": "codex", "tray_suite": "google"}))
    assert config_mod.load_config(p)["tray_suite"] == "google"  # explicit wins
    assert config_mod.load_config(tmp_path / "missing.json")["tray_suite"] == "tightest"


def test_primary_tightest():
    ov = _ov([_c("grok", _lim(20.0)), _c("claude_plan", _lim(70.0))], {})
    p = ov["primary"]
    assert p["available"] and p["id"] == "tightest" and p["used_percent"] == 70.0
    assert p["label"] == "Anthropic" and p["remaining_percent"] == 30.0


def test_primary_suite_falls_back_labelled():
    ov = _ov([_c("grok", _lim(20.0)), _c("claude_code")], {"tray_suite": "anthropic"})
    p = ov["primary"]
    assert p["available"] and p["used_percent"] == 20.0
    assert p["label"] == "Grok & Cursor (Anthropic not available)"
    assert overview.tray_value(ov, None) == (80.0, p["label"])


def test_tray_value_none_without_data():
    ov = _ov([_c("claude_code"), _c("grok", _lim(90.0, stale=True))], {"tray_suite": "xai"})
    assert overview.tray_value(ov, {"remaining_percent": 70.0})[0] is None  # no Grok fallback


def test_primary_budget():
    b = {"periods": {"daily": {"percent": 150.0, "resets_at": T + 5}}}
    p = _ov([], {"tray_suite": "budget"}, b)["primary"]
    assert p["used_percent"] == 100.0 and p["label"] == "daily budget"


def test_settings_accepts_tray_suite():
    v = settings_mod.validate
    assert v("tray_suite", "anthropic") == "anthropic"
    import pytest
    with pytest.raises(settings_mod.SettingError):
        v("tray_suite", "grok")


# ----------------------------------------------------- tray menu + CLI (Task 4)
def test_suite_menu_lines():
    sv = overview.suite_view([
        _c("grok", _lim(27.0), today=1_200_000),
        _c("claude_code", today=5000, usd=1.25),
    ] + [_c(p, today=10) for p in ("deepseek", "qwen_code", "kimi", "github_copilot", "openrouter",
                         "cline", "gemini_cli", "codex", "bedrock")])
    lines = overview.suite_menu_lines({"suites": sv}, now=T)
    assert lines[0] == "Anthropic: today 5k tok · $1.25"
    xai = next(x for x in lines if x.startswith("Grok & Cursor"))
    assert xai == "Grok & Cursor: 27% used · resets in 0h 1m · today 1.2M tok"
    assert len(overview.suite_menu_lines({"suites": sv}, now=T, limit=3)) == 3
    assert all(len(x) <= 80 for x in lines)


def test_cli_tray_maps_provider_id(tmp_path, monkeypatch, capsys):
    from omnimonitor import cli
    monkeypatch.setenv("OMNIMONITOR_HOME", str(tmp_path))
    assert cli.main(["providers", "primary", "codex"]) == 0
    assert "openai" in capsys.readouterr().out
    cfg = json.loads(next(tmp_path.rglob("config.json")).read_text())
    assert cfg["tray_suite"] == "openai" and "primary_provider" not in cfg
    assert cli.main(["tray", "tightest"]) == 0
    assert json.loads(next(tmp_path.rglob("config.json")).read_text())["tray_suite"] == "tightest"
    assert cli.main(["tray", "nonsense"]) == 2


def test_unflagged_past_reset_is_not_a_headline():
    # readings / billing cards carry "stale": False even when the window already reset
    sv = overview.suite_view([_c("openrouter", [{"name": "limit", "used_percent": 95.0,
                                                 "resets_at": T - 60, "stale": False}])], now=T)
    assert sv[0]["headline"] is None
