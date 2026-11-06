# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.5.2-beta] - 2026-11-06

Beta / prerelease. GitHub marks this release as a prerelease; `/releases/latest` stays on the last stable (0.4.2) until a final 0.5.0.

### Fixed
- **Menu-bar ring has no inner black circle.** The hairline outline was a wider dark stroke on the same circle, so on a dark menu bar it read as a second ring inside the colored arc (seen at 70% left). The gauge is the colored arc and the dim track of that same stroke. Thresholds and the optional % are unchanged.

### Added
- **Monthly USD credit cap**, when `GET https://cli-chat-proxy.grok.com/v1/billing` (same Grok CLI login, no `format=credits`) includes `used` and `monthlyLimit`. Those fields are USD cents. The suite shows them as dollars, not as a token allowance.
- **Grok Bot meter** (`usagePercent`) from `POST https://cursor.com/api/dashboard/get-sand-usage-status`, only when the opt-in Cursor session is enabled and `hasNonZeroIncludedLimit` is true. A missing percent is not shown as 0. This is a separate weekly allowance, not a share of the Grok suite pool, and it has no token count or token cap.
- The suite card groups missing token counts and the missing token allowance in one "Why some figures are missing" list. A billing field whose name is a token limit is shown as the token allowance when the payload actually contains it.

### Not verified
- The ring without the inner stroke was checked with light/dark previews and CI, not on a real Retina menu bar after this build.
- The monthly credit cap and Grok Bot meter are tested with fixtures. They have not been live-checked on Henry's account in this build.
- Binaries are ad-hoc signed only (macOS) and not notarized; Windows is unsigned.

## [0.5.1-beta] - 2026-11-06

Beta / prerelease. GitHub marks this release as a prerelease; `/releases/latest` stays on the last stable (0.4.2) until a final 0.5.0.

### Fixed
- **macOS menu-bar icon is one ring.** The green / amber / red gauge had a dark halo and a light halo drawn as wider strokes on the same circle, so at a normal size (for example 73% left) it looked like three nested rings. Colored states are still not template images. A hairline dark edge keeps the single ring visible on a light menu bar; the color itself reads on a dark bar. No second ring and no filled inner disc. Thresholds are unchanged: green ≥ 40% left (`#3FB950`), amber 20–&lt;40% (`#D29922`), red &lt; 20% (`#F85149`). The optional % stays beside the ring. Preview: [docs/menubar-ring.png](docs/menubar-ring.png).

### Added
- **Grok suite breakdown** on the dashboard, mini view, and tray. The weekly total is still the shared pool from `GET https://cli-chat-proxy.grok.com/v1/billing?format=credits` (`creditUsagePercent`). `productUsage` shares of that same pool are grouped when the API names them:
  - Grok CLI — `GrokBuild` (and a literal `Grok CLI` name)
  - Grok Bot — only a product literally named Grok Bot
  - Cursor (Grok) — a product named Cursor / Grok-in-Cursor
  - Anything else the payload lists (Grok Chat, Grok Tasks, …) is shown as its own share and is not relabeled.
- Token and activity detail for **Grok CLI** comes from `~/.grok/sessions/**/updates.jsonl`. The experimental `~/.grok/logs/unified.jsonl` (`shell.turn.inference_done`) is the same CLI and is used only when session logs have no usage events, so the two are not added together.
- Missing figures stay **not available**, with the reason: `productUsage` omitted, product not listed (not treated as 0%), no CLI log yet, or no token log for Grok Bot / Cursor. Grok Bot's separate Cursor-account meter and Cursor's own plan % are not called from this view. If the product shares do not add up to the suite total, the gap is described and not filled in.

### Not verified
- The single-ring icon was checked with 2× light/dark previews and CI, not on a real Retina menu bar.
- The Grok suite split is tested against fixture billing payloads. It has not been live-checked against Henry's account on this build.
- Binaries are ad-hoc signed only (macOS) and not notarized; Windows is unsigned.

## [0.5.0-beta] - 2026-11-05

Beta / prerelease. GitHub marks this release as a prerelease; `/releases/latest` stays on the last stable (0.4.2) until a final 0.5.0.

