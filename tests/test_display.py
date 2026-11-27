"""Customization: order, per-surface visibility and numbers, colours, thresholds, intervals."""
import json

import pytest

from omnimonitor import accounts, catalog, cli, display, overview
from omnimonitor import config as config_mod
from omnimonitor import settings as settings_mod

NOW = 1_700_000_000.0


def _card(pid, used=None, today=0, usd=None, metrics=()):
    limits = [{"name": "w", "used_percent": used, "resets_at": NOW + 7200}] if used else []
    c = overview._card(pid, True, limits=limits)
    c["ranges"] = {"today": {"tokens": today, "usd": usd}}
    c["metrics"] = list(metrics)
    return c


def _ov(cfg, cards):
    ov = {"cards": cards, "suites": overview.suite_view(cards, NOW), "budgets": {"periods": {}}}
    ov["views"] = display.views(ov, cfg, NOW)
    ov["primary"] = overview.primary(cfg, ov["views"]["tray"]["suites"], ov["budgets"], NOW)
    return ov


CARDS = lambda: [_card("claude_code", used=40.0, today=1000, usd=1.5),  # noqa: E731
                 _card("codex", used=90.0, today=500),
                 _card("deepseek", metrics=[{"label": "Balance", "value": 3.5, "unit": "USD",
                                             "kind": "balance"}])]


def test_default_view_keeps_everything_in_suite_order():
    v = _ov({}, CARDS())["views"]["menu"]
    assert [s["id"] for s in v["suites"]] == ["anthropic", "openai", "deepseek"]
    assert v["card_ids"] == ["claude_code", "codex", "deepseek"]


def test_order_applies_to_suites_and_cards():
    cfg = {"display": {"order": ["deepseek", "openai"]}}
    v = _ov(cfg, CARDS())["views"]["dashboard"]
    assert [s["id"] for s in v["suites"]] == ["deepseek", "openai", "anthropic"]
    assert v["card_ids"] == ["deepseek", "codex", "claude_code"]


def test_hiding_is_per_surface_and_works_for_suite_or_source():
    cfg = {"display": {"hide": {"menu": ["openai"], "mini": ["claude_code"]}}}
    views = _ov(cfg, CARDS())["views"]
    assert [s["id"] for s in views["menu"]["suites"]] == ["anthropic", "deepseek"]
    assert "codex" in views["dashboard"]["card_ids"]
    assert views["mini"]["card_ids"] == ["codex", "deepseek"]
    assert [s["id"] for s in views["mini"]["suites"]] == ["openai", "deepseek"]


def test_hidden_from_tray_does_not_drive_the_icon():
    assert _ov({}, CARDS())["primary"]["used_percent"] == 90.0
    ov = _ov({"display": {"hide": {"tray": ["codex"]}}}, CARDS())
    assert ov["primary"]["used_percent"] == 40.0


def test_menu_numbers_can_be_chosen():
    cfg = {"display": {"fields": {"menu": ["limit"]}}}
    lines = overview.suite_menu_lines(_ov(cfg, CARDS()), now=NOW)
    assert lines == ["Anthropic: 40% used", "OpenAI: 90% used"]
    cfg = {"display": {"fields": {"menu": ["tokens", "money"]}}}
    lines = overview.suite_menu_lines(_ov(cfg, CARDS()), now=NOW)
    assert lines == ["Anthropic: today 1k tok", "OpenAI: today 500 tok",
                     "DeepSeek: Balance $3.50"]


def test_menu_default_numbers():
    lines = overview.suite_menu_lines(_ov({}, CARDS()), now=NOW)
    assert lines[0] == "Anthropic: 40% used · resets in 2h 0m · today 1k tok · $1.50"
    assert lines[2] == "DeepSeek: Balance $3.50"


def test_provider_menu_lines_respect_view_and_fields():
    cfg = {"display": {"hide": {"menu": ["claude_code"]}, "fields": {"menu": ["limit"]}}}
    assert overview.menu_lines(_ov(cfg, CARDS())) == ["Codex CLI (+ ChatGPT plan limits): 90% w"]


def test_suite_threshold_between_provider_and_global():
    cfg = {"notify_thresholds": [95],
           "provider_thresholds": {"openai": [70], "claude_code": [50], "anthropic": [60]}}
    assert overview.provider_thresholds(cfg, "codex") == [70.0]       # suite
    assert overview.provider_thresholds(cfg, "claude_code") == [50.0]  # source wins
    assert overview.provider_thresholds(cfg, "deepseek") == [95.0]    # global


def test_suite_colors_reach_the_view():
    ov = _ov({"suite_colors": {"openai": "#ff0000"}}, CARDS())
    by = {s["id"]: s for s in ov["views"]["dashboard"]["suites"]}
    assert by["openai"]["color"] == "#ff0000" and by["anthropic"]["color"] == ""


def test_build_includes_views():
    from datetime import datetime, timezone
    ov = overview.build([], {}, datetime(2026, 1, 1, tzinfo=timezone.utc), tools=[],
                        readings={})
    assert set(ov["views"]) == set(display.SURFACES)


# ------------------------------------------------------------------ validation
def test_display_setting_validates_and_normalizes():
    got = settings_mod.validate("display", json.dumps(
        {"order": ["openai", "codex", "openai"], "hide": {"menu": ["google"], "mini": []},
         "fields": {"menu": ["limit", "cost"]}}), web=True)
    assert got == {"order": ["openai", "codex"], "hide": {"menu": ["google"]},
                   "fields": {"menu": ["limit", "cost"]}}


