# Token adapters

OmniMonitor counts tokens from **local log files written by AI coding tools on this
computer**. It reads them read-only and incrementally (it remembers the byte offset per file)
and stores only token counts, timestamps, model names, session ids and the **project folder
name** (the basename of the working directory, never the full path) in `history.sqlite3`.

None of these formats are documented by their vendors. They can change at any time. Lines the
parsers don't recognize are skipped. They are never guessed at.

## Normalization

Every adapter produces events with the same meaning, so totals are comparable:

| Field | Meaning |
|---|---|
| `input_tokens` | full prompt size, **including** cache reads and cache writes |
| `cache_read_tokens` | the part of the input that was served from cache |
| `cache_creation_tokens` | the part of the input written to cache (Claude only) |
| `output_tokens` | full output, **including** reasoning |
| `reasoning_tokens` | the part of the output that was reasoning, where reported |
| total | `input_tokens + output_tokens` |

**Only `grok_sessions` events count toward the Grok weekly pool and the "≈ weekly limit"
estimate.** Claude Code and Codex tokens are shown separately by tool. They never change any
Grok number.

The dashboard's **Grok suite** card uses the same rule for its token detail. Grok Bot and
Grok-in-Cursor have no rows in these logs. `grok_unified` is the Grok CLI as well
(`shell.turn.inference_done`); when session events exist, the suite total does not add the
unified log on top. Pool percents for CLI / Bot / Cursor come only from billing
`productUsage` — see [research.md](research.md). A missing product is "not available", not 0%.

## `grok_sessions` (Grok CLI, always on)

* Files: `<grok_home>/sessions/**/updates.jsonl` (`grok_home` = config, `$GROK_HOME` or
  `~/.grok`).
* Uses `turn_completed` events and their usage block. Unchanged from 0.1.0.

## `claude_code` (Claude Code, opt-in)

* Enable: `"extra_token_sources": ["claude_code"]`, or tick it on the settings page.
* Files: `<home>/projects/**/*.jsonl`. `home` = `claude_home` in the config (comma-separated
  list allowed), else `$CLAUDE_CONFIG_DIR`, else `~/.claude` and `~/.config/claude`.
* Lines with `type: "assistant"` carry `message.usage`:
  `input_tokens` (**excludes** cache), `cache_read_input_tokens`,
  `cache_creation_input_tokens`, `output_tokens`. OmniMonitor stores
  `input = input_tokens + cache_read + cache_creation`.
* One API response is often written as several lines (one per content block) with the same
  usage. Events are deduplicated on `message.id + requestId`, so the **first** line wins.
* `model: "<synthetic>"` lines (local placeholders, not API calls) are skipped.
* Project: basename of the line's `cwd`.

## `codex` (OpenAI Codex CLI, opt-in)

* Enable: `"extra_token_sources": ["codex"]`.
* Files: `<home>/sessions/**/rollout-*.jsonl` and `<home>/archived_sessions/...` (the active
  copy wins if both exist). `home` = `codex_home`, else `$CODEX_HOME`, else `~/.codex`.
* `session_meta` gives the session id and cwd. `turn_context` gives the active model and cwd.
* `event_msg` lines with `payload.type == "token_count"` carry `info.total_token_usage`
  (cumulative) and `info.last_token_usage` (this step):
  * `info: null` (written before the first response) is skipped.
  * A repeated event with the same cumulative total is skipped.
  * `last_token_usage` is used when present, otherwise the difference from the previous
    total.
  * `cached_input_tokens` is already **inside** `input_tokens`.
  * `token_usage_record` lines (same numbers, newer versions) are ignored to avoid double
    counting.
* If no `turn_context` named a model, the model is recorded as `unknown`.

## 0.4 additions

### `claude_code`: transcripts and ≈ 5-hour blocks
Also reads `~/.claude/transcripts/**/*.jsonl` (same row format as `projects/`). Streamed
messages are written with partial usage first, so the **last** record per
`message.id + requestId` wins. `omnimonitor blocks` groups events into ≈ 5-hour blocks
(start = the UTC hour of the first event after the previous block ended) and reports a
burn rate and projection. This is an **estimate**: Anthropic does not publish the exact
window rule or expose it.

