# Step 1: Suite layer + de-Grok defaults — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make suites the top-level concept and stop treating Grok as special in the tray, menu, overview and settings.

**Architecture:** A pure-data `suites.py` maps every catalog provider to a suite. `overview.build` gains a `suites` list that groups cards and computes each suite's headline limit. The tray follows a `tray_suite` setting (suite id, `tightest` or `budget`) that replaces `primary_provider`, migrated on config load.

**Tech Stack:** Python 3.9+, pytest, pystray, stdlib web UI (vanilla JS).

**Spec:** `docs/superpowers/specs/2026-11-25-suites-rework-design.md`

## Global Constraints

- Python >= 3.9 syntax (no `match`, no `X | Y` types at runtime).
- Existing tests stay green; ruff clean (`ruff check src tests`).
- Never invent a number: a suite with no reading has `headline = None` and a `reason`.
- Migrate, don't reset: an existing `primary_provider` maps to a `tray_suite`.
- No Grok-only wording or defaults in user-facing tray/menu/settings text touched here.

## Review Focus

- Config with `primary_provider` set to an id no longer in the catalog → `tray_suite` falls back to `tightest`, no crash. (Task 3 test)
- A stale limit window (reset in the past) must not become a suite headline. (Task 2 test)
- Chosen suite has no data → tray uses tightest available and labels "(X not available)"; nothing available → icon `?`. (Task 3 test)
- Demo mode: suites still build and are flagged demo. (Task 2 test)
- Old `providers primary <provider-id>` CLI usage keeps working by mapping to its suite. (Task 4 test)

---

### Task 1: `suites.py` and catalog `suite`/`verified`

**Files:**
- Create: `src/omnimonitor/suites.py`
- Modify: `src/omnimonitor/catalog.py` (add `verified()` and `LIVE_VERIFIED` set)
- Test: `tests/test_suites.py`

**Interfaces:**
- Produces: `Suite(id: str, name: str, families: Tuple[str, ...], usage_url: str)`; `SUITES: Tuple[Suite, ...]`; `get(sid) -> Optional[Suite]`; `suite_of(pid: str) -> str`; `members(sid: str) -> List[str]`; `SUITE_IDS: Tuple[str, ...]`; `TRAY_CHOICES = SUITE_IDS + ("tightest", "budget")`; `map_legacy_primary(pid: str) -> str`.
- Produces: `catalog.verified(pid) -> str` returning `"live" | "fixtures" | "none"`.

Suites (id → families): `anthropic`→Claude; `openai`→ChatGPT; `google`→Gemini; `xai`→Grok, Cursor (name "Grok & Cursor"); `deepseek`; `qwen`; `kimi`; `github`→Copilot; `gateways`→OpenRouter, Vercel, LiteLLM, DeepInfra, Fireworks, Venice, Ollama; `agents`→opencode, Pi, OpenClaw, Block, Nous Research, Amp, Factory, Cline, Roo Code, Kilo Code; `other`→everything else. Usage URLs: claude.ai/settings/usage, chatgpt.com/codex/settings/usage, aistudio.google.com, grok.com/dashboard, platform.deepseek.com/usage, bailian.console.aliyun.com, platform.moonshot.ai/console, github.com/settings/billing, "" for the rest.

`map_legacy_primary`: `budget`→`budget`; a suite id or `tightest` → itself; known provider id → `suite_of(id)`; unknown → `tightest`.

`LIVE_VERIFIED = {"grok_sessions", "claude_code", "codex"}` (confidence strings say tested on real files); `verified(pid)` = `"none"` for kind `none`, `"live"` if in set, else `"fixtures"`.

- [ ] Step 1: tests — `test_every_catalog_provider_has_a_suite`, `test_known_mappings` (`claude_plan`→anthropic, `codex`→openai, `gemini_cli`→google, `grok`→xai, `cursor`→xai, `deepseek`→deepseek, `openrouter`→gateways, `cline`→agents, `bedrock`→other), `test_map_legacy_primary` (`grok`→xai, `budget`→budget, `nope`→tightest), `test_verified` (`claude_code`→live, `claude_plan`→fixtures, `bedrock`→none).
- [ ] Step 2: run, expect ImportError.
- [ ] Step 3: implement.
- [ ] Step 4: `pytest tests/test_suites.py -q` passes.
- [ ] Step 5: commit `feat: suite layer over the provider catalog`.

