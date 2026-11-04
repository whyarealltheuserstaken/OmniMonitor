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

## Adding an adapter

1. Write `parse_<tool>_line(line, ctx, file_key) -> List[TokenEvent]` in `adapters.py`
   following the normalization above, plus a file iterator.
2. Subclass `JsonlScanner`, register it in `scanners()` and `LABELS`, and allow the key in
   `config.extra_token_sources`.
3. Add a **synthetic** fixture to `tests/fixtures/` (never real logs) and tests for dedupe and
   cache handling.
