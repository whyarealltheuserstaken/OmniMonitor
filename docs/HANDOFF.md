# OmniMonitor suites rework: handoff (2026-11-25)

Read first: `docs/superpowers/specs/2026-11-25-suites-rework-design.md` (approved design) and the
plans in `docs/superpowers/plans/`. Owner's working agreements are in Claude's memory
(`omnimonitor-working-agreements`): self-merge (squash) after green CI + clean review, no
attribution lines, commit as the GitHub noreply identity, ask only when blocked.

## Done
- **Step 1, suite layer**: PR #22, merged. `suites.py`, `overview.suite_view`, `tray_suite`
  (migrated from `primary_provider`), suite-first tray menu, `omnimonitor tray`.
- **Step 2, auth hardening**: PR #23, merged. `vault.py` (AES-GCM, key in OS keychain), sessions
  and API keys on the vault with migration, `login_specs.py`, sign-in in a child process
  (`omnimonitor login <pid>`), `redact.py` + `omnimonitor verify`, keyring/cryptography bundled.
- **Step 3a, plan limits from CLI logins**: PR #24 (`feat/cli-login-plans`), open until CI is
  green. `cli_logins.py` (read-only), `cli_accounts.py` (Claude, Codex, Gemini), catalog kind
  `cli_login`, `Reading.limits`. **Read live on the owner's PC on 2026-11-25**: Claude (5-hour
  and weekly) and Codex returned sensible numbers, so those two are in `catalog.LIVE_VERIFIED`.
  The owner has not yet compared them with claude.ai / ChatGPT usage pages. Gemini CLI is not
  installed on that PC (fixture-only). Security review: no Critical/High, all Low fixed.

## Next
1. `gh pr checks 24 --watch`, squash-merge if green (CI is Linux/macOS/Windows; POSIX-only
   permission tests have bitten twice, so check those first).
2. **Step 3b, remaining suites.** Grok/Cursor already has deep support (`grok_suite`,
   `cursor_usage`, `cursor_app_auth`) and the Grok CLI is not installed on the owner's PC, so
   it waits for the live check. DeepSeek / Qwen / Kimi: API balance exists for DeepSeek and
   Kimi (Moonshot); Qwen has no balance API; the owner has no accounts, so any web-plan reader
   would be guesswork. Do not invent endpoints; research and mark fixture-only.
3. **Step 4, customization.** Per-suite/source display choices (what shows in tray / menu /
   dashboard / mini view), ordering, thresholds, budgets, all validated in Settings and the
   CLI. De-Grok `status`, `statusline` and the mini view (still Grok-first).
4. **Step 5, easy setup.** First-run screen listing every suite as Connected / Sign in / Add
   key / Not available, one action each; detect installed CLIs and existing logins.
5. **Step 6, live verification** with the owner: sign in through the app's windows, run
   `omnimonitor verify`, read only the scrubbed output. Compare Claude / Codex numbers with the
   vendors' pages. Never read cookies or keys directly.

## Deferred minors (from reviews)
- The tray says "no limit readings yet" if `overview.build` raises.
- `config import` of a pre-0.6 export ignores `primary_provider` instead of migrating it.
- The Grok headline label is doubled ("Grok weekly pool weekly pool") in `primary.detail`.
- Empty Providers / Usage pages submenus may render on some pystray backends.
- `catalog.PRIMARY_CHOICES` is dead code.

## Environment notes (Windows dev box)
- venv: `py -m venv .venv`; `.venv/Scripts/python -m pytest -q`; `.venv/Scripts/ruff check src tests`.
- The checkout uses CRLF. Multi-line string replacements must normalize `\r\n` or use the
  Edit tool.
- `tests/conftest.py` gives every test a fake keyring and its own `OMNIMONITOR_HOME`. Tests
  must never touch the real Credential Manager or `%APPDATA%\omnimonitor`.
- `test_live_setup.py::test_web_use_real_route_needs_csrf` sometimes fails with
  WinError 10053. It's a pre-existing flake that passes on rerun.
