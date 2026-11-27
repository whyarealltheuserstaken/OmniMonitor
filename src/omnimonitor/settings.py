"""Allow-listed, validated settings shared by the web Settings page and `config set`.

Security: the web page can ONLY change keys in SCHEMA with web=True. Secrets and anything
that runs code or points the app at a new place (token, command, grok_refresh_command,
grok_billing_url, usage_page_url, file paths) are never writable from the browser.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Tuple

from omnimonitor import catalog, display, suites
from omnimonitor import config as config_mod
from omnimonitor.pricing import clean_prices

HIDEABLE_CARDS = ("providers", "budgets", "grok-suite", "forecast", "burn", "limit",
                  "heatmap", "breakdown", "tools", "models",
                  "projects", "cost", "insights")


class SettingError(ValueError):
    pass


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("1", "true", "yes", "on"):
        return True
    if isinstance(v, str) and v.strip().lower() in ("0", "false", "no", "off"):
        return False
    raise SettingError("expected true/false")


def _num(lo: float, hi: float, integer: bool = False) -> Callable[[Any], Any]:
    def f(v: Any):
        if isinstance(v, bool):
            raise SettingError("expected a number")
        try:
            x = float(v)
        except (TypeError, ValueError):
            raise SettingError("expected a number") from None
        if not lo <= x <= hi:
            raise SettingError("must be between %g and %g" % (lo, hi))
        return int(x) if integer else x
    return f


def _choice(*opts: str) -> Callable[[Any], str]:
    def f(v: Any) -> str:
        if v not in opts:
            raise SettingError("must be one of: %s" % ", ".join(opts))
        return v
    return f


def _list_of(allowed: Tuple[str, ...], aliases: Dict[str, str] = None
             ) -> Callable[[Any], List[str]]:
    def f(v: Any) -> List[str]:
        if isinstance(v, str):
            v = [x.strip() for x in v.split(",") if x.strip()]
        if not isinstance(v, list):
            raise SettingError("expected a list")
        out = []
        for x in v:
            x = (aliases or {}).get(x, x)
            if x not in allowed:
                raise SettingError("unknown value %r (allowed: %s)" % (x, ", ".join(allowed)))
            if x not in out:
                out.append(x)
        return out
    return f


def _thresholds(v: Any) -> List[float]:
    if isinstance(v, str):
        v = [x for x in v.split(",") if x.strip()]
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = [v]
    if not isinstance(v, list) or len(v) > 10:
        raise SettingError("expected up to 10 percentages")
    out = sorted({_num(1, 100)(x) for x in v})
    return [int(x) if float(x).is_integer() else x for x in out]


def _color(v: Any) -> str:
    if not isinstance(v, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
        raise SettingError("expected a color like #2a8bd8")
    return v.lower()


def _name(v: Any) -> str:
    if not isinstance(v, str):
        raise SettingError("expected text")
    v = re.sub(r"[\x00-\x1f\x7f]", "", v).strip()
    if len(v) > 40:
        raise SettingError("at most 40 characters")
    return v


def _prices(v: Any) -> Dict[str, Dict[str, float]]:
    if isinstance(v, str):
        try:
            v = json.loads(v or "{}")
        except ValueError:
            raise SettingError("prices must be a JSON object") from None
    if not isinstance(v, dict) or len(v) > 200:
        raise SettingError("prices must be a JSON object (at most 200 models)")
    clean = clean_prices(v)
    bad = [k for k in v if str(k).strip()[:120] not in clean]
    if bad:
        raise SettingError("each price needs numeric input and output (USD per 1M): %s"
                           % ", ".join(map(str, bad[:5])))
    return clean


def _path(v: Any) -> str:
    if not isinstance(v, str) or len(v) > 4096 or "\x00" in v:
        raise SettingError("expected a path (or comma-separated paths)")
    return v.strip()


def _providers(v: Any) -> Dict[str, Dict[str, Any]]:
    """Per-provider switches. Web-session providers also accept auth + browser.

    Shape::
        {"claude_plan": {"enabled": true, "auth": "login"|"browser"|"off",
                         "browser": "auto"|"chrome"|"edge"|"brave"|"arc"|"firefox"|"safari"}}
    ``auth`` defaults to ``off``. Cookies are never accepted through this endpoint.
    """
    if isinstance(v, str):
        try:
            v = json.loads(v or "{}")
        except ValueError:
            raise SettingError("providers must be a JSON object") from None
    if not isinstance(v, dict):
        raise SettingError('expected {"provider_id": {"enabled": true|false}, ...}')
    allowed_auth = {"off", "login", "browser", "cursor_app"}
    allowed_browsers = {"auto", "chrome", "edge", "brave", "arc", "firefox", "safari"}
    out = {}
    for pid, opts in v.items():
        if pid not in catalog.SWITCHABLE:
            raise SettingError("unknown provider %r (see `omnimonitor providers`)" % pid)
        if isinstance(opts, bool):
            opts = {"enabled": opts}
        if not isinstance(opts, dict):
            raise SettingError("%s: expected an object" % pid)
        extra = set(opts) - {"enabled", "auth", "browser"}
        if extra:
            raise SettingError("%s: unknown keys %s" % (pid, sorted(extra)))
        entry = {"enabled": _bool(opts.get("enabled", False))}
        if pid in getattr(catalog, "WEB_SESSION_IDS", ()):
            auth = str(opts.get("auth") or "off").lower()
            choices = allowed_auth if pid == "cursor" else allowed_auth - {"cursor_app"}
            if auth not in choices:
                raise SettingError("%s.auth must be one of %s" % (pid, sorted(choices)))
            entry["auth"] = auth
            browser = str(opts.get("browser") or "auto").lower()
            if browser not in allowed_browsers:
                raise SettingError("%s.browser must be one of %s" % (pid, sorted(allowed_browsers)))
            entry["browser"] = browser
            if entry["enabled"] and auth == "off":
                # enabling without a method is allowed but useless; keep off-by-default honesty
                pass
        elif "auth" in opts or "browser" in opts:
            raise SettingError("%s is not a web-session provider" % pid)
        out[pid] = entry
    return out


def _provider_thresholds(v: Any) -> Dict[str, List[float]]:
    if isinstance(v, str):
        try:
            v = json.loads(v or "{}")
        except ValueError:
            raise SettingError("provider_thresholds must be a JSON object") from None
    if not isinstance(v, dict) or len(v) > 60:
        raise SettingError('expected {"provider_id": [50, 80, 95], ...}')
    out = {}
    for pid, ths in v.items():
        if pid not in catalog.BY_ID and pid not in suites.SUITE_IDS:
            raise SettingError("unknown provider or suite %r" % pid)
        out[pid] = _thresholds(ths)
    return out


def _object(v: Any, what: str) -> Dict[str, Any]:
    if isinstance(v, str):
        try:
            v = json.loads(v or "{}")
        except ValueError:
            raise SettingError("%s must be a JSON object" % what) from None
    if not isinstance(v, dict):
        raise SettingError("%s must be a JSON object" % what)
    return v


def _id_list(v: Any, what: str) -> List[str]:
    if not isinstance(v, list) or len(v) > 200:
        raise SettingError("%s must be a list" % what)
    if not all(display.valid_id(x) for x in v):
        raise SettingError("%s: unknown suite or source id" % what)
    return list(dict.fromkeys(v))


def _display(v: Any) -> Dict[str, Any]:
    v = _object(v, "display")
    if set(v) - {"order", "hide", "fields"}:
        raise SettingError('expected {"order": [...], "hide": {...}, "fields": {...}}')
    out: Dict[str, Any] = {}
    if v.get("order"):
        out["order"] = _id_list(v["order"], "display.order")
    for key in ("hide", "fields"):
        section = _object(v.get(key) or {}, "display." + key)
        if set(section) - set(display.SURFACES):
            raise SettingError("display.%s: surfaces are %s" % (key, ", ".join(display.SURFACES)))
        clean = {}
        for surface, items in section.items():
            if key == "hide":
                ids = _id_list(items, "display.hide.%s" % surface)
                if ids:
                    clean[surface] = ids
            else:
                clean[surface] = _list_of(display.FIELDS)(items)
        if clean:
            out[key] = clean
    return out


def _suite_colors(v: Any) -> Dict[str, str]:
    v = _object(v, "suite_colors")
    if set(v) - set(suites.SUITE_IDS):
        raise SettingError("unknown suite (use: %s)" % ", ".join(suites.SUITE_IDS))
    return {k: _color(c) for k, c in v.items()}


def _source_poll(v: Any) -> Dict[str, int]:
    v = _object(v, "source_poll_seconds")
    account = catalog.API_IDS + catalog.WEB_SESSION_IDS + catalog.CLI_LOGIN_IDS
    if set(v) - set(account):
        raise SettingError("only account sources have their own interval: %s"
                           % ", ".join(account))
    return {k: _num(300, 86400, True)(s) for k, s in v.items()}


def _budgets(v: Any) -> Dict[str, float]:
    if isinstance(v, str):
        try:
            v = json.loads(v or "{}")
        except ValueError:
            raise SettingError("budgets must be a JSON object") from None
    if not isinstance(v, dict) or set(v) - {"daily", "weekly", "monthly"}:
        raise SettingError('expected {"daily": USD, "weekly": USD, "monthly": USD} (0 = off)')
    out = {}
    for k in ("daily", "weekly", "monthly"):
        x = _num(0, 1e6)(v.get(k, 0) or 0)
        out[k] = int(x) if float(x).is_integer() else round(x, 2)
    return out


def _proxy(v: Any) -> str:
    from omnimonitor.net import validate_proxy
    if not isinstance(v, str):
        raise ValueError("expected a string")
    return validate_proxy(v)


def _ident(maxlen: int, pattern: str) -> Callable[[Any], str]:
    def f(v: Any) -> str:
        if not isinstance(v, str) or len(v) > maxlen or (v and not re.fullmatch(pattern, v)):
            raise SettingError("invalid value")
        return v.strip()
    return f


# key -> (validator, writable from the web page, help)
SCHEMA: Dict[str, Tuple[Callable[[Any], Any], bool, str]] = {
    "poll_interval_seconds": (_num(60, 3600, True), True, "seconds between billing polls"),
    "extra_token_sources": (_list_of(tuple(i for i in catalog.LOCAL_IDS
                                           if i != "grok_sessions")), True,
                            "legacy list of local-log adapters (prefer `providers`)"),
    "providers": (_providers, True, 'per-provider switches; web_session also auth/browser'),
    "tray_suite": (_choice(*suites.TRAY_CHOICES), True,
                   "suite whose % drives the tray icon (or tightest / budget)"),
    "provider_thresholds": (_provider_thresholds, True,
                            "per-source or per-suite alert thresholds (USED %)"),
    "display": (_display, True, "what shows where: order, hide (per surface), fields"),
    "suite_colors": (_suite_colors, True, 'per-suite colour, e.g. {"openai": "#10a37f"}'),
    "source_poll_seconds": (_source_poll, True,
                            "per account source refresh seconds (min 300)"),
    "budgets": (_budgets, True, "USD budgets for local-log spend (0 = off)"),
    "budget_alert_percent": (_num(1, 100), True, "warn when a budget reaches this %"),
    "provider_poll_seconds": (_num(300, 86400, True), True,
                              "seconds between account API reads (min 300)"),
    "prices": (_prices, True, "your USD-per-1M-token prices for cost estimates"),
    "notifications": (_bool, True, "desktop notifications"),
    "notify_thresholds": (_thresholds, True, "alert when weekly USED % crosses these"),
    "notify_on_reset": (_bool, True, "alert when a limit you were warned about (or the Grok week) resets"),
    "status_badges": (_bool, True, "opt-in: show official status-page badges (network)"),
    "notify_on_pace": (_bool, True, "alert when a limit is on pace to run out before it resets (estimate)"),
    "icon_style": (_choice("ring", "pie", "bar", "number"), True, "tray icon style"),
    "icon_value": (_choice("remaining", "used"), True, "tray icon shows remaining or used"),
    "icon_text": (_bool, True, "macOS menu bar: show the % next to the ring"),
    "mini_on_top": (_bool, True, "keep the mini window above other windows"),
    "theme": (_choice("dark", "light", "auto"), True, "stats page theme"),
    "accent_color": (_color, True, "accent color"),
    "display_name": (_name, True, "name shown on the stats page"),
    "hidden_cards": (_list_of(HIDEABLE_CARDS), True,
                     "stats page sections to hide"),
    "heatmap_weeks": (_num(4, 106, True), True, "weeks in the heatmap"),
    "estimate_weekly_limit": (_bool, True, "show the ≈ weekly token limit estimate"),
    "estimate_min_used_percent": (_num(1, 100), True, "min used % before estimating"),
    # CLI only (never from the browser):
    "claude_home": (_path, False, "Claude Code config dir(s), comma-separated"),
    "codex_home": (_path, False, "Codex home dir(s), comma-separated"),
    "grok_home": (_path, False, "Grok CLI home dir"),
    "gemini_home": (_path, False, "Gemini CLI home dir(s) (default ~/.gemini)"),
    "qwen_home": (_path, False, "Qwen Code home dir(s) (default ~/.qwen)"),
    "kimi_home": (_path, False, "Kimi sessions dir(s) (default: Kimi CLI, Kimi Code, Kimi Work)"),
    "antigravity_home": (_path, False, "Antigravity data dir(s) holding conversations/*.db"),
    "dsh_home": (_path, False, "DeepSeek Harness home or sessions dir (default: $DSH_HOME, ~/.dsh)"),
    "pi_home": (_path, False, "Pi session dirs (default: ~/.pi/agent/sessions, ~/.omp)"),
    "openclaw_home": (_path, False, "OpenClaw agents dirs (default: ~/.openclaw/agents)"),
    "goose_home": (_path, False, "Goose sessions dir (default: platform data dir)"),
    "hermes_home": (_path, False, "Hermes home (default: $HERMES_HOME, ~/.hermes)"),
    "amp_home": (_path, False, "Amp threads dir (default: ~/.local/share/amp/threads)"),
    "factory_home": (_path, False, "Factory Droid sessions (default: ~/.factory/sessions)"),
    "vscode_storage": (_path, False, "VS Code/Cursor User/globalStorage dirs (Cline, Roo, Kilo)"),
    "opencode_home": (_path, False, "opencode data dir(s) (default ~/.local/share/opencode)"),
    "copilot_otel_path": (_path, False, "Copilot CLI OTel JSONL file or dir"),
    "moonshot_region": (_choice("global", "cn"), False, "platform.moonshot.ai or .cn"),
    "github_username": (_ident(39, r"[A-Za-z0-9-]+"), False,
                        "GitHub user for Copilot premium-request usage"),
    "copilot_premium_allowance": (_num(0, 1e6, True), False,
                                  "your plan's monthly premium requests (for a %)"),
    "xai_team_id": (_ident(64, r"[A-Za-z0-9_-]+"), False, "xAI team id for the balance"),
    "proxy_url": (_proxy, False, "HTTP(S) proxy for all requests, http://host:port "
                  "(default: HTTPS_PROXY env)"),
    "fireworks_account_id": (_ident(64, r"[A-Za-z0-9_-]+"), False,
                             "Fireworks account id for the spend summary"),
    "cursor_email": (_ident(254, r"[^@\s]+@[^@\s]+"), False,
                     "your email in the Cursor team (own spend row)"),
    "source": (_choice("demo", "grok_cli_billing", "none"), False,
               "Grok weekly pool: grok_cli_billing, none (real data without Grok) or demo"),
    "token_source": (_choice("auto", "grok_sessions", "demo", "none"), False,
                     "where Grok token counts come from"),
    "grok_agent_billing": (_bool, False,
                           "try `grok agent stdio` (_x.ai/billing, then x.ai/billing) "
                           "before the cli-chat-proxy billing call"),
}


def web_keys() -> List[str]:
    return [k for k, (_f, web, _h) in SCHEMA.items() if web]


def _tray_choice_name(choice: str) -> str:
    if choice == "tightest":
        return "Tightest limit (any suite)"
    if choice == "budget":
        return "Budget"
    return suites.get(choice).name


def validate(key: str, value: Any, web: bool = False) -> Any:
    if key not in SCHEMA:
        raise SettingError("not a settable key: %s" % key)
    fn, web_ok, _ = SCHEMA[key]
    if web and not web_ok:
        raise SettingError("%s can only be changed with `omnimonitor config set`" % key)
    return fn(value)


def apply(cfg: Dict[str, Any], changes: Any, web: bool = False
          ) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Validate a batch -> (clean changes, {key: error}). All-or-nothing is up to the
    caller; nothing is written here."""
    if not isinstance(changes, dict):
        return {}, {"_": "expected a JSON object"}
    if len(changes) > len(SCHEMA):
        return {}, {"_": "too many keys"}
    clean, errors = {}, {}
    for k, v in changes.items():
        try:
            clean[str(k)] = validate(str(k), v, web=web)
        except SettingError as e:
            errors[str(k)[:60]] = str(e)
    return clean, errors


