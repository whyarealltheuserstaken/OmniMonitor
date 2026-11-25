# OmniMonitor suites rework: design

Date: 2026-11-25. Status: draft, awaiting owner approval.

## Intent and why

OmniMonitor grew as a Grok tray app (`grok-usage-tray`) and later picked up Cursor
and ~35 other sources, but the product still reads Grok-first: defaults, the tray,
setup, the menu and the docs all start from the Grok weekly pool.

The owner wants it to be **a full usage, limits and stats monitor for every popular AI
platform**, organised as suites, with **every feature we claim working for real**, **fully
customizable**, and with **easy setup, sign-in and auth**. Features first; a UX/UI revamp
comes after everything works.

Success = a user who has accounts with some of these platforms installs the app, signs in
to each suite with a button (or has it pick up their existing CLI login), and sees accurate
plan limits, usage, stats and cost for all of them, with nothing claimed that isn't real.

## Goals

1. **Suites as the top-level concept.** Each suite groups that vendor's sources:
   - **Anthropic:** Claude plan (Pro/Max/Team limits), Claude Code (logs + plan limits), Claude API (Admin cost).
   - **OpenAI:** ChatGPT plan, Codex (logs + 5-hour/weekly windows), OpenAI API (org costs).
   - **Google:** Gemini app (if any limit data is reachable), Gemini CLI (logs + quota), Antigravity, Gemini API (documented as no usage endpoint).
   - **xAI / Cursor:** Grok plan (weekly pool, resets, dollars), Grok CLI logs, xAI API, Cursor (individual + team), Grok Bot meter.
   - **DeepSeek, Qwen, Kimi:** web plan where reachable, CLI logs, API balance.
   - **Others** kept from today's catalog and added where verifiable: GitHub Copilot, OpenRouter, Ollama Cloud, Vercel AI Gateway, Venice, DeepInfra, Fireworks, LiteLLM, opencode, Cline/Roo/Kilo, Amp, Goose, etc. Builder may add more (e.g. Mistral, Perplexity, Windsurf) only where a real source exists.
2. **No provider is special.** Grok becomes one suite among equals. The tray shows a suite
   (or "tightest limit across everything") the user picks; first-run picks the suite with
   the most data.
3. **Easy sign-in, secure.** One sign-in flow for every web-session source, plus automatic
   reuse of an existing official CLI login where one exists (see Auth).
4. **Every claim is real.** Each source has a test against recorded responses *and* a
   recorded live-verification status. Docs, README and `providers` output state per source:
   live-verified / fixture-only. Nothing is shown that the source didn't return.
5. **Fully customizable.** Per suite and per source: on/off, which numbers appear on the
   tray / menu / dashboard / mini view, order, thresholds and alerts, budgets, prices,
   poll interval, colours. All validated, all through the settings page or CLI.
6. **Easy setup.** First run detects installed tools and existing CLI logins, shows each
   suite with "Connected / Sign in / Add key / Not available", and gets the user from zero to
   real data without editing JSON. Demo mode is still there but clearly separate.

## Non-goals

- UI/visual redesign (next project; only functional UI changes now).
- Switching stack (stays Python + pystray + pywebview + local web UI).
- Team/enterprise admin dashboards beyond what already exists.
- Mobile apps, cloud sync, telemetry, auto-update.

## Anti-goals (failure even if it "works")

- Any secret (cookie, token, key) in a log, export, diagnostics bundle, URL, the web page,
  or a plaintext file.
