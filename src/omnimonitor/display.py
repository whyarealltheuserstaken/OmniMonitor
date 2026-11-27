"""What shows where: order, per-surface visibility, per-surface numbers and suite colours.

Config shape (all optional)::

    "display": {"order":  ["suite:openai", "codex", ...],      # suites and/or sources
                "hide":   {"menu": ["suite:google", "codex"]},  # per surface
                "fields": {"menu": ["limit", "reset"]}},        # numbers per surface
    "suite_colors": {"openai": "#10a37f"}

An id is a source (provider) id, ``suite:<id>``, or a bare suite id. Where a name is both
(``deepseek``, ``kimi``) the bare form means the source when hiding. Pure data, never
raises on a hand-edited config.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from omnimonitor import catalog, suites

#: tray = what can drive the icon; menu = tray menu lines.
SURFACES = ("tray", "menu", "dashboard", "mini", "statusline")
FIELDS = ("limit", "reset", "tokens", "cost", "money")
#: The status line stays short: balances only when asked for.
DEFAULT_FIELDS = {"statusline": ("limit", "reset", "tokens", "cost")}
SUITE_PREFIX = "suite:"


def valid_id(x: Any) -> bool:
    if not isinstance(x, str):
        return False
    if x.startswith(SUITE_PREFIX):
        return x[len(SUITE_PREFIX):] in suites.SUITE_IDS
    return x in catalog.BY_ID or x in suites.SUITE_IDS


def _section(cfg: Dict[str, Any], key: str) -> Any:
    d = cfg.get("display")
    return d.get(key) if isinstance(d, dict) else None


def _strings(v: Any) -> List[str]:
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def hidden(cfg: Dict[str, Any], surface: str) -> Set[str]:
    h = _section(cfg, "hide")
    return set(_strings(h.get(surface))) if isinstance(h, dict) else set()


def fields(cfg: Dict[str, Any], surface: str) -> List[str]:
    f = _section(cfg, "fields")
    chosen = f.get(surface) if isinstance(f, dict) else None
    if not isinstance(chosen, list):
        return list(DEFAULT_FIELDS.get(surface, FIELDS))
    return [x for x in FIELDS if x in chosen]


def visible(hide: Set[str], pid: str) -> bool:
    sid = suites.suite_of(pid)
    return not (pid in hide or SUITE_PREFIX + sid in hide
                or (sid in hide and sid not in catalog.BY_ID))


def color(cfg: Dict[str, Any], sid: str) -> str:
    c = cfg.get("suite_colors")
    v = c.get(sid) if isinstance(c, dict) else None
    return v if isinstance(v, str) and len(v) == 7 and v.startswith("#") else ""


def ordered_suites(cfg: Dict[str, Any], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    order = _strings(_section(cfg, "order"))

    def rank(item):
        i, s = item
        for key in (SUITE_PREFIX + s["id"], s["id"]):
            if key in order:
                return (order.index(key), i)
        return (len(order), i)
    return [s for _i, s in sorted(enumerate(rows), key=rank)]


def ordered_cards(cfg: Dict[str, Any], cards: List[Dict[str, Any]],
                  suite_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Cards grouped in suite order; inside a suite, listed sources come first."""
    order = _strings(_section(cfg, "order"))
    suite_pos = {s["id"]: i for i, s in enumerate(suite_rows)}

    def rank(item):
        i, c = item
        pid = c["id"]
        return (suite_pos.get(suites.suite_of(pid), len(suite_pos)),
                order.index(pid) if pid in order else len(order), i)
    return [c for _i, c in sorted(enumerate(cards), key=rank)]


def view(ov: Dict[str, Any], cfg: Dict[str, Any], surface: str,
         now: Optional[float] = None) -> Dict[str, Any]:
    """{suites, card_ids, fields} for one surface. A suite's headline only counts the
    sources visible on that surface."""
    from omnimonitor.overview import suite_view
    hide = hidden(cfg, surface)
    cards = [c for c in ov.get("cards") or [] if visible(hide, c["id"])]
    rows = ordered_suites(cfg, suite_view(cards, now))
    cards = ordered_cards(cfg, cards, rows)
    ids = [c["id"] for c in cards]
    for s in rows:
        s["card_ids"] = [i for i in ids if i in s["card_ids"]]
        s["color"] = color(cfg, s["id"])
        s["money"] = next(
            ({"label": m.get("label"), "value": m.get("value"), "unit": m.get("unit")}
             for c in cards if c["id"] in s["card_ids"] for m in c.get("metrics") or []
             if m.get("kind") in ("balance", "spend")), None)
    return {"suites": rows, "card_ids": ids, "fields": fields(cfg, surface)}


def views(ov: Dict[str, Any], cfg: Dict[str, Any], now: Optional[float] = None
          ) -> Dict[str, Dict[str, Any]]:
    return {s: view(ov, cfg, s, now) for s in SURFACES}
