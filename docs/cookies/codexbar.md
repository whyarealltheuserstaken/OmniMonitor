# CodexBar citations (cookie / web-session providers)

OmniMonitor’s opt-in web-session readers follow the **public documentation** of
[CodexBar](https://github.com/steipete/CodexBar) (MIT License, © Peter Steinberger).
We copy **no code**; only endpoint URLs, cookie *names*, and response field hints.

| OmniMonitor id | CodexBar doc | What we use |
|---|---|---|
| `claude_plan` | [docs/claude.md](https://github.com/steipete/CodexBar/blob/main/docs/claude.md) — “Web API (cookies)” | Cookie `sessionKey` on `claude.ai`; `GET /api/organizations`, `GET /api/organizations/{id}/usage` |
| `chatgpt_web` | [docs/codex.md](https://github.com/steipete/CodexBar/blob/main/docs/codex.md) — OpenAI web / wham usage | Domains `chatgpt.com`, `openai.com`; `GET https://chatgpt.com/backend-api/wham/usage` (CodexBar often sends a Bearer from the web session; OmniMonitor’s in-app login tries to capture that too) |
| `cursor` | [docs/cursor.md](https://github.com/steipete/CodexBar/blob/main/docs/cursor.md) and `CursorAppAuth.swift` / `CursorUsageEventsFetcher.swift` / `CursorUnifiedUsageStatus.swift` | Cookie `WorkosCursorSessionToken` (or the opt-in Cursor app `state.vscdb` key `cursorAuth/accessToken`). `GET /api/dashboard/v2/usage-status?scope=user` (dollars; preferred), else `GET /api/usage-summary`. `POST /api/dashboard/get-filtered-usage-events` with `Origin: https://cursor.com` |

Keychain / FDA behaviour for browser import is described in CodexBar’s
[docs/keychain-prompts.md](https://github.com/steipete/CodexBar/blob/main/docs/keychain-prompts.md)
and mirrored in [browser-import.md](browser-import.md).

**Confidence:** every OmniMonitor web-session provider is marked **experimental** until
verified against a real signed-in HTTP response in this project.
