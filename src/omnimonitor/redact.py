"""Scrub secrets and personal data out of structured payloads (JSON-like objects).

Used before any provider response is written to disk (``omnimonitor verify``). Numbers,
booleans, percentages and timestamps are kept, so the result still works as a test
fixture; identifying strings are replaced with placeholders.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, List

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_KEY = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|sk-ant-[A-Za-z0-9_-]+|xai-[A-Za-z0-9_-]{8,}|"
                  r"gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,})")
_LONG = re.compile(r"[A-Za-z0-9_\-+/=]{32,}")
_ID_KEY = re.compile(r"(?i)(^|_)(id|uuid|email|org|organization|account|user|team|name|"
                     r"login|username|owner|customer|member|phone|phone_number|address|ip|project|"
                     r"ip_address|avatar|avatar_url|picture|image|slug|workspace|title|"
                     r"company|domain|handle|display_name)s?$|^billing_")
_ID_CAMEL = re.compile(r"[a-z](Id|Uuid|Email|Org|Account|User|Team|Name|Login|Phone|"
                       r"Address|Avatar|Picture|Slug|Workspace|Project)s?$")


def _key_is_identifying(k: str) -> bool:
    return bool(_ID_KEY.search(k) or _ID_CAMEL.search(k))


def _text(s: str, secrets: List[str]) -> str:
    for sec in secrets:
        if sec:
            s = s.replace(sec, "<secret>")
    s = _JWT.sub("<jwt>", s)
    s = _KEY.sub("<key>", s)
    s = _EMAIL.sub("<email>", s)
    return _LONG.sub("<token>", s)


def scrub(obj: Any, secrets: Iterable[str] = ()) -> Any:
    """A scrubbed deep copy of ``obj``. ``secrets`` are exact strings that must never
    survive (session values, keys) - pass every one you used for the request."""
    secs = sorted({s for s in secrets if isinstance(s, str) and len(s) >= 4}, key=len,
                  reverse=True)
    return _walk(obj, secs, None)


def _walk(obj: Any, secrets: List[str], key: Any) -> Any:
    if isinstance(obj, dict):
        return {(_text(k, secrets) if isinstance(k, str) else k): _walk(v, secrets, k)
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_walk(v, secrets, key) for v in obj]
    if isinstance(key, str) and _key_is_identifying(key) and obj is not None \
            and not isinstance(obj, (dict, list, bool)):
        return "<redacted>"
    if isinstance(obj, str):
        return _text(obj, secrets)
    return obj
