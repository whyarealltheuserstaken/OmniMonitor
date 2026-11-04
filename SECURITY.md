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
| Network | `GET https://cli-chat-proxy.grok.com/v1/billing?format=credits` only (configurable to other `*.grok.com` or loopback hosts; anything else is rejected) |
| Local stats page | `127.0.0.1` only, random port, random per-run URL secret, `Host` header check, GET/HEAD plus one write (below), CSP `default-src 'self'`, `no-store` |
| Claude Code / Codex logs (`~/.claude/projects/**/*.jsonl`, `~/.codex/sessions/**/rollout-*.jsonl`) | **only if enabled** in `extra_token_sources`; read-only; only token counts, model names, timestamps, session ids and the project folder **name** are stored |
| Settings page write (`POST …/api/settings`) | same host/secret rules, plus `Origin` must equal the page's origin (403), per-run CSRF token in `X-OmniMonitor-CSRF` (403), `Content-Type: application/json` (415), body ≤ 64 KB (411/413), valid JSON (400), allow-listed and validated keys only (400). It can never set `source`, `token_source`, `token`, `command`, `grok_refresh_command`, `grok_billing_url`, `usage_page_url` or any path. Changes are merged into the config file only, so env-provided tokens are not persisted |
| Other tools' local logs (Gemini CLI, Qwen Code, Kimi, opencode, Copilot OTel file) | **only if enabled** in `providers`; read-only (SQLite opened with `mode=ro`); same minimal fields as above |
| Account APIs (Anthropic, OpenAI, xAI, DeepSeek, Moonshot, GitHub, Cursor, OpenRouter, Ollama) | **only if enabled and given a key**; HTTPS `GET`/`POST` to a fixed host allow-list, no redirects, ≥ 5 min apart; responses ≤ 4 MB; key-bearing error text is never shown |
| API keys | from environment variables or `secrets.json` (config dir, chmod 600; ignored if group/world-readable). Never in `config.json`, never accepted from or returned to the web page, never exported, never logged. `secrets list` shows only presence and source |
| Exports | written only where you choose (CLI `-o`) or downloaded by your browser; CSV cells starting with `= + - @` are escaped |
| Desktop notifications | via the tray library; contain only percentages and times |
| Config + history (`config.json`, `history.sqlite3`) | in the per-user config dir, chmod 600 where supported |

It never reads browser cookies, the macOS Keychain, Windows Credential Manager, other
apps' login sessions or tokens (other than the Grok CLI auth file above), and never calls
undocumented provider endpoints. There is no telemetry.

## Known limitations

- On Windows, chmod 600 is a no-op. The files rely on the per-user `%APPDATA%` ACLs.
- If you put a manual `token` in the config, it is stored in plain text (chmod 600).
  Prefer the CLI auth file.
- Any local process running as **your** user can already read your auth file. The URL
  secret protects the stats page from other users, other machines and web pages
  (DNS rebinding), not from malware running as you.
- `secrets.json` stores keys in plain text (chmod 600), like most CLI tools. Prefer
  environment variables from your OS secret manager if you need more.
- Admin / management keys (Anthropic, OpenAI, xAI, Cursor) are powerful. Create the
  narrowest read-only key the provider offers.
- PyInstaller release binaries are unsigned.
