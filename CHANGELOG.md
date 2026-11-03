# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [0.3.0] - unreleased

More providers, all opt-in. Format knowledge (not code) was cross-checked against the
public documentation and open-source CLIs linked in `docs/providers.md`; ideas from
[Token Monitor](https://github.com/Javis603/token-monitor) and
[tokscale](https://github.com/junhoyeo/tokscale) (both MIT). No code was copied.

### Added
- Provider catalog (`catalog.py`) with 33 entries and a generated matrix,
  `docs/providers.md` (`scripts/gen_providers_md.py`; a test fails if it is stale).
- Local-log adapters: **Gemini CLI** (`~/.gemini/tmp/**/chats`), **Qwen Code**
  (`~/.qwen/projects/**/chats`), **Kimi CLI / Kimi Code / Kimi Work** (`wire.jsonl`),
  **opencode** (message JSON or `opencode.db` opened read-only) and **GitHub Copilot CLI**
  (OpenTelemetry file exporter). All read-only, incremental and deduplicated.
- **Codex plan limits:** the 5-hour / weekly `rate_limits` Codex writes to its rollout logs
  (used %, reset time, plan type). Out-of-range values are ignored.
- Account adapters using official APIs and a key you supply: Anthropic Admin cost report,
  OpenAI organization costs, xAI prepaid balance, DeepSeek balance, Moonshot balance
  (global or cn), GitHub Copilot premium-request usage, Cursor team spend (Admin API),
  OpenRouter key credits / limits, Ollama Cloud balance. HTTPS only, host allow-list, no
  redirects, ≥ 5 min between calls (`provider_poll_seconds`, default 900), last good
  reading kept on failure, errors scrubbed of the key.
- `providers` setting with per-provider switches; `primary_provider` picks what the tray
  icon shows (with a labelled fallback to the Grok pool).
- `omnimonitor providers [list|enable|disable|primary|check] [--json]`,
  `omnimonitor secrets [list|set|unset]` (chmod-600 `secrets.json`, hidden input),
  `omnimonitor config export|import` (validated settings only; never tokens or keys).
- Stats page: one card per enabled provider (tokens and cost today / 7 d / 30 d, limit %,
  pace, reset, balances, errors), all-tools totals, budgets card. Settings page: Providers
  table (switches, primary, key status only), budgets, per-provider thresholds.
- USD **budgets** (daily / weekly / monthly) on local-log cost with alerts at
  `budget_alert_percent` and 100 %; **per-provider thresholds** (`provider_thresholds`).
- Compact "today" tray tooltip and one tray menu line per enabled provider.
- `export --kind providers` (CSV / JSON).
- `doctor` checks every enabled provider (log folders; key, secrets-file permissions and
  one live read, `--offline` to skip).
- Tests: 51 new tests with synthetic fixtures and mocked HTTP (184 total).

### Changed
- `extra_token_sources` still works; explicit `providers` entries take precedence.
- Demo mode also shows demo provider cards, prices and budgets, all labelled DEMO.

### Security
- API keys are never accepted from, or returned by, the web UI (`api/settings` shows only
  "set (env)" / "set (file)" / "not set"). A secrets file readable by other users is
  ignored and flagged by `doctor`.
- A test scans the sources to make sure nothing reads browser cookies or keychains.

## [0.2.0] - unreleased

Feature ideas inspired by [Token Monitor](https://github.com/Javis603/token-monitor)
(MIT, © 2026 Javis). No code or assets were copied; see `docs/inspiration-notes.md`.

### Added
- Opt-in token adapters for **Claude Code** (`~/.claude/projects/**/*.jsonl`) and **Codex**
  (`~/.codex/sessions/**/rollout-*.jsonl`), read-only and incremental
  (`extra_token_sources`, `claude_home`, `codex_home`). Their tokens are shown per tool and
  never count toward the Grok pool or the ≈ limit estimate. Formats: `docs/token-adapters.md`.
- Breakdowns by tool, model and project (folder name only) for today, the billing week,
  7 days, 30 days and all time, with cache-hit share.
- Cost: recorded cost from Grok logs, plus ≈ estimates from a user-supplied `prices` table
  (model names or globs). No prices are bundled. Unpriced tokens are reported, not guessed.
- Pace and forecast: ≈ used at reset and ≈ runs-out time (average pace from the real
  weekly % and period dates, recent pace from saved snapshots), and burn rate (last hour,
  per active hour today). "Not available" when the data is insufficient.
- Desktop alerts: % used thresholds (`notify_thresholds`, default 80/95), weekly reset,
  optional on-pace-to-run-out. Each fires once per billing period, never on demo data.
  `omnimonitor notify-test`.
- Export: `daily.csv`, `daily-models.csv`, `breakdown.csv`, `events` CSV and `export.json`
  from `omnimonitor export`, the tray **Export** submenu and the stats page. Cells that start
  with `= + - @` are escaped against spreadsheet formula injection.
- Personalization: `theme` (dark/light/auto), `accent_color`, `display_name`,
  `hidden_cards`, tray `icon_style` (ring/pie/bar/number) and `icon_value`
  (remaining/used).
- Local **settings page** (`…/settings`) and **mini view** (`…/mini`).
- Tray menu: Today and Pace lines, Open Mini View, Settings…, Export submenu.
- CLI: `breakdown`, `providers`, `export`, `config get/set/keys`, `notify-test`.
- `scripts/screenshot.py` (Playwright) and `scripts/render_icons.py` for docs images.

### Changed
- History database schema: events gain `source` and `project` columns. Existing 0.1
  databases are migrated in place (old rows become `grok_sessions`).
- The web server accepts exactly one write, `POST api/settings`, guarded by Host + Origin
  checks, a per-run CSRF header, JSON-only bodies up to 64 KB and a key allow-list.
- Settings saved from the web page or `config set` are merged into the config **file**,
  so environment overrides (such as `OMNIMONITOR_TOKEN`) are never written to disk.
- `omnimonitor setup` now changes only `source` in the config file (and honors `--config`)
  instead of rewriting the whole in-memory config, which could have saved an env-provided
  token.
- Token scans are serialized with a lock (settings saves and the poll loop can both
  trigger one).
- `docs/screenshot-*.png` and `docs/tray-icons.png` regenerated from demo data.

## [0.1.0] - unreleased

### Added
- Project name **OmniMonitor** (repo `whyarealltheuserstaken/OmniMonitor`, package
  `omnimonitor`, command `omnimonitor` / `omnimonitor-gui`), formerly `grok-usage-tray`.
- Automatic migration of the old `grok-usage-tray` config dir to the new `omnimonitor`
  dir (copy; falls back to reading the old dir if the copy fails), legacy
  `GROK_USAGE_TRAY_*` environment variables as fallbacks, and cleanup of old autostart
  entries on `install-autostart` / `uninstall-autostart`.
- Standalone cross-platform tray app (pystray + Pillow): macOS, Windows, Linux.
- Color-coded icon with the remaining %, and a menu with the reset time, the tokens line,
  errors, Refresh Now, Open Stats Page, Open Grok Usage Page, Edit Config and Quit.
- Sources: `demo` (default, labeled), `grok_cli_billing` (unofficial), `command`.
- Token stats from the Grok CLI's local session logs (incremental, read-only scanner) and
  SQLite history with backfill from existing logs.
- Local stats page (127.0.0.1, per-run secret) showing tokens this week, lifetime, peak
  day, longest task, streaks, a Daily/Weekly/Cumulative heatmap, top models and insights.
- Optional clearly labeled "≈ weekly token limit" estimate with a minimum-usage threshold.
- `status`, `serve`, `doctor`, `setup`, `install-autostart` and `uninstall-autostart`
  commands.
- CI (ruff + pytest on 3 OSes) and a PyInstaller release workflow producing
  `omnimonitor-<os>-<arch>` artifacts.

### Changed
- Replaces the SwiftBar plugin (kept in `legacy/swiftbar/`). The parsing rules are
  unchanged.