### Task 2: suite view in `overview.build`

**Files:** Modify `src/omnimonitor/overview.py`; Test `tests/test_suites.py`.

**Interfaces:**
- Consumes: Task 1 `suites`.
- Produces: `suite_view(cards: List[dict]) -> List[dict]`; each item `{id, name, card_ids, headline: Optional[{used_percent, resets_at, label, source}], today_tokens, today_usd, demo, reason, usage_url}`. `build()` output gains `"suites"` (only suites with ≥1 card, in `SUITES` order).
- Headline = the non-stale limit with the highest `used_percent` across member cards; `label` = "<card name> <limit name>"; `source` = card id. None → `reason = "no limit reading yet"`.

- [ ] Step 1: tests — `test_suite_view_groups_cards`, `test_suite_headline_is_tightest_non_stale`, `test_suite_view_no_limits_has_reason`, `test_build_includes_suites_in_demo`.
- [ ] Step 2: fail. Step 3: implement. Step 4: pass. Step 5: commit `feat: group overview cards into suites`.

### Task 3: `tray_suite` replaces `primary_provider`

**Files:** Modify `config.py` (default `"tray_suite": "tightest"`, migrate in load), `overview.py` (`primary()` follows `tray_suite`, `tray_value()` drops Grok fallback), `settings.py` (`tray_suite` choice of `TRAY_CHOICES`), `doctor.py`, `static/settings.js`; Test `tests/test_suites.py`, update existing tests asserting `primary_provider`.

**Interfaces:**
- Produces: `primary(cfg, suites_view, budget_view) -> dict` with the same keys as today (`id, name, available, used_percent, remaining_percent, resets_at, label, reason`).
- `config.migrate_tray_suite(cfg: dict) -> dict`: if `tray_suite` absent and `primary_provider` present → `map_legacy_primary`; drops `primary_provider`.
- `tray_value(ov, billing)` keeps its signature; no Grok fallback. Chosen suite unavailable → tightest available suite, label suffixed `" (<name> not available)"`; nothing → `(None, "no data")`.

- [ ] Step 1: tests — `test_migrate_primary_provider`, `test_migrate_unknown_primary`, `test_primary_tightest`, `test_primary_suite_falls_back_labelled`, `test_tray_value_none_without_data`, `test_settings_accepts_tray_suite`.
- [ ] Step 2: fail. Step 3: implement. Step 4: full `pytest -q` passes. Step 5: commit `feat: tray follows a suite (tray_suite), migrated from primary_provider`.

### Task 4: tray menu, CLI and copy de-Grok

**Files:** Modify `tray.py`, `cli.py`, `README.md` (tray + config rows), `config.example.json`; Test `tests/test_tray.py`, `tests/test_app_cli.py`.

- Tray title from `overview.tray_value`: `"<label>: N% left"`; loading → `"OmniMonitor: loading…"`.
- Menu: one disabled line per suite (`overview.suite_menu_lines(ov) -> List[str]`, max 10, e.g. `"Grok & Cursor: 27% used · resets in 4d 3h · today 1.2M tok"`), replacing the Grok-suite / Grok tokens / pace lines (still on the stats page).
- "Open Grok Usage Page" → "Usage pages" submenu, one item per suite with data and a `usage_url`.
- CLI: `providers primary X` and new `tray X` accept `TRAY_CHOICES` or a provider id (mapped, mapping printed).

- [ ] Step 1: tests — `test_suite_menu_lines`, `test_cli_tray_maps_provider_id`.
- [ ] Step 2: fail. Step 3: implement. Step 4: `pytest -q`, `ruff check src tests`, `omnimonitor --demo status`. Step 5: commit `feat: suite-first tray menu, CLI and docs`.

### Task 5: PR

- [ ] Push `feat/suite-layer`, open PR, wait for CI, squash-merge.
