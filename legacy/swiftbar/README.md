# usage-menubar

A tiny macOS menu bar item that shows how much of your **weekly usage pool**
(X Subscription Super / SuperGrok — shared across Grok, Cursor and Grok Bot)
you have **left**, e.g. `🟢 72%`.

- Refreshes every 5 minutes (the `5m` in `usage.5m.py`).
- Dropdown: remaining %, reset time (your local time), per-product breakdown
  (when the source provides it), last updated, **Refresh Now**, **Open usage page**,
  **Edit config…**, **Quit**.
- Starts in **DEMO mode** — fake numbers, shown as `72% DEMO` with an orange
  "DEMO MODE - fake numbers" line. It is NOT your real usage until you change `source`.
- Python 3 standard library only. Never reads browser cookies, the Keychain, or
  other apps' credential files by itself. It reads the Grok CLI's own
  `~/.grok/auth.json` **only if you set `grok_auth_file`**, read-only.

## Important: where the real number can come from

| Source (`"source"` in config) | Status | What you need |
|---|---|---|
| `demo` | Fake data, for trying it out | nothing |
| `grok_cli_billing` | **Unofficial / undocumented.** The same endpoint the Grok CLI's `/usage` panel calls. Can break or change any time. | Grok CLI installed + `grok login` once. Recommended: `grok_auth_file` + `grok_refresh_command` (no re-pasting). Or paste a token yourself (expires after ~6 h). |
| `command` | Your own script that prints `{"remaining_percent": 72, "resets_at": "2026-11-01T00:00:00Z"}` | a script |
| `stub` | Placeholder for an official API — **xAI has not published one** for the weekly pool | — |

The only official place to see the number is **Settings → Usage** on grok.com
(`https://grok.com/dashboard`). That's what "Open usage page" opens.

## Install (Apple Silicon, ~3 minutes) — SwiftBar (recommended)

1. Install Homebrew if you don't have it: https://brew.sh
2. Install SwiftBar and make sure python3 exists:
   ```bash
   brew install --cask swiftbar
   xcode-select --install   # only if `python3 --version` asks you to install tools
   ```
3. Put the plugin in a plugins folder:
   ```bash
   mkdir -p ~/SwiftBarPlugins
   cp usage.5m.py ~/SwiftBarPlugins/
   chmod +x ~/SwiftBarPlugins/usage.5m.py
   ```
   Only copy `usage.5m.py` into that folder (SwiftBar runs *every* file in it).
4. Create your config:
   ```bash
   mkdir -p ~/.config/usage-menubar
   cp config.example.json ~/.config/usage-menubar/config.json
   ```
5. Open SwiftBar (`open -a SwiftBar`), choose `~/SwiftBarPlugins` as the plugin
   folder when asked. You should see `🟢 xx% DEMO` in the menu bar.
6. Optional: SwiftBar → Preferences → *Launch at login*.

Test it in Terminal any time: `python3 ~/SwiftBarPlugins/usage.5m.py`

## Alternative: standalone rumps app (no SwiftBar)

```bash
python3 -m pip install --user rumps
python3 usage_menubar_rumps.py     # keep usage.5m.py in the same folder
```
It uses the same config and the same `fetch_usage()` function.

## Switching from demo to real data (unofficial endpoint)