### Added
- **Opt-in web-session providers** (off by default): `claude_plan`, `chatgpt_web`, `cursor`.
  Per provider you choose **in-app sign-in** (pywebview on the real login page) or **import
  from a browser** (Firefox / Chrome / Edge / Brave / Arc / Safari where feasible). Only that
  provider's domains and cookie names are read. Sessions are stored in the OS keyring when
  possible, else a chmod-600 file; never logged, exported, shown, or included in Report a
  problem. Sign out / forget per provider.
- Docs: [docs/cookies/](docs/cookies/) cites CodexBar's public endpoint/cookie notes (MIT;
  no code copied). Confidence for each new reader is **experimental** until live-verified.

### Changed
- **Tray icon colors follow remaining %:** green when ≥ 40 % left, amber when 20–&lt;40 %,
  red when &lt; 20 %. Same thresholds on macOS (menu-bar ring + optional %) and
  Windows/Linux gauges. On macOS the colored states are **not** NSImage templates
  (templates are monochrome); a thin dark+light halo keeps them readable on light and
  dark menu bars. Error / no-data stay template gray (non-alarm).
- CI/Release: Apple Silicon builds now run on `macos-15` (GitHub retired `macos-14` images on 2026-11-02).
- Privacy policy: cookie use is allowed only for explicitly enabled web-session providers,
  with a clear Settings explanation (Chromium Safe Storage prompt; Safari Full Disk Access).

### Not verified
- Web-session providers are **experimental** and have not been live-verified against real
  Claude / ChatGPT / Cursor accounts on a personal Mac.
- Colored menu-bar icons were checked via 2× light/dark previews and CI, not on a real
  Retina menu bar.
- Binaries are ad-hoc signed only (macOS) and not notarized; Windows is unsigned.

## [0.4.2] - 2026-11-05

### Fixed
- **Mini → full no longer opens the browser.** Closing the mini window to open the full one looked like a crash to the old window's watcher, so it fell back to the browser. Our own terminate is no longer treated as a crash. CI switch smoke on macOS and Windows confirms the parent and the stats server stay alive.
- **Mini view no longer "kills the app".** Before, the full window navigated itself to the mini page, and the only way back was a `target=_blank` "Full stats" link that WKWebView handed to the system browser. Mini is now its own compact native window (340x540, optional "keep the mini window on top" setting), with a **Full window** link that reopens the native full window. The tray process and stats server are never touched. CI runs a mini smoke and a mini -> full switch smoke on macOS and Windows.
- **"Full stats" / tray items open or focus the native window.** The browser opens only from the explicit **Open in browser** item.
- **Controls that did nothing inside the app window.** WKWebView ignores `<a download>`, `target=_blank`, `window.open`, and some dialogs and clipboard calls.
  - Exports are now saved to ~/Downloads (never overwriting) and revealed in Finder or Explorer.
  - External https links open in the default browser.
  - Copy falls back when the clipboard API is blocked.
  - All of this goes through new CSRF-protected `api/app/*` actions.
- **Tray "show file" on macOS** now reveals the file (`open -R`) instead of opening it in TextEdit.
- **Empty token stats say why** in a visible banner, not only in a tooltip. For example: no `~/.grok/sessions`, no `updates.jsonl`, unreadable files, or no `turn_completed` usage yet.
- **Grok session parsing:**
  - Turns are deduplicated by Grok's `prompt_id`, so forked sessions are not double counted.
  - Turns that have only top-level `usage` (no `modelUsage`) are counted once, with model "unknown".

### Changed
- **New tray icon.**
  - macOS: a crisp 22pt (44 px @2x) template ring that macOS tints for light and dark menu bars. An optional % label is controlled by the "macOS: show the % next to the ring" setting. The critical state (< 20%) is red.
  - Windows/Linux: a cleaner colored gauge, 8x supersampled, with no digits below 24 px.

### Added
- **Report a problem.** A sidebar button shows a redacted diagnostics report (version, platform, status, offline doctor checks). It contains no keys, tokens, secret URLs or home paths. You can copy it or save it to Downloads.

## [0.4.1] - 2026-11-04

### Fixed
- Installing the Grok CLI from the in-app Setup wizard and logging in left the app on demo numbers: the wizard never switched `source` away from `demo`, the running app never re-read `config.json`, and `doctor` reported `grok_cli_billing` even when the config still said `demo`.
  - The app now reloads `config.json` when it changes (for example after `omnimonitor setup` in a terminal).
  - The Setup page shows the Grok login state, the exact `grok login` command, a Re-check button and **Use my real Grok usage** (switches only after a successful billing read).
  - `doctor` reports the configured source honestly and adds a "running app" section from `app-state.json`, flagging app vs config mismatches.
  - Every poll is logged (source, % remaining or error, duration), as are Refresh clicks and the window process.
  - Token stats explain why they are unavailable and mention the experimental unified-log provider when that log exists.

