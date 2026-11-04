"""Provider catalog: the single source of truth for what OmniMonitor can read.

Every provider is opt-in. ``kind`` says where numbers come from:

* ``billing``    - the Grok weekly pool (existing ``source`` setting)
* ``local_logs`` - read-only parsing of files the tool itself writes on this computer
* ``api``        - the provider's official usage/billing API with a key *you* supply
                   (environment variable or the chmod-600 secrets file, never the web UI)
* ``none``       - not supported; ``reason`` says why and ``needs`` what would be required

Nothing here reads browser cookies, keychains or another app's login session.
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
    _P("claude_plan", "Claude Pro/Max plan limits", "Claude", "none", NOT_SUPPORTED,
       reason="No official API exposes the 5-hour/weekly plan limits; the in-app /usage view uses "
              "a private OAuth endpoint and the web dashboard needs a browser session.",
       needs="An official, documented limits endpoint from Anthropic."),
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
       reason="No official API. The chatgpt.com backend route some tools call is undocumented "
              "and needs the app's login token. Codex plan windows are shown via the Codex adapter.",
       needs="An official usage endpoint from OpenAI."),
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
       shows="prepaid credit balance (USD)",
       source="GET https://management-api.x.ai/v1/billing/teams/{team_id}/prepaid/balance",
       auth="Management key in XAI_MANAGEMENT_KEY + team id (config xai_team_id or XAI_TEAM_ID)",
       reset="n/a (balance)",
       confidence="low-medium: docs show purchases as negative changes, so balance = -total; "
                  "not verified with a live key",
       docs=("https://docs.x.ai/docs/management-api",), env=("XAI_MANAGEMENT_KEY", "XAI_TEAM_ID")),
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
    _P("cursor", "Cursor (individual plans)", "Cursor", "none", NOT_SUPPORTED,
       reason="Individual usage is only in the signed-in dashboard (session cookie). OmniMonitor "
              "never reads browser cookies.",
       needs="An official per-user usage API, or a team plan (see Cursor team Admin API)."),
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
    _P("amp", "Amp", "Amp", "none", NOT_SUPPORTED,
       reason="Usage is shown in ampcode.com settings (signed-in). Local thread files exist but "
              "their format is not documented and could not be verified.",
       needs="A documented usage log or API."),
    _P("zed", "Zed AI", "Zed", "none", NOT_SUPPORTED,
       reason="Agent threads are stored compressed in a private SQLite schema; plan usage is only "
              "in the signed-in account page.", needs="A documented usage log or API."),
)

BY_ID: Dict[str, ProviderInfo] = {p.id: p for p in CATALOG}
LOCAL_IDS: Tuple[str, ...] = tuple(p.id for p in CATALOG if p.kind == "local_logs")
API_IDS: Tuple[str, ...] = tuple(p.id for p in CATALOG if p.kind == "api")
#: Ids that can be switched on (local adapters other than the built-in Grok one, and APIs).
SWITCHABLE: Tuple[str, ...] = tuple(i for i in LOCAL_IDS + API_IDS if i != "grok_sessions")
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
