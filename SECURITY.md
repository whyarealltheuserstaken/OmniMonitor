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
| Local stats page | `127.0.0.1` only, random port, random per-run URL secret, `Host` header check, GET/HEAD only, CSP `default-src 'self'`, `no-store` |
| Config + history (`config.json`, `history.sqlite3`) | in the per-user config dir, chmod 600 where supported |

It never reads browser cookies, the macOS Keychain, Windows Credential Manager or other
apps' secrets. There is no telemetry.

## Known limitations

- On Windows, chmod 600 is a no-op. The files rely on the per-user `%APPDATA%` ACLs.
- If you put a manual `token` in the config, it is stored in plain text (chmod 600).
  Prefer the CLI auth file.
- Any local process running as **your** user can already read your auth file. The URL
  secret protects the stats page from other users, other machines and web pages
  (DNS rebinding), not from malware running as you.
- PyInstaller release binaries are unsigned.