> ⚠️ This uses an **undocumented** xAI endpoint (found by third-party tools, not
> in xAI's docs). It's your own account and your own token, but it may stop
> working without notice. Don't share your token or commit it to git.

### Recommended: let the Grok CLI keep the token fresh (no re-login every 6 h)

The Grok CLI stores its login in `~/.grok/auth.json` (owner-only, `0600`). The
access token (`key`) lasts ~6 hours, but the file also holds a long-lived
`refresh_token`, and **the CLI refreshes the access token itself** whenever it
runs and finds it expired, rewriting `auth.json`. So you only need `grok login`
once; after that, running any CLI command that talks to xAI (e.g. `grok models`)
silently mints a new token.

This plugin can use that, **only if you opt in**:

1. Install the Grok CLI (`curl -fsSL https://x.ai/cli/install.sh | bash`,
   installs to `~/.grok/bin/grok`) and run `grok login` once.
2. Check the refresh works without a browser: `~/.grok/bin/grok models`
   should print "You are logged in with …" and a model list.
3. Put this in `~/.config/usage-menubar/config.json`:
   ```json
   {
     "source": "grok_cli_billing",
     "grok_auth_file": "~/.grok/auth.json",
     "grok_refresh_command": "~/.grok/bin/grok models",
     "usage_page_url": "https://grok.com/dashboard"
   }
   ```
   (Leave `token` empty — a pasted `token` takes priority over `grok_auth_file`.)
4. `python3 ~/SwiftBarPlugins/usage.5m.py` should show your real `%`.

How it behaves:
- Every run it reads the current `key` + `expires_at` from `auth.json` (read-only;
  the plugin never writes the file and never uses the `refresh_token` itself —
  the CLI owns refresh, including its file locking and token rotation).
- If the token is expired / within `refresh_skew_seconds` (300) of expiry, or the
  endpoint answers 401/403, it runs `grok_refresh_command` once (up to
  `refresh_timeout_seconds`), re-reads `auth.json` and retries.
- If that still fails, the refresh token itself is dead (logged out, revoked,
  or long idle) → the menu says to run `grok login`. Expected to be rare.
- Without `grok_refresh_command`, it still reads the file but just tells you to
  run `grok` once when the token has expired.
- If your CLI uses a non-default location (`GROK_HOME` or `GROK_AUTH_PATH`), point
  `grok_auth_file` there.

### Manual alternative: paste a token

Put `"token": "..."` (or `"token_file": "/path/you/control"`, or
`export USAGE_MENUBAR_TOKEN=...` — SwiftBar doesn't see Terminal env vars) in the
config and `chmod 600` it. It expires after ~6 hours and shows
`HTTP 401: token expired` — then paste a new one.

What it calls:
```
GET https://cli-chat-proxy.grok.com/v1/billing?format=credits
Authorization: Bearer <token>
X-XAI-Token-Auth: xai-grok-cli
```
Shown as remaining = `100 - config.creditUsagePercent`, reset = `config.currentPeriod.end`.

## Changing the data source

Everything is in `fetch_usage(cfg)` in `usage.5m.py`. Add a function that returns
`UsageResult(remaining_percent, resets_at_datetime, label="...")` (or raises
`UsageError("message")`), register it in `SOURCES`, and set `"source"` in the config.

## Config reference (`~/.config/usage-menubar/config.json`)

| key | default | meaning |
|---|---|---|
| `source` | `demo` | `demo`, `grok_cli_billing`, `command`, `stub` |
| `usage_page_url` | `https://grok.com/dashboard` | opened by "Open usage page" |
| `token` / `token_file` | empty | only for `grok_cli_billing`; manual token (wins over `grok_auth_file`) |
| `grok_auth_file` | empty | opt-in path to the Grok CLI's `auth.json`, e.g. `~/.grok/auth.json` (read-only) |
| `grok_refresh_command` | empty | opt-in command that makes the CLI refresh its token, e.g. `~/.grok/bin/grok models` |
| `refresh_timeout_seconds` | 60 | max time for `grok_refresh_command` |
| `refresh_skew_seconds` | 300 | refresh this many seconds before `expires_at` |
| `command` | empty | only for `source: command` |
| `timeout_seconds` | 15 | network/command timeout |
| `use_sf_symbols` | false | `true` = gauge SF Symbol icon instead of 🟢🟡🔴 (macOS 13+) |

Env overrides: `USAGE_MENUBAR_SOURCE`, `USAGE_MENUBAR_TOKEN`.

## Troubleshooting

- Nothing in the menu bar: run `python3 ~/SwiftBarPlugins/usage.5m.py` in Terminal and read the error.
- "Permission denied": `chmod +x ~/SwiftBarPlugins/usage.5m.py`.
- `grok_refresh_command exited …` or `HTTP 401 after CLI refresh`: run `grok login` in
  Terminal once, then Refresh Now.
- `grok_refresh_command` hangs/times out: run it in Terminal to see if it's waiting
  for a browser login; SwiftBar's PATH is minimal, so use the full path
  (`~/.grok/bin/grok`).
- Quit only quits SwiftBar; reopen with `open -a SwiftBar`.