### Not verified
- **The fix has not been run on a real Mac or with a real Grok account.** The diagnosis (the app stayed in demo mode) comes from reading the code and the reporter's log; it is covered by automated tests with mocked billing only.
- The "Use my real Grok usage" button, config reload and the doctor "running app" section have been tested only in CI (macOS, Windows, Linux runners), not by a person in the app window.
- Binaries are ad-hoc signed only (macOS) and not notarized; Windows is unsigned.

## [0.4.0] - 2026-11-04

### Added
- **Native app window** (pywebview) with an app-style UI: sidebar, dashboard charts, providers, stats and settings.
- **Setup wizard** (`omnimonitor setup` and in the app) that detects provider CLIs and can install them with each vendor's official installer.
- **More providers.** The matrix now has 56 entries: 13 supported, 11 partial, 11 experimental and 21 not supported, each with a reason ([docs/providers.md](docs/providers.md)).
  - New local logs: Claude Code transcripts with ≈5-hour block estimates, Kimi Work shareDir, Pi, Goose and Hermes (partial), and Grok CLI unified log, Antigravity, DeepSeek Harness, OpenClaw, Amp, Factory Droid, Cline, Roo Code and Kilo Code (experimental).
  - New API readers (your own key, env var or `secrets.json` only): Vercel AI Gateway (supported), DeepInfra, Fireworks and xAI month-to-date spend plus spending limit (partial), and Venice and self-hosted LiteLLM (experimental).
  - Documented as not supported, with the reason: z.ai, Volcengine, Hugging Face and Amazon Bedrock.
- **Service status badges** (opt-in, off by default) from official status pages: Claude, OpenAI, GitHub, Cursor, Fireworks and Vercel.
- **Reset countdowns** next to every reset time.
- **Proxy support** (`proxy_url`, CLI or config only).
- **`omnimonitor statusline [--json]`**: an offline one-line summary for shell prompts and tmux.
- Packaging: macOS `.dmg` files (ad-hoc signed app with an Applications link) and a Linux AppImage, in addition to the zip and tar.gz.

### Changed
- The tray, the window and the CLI share one outbound HTTP opener (proxy, certifi CA bundle, host allow-list, no redirects).
- Release CI publishes a non-draft release with `SHA256SUMS.txt`, mounts and checks each `.dmg`, and runs the AppImage.

### Not verified
- Nobody has run the window or tray on a real desktop yet; only CI runners have.
- The new providers are tested against mocked responses only, not real accounts.
- The binaries are unsigned (macOS ad-hoc only) and not notarized.

## [0.3.1] - 2026-11-03

### Fixed
- macOS: `run` crashed with SIGTRAP ("trace trap") right after launch. pystray calls the setup
  callback on a worker thread, and the app made the menu-bar icon visible and updated its
  image / menu from worker threads; AppKit requires the main thread. All tray updates are
  now marshalled to the main thread (`PyObjCTools.AppHelper.callAfter`).
- HTTPS failed with `CERTIFICATE_VERIFY_FAILED` in the prebuilt apps (no system CA store
  in frozen Python). Requests now use certifi's CA bundle (new dependency, bundled in the
  binaries), falling back to the system store; verification is never disabled.
- Finder's `-psn_…` launch argument is ignored instead of making argparse exit.

### Added
- Log file (`~/Library/Logs/OmniMonitor/omnimonitor.log` on macOS, `<config dir>/logs/`
  elsewhere) and `crash.log` with Python stacks on hard crashes; `omnimonitor log-path`.
- First launch shows a notification saying where the icon is (it can hide behind the
  notch on MacBooks) and opens the stats page once.
- `omnimonitor net-check [URL]` and a `tls` / `log file` line in `doctor`.
- Release CI also runs on packaging PRs; the macOS jobs make a real HTTPS request from the
  frozen app and launch the tray for 10 s.

## [0.3.0] - 2026-11-03

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

### Fixed
- CLI no longer crashes with UnicodeEncodeError on non-UTF-8 consoles (Windows cp1252).

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
