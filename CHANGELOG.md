# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

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
