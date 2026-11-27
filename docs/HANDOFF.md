# OmniMonitor suites rework: handoff (2026-11-27)

Read first: `docs/superpowers/specs/2026-11-25-suites-rework-design.md` (approved design) and the
plans in `docs/superpowers/plans/` (the last one is `2026-11-27-finish-0.6.md`). Owner's working
agreements are in Claude's memory (`omnimonitor-working-agreements`): self-merge (squash) after
green CI + clean review, no attribution lines, commit as the GitHub noreply identity, ask only
when blocked.

## Done (0.6.0-beta)
- Suite layer (#22), vault + sign-in hardening (#23), plan limits from CLI logins (#24).
- Real data without Grok (#26), customization (#27), easy setup (#28), then verification, docs
  and release, including the fixes from the pre-release security + correctness review.

## Checked on the owner's PC (2026-11-27)
`omnimonitor verify` passed for Claude Code logs, the Claude plan (Claude Code login) and the
ChatGPT plan (Codex login). The vault round trip worked with Windows Credential Manager, the
dashboard window loaded, and the three sign-in windows reached claude.ai, chatgpt.com and
Cursor's sign-in pages. The pages were driven in Edge with no console errors.

## Still needs the owner
1. **Finish a website sign-in** (Claude, ChatGPT, Cursor) from Setup, then `omnimonitor verify`.
   Their readers are still fixture-only.
2. Compare the Claude and ChatGPT percentages with the vendors' own usage pages.
3. Grok CLI, Gemini CLI and API keys, if/when available: turn on, run `omnimonitor verify`, read
   only the scrubbed output, then add passing ids to `catalog.LIVE_VERIFIED`.

## Deferred
- `Controller._apply_cfg` clears and refills the shared config dict without a lock; another
  thread could briefly see it empty (older than 0.6).
- The README screenshots predate suites. UI/visual redesign is the next project (spec).
- No DeepSeek / Qwen / Kimi web-plan reader or Qwen balance: no source could be confirmed.

## Environment notes (Windows dev box)
- venv: `py -m venv .venv`; `.venv/Scripts/python -m pytest -q`; `.venv/Scripts/ruff check src tests`.
- The checkout uses CRLF.
- `tests/conftest.py` gives every test a fake keyring and its own `OMNIMONITOR_HOME`. Tests
  must never touch the real Credential Manager or `%APPDATA%\omnimonitor`.
- Local web-server tests now and then fail with `WinError 10053` when the whole suite runs; they
  pass on rerun.
- CI's packaged-app window smoke on Windows failed once with `loaded: False` and passed on rerun.
- No Chrome on this PC. For UI checks use Playwright with `channel="msedge"`; the pages' CSP
  blocks `wait_for_function` with a string, so wait on locators.
