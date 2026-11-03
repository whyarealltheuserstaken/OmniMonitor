# OmniMonitor

**Unofficial** tray / menu-bar app that shows how much of your **Grok weekly usage pool** is
left, plus **local token stats** (tokens this week, lifetime, peak day, longest task, streaks
and a GitHub-style heatmap) on a private page served only to your own computer.

Runs on **macOS** (Apple Silicon and Intel), **Windows 10/11** and **Linux** (GNOME/KDE). It
is the standalone successor to the old SwiftBar plugin in [`legacy/`](legacy/), so SwiftBar
is no longer needed.

> **Unofficial. Not affiliated with, endorsed by, or supported by xAI or X.** "Grok" is a
> trademark of its owner and is mentioned here only to describe what the app works with.
> This app reads an **undocumented** endpoint used by the official Grok CLI, so it may break
> at any time. See the [disclaimer](#disclaimer).
>
> *OmniMonitor was previously called `grok-usage-tray`.* Existing configs and autostart
> entries are migrated automatically; see [Upgrading from grok-usage-tray](#upgrading-from-grok-usage-tray).

![Stats page (demo data)](docs/screenshot-stats.png)

<sub>Screenshot rendered from **demo mode** (fake, labeled data). Tray icons: green ≥ 50 %
left, yellow ≥ 20 %, red below that, gray `?` = no data yet, gray `!` = error, blue dot =
demo.</sub>

![Tray icons](docs/tray-icons.png)

---

## What it shows

**Tray menu**

- `72% left`: remaining share of the weekly pool. In demo mode this reads `DEMO · 72% left`.
- `Resets Sun Nov 8, 4:00 PM (in 4d 3h)`, in your local time.
- `Tokens: 1.9M of ≈8.7M (estimate)`, `Tokens this week: 1.9M (limit not exposed)` or
  `Tokens this week: not available`.
- `⚠ <error>`, shown only when the last refresh failed. The last good value stays in the
  menu and is marked *stale*.
- **Refresh Now**, **Open Stats Page** (also the default action when you click the icon),
  **Open Grok Usage Page**, **Edit Config**, **Quit**.

**Stats page** (`http://127.0.0.1:<random port>/s/<random secret>/`, opened from the menu)

| Stat | Where it comes from | Real or not |
|---|---|---|
| Weekly pool remaining %, reset time, per-product % | the billing endpoint | **real** (unofficial API) |
| Tokens used this billing week | Grok CLI session logs on **this computer** | **real**, but local CLI only |
| Weekly token limit ("out of Y") | **not exposed by xAI anywhere** | shown as *not available*, or as a flagged **≈ estimate** (see below) |
| Lifetime tokens, peak day, streaks, heatmap (Daily / Weekly / Cumulative) | session logs + local history | real, local CLI only |
| Longest task | turn durations in the logs, or the time between the first and last event of a turn | real; derived values are labeled "from log times" |
| Top models, insights (sessions, turns, cache share, reasoning share, recorded cost) | session logs | shown only if the data exists |
| Top plugins | no source exposes this | card hidden |

Anything that cannot be measured says **"not available"**, with a tooltip that explains
why. Nothing is made up or silently estimated.

### Token stats: what's real, what isn't

- **The billing endpoint returns percentages, not tokens.** Your plan's weekly allowance is
  a pool of *credits* weighted by model, caching and product (Grok chat, Grok Build/CLI and
  others). Credits are not the same thing as tokens, and xAI does not publish a token limit.
- **Token counts come from the Grok CLI's own logs** (`~/.grok/sessions/**/updates.jsonl`,
  `turn_completed` events), read **read-only**. Usage on grok.com, in the mobile apps, in
  other tools or on other computers is **not** in those logs.
- **The "≈Y" estimate** is `tokens this week ÷ (Grok Build % used ÷ 100)`, or the overall %
  when no per-product number is returned. It appears only if `estimate_weekly_limit` is
  true **and** at least `estimate_min_used_percent` (default 5 %) is used. It is always
  shown with `≈` and the word *estimate*, and it can be far off. Set
  `"estimate_weekly_limit": false` to hide it.
- **History:** tokens, turns and billing snapshots are stored in a local SQLite file next
  to the config (`history.sqlite3`, chmod 600). On first run the app **backfills from any
  Grok CLI session logs already on disk**. Without such logs, history **starts from the day
  you install it**. The weekly % has no history API, so the snapshot history starts at
  install.

---

## Install

You need **Python 3.9+**. Prebuilt binaries are planned via
[GitHub Releases](https://github.com/whyarealltheuserstaken/OmniMonitor/releases) (see
[Maintaining](#maintaining)). Until those are published, install from source:

```bash
git clone https://github.com/whyarealltheuserstaken/OmniMonitor.git
cd OmniMonitor
python3 -m pip install --user .        # or: pipx install .
omnimonitor --demo                     # try it with fake data
```

### macOS (Apple Silicon and Intel)

```bash
brew install python                    # if you don't have Python 3.9+
pipx install /path/to/OmniMonitor      # or: python3 -m pip install --user .
omnimonitor setup
omnimonitor                            # the icon appears in the menu bar
```

The app runs as a normal background process, without a Dock icon. If macOS hides menu-bar
items (notch or full bar), use Bartender/Ice or Control Center settings to show it.

### Windows 10/11

```powershell
py -m pip install --user .
omnimonitor setup
omnimonitor-gui            # same app, no console window
```

On Windows 11 the icon can land in the "hidden icons" overflow (^). Drag it onto the
taskbar to pin it.

### Linux

```bash
python3 -m pip install --user .     # Debian/Ubuntu may need: pipx install .
omnimonitor setup
omnimonitor
```

**Tray caveat (AppIndicator):** pystray needs a status-notifier/AppIndicator host.

- **GNOME** has none by default. Install the *AppIndicator and KStatusNotifierItem
  Support* extension (`gnome-shell-extension-appindicator`; Ubuntu ships it enabled).
- For the best backend also install the GObject bindings and indicator library:
  `sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1` (Fedora:
  `python3-gobject libayatana-appindicator-gtk3`). If you use a venv, create it with
  `--system-site-packages` so it can see `gi`.
- **KDE Plasma**, XFCE, Cinnamon and MATE show tray icons natively.
- Without a tray host the icon can't show. Use `omnimonitor serve --open` (stats page
  only) or `omnimonitor status` instead.

---

## Run

```bash
omnimonitor                     # tray app (same as `omnimonitor run`)
omnimonitor --demo              # force demo mode
omnimonitor status [--json]     # print once and exit (also handy for scripts)
omnimonitor serve --open        # stats page only, no tray
omnimonitor doctor              # diagnose CLI / login / billing / logs  (--offline, --json)
omnimonitor setup               # first run: check login, switch off demo mode  (-y = no prompt)
omnimonitor config-path         # where the config lives
```

### First run

1. Install the official Grok CLI and sign in once (`grok login`). See https://docs.x.ai.
2. Run `omnimonitor setup`. It checks the CLI, your login, a billing call and your
   session logs, and switches `source` to `grok_cli_billing` **only if the billing call
   worked**.
3. Run `omnimonitor`. Until setup succeeds, the app stays in **demo mode**: every
   number is fake and labeled **DEMO**.

### Start at login

```bash
omnimonitor install-autostart         # and: omnimonitor uninstall-autostart
```

| OS | What it creates |
|---|---|
| macOS | `~/Library/LaunchAgents/com.omnimonitor.tray.plist` (loaded with `launchctl`) |
| Windows | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` value `omnimonitor` (uses `pythonw`/`omnimonitor-gui`, no console) |
| Linux | `~/.config/autostart/omnimonitor.desktop` (XDG; GNOME, KDE, XFCE, …) |

It registers whichever program you ran it with (the PyInstaller binary or the installed
script). Run it again after moving or reinstalling. Both commands also remove an old
`grok-usage-tray` entry (`io.github.grok-usage-tray.plist`, Run value `grok-usage-tray`,
`grok-usage-tray.desktop`) if one is left over, so the app never starts twice.

---

## Configuration

`omnimonitor config-path` shows where the config lives:

| OS | Config dir |
|---|---|
| macOS | `~/Library/Application Support/omnimonitor/` |
| Windows | `%APPDATA%\omnimonitor\` |
| Linux | `$XDG_CONFIG_HOME/omnimonitor/` (default `~/.config/omnimonitor/`) |

The dir holds `config.json` (chmod 600 where supported) and `history.sqlite3`. Set
`OMNIMONITOR_HOME` to move both. A broken config never crashes the app: it falls back
to defaults and shows the error in the menu. See [`config.example.json`](config.example.json).

| Key | Default | Meaning |
|---|---|---|
| `source` | `"demo"` | `demo` (fake, labeled), `grok_cli_billing` (unofficial endpoint used by the Grok CLI), `command` (your own script) |
| `token_source` | `"auto"` | `auto` (= `demo` in demo mode, else `grok_sessions`), `grok_sessions`, `demo`, `none` |
| `poll_interval_seconds` | `120` | how often to refresh; minimum **60** |
| `usage_page_url` | `https://grok.com/dashboard` | opened by "Open Grok Usage Page" |
| `grok_home` | `""` | Grok CLI dir; empty = `$GROK_HOME` or `~/.grok` |
| `grok_auth_file` | `""` | CLI auth file; empty = `$GROK_AUTH_PATH` or `<grok_home>/auth.json` (read-only) |
| `grok_refresh_command` | `"auto"` | when the CLI token is stale or rejected, run `grok models` so **the official CLI** refreshes its own login. `""` = never. A list such as `["/path/grok","models"]` is also accepted |
| `refresh_timeout_seconds` | `60` | max time for that command |
| `refresh_skew_seconds` | `300` | treat the token as stale this many seconds before expiry |
| `timeout_seconds` | `15` | HTTP / command timeout |
| `token` | `""` | manual bearer token instead of the auth file (expires after a few hours; prefer the auth file) |
| `command` | `""` | for `source: command`: argv list or string; must print `{"remaining_percent": 72, "resets_at": "2026-11-09T00:00:00Z"}` (or `used_percent`) |
| `grok_billing_url` | the CLI endpoint | only `https://*.grok.com` or `http://127.0.0.1`/`localhost` (for testing) are accepted |
| `web_port` | `0` | stats page port; 0 = random free port (recommended) |
| `estimate_weekly_limit` | `true` | show the flagged "≈Y" estimate |
| `estimate_min_used_percent` | `5.0` | minimum % used before an estimate is shown |
| `heatmap_weeks` | `53` | heatmap length |

Environment overrides: `OMNIMONITOR_HOME`, `OMNIMONITOR_SOURCE` and
`OMNIMONITOR_TOKEN`, plus the CLI's own `GROK_HOME` and `GROK_AUTH_PATH`. The pre-rename
names (`GROK_USAGE_TRAY_HOME`, `GROK_USAGE_TRAY_SOURCE`, `GROK_USAGE_TRAY_TOKEN`) are still
honored when the new ones are unset.

### Upgrading from grok-usage-tray

OmniMonitor is the renamed `grok-usage-tray`. On first start, if the new config dir doesn't
exist yet but the old one does (`~/Library/Application Support/grok-usage-tray/`,
`%APPDATA%\grok-usage-tray\` or `~/.config/grok-usage-tray/`), its `config.json` and
`history.sqlite3` are **copied** to the new dir. The old dir is left untouched, so you can
delete it yourself once you're happy. If the copy fails (for example, a read-only disk), the
app keeps reading the old dir instead. Uninstall the old package with
`pip uninstall grok-usage-tray` (or `pipx uninstall grok-usage-tray`) and re-run
`omnimonitor install-autostart` if you used start-at-login.

### Parsing rules (unchanged from the SwiftBar plugin)

- `creditUsagePercent` is the **used** share. The endpoint omits it when it's 0, so
  *missing* means **0 % used**, but only when the rest of the response looks like a real
  billing payload. Otherwise the app reports an error.
- Values over 100 (on-demand overage) clamp to **100 % used / 0 % left**. The raw value is
  kept in `status --json`.
- The reset time is `currentPeriod.end`, falling back to `billingPeriodEnd`.

---

## Security and privacy

- Reads the Grok CLI auth file **read-only**. It never writes it, never uses the refresh
  token itself, and never reads browser cookies, the macOS Keychain or other apps' stores.
  When the token is stale, it runs the official `grok models` command (no shell) and lets
  the CLI refresh its own login.
- The only network request goes to the billing endpoint. There is no telemetry and no
  update check.
- The stats page binds to **127.0.0.1 only**, on a random port, under a random per-run URL
  secret. It also checks the `Host` header (DNS-rebinding guard), allows GET/HEAD only and
  sends a strict CSP.
- The config and history files are chmod 600 where the OS supports it.

See [SECURITY.md](SECURITY.md).

---

## Troubleshooting

Start with `omnimonitor doctor`.

| Symptom | Fix |
|---|---|
| Menu says **DEMO** | You haven't run `omnimonitor setup` yet, or it failed. Run `doctor` |
| `grok CLI not found` | Install it (https://docs.x.ai) or set `grok_refresh_command` to its full path |
| `not logged in` / `401` | Run `grok login` once, then **Refresh Now** |
| `No personal team` (HTTP 412) | The endpoint only serves personal accounts. Team/enterprise seats aren't supported |
| `Tokens this week: not available` | No Grok CLI session logs on this machine (`<grok_home>/sessions`). Usage elsewhere can't be counted |
| Weekly token limit "not available" | Expected: xAI doesn't expose it. Below 5 % used, the estimate is hidden on purpose |
| No icon on Linux | See the [AppIndicator caveat](#linux). Use `serve --open` meanwhile |
| No icon on Windows | Check the hidden-icons overflow (^) |
| Stats page won't open | `omnimonitor serve` prints the URL. The secret changes on every run, so old bookmarks stop working |
| Numbers look stale | The menu marks stale data. Polling is ≥ 60 s; use **Refresh Now** |

---

## Maintaining

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
ruff check src tests
pytest -q
python scripts/mock_billing_server.py --used 42.5  # fake endpoint on 127.0.0.1:8765
python scripts/make_fake_sessions.py /tmp/fake-grok-home   # synthetic CLI logs
scripts/screenshot.sh                              # re-render docs/screenshot-stats.png
```

![Stats page against the mock billing server](docs/screenshot-stats-mock-billing.png)
<sub>Mock billing server + synthetic session logs (no real account data).</sub>

**Layout:** `billing.py` (sources and parsing), `tokens.py` (session-log scanner),
`history.py` (SQLite), `stats.py` (pure stat functions), `app.py` (controller, never
raises), `tray.py` (pystray), `webui.py` + `static/` (stats page), `autostart.py`,
`doctor.py`, `cli.py`.

**Adding a usage source:** write `fetch_x(cfg) -> UsageResult` in `billing.py`, register it
in `SOURCES`, raise `UsageError` with a human-readable message on failure, and add tests
with a fixture. **Never return made-up numbers.**

**If the endpoint changes:** capture a redacted response into `tests/fixtures/`, update
`parse_billing`, and keep the rules above.

**Releases:** bump `__version__` (in `src/omnimonitor/__init__.py`) and
`pyproject.toml`, update `CHANGELOG.md`, then tag `vX.Y.Z` and push the tag.
`.github/workflows/release.yml` builds PyInstaller binaries (`omnimonitor-<os>-<arch>`, e.g.
`omnimonitor-macos-arm64.zip`, `omnimonitor-windows-x86_64.zip`, `omnimonitor-linux-x86_64.tar.gz`) on macOS 14 (arm64), macOS 15 Intel
(`macos-15-intel`; GitHub retired `macos-13`), Windows and Ubuntu, and attaches them to a GitHub Release. The binaries are
unsigned, so macOS Gatekeeper and Windows SmartScreen will warn until signing is set up.

---

## Disclaimer

OmniMonitor is an independent, unofficial community project. It is **not affiliated with,
endorsed by or supported by xAI or X**. "Grok" is a trademark of its owner; it is used
here only descriptively, to say which service the app reads usage for. It uses an **undocumented** endpoint
(`cli-chat-proxy.grok.com/v1/billing`) that the official Grok CLI calls, and it may stop
working or change without notice. Usage numbers are informational only; the official
source is your account's usage page. Use at your own risk, under your account's terms of
service. Background research: [docs/research.md](docs/research.md).

## License

[MIT](LICENSE)
