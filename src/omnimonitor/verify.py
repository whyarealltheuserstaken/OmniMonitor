"""``omnimonitor verify``: call each enabled account / web-session source once, report
pass or fail, and save the responses - scrubbed - as candidate test fixtures.

Nothing secret leaves this function: request headers are never saved, query strings are
dropped from URLs, and every saved payload goes through :func:`omnimonitor.redact.scrub`
with the key / cookie that was used.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from omnimonitor import catalog, redact
from omnimonitor import config as config_mod

_SECRET_HEADER = re.compile(r"(?i)auth|cookie|key|token|secret|session|account")

Http = Callable[[str, str, Dict[str, str], Optional[bytes]], Tuple[int, Any]]


class RecordingHttp:
    """Wraps the HTTP function; remembers (method, url without query, status, json) and
    every header value, so those can be scrubbed out of what is saved."""

    def __init__(self, inner: Http):
        self.inner, self.calls, self.secrets = inner, [], []  # type: ignore[var-annotated]

    def __call__(self, method, url, headers, body):
        for k, v in (headers or {}).items():
            if isinstance(v, str) and _SECRET_HEADER.search(str(k)):
                self.secrets.append(v)
                self.secrets.extend(p.split("=", 1)[-1] for p in v.split(";") if "=" in p)
                self.secrets.extend(v.split()[1:])  # "Bearer <tok>"
        status, data = self.inner(method, url, headers, body)
        u = urlsplit(url)
        self.calls.append({"method": method, "url": urlunsplit((u.scheme, u.netloc, u.path,
                                                                 "", "")),
                           "status": status, "json": data})
        return status, data


def default_dir() -> Path:
    return config_mod.config_dir() / "verify"


def targets(cfg: Dict[str, Any]) -> List[str]:
    on = set(catalog.enabled_ids(cfg))
    return [p for p in catalog.API_IDS + catalog.WEB_SESSION_IDS + catalog.CLI_LOGIN_IDS
            if p in on]


def run(cfg: Dict[str, Any], pids: Optional[List[str]] = None, http: Optional[Http] = None,
        out_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    from omnimonitor import accounts
    out_dir = out_dir or default_dir()
    config_mod.secure_mkdir(out_dir)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    results = []
    for pid in pids or targets(cfg):
        rec = RecordingHttp(http or accounts.default_http)
        secrets = []
        if pid in catalog.WEB_SESSION_IDS:
            from omnimonitor import sessions, web_accounts
            secrets += [sessions.load(pid) or ""] + list(sessions.meta(pid).values())
            cookie = sessions.load(pid) or ""
            secrets += [p.split("=", 1)[-1] for p in cookie.split(";") if "=" in p]
            r = web_accounts.fetch(pid, cfg, http=rec)
        elif pid in catalog.CLI_LOGIN_IDS:
            from omnimonitor import cli_accounts
            r = cli_accounts.fetch(pid, cfg, http=rec)
        else:
            r = accounts.fetch(pid, cfg, http=rec)
        info = catalog.BY_ID.get(pid)
        for n in (info.env if info else ()):  # keys and account / team ids
            from omnimonitor import apikeys
            secrets.append(apikeys.get(n))
        secrets += rec.secrets
        doc = redact.scrub({"pid": pid, "ok": r.ok, "error": r.error,
                            "reading": r.to_dict(), "calls": rec.calls}, secrets)
        name = "%s-%s.json" % (pid, stamp)
        path = out_dir / name
        path.write_text(json.dumps(doc, indent=2, sort_keys=True, default=str), "utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        results.append({"pid": pid, "ok": bool(r.ok), "error": doc["error"], "saved": name})
    return results
