"""Opt-in service-status badges from providers' OFFICIAL status pages.

Each page below serves the public Statuspage-compatible ``/api/v2/status.json`` summary
(verified 2026-11-04). Off by default (``status_badges``); when on, one GET per page at
most every 5 minutes, through the same allow-listed, no-redirect HTTPS client as the
account readers. Nothing is sent but the GET; nothing is inferred beyond the page's own
indicator."""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

PAGES: Dict[str, str] = {  # catalog family -> official status page
    "Claude": "https://status.claude.com",
    "ChatGPT": "https://status.openai.com",
    "Copilot": "https://www.githubstatus.com",
    "Cursor": "https://status.cursor.com",
    "Fireworks": "https://status.fireworks.ai",
    "Vercel": "https://www.vercel-status.com",
}
HOSTS = {u.split("//", 1)[1] for u in PAGES.values()}
INDICATORS = {"none": "operational", "minor": "minor", "major": "major",
              "critical": "critical", "maintenance": "maintenance"}
TTL = 300

_cache: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


def parse(d: Any) -> Optional[Dict[str, str]]:
    st = d.get("status") if isinstance(d, dict) else None
    if not isinstance(st, dict):
        return None
    ind = st.get("indicator")
    if ind not in INDICATORS:
        return None
    desc = st.get("description") if isinstance(st.get("description"), str) else ""
    return {"indicator": ind, "level": INDICATORS[ind], "description": desc[:80]}


def fetch_all(cfg: Dict[str, Any], families, http=None, now: Optional[float] = None
              ) -> Dict[str, Dict[str, Any]]:
    """family -> {indicator, level, description, url, fetched_at}. {} when disabled."""
    if not cfg.get("status_badges"):
        return {}
    from omnimonitor.accounts import AccountError, _get, default_http
    now = time.time() if now is None else now
    out: Dict[str, Dict[str, Any]] = {}
    for fam in sorted(set(families) & set(PAGES)):
        url = PAGES[fam]
        with _lock:
            hit = _cache.get(url)
        if hit and now - hit["fetched_at"] < TTL:
            out[fam] = hit
            continue
        try:
            got = parse(_get(http or default_http, url + "/api/v2/status.json", {}))
        except AccountError:
            got = None
        except Exception:  # noqa: BLE001 - a status page must never break the overview
            got = None
        if got is None:
            if hit:
                out[fam] = dict(hit, stale=True)
            continue
        entry = dict(got, url=url, fetched_at=now)
        with _lock:
            _cache[url] = entry
        out[fam] = entry
    return out


def clear_cache() -> None:
    with _lock:
        _cache.clear()
