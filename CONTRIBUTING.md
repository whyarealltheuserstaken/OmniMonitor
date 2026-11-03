# Contributing to OmniMonitor

Thanks for helping! Small, focused PRs against
<https://github.com/whyarealltheuserstaken/OmniMonitor> are easiest to review.

## Ground rules

1. **No fabricated numbers.** Every value on screen is either real data, an explicitly
   labeled estimate (`≈`, "estimate", with a caveat), or "not available" plus a reason.
2. **Privacy.** Don't add network calls other than the billing endpoint. Don't add
   telemetry or update checks. Don't read browser cookies, keychains or other apps'
   credential stores. Don't write the Grok CLI's `auth.json`, and don't use its
   refresh token.
3. **Never crash the tray.** Errors become a menu line, and the last good value stays
   visible, marked stale.
4. Keep runtime dependencies to `pystray` + `Pillow` (plus the standard library).
   Python 3.9 compatible.

## Dev setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check src tests
pytest -q
```

Useful helpers: `scripts/mock_billing_server.py`, `scripts/make_fake_sessions.py`,
`scripts/screenshot.sh`. To keep your real config untouched, point the app at a scratch
dir: `OMNIMONITOR_HOME=/tmp/omni omnimonitor --demo`.

## Fixtures

Response fixtures in `tests/fixtures/` must be **redacted**: no tokens, emails, team or
user ids. If you capture a new billing shape, keep only the fields the parser reads.

## Platforms

CI runs on Ubuntu, macOS and Windows, but cannot click a real tray. When you change
`tray.py`, `autostart.py` or `open_path`, please say which OS you tested by hand.
