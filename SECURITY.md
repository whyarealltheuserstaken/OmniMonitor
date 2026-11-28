# Security policy

## Reporting

Please report vulnerabilities **privately**, through GitHub's "Report a vulnerability"
(Security Advisories) on this repository:
<https://github.com/whyarealltheuserstaken/OmniMonitor/security/advisories/new>. Don't open a public issue. Expect a reply within
about a week. This is a volunteer project.

## What the app touches

| Thing | Access |
|---|---|
| Grok CLI auth file (`~/.grok/auth.json`, `$GROK_AUTH_PATH`) | **read-only**; access token only; never written; refresh token never used |
| Grok CLI session logs (`~/.grok/sessions/**/updates.jsonl`) | read-only; only token counts, timestamps, model names, ids and durations are stored |
| `grok models` | run without a shell when the token is stale or rejected, so the official CLI refreshes itself |
| Network | Grok: `grok agent stdio` billing, then `GET https://cli-chat-proxy.grok.com/v1/billing` and `POST https://grok.com/.../GetRemainingResets` (bearer from the CLI login; hosts limited to `*.grok.com` / `grok.com`). Cursor, Claude, and ChatGPT web sessions only when that provider is enabled, to the fixed dashboard hosts, no redirects |
| Local stats page | `127.0.0.1` only, random port, random per-run URL secret, `Host` header check, GET/HEAD plus one write (below), CSP `default-src 'self'`, `no-store` |
| Claude Code / Codex logs (`~/.claude/projects/**/*.jsonl`, `~/.codex/sessions/**/rollout-*.jsonl`) | **only if enabled** in `extra_token_sources`; read-only; only token counts, model names, timestamps, session ids and the project folder **name** are stored |
| Settings page write (`POST …/api/settings`) | same host/secret rules, plus `Origin` must equal the page's origin (403), per-run CSRF token in `X-OmniMonitor-CSRF` (403), `Content-Type: application/json` (415), body ≤ 64 KB (411/413), valid JSON (400), allow-listed and validated keys only (400). It can never set `source`, `token_source`, `token`, `command`, `grok_refresh_command`, `grok_billing_url`, `usage_page_url` or any path. The Setup action (`POST …/api/app/onboard`, same checks) takes only known source ids: it switches sources on or off, can set `source` to `none` or `grok_cli_billing` (and `token_source` to `auto` or `none`), and can open a source's sign-in window. It cannot set `command`, a URL, a path or a key. Changes are merged into the config file only, so env-provided tokens are not persisted |
| Other tools' local logs (Gemini CLI, Qwen Code, Kimi, opencode, Copilot OTel file) | **only if enabled** in `providers`; read-only (SQLite opened with `mode=ro`); same minimal fields as above |
| Account APIs (Anthropic, OpenAI, xAI, DeepSeek, Moonshot, GitHub, Cursor, OpenRouter, Ollama) | **only if enabled and given a key**; HTTPS `GET`/`POST` to a fixed host allow-list, no redirects, ≥ 5 min apart; responses ≤ 4 MB; key-bearing error text is never shown |
| API keys | from environment variables or the encrypted vault (without a system keychain: `secrets.json` in the config dir, chmod 600, ignored if group/world-readable). Never in `config.json`, never accepted from or returned to the web page, never exported, never logged. `secrets list` shows only presence and source |
| Exports | written only where you choose (CLI `-o`) or downloaded by your browser; CSV cells starting with `= + - @` are escaped |
| Desktop notifications | via the tray library; contain provider and limit names, percentages, times and, for budgets, dollar amounts |
| Alert webhook (off until you save one) | `OMNIMONITOR_ALERT_WEBHOOK`, stored like an API key (it can carry its own token) and never settable from the web page or logged; each alert's title and text are `POST`ed to it over HTTPS only, no redirects, 10 s limit. Discord mentions are disabled |
| Update check (on by default, once a day, not in demo mode; off in Settings) | `GET` of this repository's public release list and files, over HTTPS to GitHub hosts only (redirects included). No token or account data; GitHub sees your IP address |
| Update install (Windows download only, only when you choose it) | needs an Ed25519 signature from a key built into the app, covering the version, size and SHA-256; never installs an older or equal version; download addresses are built by the app; the web page cannot start an install. Replaces `omnimonitor.exe` in its own folder, without administrator rights |
| Config + history (`config.json`, `history.sqlite3`) | in the per-user config dir, chmod 600 where supported |

Browser cookies are read only for opt-in web-session providers (see docs/cookies/). The opt-in
"Use signed-in Cursor app" method reads `cursorAuth/accessToken` from Cursor's `state.vscdb`
and, on macOS when that value is `cursor-safe-storage`, asks once for the "Cursor Safe Storage"
keychain item. That token stays in memory for the request. It is not copied into OmniMonitor's
keyring, logs, exports, or Report a problem. Other apps' tokens are not read. Several of these
dashboard endpoints are unofficial; a failure is shown as "not available" and is not turned into
a number. There is no telemetry.

## Known limitations

- On Windows, chmod 600 is a no-op. The files rely on the per-user `%APPDATA%` ACLs.
- If you put a manual `token` in the config, it is stored in plain text (chmod 600).
  Prefer the CLI auth file.
- Any local process running as **your** user can already read your auth file. The URL
  secret protects the stats page from other users, other machines and web pages
  (DNS rebinding), not from malware running as you.
- Without a system keychain, API keys (and the alert webhook URL) fall back to
  `secrets.json` in plain text (chmod 600), like most CLI tools. Prefer environment
  variables from your OS secret manager if you need more.
- Admin / management keys (Anthropic, OpenAI, xAI, Cursor) are powerful. Create the
  narrowest read-only key the provider offers.
- PyInstaller release binaries have no code-signing certificate, so the system still warns.
- The update signature shows who released a build, not that its code is safe. It does not
  help on a computer where someone can already run programs as you, and someone who can block
  GitHub can keep you on an old version.