def public_view(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """What the Settings page may READ: web keys only, plus a few read-only facts."""
    out = {k: cfg.get(k) for k in web_keys()}
    out.update(_valid_layout(cfg))  # the page sends these back: one typo would block a save
    out["_readonly"] = {"source": cfg.get("source"), "token_source": cfg.get("token_source"),
                        "config_path": cfg.get("_config_path"),
                        "hideable_cards": list(HIDEABLE_CARDS),
                        "catalog": [{"id": p.id, "name": p.name, "family": p.family,
                                     "kind": p.kind, "status": p.status, "shows": p.shows,
                                     "env": list(p.env), "has_limit": p.has_limit,
                                     "note": p.extra.get("note", "")}
                                    for p in catalog.CATALOG if p.id in catalog.SWITCHABLE],
                        "tray_choices": [{"id": i, "name": _tray_choice_name(i)}
                                         for i in suites.TRAY_CHOICES],
                        "suites": _layout_suites(cfg),
                        "surfaces": list(display.SURFACES),
                        "fields": {s: display.fields(cfg, s) for s in display.SURFACES},
                        "all_fields": list(display.FIELDS),
                        "keys": _key_presence(),
                        "sessions": _session_presence()}
    return out


def _valid_layout(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """The stored layout settings with anything the validators would refuse left out."""
    def as_dict(v: Any) -> Dict[str, Any]:
        return v if isinstance(v, dict) else {}

    def ids(v: Any) -> List[str]:
        return [x for x in v if display.valid_id(x)] if isinstance(v, list) else []

    def passes(check: Callable[[Any], Any], value: Any) -> bool:
        try:
            check(value)
            return True
        except SettingError:
            return False
    stored = as_dict(cfg.get("display"))
    out: Dict[str, Any] = {}
    if ids(stored.get("order")):
        out["order"] = ids(stored.get("order"))
    hide = {s: ids(v) for s, v in as_dict(stored.get("hide")).items()
            if s in display.SURFACES and ids(v)}
    fields = {s: [x for x in v if x in display.FIELDS]
              for s, v in as_dict(stored.get("fields")).items()
              if s in display.SURFACES and isinstance(v, list)}
    out.update({k: v for k, v in (("hide", hide), ("fields", fields)) if v})
    return {"display": out,
            "suite_colors": {k: v for k, v in as_dict(cfg.get("suite_colors")).items()
                             if passes(_suite_colors, {k: v})},
            "source_poll_seconds": {
                k: v for k, v in as_dict(cfg.get("source_poll_seconds")).items()
                if passes(_source_poll, {k: v})}}


def _layout_suites(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Suites and their switched-on sources, in the user's order (the Layout editor)."""
    on = set(catalog.enabled_ids(cfg))
    if cfg.get("source") != "none":
        on.add("grok")
    if config_mod.effective_token_source(cfg) == "grok_sessions":
        on.add("grok_sessions")
    cards = [{"id": p.id} for p in catalog.CATALOG if p.id in on]
    rows = [{"id": s.id, "name": s.name} for s in suites.SUITES
            if any(suites.suite_of(c["id"]) == s.id for c in cards)]
    rows = display.ordered_suites(cfg, rows)
    cards = display.ordered_cards(cfg, cards, rows)
    for s in rows:
        s["shared_id"] = s["id"] in catalog.BY_ID  # "deepseek": a source and a suite
        s["sources"] =[{"id": c["id"], "name": catalog.name(c["id"]),
                         "account": catalog.get(c["id"]).kind in ("api", "web_session",
                                                                  "cli_login")}
                        for c in cards if suites.suite_of(c["id"]) == s["id"]]
    return rows


def _session_presence() -> Dict[str, str]:
    """Where a web session is stored ('vault' / '') — never cookie values."""
    try:
        from omnimonitor import sessions
        return sessions.status()
    except Exception:  # noqa: BLE001
        return {}


def _key_presence() -> Dict[str, str]:
    """Which API keys are set and where ('env' / 'vault' / 'file' / '') - never any characters."""
    try:
        from omnimonitor import apikeys
        return apikeys.status()
    except Exception:  # noqa: BLE001
        return {}


def parse_cli_value(raw: str) -> Any:
    """`config set key value`: JSON if it parses, else the raw string."""
    try:
        return json.loads(raw)
    except ValueError:
        return raw
