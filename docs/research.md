# Research notes: where Grok usage data can (and can't) come from

Compiled Nov 2026. Everything here is **unofficial** unless marked otherwise.

## 1. Weekly pool % and reset time: available (undocumented)

`GET https://cli-chat-proxy.grok.com/v1/billing?format=credits`
Headers: `Authorization: Bearer <Grok CLI access token>`, `X-XAI-Token-Auth: xai-grok-cli`,
`Accept: application/json`. This is the request the Grok CLI's `/usage` panel makes.

Relevant fields under `config`:

| Field | Meaning |
|---|---|
| `creditUsagePercent` | **used** share of the current period (omitted when 0; can exceed 100 with on-demand use) |
| `currentPeriod.{type,start,end}` | e.g. `USAGE_PERIOD_TYPE_WEEKLY`; `end` = reset time |
| `productUsage[].{product,usagePercent}` | e.g. `GrokBuild`, `GrokChat` |
| `onDemandCap`, `onDemandUsed` `{val}` | on-demand spending, when enabled |
| `billingPeriodStart/End` | subscription period (fallback for the reset time) |

The response contains **no token counts and no token limit**. Team/enterprise seats get
HTTP 412 `"No personal team."`

Sources:
- claudia: <https://github.com/marcelocantos/claudia/blob/master/docs/grok-usage-billing.md>
- openusage Grok provider doc (later removed from `main`; see history, e.g. commit
  `37f9a21` and `GrokLogUsageScanner.swift`):
  <https://github.com/robinebers/openusage/blob/main/docs/providers/grok.md>, the 412 team
  issue <https://github.com/robinebers/openusage/issues/1162>, and PR
  <https://github.com/robinebers/openusage/pull/484>

## 2. Grok CLI auth

`~/.grok/auth.json` (or `$GROK_HOME`, `$GROK_AUTH_PATH`). The app prefers OIDC entries for
`x.ai` and ignores `web_login` entries. Access tokens are short-lived (hours). This app
**never** uses the refresh token. When the access token is stale or rejected, it runs
`grok models` once so the official CLI refreshes its own session.

## 3. Real token counts: local CLI logs only

`~/.grok/sessions/<url-encoded cwd>/<session id>/updates.jsonl`. Each line is a JSON-RPC
`session/update`. `turn_completed` updates carry
`usage.modelUsage{<model>: {inputTokens, cachedReadTokens, cacheCreationTokens,
outputTokens, reasoningTokens, costUsdTicks}}`, where `inputTokens` is the full prompt
including cache reads, and `costUsdTicks / 1e10` = USD. Timestamps are in
`params._meta.agentTimestampMs`, ids in `_meta.eventId`.

Sources:
- Grok Build user guide, sessions:
  <https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/17-sessions.md>
- Headless mode:
  <https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/14-headless-mode.md>

These logs cover **only the Grok CLI on that computer**: not grok.com, the apps, or other
machines.

## 4. Official APIs: not applicable to the consumer weekly pool

- xAI Management API billing (API teams; USD spend, invoices, prepaid credits). It does not
  cover the consumer/subscription weekly pool:
  <https://docs.x.ai/developers/rest-api-reference/management/billing>
- Consumer FAQ (usage limits are described qualitatively, not as token numbers):
  <https://docs.x.ai/grok/faq>

## Conclusions used in the design

1. The weekly % and reset time are real but come from an unofficial API. Show them, with a
   disclaimer.
2. The weekly **token** limit ("Y") is not exposed anywhere. Show "not available". An
   optional `≈` estimate is allowed only when clearly labeled and above a minimum usage.
3. Token totals, streaks, peak day, longest task and the heatmap can be computed honestly
   from local CLI logs, labeled "this computer's Grok CLI".
4. Credits ≠ tokens: the pool is weighted by model, caching and product.
5. Top plugins: no data source. Hidden.
