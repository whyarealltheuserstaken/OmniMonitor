# Inspiration notes: Token Monitor

OmniMonitor 0.2.0 borrows *ideas* (not code or assets) from
[Javis603/token-monitor](https://github.com/Javis603/token-monitor). These notes record what
we studied, what we adopted, and what we deliberately did differently.

Studied: commit `6f01d38` (2026-11-03), package version 0.85.0. Sources: its README,
`docs/architecture.md`, `docs/configuration.md`, `docs/export.md`, `docs/providers/grok.md`,
`docs/providers/claude.md`, `docs/providers/codex.md`, and the showcase screenshots.

## License

* Token Monitor: **MIT**, © 2026 Javis (`LICENSE`). One vendored provider has its own MIT
  license (© 2026 Peter Steinberger, `src/shared/providers/muse/LICENSE`).
* OmniMonitor is also MIT. MIT would allow copying with attribution, but **we copied no code,
  text, icons, images or other assets**. Everything in 0.2.0 is our own Python
  re-implementation of the ideas below. Because nothing was copied, there is no NOTICE file;
  the README credits Token Monitor as inspiration. If anyone ever does copy code from it, add
  a `NOTICE` file with its copyright line and license text.

## What Token Monitor is

An Electron desktop widget (Windows, macOS, Linux) that shows live token usage, cost and
"AI Tool Limits" for 48+ AI coding tools (Claude Code, Codex, Cursor, Copilot, Grok Build and
more). Highlights:

* **Data sources:** local session logs and databases of each tool, parsed by a vendored
  [tokscale](https://github.com/junhoyeo/tokscale) binary, plus provider limit APIs
  (session/weekly/credit windows) using each tool's own credentials.
* **Architecture:** Electron widget + shared collector (`src/shared/usage`), optional
  headless agent, optional sync hub (in-widget, Node, or Cloudflare Worker) that pushes
  aggregates to every device over SSE; also iCloud Drive sync.
* **Surfaces:** main widget window, macOS menu bar / Windows tray popover with a
  configurable "layout composer" (icon, quota bars, %, reset time, cost, tok/s),
  floating bubble (draggable mini window), Edge Dock, macOS Notch Panel, native macOS
  widgets, separate Dashboard window (trends, stacked per-tool/model history, streaks).
* **Views:** Day / Month / Total, plus This week / Last 7 days / Last 30 days; breakdowns
  by tool, model, session, project, device; cache hit vs miss per tool/model; activity
  heatmap; live tok/s or tok/min rate.
* **Cost:** pricing from tokscale (LiteLLM-style table) with custom per-model pricing and
  model aliases; currency conversion (USD/TWD/HKD/CNY). When some usage has no price it
  shows the known subtotal and flags the excluded tokens instead of guessing.
* **Limits:** per provider/account windows with reset countdowns; optional third-party
  Codex reset forecast. For Grok it reads `cli-chat-proxy.grok.com/v1/billing`
  (same unofficial endpoint OmniMonitor uses) plus Grok session logs.
* **Export:** CSV + JSON (`snapshot.csv`, `daily.csv`, `daily-models.csv`, `export.json`),
  manual or auto-written to a folder; tokens it can't attribute go in
  `unclassified_tokens` rather than being guessed.
* **Retention:** "Preserve deleted session usage" archives observed daily usage so history
  survives when tools prune their logs.
* **Personalization:** theme presets, light mode, custom colors, per-vendor colors, glass
  effects, fonts, which Home modules show and their order, hide/pin/reorder tools.
* **Other:** subscription records (what you pay, renewal), status pages, Discord Rich
  Presence, plugins (experimental), multi-account switching.
* **Install:** Homebrew cask, signed DMG/EXE, AppImage, auto-update from GitHub Releases.
* **Notifications:** desktop notifications are used for errors (refresh failed, account
  switch failed). We did not find usage-threshold alerts.

## Adopted (re-implemented in our own code)

| Token Monitor idea | OmniMonitor 0.2.0 |
|---|---|
| Many tools in one view | Pluggable token adapters: `grok_sessions` (default), `claude_code`, `codex` (opt-in) |
| Breakdowns by tool / model / project | Same, on the stats page, mini view and `omnimonitor breakdown` |
| Day / week / 7d / 30d / total ranges | Today, billing week, 7 days, 30 days, all time |
| Cache hit rate | Per tool and per model, in breakdown rows |
| Cost with custom pricing; unpriced usage flagged | Recorded cost (from Grok logs) + **estimates** from a user-editable `prices` table; unpriced tokens are counted and shown, never priced by guess |
| Live token rate | Burn rate: tokens in the last hour and per hour today |
| Reset countdown / forecast | Pace projection for the weekly % (see below) |
| CSV + JSON export, `unclassified` instead of guessing | `omnimonitor export` + download links; `daily.csv`, `daily-models.csv`, `breakdown.csv`, `export.json` |
| Floating bubble | Compact "mini" page sized for a small browser window |
| Themes, light mode, accent colors, choose which modules show | `theme`, `accent_color`, `hidden_cards`, `display_name` |
| Menu bar layout composer | Tray icon styles (`ring`, `pie`, `bar`, `number`) and value (remaining vs used) |
| Settings panel | Local settings page (secured POST, allow-listed keys) |
| Preserve deleted session usage | Already built in: OmniMonitor's SQLite history never forgets scanned events |

## Built differently (and why)

* **Python + pystray + stdlib web page** instead of Electron: tiny, no Node runtime, the
  existing OmniMonitor stack. No glass effects / notch / edge dock / native widgets.
* **Own log parsers** instead of vendoring tokscale: three small, documented, tested
  parsers. Formats are documented in `docs/token-adapters.md`.
* **Threshold notifications** (80 % / 95 % used, weekly reset detected, optional "on pace to
  run out"): Token Monitor only notifies on errors; this is new in OmniMonitor.
* **Pace projection** uses only what billing really reports (the weekly % and period
  start/end, plus our own snapshots). Shown as "projection", and "not available" when there
  are too few data points.
* **No bundled price table.** Token Monitor ships prices via tokscale. We don't ship
  numbers we can't verify; you add your own (`prices` in config or the settings page). Grok
  logs that already record a cost show that as "recorded".
* **No multi-device sync, hub, accounts switching, plugins, Discord, status pages, currency
  conversion.** Out of scope for a single-user tray app; they could come later.
* **Security of the settings page:** loopback only, per-run secret URL, Host + Origin checks,
  a per-run CSRF token in a custom header, JSON-only POST, an allow-list of keys. Keys that
  run commands or hold secrets (`command`, `grok_refresh_command`, `token`,
  `grok_billing_url`, file paths) are **not** editable from the browser.