- A number that looks real but was guessed, defaulted to 0, or computed from a mislabelled field.
- Breaking existing users: configs, history, autostart and existing providers keep working
  (migrate, don't reset).
- Grok-only wording or defaults left in user-facing places.

## Constraints

- Python >= 3.9 (CI), tested locally on Windows 11 / Python 3.15. macOS + Linux must keep building in CI.
- Existing 325 tests stay green; new code follows the same style (pure parsers + fixtures,
  `Reading` objects, `UsageError` with human messages).
- Network: HTTPS only, per-source host allow-list, no redirects, poll >= 5 min for web/API
  sources (existing `net.py` rules).
- Web sessions use undocumented endpoints; they can break. Failures must degrade to
  "Sign in again" / "not available" with the last good reading marked stale.
- Owner has accounts for Google, OpenAI, Anthropic, xAI/Grok and Cursor; **not** DeepSeek,
  Qwen or Kimi. Those three can only be fixture-verified and must be labelled so.
- Live verification happens at the end, after everything verifiable without accounts is done.

## Approaches considered

| | Approach | Trade-off |
|---|---|---|
| **A (chosen)** | Rework in place: add a suite layer over the existing catalog, one source-adapter interface, one hardened sign-in flow; port existing adapters onto it | Keeps working parsers, tests, packaging; some legacy code remains and is cleaned as touched |
| B | Rewrite the core, port parsers | Cleaner, but weeks before anything new works; high regression risk |
| C | New stack (Electron/Tauri) | Only makes sense with the UI revamp, which is explicitly later |

Owner picked A.

## Decided with the owner

- Suites as above, Grok/Cursor no longer the centre.
- **Web sign-in is the primary auth path** (a "Sign in" button per suite), replacing the
  "no cookies, no scraping" stance. It must be secure.
- Features before UX/UI.
- Live testing on the owner's accounts at the end, via an `omnimonitor verify` command:
  the owner signs in through the app's own sign-in windows on their PC; `verify` calls each
  enabled source, prints pass/fail, and saves **scrubbed** responses (no cookies, tokens,
  emails, account/org ids) as candidate fixtures. The builder only reads scrubbed output.
  Build `verify` + the scrubber early (in PR 2) so every suite PR uses the same fixture format.
- Builder merges its own PRs (squash) once CI is green and, for auth/network/web-server
  PRs, the security review is clean.

## Builder's calls (accepted by the owner)

- **No keyring available** (e.g. Linux without Secret Service): API keys keep using the
  owner-only `secrets.json` as today; web sign-in is refused with an explanation. Legacy
  plaintext session files are migrated into the encrypted store when a keyring exists,
  otherwise deleted, and the source shows "Sign in again".
- **Existing sources that can't be verified or are broken:** kept, labelled "not
  live-verified"; one confirmed dead is disabled with its reason shown, never silently removed.
- **Fixture-only sources** (DeepSeek, Qwen, Kimi, others) are off by default, shown in
  setup as "not live-verified"; "tightest limit" only considers enabled sources.
- **Migration is one-way.** A backup copy of the old `config.json` is kept; no plaintext
  backup of keys or sessions.

## Auth design

Source priority per suite (builder's call, owner may correct): **existing official CLI
login → web sign-in → API key**. All three can be active; each fills what it can.

1. **Reuse official CLI logins (read-only)** where a CLI stores its own login, the way the
   Grok source already uses `~/.grok/auth.json`:
   - Claude Code OAuth credentials → Claude plan 5-hour / weekly utilisation.
   - Codex `~/.codex/auth.json` → ChatGPT plan windows.
   - Gemini CLI OAuth creds → Code Assist quota.
   - Grok CLI (existing).
   Never write those files; never use their refresh tokens ourselves; if stale, ask the
   user to run the CLI (or run its harmless refresh command, as Grok does today).
   Exact endpoints and file formats are for the builder to research and confirm; each is
   flagged "undocumented" in the catalog.
2. **Web sign-in** (in-app pywebview window on the provider's real login page):
   - One `LoginSpec` per source: login URL, the exact signed-in URL pattern (not a prefix
     that also matches the login page), required cookie names, allowed cookie domains.
   - Only allow-listed cookie names from allow-listed domains are kept. **Fix today's gap**
     in `session_login._cookie_header` where a cookie with a non-matching domain can be kept.
   - Passwords are never touched; the window's storage is per-provider and cleared after
     capture (no persistent webview profile holding other sites' cookies).
   - **Google blocks sign-in inside embedded webviews.** For Google (and any provider that
     does the same) the fallback is the Gemini CLI login (above) or a browser-cookie import
     from a browser the user picks. The builder confirms which providers this affects.
   - Browser-cookie import (existing `browser_cookies.py`) stays as the alternative.
3. **Storage: OS keyring only, no plaintext fallback.**
   - Today `sessions.py` falls back to a chmod-600 file when `keyring` is missing; on
     Windows chmod is not meaningful. Remove the plaintext fallback.
   - Windows Credential Manager caps a secret at ~2.5 KB, and some sessions (ChatGPT cookie
     + bearer JWT) are larger. So: keyring stores a random 256-bit key per install; session
     blobs are AES-GCM encrypted files in the config dir. No keyring available (e.g. Linux
     without a Secret Service) → sign-in is refused with a clear message, API keys and CLI
     logins still work.
   - `keyring` and `cryptography` move from the `cookies` extra into core dependencies and
     into the PyInstaller builds.
   - API keys: same store when a keyring exists (migrate existing `secrets.json` on first
     run, then delete it after a successful migration; env vars still win). Without a
     keyring, keys stay in the owner-only `secrets.json` (see Builder's calls).
4. **Lifecycle.** Each source reports `connected / expired / signed_out / error`. Expired →
   "Sign in again" action, never a stack trace. "Sign out / forget" deletes the blob and
   the keyring entry. Redaction (`_scrub_reading`-style) applies to every source, not just
   Cursor.
5. **Review gate.** Every PR touching auth, sessions, network or the web server gets a
   security review (ECC `security-reviewer` agent) before merge.

## Architecture

- **`Suite`** (new, `suites.py`): id, name, member source ids, which source supplies the
  suite's headline limit. Pure data, like `catalog.py`.
- **Source adapter interface** (new, `sources/` package or a protocol in `adapters.py`):
  `id`, `kind` (`cli_login` | `web_session` | `api` | `local_logs`), `fetch(ctx) -> Reading`,
  `auth_status(ctx)`, `login_spec` (optional). Existing modules (`web_accounts`,
  `accounts`, `local_*`, `billing`, `grok_suite`, `cursor_usage`) are wrapped/moved onto it
  incrementally; behaviour unchanged, tests unchanged or ported.
- **`Reading`** gains a stable shape for limit windows: a list of
  `{label, used_percent, resets_at, window}` (5-hour, weekly, monthly, daily…), plus
  money (`spent`, `cap`, `balance`, currency) and tokens. Missing stays missing.
- **Overview / tray** read suites, not Grok: `primary_provider` becomes `tray_suite`
  (migrated: `grok` → `xai`, others mapped to their suite; `budget` kept) plus a
  "tightest limit" option.
- **Catalog** gains `suite` and `verified` (`live` | `fixtures` | `none`) fields;
  `docs/providers.md` is regenerated from it.

## Work order (one PR each, CI green, security review where noted)

1. Suite layer + adapter interface + Reading limit windows; de-Grok defaults, tray, menu,
   setup and README wording; config migration.
2. Auth hardening (security review): keyring + encrypted blobs, no plaintext, unified
   `LoginSpec`, fixed cookie filtering, status lifecycle, secrets migration.
3. Suites, one PR each: Anthropic → OpenAI → Google → xAI/Cursor → DeepSeek/Qwen/Kimi →
   others. Each: CLI-login source, web source, API source, local logs, fixtures, catalog
   entries with honest `verified`.
4. Customization: per-suite/source display choices, ordering, thresholds, budgets, all in
   the settings page with validation.
5. Easy setup: first-run flow showing every suite's connection state with one action each.
6. Live verification on the owner's accounts (Google, OpenAI, Anthropic, xAI, Cursor);
   fix what breaks; update `verified` and docs. DeepSeek/Qwen/Kimi stay `fixtures`.

## Left to the builder

- Exact endpoints, payload shapes and file formats for each new source (research, then
  record fixtures; mark undocumented ones).
- Module layout for the adapter interface and how far to move existing code.
- Which extra providers to add, provided a real source exists.
- Wording of statuses and messages (plain, consistent with existing tone).
- Release versioning (likely 0.6.0-beta series).
