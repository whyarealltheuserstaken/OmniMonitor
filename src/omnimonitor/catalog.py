"""Provider catalog: the single source of truth for what OmniMonitor can read.

Every provider is opt-in. ``kind`` says where numbers come from:

* ``billing``    - the Grok weekly pool (existing ``source`` setting)
* ``local_logs`` - read-only parsing of files the tool itself writes on this computer
* ``api``        - the provider's official usage/billing API with a key *you* supply
                   (environment variable or the chmod-600 secrets file, never the web UI)
* ``web_session``- opt-in session cookie to the provider's own usage endpoints
                   (in-app sign-in window, or import from a chosen browser; off by default)
* ``none``       - not supported; ``reason`` says why and ``needs`` what would be required

Cookie / web-session providers (``kind="web_session"``) are opt-in and off by default.
They use either an in-app sign-in window or a user-approved browser cookie import for
that provider's domains only — see docs/cookies/.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

SUPPORTED, PARTIAL, NOT_SUPPORTED = "supported", "partial", "not_supported"
#: Works against the format as we understand it, but the format is undocumented by the
#: vendor (or only partly verified); numbers may be wrong after an update. Off by default.
EXPERIMENTAL = "experimental"


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    name: str
    family: str
    kind: str
    status: str
    shows: str = ""
    source: str = ""
    auth: str = "none"
    reset: str = ""
    confidence: str = ""
    docs: Tuple[str, ...] = ()
    env: Tuple[str, ...] = ()
    reason: str = ""
    needs: str = ""
    has_limit: bool = False  # can supply a "% used" for the tray
    extra: Dict[str, str] = field(default_factory=dict)


_P = ProviderInfo

CATALOG: Tuple[ProviderInfo, ...] = (
    # ---------------------------------------------------------------- Grok
    _P("grok", "Grok weekly pool", "Grok", "billing", PARTIAL,
       shows="limit % used/left, reset time, ≈ token limit estimate",
       source="Grok CLI billing endpoint using the Grok CLI's own login file (see README)",
       auth="Grok CLI login (~/.grok/auth.json)", reset="weekly (from the API's period dates)",
       confidence="high for %, ≈ for the token-limit estimate",
       docs=("docs/research.md",), has_limit=True),
    _P("grok_sessions", "Grok CLI sessions", "Grok", "local_logs", SUPPORTED,
       shows="tokens, recorded cost, models, projects",
       source="~/.grok/sessions/**/updates.jsonl",
       reset="n/a (history)", confidence="high (tested on real files)",
       docs=("docs/token-adapters.md",)),
    _P("grok_unified", "Grok CLI unified log", "Grok", "local_logs", EXPERIMENTAL,
       shows="tokens per inference (prompt incl. cached, completion, reasoning), models",
       source="~/.grok/logs/unified.jsonl, msg=shell.turn.inference_done ($GROK_HOME honoured)",
       reset="n/a (history)",
       confidence="low: undocumented log; checked against published descriptions, not a real "
                  "install. Builds before ~June 2026 log no tokens",
       docs=("docs/token-adapters.md",),
       extra={"note": "Overlaps Grok CLI sessions: enabling both counts the same Grok turns "
                      "twice in totals. Use it only if updates.jsonl has no usage for you."}),
    # ------------------------------------------------------- Anthropic
    _P("claude_code", "Claude Code", "Claude", "local_logs", SUPPORTED,
       shows="tokens (incl. cache), models, projects; ≈ cost from your price table; ≈ 5-hour "
             "block + burn rate (estimate, `omnimonitor blocks`)",
       source="~/.claude/projects/**/*.jsonl + ~/.claude/transcripts/**/*.jsonl "
              "($CLAUDE_CONFIG_DIR honoured)",
       reset="n/a (history)", confidence="high (fixtures; widely documented format)",
       docs=("docs/token-adapters.md",)),
    _P("anthropic_api", "Claude API (organization cost)", "Claude", "api", SUPPORTED,
       shows="month-to-date USD cost (UTC month)",
       source="GET https://api.anthropic.com/v1/organizations/cost_report",
       auth="Admin API key (sk-ant-admin…) in ANTHROPIC_ADMIN_KEY", reset="calendar month (UTC)",
       confidence="medium (docs + mocked tests, no live key tested)",
       docs=("https://docs.anthropic.com/en/api/admin-api/usage-cost/get-cost-report",),
       env=("ANTHROPIC_ADMIN_KEY",)),
    _P("claude_plan", "Claude Pro/Max plan limits", "Claude", "web_session", EXPERIMENTAL,
       shows="5-hour session and weekly (and model-scoped) % used, reset times when returned",
       source="GET https://claude.ai/api/organizations/{id}/usage with a sessionKey cookie",
       auth="opt-in: in-app sign-in to claude.ai, or import sessionKey from a chosen browser",
       reset="per window (from the API)",
       confidence="experimental (endpoints/cookie name from CodexBar docs; not verified on a "
                  "live OmniMonitor account yet)",
       docs=("docs/cookies/codexbar.md",
             "https://github.com/steipete/CodexBar/blob/main/docs/claude.md"),
       has_limit=True),
    # ---------------------------------------------------------- OpenAI
    _P("codex", "Codex CLI (+ ChatGPT plan limits)", "ChatGPT", "local_logs", SUPPORTED,
       shows="tokens, models, projects; plan limit % (5-hour and weekly windows) when Codex logs them",
       source="~/.codex/sessions/**/rollout-*.jsonl + ~/.codex/archived_sessions/ "
              "($CODEX_HOME honoured); "
              "rate_limits from token_count events",
       reset="per window (resets_at from the log)",
       confidence="high for tokens (fixtures); medium for plan limits (verified against Codex "
                  "source, not against real files)",
       docs=("docs/token-adapters.md",
             "https://github.com/openai/codex/blob/main/codex-rs/protocol/src/protocol.rs"),
       has_limit=True),
    _P("openai_api", "OpenAI API (organization cost)", "ChatGPT", "api", SUPPORTED,
       shows="month-to-date USD cost (UTC month)",
       source="GET https://api.openai.com/v1/organization/costs",
       auth="Admin key in OPENAI_ADMIN_KEY", reset="calendar month (UTC)",
       confidence="medium (docs + mocked tests)",
       docs=("https://platform.openai.com/docs/api-reference/usage/costs",),
       env=("OPENAI_ADMIN_KEY",)),
    _P("chatgpt_app", "ChatGPT app message limits", "ChatGPT", "none", NOT_SUPPORTED,
       reason="No official API for the consumer ChatGPT app message counters. Prefer "
              "chatgpt_web (session) or the Codex CLI adapter for plan windows.",
       needs="An official usage endpoint from OpenAI."),
    _P("chatgpt_web", "ChatGPT / Codex web plan limits", "ChatGPT", "web_session", EXPERIMENTAL,
       shows="session and weekly plan windows when chatgpt.com returns them",
       source="GET https://chatgpt.com/backend-api/wham/usage with session cookies "
              "(and optional access token from in-app login)",
       auth="opt-in: in-app sign-in to chatgpt.com, or import cookies from a chosen browser",
       reset="per window (from the API)",
       confidence="experimental (endpoint from CodexBar docs; cookie-only auth often needs a "
                  "Bearer from the web session — use in-app sign-in if browser import 401s)",
       docs=("docs/cookies/codexbar.md",
             "https://github.com/steipete/CodexBar/blob/main/docs/codex.md"),
       has_limit=True),
    # ---------------------------------------------------------- Google
    _P("gemini_cli", "Gemini CLI", "Gemini", "local_logs", SUPPORTED,
       shows="tokens (input incl. tool, output incl. thoughts, cached), models, projects",
       source="~/.gemini/tmp/**/chats/*.json|*.jsonl ($GEMINI_CLI_HOME honoured)",
       reset="n/a (history)", confidence="medium (format from the open-source CLI; fixtures only)",
       docs=("https://github.com/google-gemini/gemini-cli",)),
    _P("gemini_api", "Gemini API (AI Studio)", "Gemini", "none", NOT_SUPPORTED,
       reason="The Gemini API has no balance/usage endpoint; spend is only in Google Cloud "
              "Billing (export to BigQuery).",
       needs="Cloud Billing BigQuery export + a service account; out of scope for a tray app.",
       docs=("https://ai.google.dev/gemini-api/docs/billing",)),
    _P("antigravity", "Antigravity (CLI / IDE)", "Gemini", "local_logs", EXPERIMENTAL,
       shows="tokens (input incl. cache, output incl. thinking), models",
       source="~/.gemini/antigravity-cli|antigravity|antigravity-ide/conversations/*.db, "
              "~/.config/antigravity/conversations/*.db ($GEMINI_CLI_HOME honoured); "
              "read-only SQLite, protobuf gen_metadata rows",
       reset="n/a (history)",
       confidence="low: protobuf field numbers are undocumented and were inferred by others "
                  "from real files; synthetic fixtures only",
       docs=("docs/token-adapters.md",),
       extra={"note": "The Gemini app has no usage API; Antigravity plan quotas are not shown."}),
    _P("xai_api", "xAI API (prepaid credits)", "Grok", "api", PARTIAL,
       shows="prepaid credit balance, month-to-date spend, postpaid spending limit % used",
       source="GET .../billing/teams/{team_id}/prepaid/balance; POST .../usage; "
              "GET .../postpaid/invoice/preview (management-api.x.ai)",
       auth="Management key in XAI_MANAGEMENT_KEY + team id (config xai_team_id or XAI_TEAM_ID)",
       reset="n/a (balance)",
       confidence="low-medium: docs show purchases as negative changes, so balance = -total; "
                  "not verified with a live key",
       docs=("https://docs.x.ai/docs/management-api",), env=("XAI_MANAGEMENT_KEY", "XAI_TEAM_ID")),
    _P("venice", "Venice API", "Venice", "api", EXPERIMENTAL,
       shows="USD / DIEM / credit balances; DIEM % used of the epoch allocation",
       source="GET https://api.venice.ai/api/v1/billing/balance",
       auth="ADMIN API key in VENICE_ADMIN_KEY (inference keys are rejected)",
       reset="DIEM epoch (time not returned)",
       confidence="low-medium: Billing API is marked beta; docs + mocked tests only",
       docs=("https://docs.venice.ai/api-reference/endpoint/billing/balance",),
       env=("VENICE_ADMIN_KEY",), has_limit=True),
    _P("deepinfra", "DeepInfra", "DeepInfra", "api", PARTIAL,
       shows="prepaid balance, usage since last invoice, spending limit % used",
       source="GET https://api.deepinfra.com/payment/checklist",
       auth="API key in DEEPINFRA_API_KEY", reset="per invoice",
       confidence="medium (docs + mocked tests)",
       docs=("https://api.deepinfra.com/docs",),
       env=("DEEPINFRA_API_KEY",), has_limit=True),
    _P("fireworks", "Fireworks AI", "Fireworks", "api", PARTIAL,
       shows="month-to-date spend (no public balance endpoint)",
       source="GET https://api.fireworks.ai/v1/accounts/{account_id}/billing/summary",
       auth="API key in FIREWORKS_API_KEY + account id (config fireworks_account_id or "
            "FIREWORKS_ACCOUNT_ID)",
       reset="calendar month (UTC)", confidence="medium (docs + mocked tests)",
       docs=("https://docs.fireworks.ai/api-reference/get-billing-summary",),
       env=("FIREWORKS_API_KEY", "FIREWORKS_ACCOUNT_ID")),
    _P("vercel_ai_gateway", "Vercel AI Gateway", "Vercel", "api", SUPPORTED,
       shows="credit balance and lifetime credits used (USD)",
       source="GET https://ai-gateway.vercel.sh/v1/credits",
       auth="API key in AI_GATEWAY_API_KEY", reset="n/a (balance)",
       confidence="medium-high (docs + mocked tests)",
       docs=("https://vercel.com/docs/ai-gateway/capabilities/usage",),
       env=("AI_GATEWAY_API_KEY",)),
    _P("litellm", "LiteLLM proxy (self-hosted)", "LiteLLM", "api", EXPERIMENTAL,
       shows="the calling virtual key's spend, budget % used and budget reset",
       source="GET {LITELLM_BASE_URL}/key/info",
       auth="virtual key in LITELLM_API_KEY; proxy URL in LITELLM_BASE_URL (env or "
            "secrets.json only; https, or http on localhost)",
       reset="key budget_duration, when set",
       confidence="low-medium: response fields vary across LiteLLM versions; mocked tests",
       docs=("https://docs.litellm.ai/docs/proxy/virtual_keys",),
       env=("LITELLM_API_KEY", "LITELLM_BASE_URL"), has_limit=True),
    _P("zai", "Z.ai (GLM)", "Z.ai", "none", NOT_SUPPORTED,
       reason="Z.ai documents no balance, usage or quota API; figures are dashboard-only.",
       needs="A documented billing or usage endpoint."),
    _P("volcengine", "Volcengine Ark", "Volcengine", "none", NOT_SUPPORTED,
       reason="Balance lives in the Volcengine Billing OpenAPI, which needs AK/SK request "
              "signing with account-level credentials; not implemented (an Ark inference "
              "key cannot read it).",
       needs="A signed Billing OpenAPI client with a narrowly scoped IAM user."),
    _P("huggingface", "Hugging Face Inference Providers", "Hugging Face", "none",
       NOT_SUPPORTED,
       reason="No documented API returns credit balance or inference spend; it is shown "
              "only on the billing settings page.",
       needs="A documented billing endpoint."),
    _P("bedrock", "Amazon Bedrock", "AWS", "none", NOT_SUPPORTED,
       reason="Bedrock has no balance endpoint; spend is in Cost Explorer and token metrics "
              "in CloudWatch, both needing SigV4-signed AWS credentials.",
       needs="A SigV4 Cost Explorer / CloudWatch reader with read-only IAM credentials."),
    _P("grok_bot", "Grok Bot", "Grok", "none", NOT_SUPPORTED,
       reason="No usage API or local usage log is available to third-party tools.",
       needs="An official usage endpoint."),
    # -------------------------------------------------------- DeepSeek
    _P("deepseek", "DeepSeek API", "DeepSeek", "api", SUPPORTED,
       shows="balance (total, granted, topped-up) per currency",
       source="GET https://api.deepseek.com/user/balance", auth="API key in DEEPSEEK_API_KEY",
       reset="n/a (balance)", confidence="medium (docs + mocked tests)",
       docs=("https://api-docs.deepseek.com/api/get-user-balance",), env=("DEEPSEEK_API_KEY",)),
    _P("dsh", "DeepSeek Harness", "DeepSeek", "local_logs", EXPERIMENTAL,
       shows="tokens (incl. cache, reasoning), models, projects",
       source="$DSH_HOME or ~/.dsh/sessions/*/session-*/session[.vN].jsonl[.zstd]",
       reset="n/a (history)",
       confidence="medium-low: format from public parser docs; synthetic fixtures. Compressed "
                  "logs need the zstandard package (bundled) or Python 3.14",
       docs=("docs/token-adapters.md",),
       extra={"note": "Never reads ~/.dsh/.credentials.yaml."}),
    _P("deepseek_app", "DeepSeek chat app", "DeepSeek", "none", NOT_SUPPORTED,
       reason="The consumer app publishes no usage or limit data.", needs="n/a"),
    # ------------------------------------------------------------ Qwen
    _P("qwen_code", "Qwen Code", "Qwen", "local_logs", SUPPORTED,
       shows="tokens (incl. cached and thoughts), models, projects",
       source="~/.qwen/projects/**/chats/*.jsonl (usageMetadata)",
       reset="n/a (history)", confidence="medium (format from the open-source CLI; fixtures only)",
       docs=("https://github.com/QwenLM/qwen-code",)),
    _P("dashscope", "Qwen API (Alibaba Model Studio)", "Qwen", "none", NOT_SUPPORTED,
       reason="Balance is only available via Alibaba Cloud BSS OpenAPI (QueryAccountBalance), "
              "which needs an AccessKey pair and request signing; not implemented.",
       needs="Signature V3 client + a RAM user with read-only billing permission.",
       docs=("https://www.alibabacloud.com/help/en/bss-openapi",)),
    # ------------------------------------------------------------ Kimi
    _P("kimi", "Kimi CLI / Kimi Code / Kimi Work", "Kimi", "local_logs", SUPPORTED,
       shows="tokens (incl. cache), models, projects",
       source="~/.kimi/sessions/**/wire.jsonl, ~/.kimi-code/sessions/**, Kimi Work (macOS/Windows) "
              "…/kimi-desktop/daimon-share/daimon/runtime/kimi-code/home/sessions (or the shareDir "
              "set in kimi-desktop/daimon-storage.json)",
       reset="n/a (history)",
       confidence="medium (fixtures only; Kimi Work paths match the layout other open-source "
                  "parsers report from real installs, not tested by us)",
       docs=("https://github.com/MoonshotAI/kimi-cli",)),
    _P("moonshot", "Kimi API (Moonshot) balance", "Kimi", "api", SUPPORTED,
       shows="available, voucher and cash balance",
       source="GET https://api.moonshot.ai/v1/users/me/balance (or api.moonshot.cn)",
       auth="API key in MOONSHOT_API_KEY; region via config moonshot_region",
       reset="n/a (balance)", confidence="medium (docs + mocked tests)",
       docs=("https://platform.moonshot.ai/docs/api/misc",), env=("MOONSHOT_API_KEY",)),
    _P("kimi_app", "Kimi app membership", "Kimi", "none", NOT_SUPPORTED,
       reason="The consumer app publishes no usage or limit API.", needs="n/a"),
    # ------------------------------------------------- GitHub Copilot
    _P("github_copilot", "GitHub Copilot premium requests", "Copilot", "api", PARTIAL,
       shows="premium requests this month, billed USD; % only against an allowance you enter",
       source="GET https://api.github.com/users/{user}/settings/billing/premium_request/usage",
       auth="Fine-grained token with Plan: read in GITHUB_COPILOT_TOKEN (or GITHUB_TOKEN) "
            "+ github_username", reset="monthly, 1st 00:00 UTC",
       confidence="medium (docs + mocked tests). Only for individually billed accounts; the API "
                  "does not return the included allowance.",
       docs=("https://docs.github.com/en/rest/billing/usage",
             "https://docs.github.com/en/copilot/concepts/billing/copilot-requests"),
       env=("GITHUB_COPILOT_TOKEN", "GITHUB_TOKEN"), has_limit=True),
    _P("copilot_otel", "GitHub Copilot CLI (OpenTelemetry file)", "Copilot", "local_logs", PARTIAL,
       shows="tokens (incl. cache, reasoning), models",
       source="~/.copilot/otel/*.jsonl or $COPILOT_OTEL_FILE_EXPORTER_PATH, written only after "
              "you set COPILOT_OTEL_ENABLED=true and COPILOT_OTEL_EXPORTER_TYPE=file",
       reset="n/a (history)",
       confidence="low-medium (GenAI semantic-convention attributes; fixtures only)",
       docs=("https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/",)),
    # ------------------------------------------------------- Cursor
    _P("cursor_team", "Cursor (team Admin API)", "Cursor", "api", PARTIAL,
       shows="team cycle spend (USD), on-demand spend, your spend vs. your per-user limit",
       source="POST https://api.cursor.com/teams/spend", auth="Team Admin API key in CURSOR_ADMIN_KEY",
       reset="billing cycle (subscriptionCycleStart from the API)",
       confidence="medium (docs + mocked tests). Team plans only.",
       docs=("https://cursor.com/docs/account/teams/admin-api",), env=("CURSOR_ADMIN_KEY",),
       has_limit=True),
    _P("cursor", "Cursor (individual plans)", "Cursor", "web_session", EXPERIMENTAL,
       shows="included plan % used / spend vs limit, on-demand spend, billing cycle when returned",
       source="GET https://cursor.com/api/usage-summary with a cursor.com session cookie",
       auth="opt-in: in-app sign-in (authenticator.cursor.sh), or import cookies from a browser",
       reset="billing cycle (from the API when present)",
       confidence="experimental (endpoint/cookie names from CodexBar docs; response field names "
                  "vary by plan — not verified on a live OmniMonitor account yet)",
       docs=("docs/cookies/codexbar.md",
             "https://github.com/steipete/CodexBar/blob/main/docs/cursor.md"),
       has_limit=True),
    # ------------------------------------------------------- Others
    _P("openrouter", "OpenRouter", "OpenRouter", "api", SUPPORTED,
       shows="credits used (total/day/week/month), key limit % and reset",
       source="GET https://openrouter.ai/api/v1/key", auth="API key in OPENROUTER_API_KEY",
       reset="per key: daily/weekly/monthly at 00:00 UTC, or none",
       confidence="medium (docs + mocked tests)",
       docs=("https://openrouter.ai/docs/api/reference/limits",), env=("OPENROUTER_API_KEY",),
       has_limit=True),
    _P("ollama_cloud", "Ollama Cloud", "Ollama", "api", SUPPORTED,
       shows="balance / allowance and % used when the API returns them",
       source="GET https://ollama.com/api/balance", auth="API key in OLLAMA_API_KEY",
       reset="as returned by the API", confidence="low-medium (docs + mocked tests)",
       docs=("https://docs.ollama.com/cloud",), env=("OLLAMA_API_KEY",), has_limit=True),
    _P("ollama_local", "Ollama (local)", "Ollama", "none", NOT_SUPPORTED,
       reason="A local Ollama server keeps no usage log; per-request counts only appear in API "
              "responses.", needs="A logging proxy in front of Ollama."),
    _P("opencode", "opencode", "opencode", "local_logs", PARTIAL,
       shows="tokens (incl. cache, reasoning), recorded cost, models, projects",
       source="~/.local/share/opencode/storage/message/**/*.json or opencode.db (opened read-only)",
       reset="n/a (history)",
       confidence="medium for JSON files; low for SQLite (schema varies by version)",
       docs=("https://opencode.ai/docs/",)),
    _P("windsurf", "Windsurf", "Windsurf", "none", NOT_SUPPORTED,
       reason="Usage APIs are Enterprise-only (service key) and report acceptances/credits per "
              "team, not personal usage; no local usage log.",
       needs="An Enterprise service key; an adapter could be added on request.",
       docs=("https://docs.windsurf.com/windsurf/accounts/api-reference/api-introduction",)),
    _P("mistral", "Mistral (Le Chat / API)", "Mistral", "none", NOT_SUPPORTED,
       reason="No public usage or billing API; usage is only in the console.", needs="An official API."),
    _P("perplexity", "Perplexity", "Perplexity", "none", NOT_SUPPORTED,
       reason="No public usage or billing API; usage is only in the API console.",
       needs="An official API."),
    # ------------------------------------------------------------ 0.4 batch 1b (local)
    _P("pi", "Pi coding agent", "Pi", "local_logs", PARTIAL,
       shows="tokens (incl. cache), models, projects, cost as logged by Pi",
       source="~/.pi/agent/sessions/**/*.jsonl ($PI_CODING_AGENT_DIR, "
              "$PI_CODING_AGENT_SESSION_DIR; also ~/.omp fork)",
       reset="n/a (history)",
       confidence="medium: format read from the open-source pi-mono session code; synthetic "
                  "fixtures. Forked sessions are de-duplicated by entry id",
       docs=("https://github.com/badlogic/pi-mono",)),
    _P("openclaw", "OpenClaw", "OpenClaw", "local_logs", EXPERIMENTAL,
       shows="tokens (incl. cache), models, cost as logged",
       source="~/.openclaw/agents/**/*.jsonl (legacy ~/.clawdbot, ~/.moltbot)",
       reset="n/a (history)",
       confidence="low-medium: Pi-format transcripts; OpenClaw's own layout is not documented",
       docs=("docs/token-adapters.md",),
       extra={"note": "Delivery-mirror rows are skipped so replies are not counted twice."}),
    _P("goose", "Goose", "Block", "local_logs", PARTIAL,
       shows="tokens per session (incl. cache), models, projects, cost",
       source="<goose data dir>/sessions/sessions.db, opened read-only "
              "($GOOSE_PATH_ROOT/data, ~/.local/share/goose, %APPDATA%\\Block\\goose\\data)",
       reset="n/a (history)",
       confidence="medium: schema read from block/goose session_manager.rs; totals are per "
                  "session, dated when the session started",
       docs=("https://github.com/block/goose",)),
    _P("hermes", "Hermes Agent", "Nous Research", "local_logs", PARTIAL,
       shows="tokens per session (incl. cache, reasoning), models, cost",
       source="$HERMES_HOME or ~/.hermes/state.db (+ profiles/*/state.db), opened read-only",
       reset="n/a (history)",
       confidence="medium: sessions schema is in the official developer guide; totals are per "
                  "session, dated at started_at",
       docs=("https://hermes-agent.nousresearch.com/docs/developer-guide/session-storage",)),
    _P("amp", "Amp", "Amp", "local_logs", EXPERIMENTAL,
       shows="tokens (incl. cache), models",
       source="~/.local/share/amp/threads/*.json (usageLedger)",
       reset="n/a (history)",
       confidence="low: thread files are undocumented; Amp credits are not shown as dollars",
       docs=("docs/token-adapters.md",),
       extra={"note": "Plan/credit balance is only on ampcode.com and is not read."}),
    _P("droid", "Factory Droid", "Factory", "local_logs", EXPERIMENTAL,
       shows="tokens per session (incl. cache, thinking), models, projects",
       source="~/.factory/sessions/**/*.settings.json (tokenUsage)",
       reset="n/a (history)", confidence="low: undocumented session files; session totals only",
       docs=("docs/token-adapters.md",)),
    _P("cline", "Cline", "Cline", "local_logs", EXPERIMENTAL,
       shows="tokens (incl. cache), cost as logged, model when found",
       source="<VS Code|Cursor>/User/globalStorage/saoudrizwan.claude-dev/tasks/*/ui_messages.json",
       reset="n/a (history)",
       confidence="low-medium: api_req_started records from the open-source extension; model "
                  "is parsed from environment details and may show as unknown",
       docs=("https://github.com/cline/cline",)),
    _P("roo_code", "Roo Code", "Roo Code", "local_logs", EXPERIMENTAL,
       shows="tokens (incl. cache), cost as logged, model when found",
       source="<VS Code|Cursor>/User/globalStorage/rooveterinaryinc.roo-cline/tasks/*/"
              "ui_messages.json",
       reset="n/a (history)", confidence="low-medium: same record format as Cline (a fork)",
       docs=("https://github.com/RooCodeInc/Roo-Code",)),
    _P("kilo_code", "Kilo Code", "Kilo Code", "local_logs", EXPERIMENTAL,
       shows="tokens (incl. cache), cost as logged, model when found",
       source="<VS Code|Cursor>/User/globalStorage/kilocode.kilo-code/tasks/*/ui_messages.json",
       reset="n/a (history)", confidence="low-medium: same record format as Cline (a fork)",
       docs=("https://github.com/Kilo-Org/kilocode",)),
    _P("zed", "Zed AI", "Zed", "none", NOT_SUPPORTED,
       reason="Agent threads are stored zstd-compressed in a private SQLite schema without "
              "per-request token counts we could verify; plan usage is only in the signed-in "
              "account page.", needs="A documented usage log or API."),
    _P("kiro", "Kiro", "AWS", "none", NOT_SUPPORTED,
       reason="Usage (credits) is shown only in the signed-in Kiro app/account; no documented "
              "local usage log or personal usage API.", needs="A documented usage log or API."),
    _P("minimax_agent", "MiniMax Agent / Code", "MiniMax", "none", NOT_SUPPORTED,
       reason="No documented local usage log or public balance/usage endpoint could be verified.",
       needs="An official usage or balance API."),
    _P("devin_cli", "Devin CLI", "Cognition", "none", NOT_SUPPORTED,
       reason="ACU usage is only in the signed-in Devin web app; the CLI's local files are "
              "undocumented and could not be verified.", needs="A documented usage log or API."),
    _P("lmstudio", "LM Studio (local)", "LM Studio", "none", NOT_SUPPORTED,
       reason="Local models have no quota; the server returns usage per response but keeps no "
              "documented usage log to read.", needs="n/a (local, unmetered)"),
)

BY_ID: Dict[str, ProviderInfo] = {p.id: p for p in CATALOG}
LOCAL_IDS: Tuple[str, ...] = tuple(p.id for p in CATALOG if p.kind == "local_logs")
API_IDS: Tuple[str, ...] = tuple(p.id for p in CATALOG if p.kind == "api")
WEB_SESSION_IDS: Tuple[str, ...] = tuple(p.id for p in CATALOG if p.kind == "web_session")
#: Ids that can be switched on (local adapters other than the built-in Grok one, APIs, web sessions).
SWITCHABLE: Tuple[str, ...] = tuple(
    i for i in LOCAL_IDS + API_IDS + WEB_SESSION_IDS if i != "grok_sessions")
#: Choices for the tray's primary provider. ``budget`` = the tightest budget cap.
PRIMARY_CHOICES: Tuple[str, ...] = tuple(p.id for p in CATALOG if p.has_limit) + ("budget",)


def get(pid: str) -> Optional[ProviderInfo]:
    return BY_ID.get(pid)


def name(pid: str) -> str:
    p = BY_ID.get(pid)
    return p.name if p else pid


def enabled_ids(cfg: dict) -> List[str]:
    """Switchable providers the user turned on. An explicit ``providers`` entry wins; the
    legacy ``extra_token_sources`` list only counts for ids ``providers`` doesn't mention."""
    explicit = {pid: opts.get("enabled") is True
                for pid, opts in (cfg.get("providers") or {}).items()
                if pid in SWITCHABLE and isinstance(opts, dict)}
    on = {pid for pid, v in explicit.items() if v}
    for pid in cfg.get("extra_token_sources") or []:
        if pid in SWITCHABLE and pid not in explicit:
            on.add(pid)
    return [p for p in SWITCHABLE if p in on]


def counts() -> Dict[str, int]:
    out = {SUPPORTED: 0, PARTIAL: 0, EXPERIMENTAL: 0, NOT_SUPPORTED: 0}
    for p in CATALOG:
        out[p.status] += 1
    return out