@pytest.mark.parametrize("bad", [
    {"order": ["nope"]}, {"order": "openai"}, {"hide": {"toaster": ["openai"]}},
    {"hide": {"menu": ["<script>"]}}, {"fields": {"menu": ["secrets"]}}, {"extra": 1}, []])
def test_display_setting_rejects(bad):
    with pytest.raises(settings_mod.SettingError):
        settings_mod.validate("display", bad)


def test_suite_colors_setting():
    assert settings_mod.validate("suite_colors", {"openai": "#FF0000"}, web=True) == \
        {"openai": "#ff0000"}
    for bad in ({"nope": "#ff0000"}, {"openai": "red"}, {"openai": "#fff;}"}):
        with pytest.raises(settings_mod.SettingError):
            settings_mod.validate("suite_colors", bad)


def test_suite_ids_are_valid_threshold_keys():
    assert settings_mod.validate("provider_thresholds", {"openai": [70]}) == {"openai": [70]}


def test_source_poll_seconds_setting():
    assert settings_mod.validate("source_poll_seconds", {"deepseek": 3600}, web=True) == \
        {"deepseek": 3600}
    for bad in ({"deepseek": 60}, {"claude_code": 600}, {"nope": 600}):
        with pytest.raises(settings_mod.SettingError):
            settings_mod.validate("source_poll_seconds", bad)


def test_hand_edited_garbage_never_crashes():
    cfg = {"display": {"order": 5, "hide": [1], "fields": {"menu": "limit"}},
           "suite_colors": [], "source_poll_seconds": {"deepseek": "soon"}}
    assert _ov(cfg, CARDS())["views"]["menu"]["card_ids"]
    assert accounts.poll_seconds(cfg, "deepseek") == 900


def test_per_source_poll_interval():
    cfg = {"provider_poll_seconds": 900, "source_poll_seconds": {"deepseek": 3600}}
    assert accounts.poll_seconds(cfg, "deepseek") == 3600
    assert accounts.poll_seconds(cfg, "openrouter") == 900
    assert accounts.poll_seconds({"source_poll_seconds": {"deepseek": 5}}, "deepseek") == 300


def test_cli_can_set_display(tmp_path, capsys):
    p = tmp_path / "config.json"
    p.write_text("{}")
    assert cli.main(["--config", str(p), "config", "set", "display",
                     '{"hide": {"statusline": ["google"]}}']) == 0
    assert config_mod.load_config(p)["display"] == {"hide": {"statusline": ["google"]}}


# ------------------------------------------------------------- deferred minors
def test_grok_headline_label_is_not_doubled():
    c = overview._card("grok", True, limits=[{"name": "weekly pool", "used_percent": 5.0}])
    assert overview.suite_view([c])[0]["headline"]["label"] == "Grok weekly pool"


def test_import_of_an_old_export_migrates_primary_provider(tmp_path):
    p, src = tmp_path / "config.json", tmp_path / "old.json"
    p.write_text("{}")
    src.write_text(json.dumps({cli.EXPORT_MARK: "0.5.3", "primary_provider": "codex"}))
    assert cli.main(["--config", str(p), "config", "import", str(src)]) == 0
    assert config_mod.load_config(p)["tray_suite"] == "openai"


def test_dead_primary_choices_removed():
    assert not hasattr(catalog, "PRIMARY_CHOICES")


def test_tray_title_reports_a_failed_overview():
    from omnimonitor.tray import TrayApp
    app = TrayApp.__new__(TrayApp)
    app.ctrl = type("C", (), {"usage": None, "error": None, "stats": lambda self: {
        "providers": {"error": "provider overview failed: KeyError", "cards": []}}})()
    assert app._title() == "OmniMonitor: provider overview failed: KeyError"


def test_empty_submenus_have_a_placeholder():
    from omnimonitor.tray import submenu_empty
    assert submenu_empty({}) == {"providers": True, "usage": True}
    ov = {"cards": [_card("codex", used=5.0)], "suites": [{"id": "openai"}]}
    assert submenu_empty(ov) == {"providers": False, "usage": False}


def test_settings_page_has_a_layout_editor_and_views_are_used():
    from importlib import resources

    def read(n):
        return resources.files("omnimonitor").joinpath("static", n).read_text("utf-8")
    assert 'id="layout"' in read("settings.html") and 'id="layout_fields"' in read("settings.html")
    for key in ("display", "suite_colors", "source_poll_seconds"):
        assert "out.%s" % key in read("settings.js")
    assert "views.dashboard" in read("providers.js")
    assert "views.mini" in read("mini.js")


def test_public_view_lists_layout_rows_in_order(tmp_path):
    cfg = {"source": "none", "providers": {"codex": {"enabled": True},
                                           "claude_code": {"enabled": True}},
           "display": {"order": ["suite:openai"]}}
    ro = settings_mod.public_view(cfg)["_readonly"]
    assert [s["id"] for s in ro["suites"]] == ["openai", "anthropic"]
    assert ro["suites"][0]["sources"] == [{"id": "codex", "name": catalog.name("codex"),
                                           "account": False}]
