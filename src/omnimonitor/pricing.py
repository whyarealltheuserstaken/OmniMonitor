"""Cost: recorded (from the logs) vs ESTIMATED (from YOUR price table). Pure functions.

Nothing is bundled: prices change and differ by plan, so a model without a price in your
table is "unpriced" and its tokens are reported as such - never priced by guess.

Price table (config "prices"), USD per 1M tokens:
    {"grok-code-fast*": {"input": 0.2, "cached_input": 0.02, "output": 1.5},
     "claude-sonnet-*": {"input": 3, "cached_input": 0.3, "cache_write": 3.75, "output": 15}}
Keys are exact model names or fnmatch globs; the most specific (longest) match wins.
`cached_input` defaults to `input`, `cache_write` defaults to `input`.
"""

from __future__ import annotations

import fnmatch
import math
from typing import Any, Dict, Iterable, Optional

PRICE_FIELDS = ("input", "cached_input", "cache_write", "output")


def clean_prices(raw: Any) -> Dict[str, Dict[str, float]]:
    """Validate a user price table; drops anything malformed. Raises nothing."""
    out: Dict[str, Dict[str, float]] = {}
    if not isinstance(raw, dict):
        return out
    for model, row in raw.items():
        if not isinstance(model, str) or not model.strip() or not isinstance(row, dict):
            continue
        clean = {}
        for k in PRICE_FIELDS:
            v = row.get(k)
            if isinstance(v, bool) or v is None:
                continue
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(f) and 0 <= f < 1e6:
                clean[k] = f
        if "input" in clean and "output" in clean:
            out[model.strip()[:120]] = clean
    return out


def price_for(model: str, prices: Dict[str, Dict[str, float]]) -> Optional[Dict[str, float]]:
    if not model:
        return None
    if model in prices:
        return prices[model]
    best = None
    for pat in prices:
        if fnmatch.fnmatchcase(model.lower(), pat.lower()):
            if best is None or len(pat) > len(best):
                best = pat
    return prices[best] if best else None


def estimate_event_cost(e: Dict[str, Any], prices: Dict[str, Dict[str, float]]
                        ) -> Optional[float]:
    p = price_for(str(e.get("model") or ""), prices)
    if p is None:
        return None
    inp = int(e.get("input_tokens") or 0)
    cr = int(e.get("cache_read_tokens") or 0)
    cw = int(e.get("cache_creation_tokens") or 0)
    fresh = max(0, inp - cr - cw)
    out = int(e.get("output_tokens") or 0)
    usd = (fresh * p["input"] + cr * p.get("cached_input", p["input"])
           + cw * p.get("cache_write", p["input"]) + out * p["output"]) / 1e6
    return usd


def summarize_cost(events: Iterable[Dict[str, Any]], prices: Dict[str, Dict[str, float]]
                   ) -> Dict[str, Any]:
    """{recorded_usd, estimated_usd, priced_tokens, unpriced_tokens, unpriced_models}.

    recorded_usd: sum of costs the tool itself wrote in its logs (Grok `costUsdTicks`),
                  None if no event had one.
    estimated_usd: from your price table, None if no event matched a price.
    """
    rec, est = None, None
    priced = unpriced = 0
    unpriced_models: Dict[str, int] = {}
    for e in events:
        tot = int(e.get("input_tokens") or 0) + int(e.get("output_tokens") or 0)
        if e.get("cost_usd") is not None:
            rec = (rec or 0.0) + float(e["cost_usd"])
        c = estimate_event_cost(e, prices)
        if c is None:
            unpriced += tot
            m = str(e.get("model") or "unknown")
            unpriced_models[m] = unpriced_models.get(m, 0) + tot
        else:
            est = (est or 0.0) + c
            priced += tot
    return {"recorded_usd": rec, "estimated_usd": est, "priced_tokens": priced,
            "unpriced_tokens": unpriced,
            "unpriced_models": sorted(unpriced_models, key=lambda m: -unpriced_models[m])[:10]}


DEMO_PRICES = {  # DEMO ONLY - fake prices for the fake demo models
    "demo-model-a": {"input": 1.0, "cached_input": 0.1, "output": 4.0},
    "demo-model-b": {"input": 3.0, "cached_input": 0.3, "output": 15.0},
}
