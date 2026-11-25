# Step 3a: Plan limits from official CLI logins (Anthropic, OpenAI, Google) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show Claude, ChatGPT/Codex and Gemini plan limits with no extra sign-in by reusing the login the user's official CLI already has, read-only.

**Architecture:** `cli_logins.py` reads each CLI's own credential file (never writes it, never refreshes with its refresh token). `cli_accounts.py` calls the same usage endpoints the CLIs / their web apps use and returns a `Reading` with a list of limit windows. A new catalog kind `cli_login` is dispatched like `web_session`, shown on suite cards, and covered by `verify`.

**Tech Stack:** Python 3.9+, stdlib HTTP via `accounts.default_http` (allow-listed hosts, no redirects).

**Spec:** `docs/superpowers/specs/2026-11-25-suites-rework-design.md` (Auth design 1: reuse official CLI logins)

## Sources (community-documented, undocumented by vendors — catalog status `experimental`)

| id | name | credential | request | windows |
|---|---|---|---|---|
| `claude_cli_plan` | Claude plan (Claude Code login) | `$CLAUDE_CONFIG_DIR` or `~/.claude/.credentials.json` → `claudeAiOauth.accessToken`, `expiresAt` (ms); macOS: Keychain item `Claude Code-credentials` when the file is absent | `GET https://api.anthropic.com/api/oauth/usage`, headers `Authorization: Bearer …`, `anthropic-beta: oauth-2025-04-20` | `five_hour` "5-hour", `seven_day` "Weekly", `seven_day_opus` "Weekly (Opus)", `seven_day_sonnet` "Weekly (Sonnet)": `utilization` (0–100), `resets_at` ISO; null blocks skipped |
| `codex_cli_plan` | ChatGPT plan (Codex login) | `$CODEX_HOME` or `~/.codex/auth.json` → `tokens.access_token`, `tokens.account_id` | `GET https://chatgpt.com/backend-api/wham/usage`, `Authorization: Bearer …`, `chatgpt-account-id: <id>` | reuse `web_accounts` window parsing (`rate_limit.primary_window` "5-hour"/`secondary_window` "Weekly") |
| `gemini_cli_quota` | Gemini CLI quota (Google login) | `$GEMINI_CLI_HOME` or `~/.gemini/oauth_creds.json` → `access_token`, `expiry_date` (ms) | `POST https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist` → `cloudaicompanionProject`; then `POST …/v1internal:retrieveUserQuota {"project": id}` | per `buckets[]`: `modelId`, used % = `100 * (1 - remainingFraction)`, `resetTime` |

## Global Constraints

- Credential files are opened read-only; tokens are never stored, logged, exported or placed in `Reading` fields. Errors never contain a token.
- Expired token (expiry < now) → `Reading.ok=False`, error `"<CLI> login expired - run `<cmd>` once to refresh it"` (`claude`, `codex`, `gemini`). No refresh-token use.
- Missing file → error `"not signed in to <CLI> on this computer"`.
- New catalog entries are off by default, `kind="cli_login"`, `status=EXPERIMENTAL`, `has_limit=True`, `verified` = `fixtures`.
- Hosts: add `cloudcode-pa.googleapis.com` to `accounts.ALLOWED_HOSTS` (api.anthropic.com and chatgpt.com already allowed — confirm).

## Review Focus

- `utilization` given as a fraction (≤ 1.0) by some accounts → still shown as %? Rule: values ≤ 1.0 with every window ≤ 1.0 are treated as fractions ×100. (Task 2 test)
- All windows null (enterprise plan) → ok=True, no limits, note "this plan reports no usage windows". (Task 2 test)
- 401 from the endpoint with an unexpired token → error "login rejected - run `claude` once". (Task 2 test)
- Gemini `loadCodeAssist` without a project → `retrieveUserQuota` called with `{}`. (Task 3 test)
- A token string never appears in `json.dumps(reading.to_dict())`. (every fetcher test)

---

### Task 1: `Reading.limits` + `cli_logins.py` + catalog entries

**Files:** Modify `accounts.py` (`Reading.limits: List[Dict[str, Any]] = field(default_factory=list)` items `{name, used_percent, resets_at, window}`; `ALLOWED_HOSTS` + cloudcode host), `catalog.py` (3 entries, `CLI_LOGIN_IDS`, included in `SWITCHABLE`), `overview.py` (a reading's `limits` list becomes the card's limits when non-empty); Create `cli_logins.py`, `tests/test_cli_logins.py`.

**Interfaces:** `cli_logins.CliLogin(token: str, expires_at: Optional[float], extra: Dict[str, str])`; `claude(cfg) -> CliLogin`, `codex(cfg) -> CliLogin` (extra `account_id`), `gemini(cfg) -> CliLogin`; raise `cli_logins.LoginMissing(msg)` / `LoginExpired(msg)`.

- [ ] Tests: `test_claude_reads_file_only`, `test_claude_expired`, `test_codex_reads_account_id`, `test_gemini_missing`, `test_card_uses_reading_limits`, `test_catalog_cli_login_entries` (suite_of → anthropic/openai/google, off by default).
- [ ] Fail → implement → pass → commit `feat: read official CLI logins (read-only) and multi-window readings`.

### Task 2: Claude and Codex plan fetchers

**Files:** Create `cli_accounts.py`; modify `accounts.py` refresh dispatch (`cli_login` kind → `cli_accounts.fetch`); fixtures `tests/fixtures/claude_oauth_usage.json`, `tests/fixtures/codex_wham_usage.json`; Test `tests/test_cli_logins.py`.

**Interfaces:** `cli_accounts.fetch(pid, cfg, http, now) -> Reading`.

- [ ] Tests: `test_claude_plan_windows` (fixture → limits 5-hour 12.0 / Weekly 34.0, limit_percent 34.0, resets parsed), `test_claude_fraction_scale`, `test_claude_all_null_windows`, `test_claude_401`, `test_codex_plan_windows`, `test_no_token_in_reading`.
- [ ] Fail → implement → commit `feat: Claude and ChatGPT plan limits from the Claude Code / Codex logins`.

### Task 3: Gemini quota fetcher + verify + docs

**Files:** `cli_accounts.py`, `verify.py` (`targets` includes `CLI_LOGIN_IDS`; scrub the token), `tests/fixtures/gemini_quota.json`, README provider list + `docs/providers.md` regenerated (`python scripts/gen_providers_md.py`).

- [ ] Tests: `test_gemini_quota_buckets` (two models; headline = lowest remaining), `test_gemini_no_project`, `test_verify_includes_cli_logins`.
- [ ] Fail → implement → commit `feat: Gemini CLI quota from the Gemini CLI login`; PR, CI, merge.
