# OmniMonitor suites rework: handoff (2026-11-25)

Read first: `docs/superpowers/specs/2026-11-25-suites-rework-design.md` (approved design) and the
plans in `docs/superpowers/plans/`. Owner's working agreements are in Claude's memory
(`omnimonitor-working-agreements`): self-merge (squash) after green CI + clean review, no
attribution lines, commit as the GitHub noreply identity, ask only when blocked.

## Done
- **Step 1, suite layer**: PR #22, merged. Suites (`suites.py`), `overview.suite_view`,
  `tray_suite` (migrated from `primary_provider`), suite-first tray menu, `omnimonitor tray`.
- **Step 2, auth hardening**: PR #23 (`feat/auth-hardening`), **open, waiting for CI**.
  `vault.py` (AES-GCM, key in OS keychain), sessions + API keys on the vault with migration,
  `login_specs.py`, sign-in in a child process (`omnimonitor login <pid>`), `redact.py` +
  `omnimonitor verify`, keyring/cryptography bundled in release builds. The security review
  findings are all fixed (see PR body). 384 tests pass locally.

## Next
1. `gh pr checks 23 --watch`, and squash-merge if green. If Windows/macOS CI fails, check the
   vault lock file and the `keyring` import on those runners.
2. **Step 3a**: `docs/superpowers/plans/2026-11-25-step3a-cli-login-plans.md`. Plan limits
   from the Claude Code / Codex / Gemini CLI logins. Branch from main after #23 merges.
   This PC has `~/.claude/.credentials.json` and `~/.codex/auth.json`. Gemini CLI and Grok
   CLI are not installed here.
3. Step 3b+: remaining suites (xAI/Cursor polish, DeepSeek/Qwen/Kimi, others), then step 4
   customization (also de-Grok `status` / `statusline` / mini view), step 5 easy setup,
   step 6 live verification with the owner (`omnimonitor verify`).

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
