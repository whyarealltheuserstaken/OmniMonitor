"""Allow-listed, validated settings shared by the web Settings page and `config set`.

Security: the web page can ONLY change keys in SCHEMA with web=True. Secrets and anything
that runs code or points the app at a new place (token, command, grok_refresh_command,
grok_billing_url, usage_page_url, file paths) are never writable from the browser.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Tuple

from omnimonitor.pricing import clean_prices

HIDEABLE_CARDS = ("forecast", "burn", "limit", "heatmap", "breakdown", "tools", "models",
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


# key -> (validator, writable from the web page, help)
SCHEMA: Dict[str, Tuple[Callable[[Any], Any], bool, str]] = {
    "poll_interval_seconds": (_num(60, 3600, True), True, "seconds between billing polls"),
    "extra_token_sources": (_list_of(("claude_code", "codex")), True,
                            "also read Claude Code / Codex local logs"),
    "prices": (_prices, True, "your USD-per-1M-token prices for cost estimates"),
    "notifications": (_bool, True, "desktop notifications"),
    "notify_thresholds": (_thresholds, True, "alert when weekly USED % crosses these"),
    "notify_on_reset": (_bool, True, "alert when the weekly pool resets"),
    "notify_on_pace": (_bool, True, "alert when on pace to run out before reset (estimate)"),
    "icon_style": (_choice("ring", "pie", "bar", "number"), True, "tray icon style"),
    "icon_value": (_choice("remaining", "used"), True, "tray icon shows remaining or used"),
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
    "token_source": (_choice("auto", "grok_sessions", "demo", "none"), False,
                     "where Grok token counts come from"),
}


def web_keys() -> List[str]:
    return [k for k, (_f, web, _h) in SCHEMA.items() if web]


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
    out["_readonly"] = {"source": cfg.get("source"), "token_source": cfg.get("token_source"),
                        "config_path": cfg.get("_config_path"),
                        "hideable_cards": list(HIDEABLE_CARDS)}
    return out


def parse_cli_value(raw: str) -> Any:
    """`config set key value`: JSON if it parses, else the raw string."""
    try:
        return json.loads(raw)
    except ValueError:
        return raw