### `codex`: archived sessions
`~/.codex/archived_sessions/` is read alongside `sessions/` (same rollout format).

### `grok_unified` (experimental)
`~/.grok/logs/unified.jsonl`, lines `{ts, sid, msg, ctx}`. Only
`msg == "shell.turn.inference_done"` is counted: `ctx.prompt_tokens` (gross, includes
cached) → input, `cached_prompt_tokens` → cache read, `completion_tokens +
reasoning_tokens` → output. The model comes from the latest `ctx.model` in the same
session. Not checked against a real install; overlaps `grok_sessions` (enable only one).

### `antigravity` (experimental)
Read-only SQLite `conversations/*.db`, table `gen_metadata(idx, data)`, protobuf blobs
decoded with a minimal wire-format reader: `1.4` usage {2 uncached input, 3 output,
4 cache write, 5 cache read, 9 thinking, 11 response id (dedupe)}, `1.19` model,
`1.9.4.1` timestamp seconds (file mtime otherwise). Field numbers are undocumented.

### `dsh` (DeepSeek Harness, experimental)
`$DSH_HOME` or `~/.dsh/sessions/*/session-*/session[.vN].jsonl[.zstd]`. `.zstd` logs are one
frame per flush and are decoded frame by frame (a torn final frame keeps the rest).
`assistant/message` → `data.usage` {inputTokens uncached, cacheReadTokens,
cacheWriteTokens, outputTokens, reasoningTokens ⊂ output}. `.credentials.yaml` is never read.

### `kimi`: Kimi Work relocated data
On macOS/Windows, `kimi-desktop/daimon-storage.json` → `shareDir` (absolute paths only) is
honoured before the default `kimi-desktop/daimon-share`.

### `pi` and `openclaw`
Pi session JSONL (`~/.pi/agent/sessions/**`, `$PI_CODING_AGENT_DIR`,
`$PI_CODING_AGENT_SESSION_DIR`, `~/.omp`): assistant `message.usage` {input (uncached),
output (incl. reasoning), cacheRead, cacheWrite, cost.total}. Forks copy parent entries, so
entry `id` is the dedupe key. OpenClaw (`~/.openclaw/agents/**`, legacy `~/.clawdbot`,
`~/.moltbot`) uses the same transcripts (experimental); delivery-mirror rows are skipped and
OpenAI-style `prompt_tokens` usage is accepted.

### `goose`
Read-only `sessions/sessions.db`, table `sessions` (from block/goose `session_manager.rs`).
One event per session (upserted as totals grow), dated at `created_at`; `accumulated_*`
columns are preferred over the per-context ones. Input is gross (cache not added again).

### `hermes`
Read-only `$HERMES_HOME/state.db` plus `profiles/*/state.db`, per the Hermes developer
guide. Per-session totals: input = input + cache read + cache write; output = output +
reasoning; cost = actual, else estimated.

### `amp`, `droid` (experimental)
Amp `threads/*.json` `usageLedger.events[]` (cache from the target message's usage; credits
are not converted to dollars). Factory Droid `sessions/**/<id>.settings.json` `tokenUsage`
session totals; cwd from the sibling `.jsonl` `session_start`.

### `cline`, `roo_code`, `kilo_code` (experimental)
`<VS Code|Cursor>/User/globalStorage/<extension>/tasks/*/ui_messages.json`,
`say == "api_req_started"` rows whose `text` JSON holds tokensIn/Out, cacheReads/Writes,
cost. Cline's tokensIn excludes cache; Roo/Kilo may include it (detected per row). Model
from the `<model>` tag in `api_conversation_history.json`, otherwise "unknown".

### Not supported (researched)
Zed, Kiro, MiniMax Agent, Devin CLI, LM Studio and the local Ollama server: no documented
local usage log or usage API could be verified; see providers.md for each reason.

## Adding an adapter

1. Write `parse_<tool>_line(line, ctx, file_key) -> List[TokenEvent]` in `adapters.py`
   following the normalization above, plus a file iterator.
2. Subclass `JsonlScanner`, register it in `scanners()` and `LABELS`, and allow the key in
   `config.extra_token_sources`.
3. Add a **synthetic** fixture to `tests/fixtures/` (never real logs) and tests for dedupe and
   cache handling.
