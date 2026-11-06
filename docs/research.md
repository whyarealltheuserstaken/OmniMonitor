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

## 5. Grok CLI vs Grok Bot vs Cursor — what is actually available

The weekly number is **one pool**. `creditUsagePercent` is the suite total (used).
`productUsage[].usagePercent` is a share of that same pool. Proto-JSON omits a zero
`usagePercent` on a product it still lists; a product that is **not listed** is not 0%.

| Wanted row | Pool % | Token / activity detail |
|---|---|---|
| Grok suite total | `creditUsagePercent`, reset from `currentPeriod.end` or `billingPeriodEnd` | not a token figure |
| Grok CLI | `productUsage` product `GrokBuild` (Grok Build / the `grok` CLI). A literal `Grok CLI` name is accepted. | `~/.grok/sessions/**/updates.jsonl` (`turn_completed`). Experimental `~/.grok/logs/unified.jsonl` lines with `msg == shell.turn.inference_done` are the **same CLI** (shell telemetry, not Grok Bot). If both logs have events, session logs win so turns are not counted twice. |
| Grok Bot | Only if `productUsage` contains a product named Grok Bot. Documented captures of this payload list `GrokBuild` / `GrokChat` / `GrokTasks` and **do not** include Grok Bot. `GrokChat` is grok.com chat, not Grok Bot. | **Not available.** `~/.grokbot` has daemon settings and logs, not token counts. Transcript replicas under Application Support are not a usage API and are not read. |
| Cursor (Grok-in-Cursor) | Only if `productUsage` names Cursor (or Grok-in-Cursor). | **Not available** from Grok CLI logs. |

Not used for this breakdown, on purpose:

* `POST https://cursor.com/api/dashboard/get-sand-usage-status` — Grok Bot's own weekly
  meter, Cursor session cookie. Separate from the shared Grok pool. Not called.
* `GET https://cursor.com/api/usage-summary` — Cursor plan (the opt-in `cursor` provider).
  A different pool. Not mixed into the Grok suite total.
* xAI Management API — API-team spend, not the consumer weekly pool.

If the listed product shares do not sum to `creditUsagePercent`, the difference is shown
as unexplained. It is not assigned to a product.

## 6. Token counts vs token allowance vs dollar cap (2026-11-06)

| Figure | Source | What it is |
|---|---|---|
| Grok CLI token counts | `~/.grok/sessions/**/updates.jsonl`, or `~/.grok/logs/unified.jsonl` only when sessions have no events | Real tokens for this computer's CLI |
| Grok CLI / grok.com / app token allowance ("out of Y tokens") | Not in either billing payload. Not in the local logs. `grok.com` `GetGrokCreditsConfig` needs a browser session this app does not send | **not available** |
| Weekly pool % | `GET https://cli-chat-proxy.grok.com/v1/billing?format=credits` `creditUsagePercent` | A percent, not a token cap |
| Monthly credit cap | `GET https://cli-chat-proxy.grok.com/v1/billing` `used` / `monthlyLimit` as USD cents, same CLI login | Dollars. Shown only when the fields are present |
| Grok Bot percent | `POST https://cursor.com/api/dashboard/get-sand-usage-status` `usagePercent`, opt-in Cursor session, and only when `hasNonZeroIncludedLimit` is true | A percent. No token count, no token cap |
| Grok Bot tokens | `~/.grokbot` has no usage figures. Sand status has none | **not available** |
| Cursor plan | `GET https://cursor.com/api/usage-summary` (opt-in) | Cursor's own plan used/limit, not Grok tokens |
| Grok-in-Cursor tokens | Not in `~/.grok` logs. usage-summary has no documented Grok token field | **not available** |
| xAI API team | Management API already in the app (`xai_api`): USD spend and a postpaid limit | Not the consumer weekly pool and not tokens |

## Conclusions used in the design

1. The weekly % and reset time are real but come from an unofficial API. Show them, with a
   disclaimer.
2. The weekly **token** limit ("Y") is not exposed anywhere. Show "not available". An
   optional `≈` estimate is allowed only when clearly labeled and above a minimum usage.
3. Token totals, streaks, peak day, longest task and the heatmap can be computed honestly
   from local CLI logs, labeled "this computer's Grok CLI".
4. Credits ≠ tokens: the pool is weighted by model, caching and product.
5. Top plugins: no data source. Hidden.
