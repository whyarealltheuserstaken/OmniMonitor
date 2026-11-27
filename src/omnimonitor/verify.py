"""``omnimonitor verify``: check each enabled source once (Grok pool, local logs,
accounts), report pass or fail, and save what came back - scrubbed - as candidate test
fixtures.

Nothing secret leaves this function: request headers are never saved, query strings are
dropped from URLs, and every saved payload goes through :func:`omnimonitor.redact.scrub`
with the key / cookie that was used.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
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
    """Every source that is switched on: the Grok pool, local logs and accounts."""
    on = set(catalog.enabled_ids(cfg))
    if cfg.get("source") == "grok_cli_billing":
        on.add("grok")
    if config_mod.effective_token_source(cfg) == "grok_sessions":
        on.add("grok_sessions")
    order = ("grok",) + catalog.LOCAL_IDS + catalog.API_IDS + catalog.WEB_SESSION_IDS \
        + catalog.CLI_LOGIN_IDS
    return [p for p in order if p in on]


def _default_http(pid: str) -> Http:
    """Each kind of account source has its own client with its own host allow-list."""
    if pid in catalog.WEB_SESSION_IDS:
        from omnimonitor import web_accounts
        return web_accounts._json_http
    if pid in catalog.CLI_LOGIN_IDS:
        from omnimonitor import cli_accounts
        return cli_accounts._cli_http
    from omnimonitor import accounts
    return accounts.default_http


def _check_local(cfg: Dict[str, Any], pid: str) -> Dict[str, Any]:
    """Parse this tool's real logs into a throwaway database. Only counts are kept: no
    paths, project names or message content."""
    from omnimonitor import adapters
    from omnimonitor.history import History
    only = dict(cfg, providers={pid: {"enabled": True}}, extra_token_sources=[])
    files = events = tokens = 0
    error = ""
    with tempfile.TemporaryDirectory() as tmp:
        history = History(Path(tmp) / "verify.sqlite3")
        try:
            for sc in adapters.scanners(only, history, include_grok=pid == "grok_sessions"):
                if getattr(sc, "name", "") != pid:
                    continue
                if not sc.available()[0]:
                    error = "no logs found on this computer"
                    continue
                files += int((sc.scan() or {}).get("files") or 0)
            rows = [e for e in history.events() if (e.get("source") or "grok_sessions") == pid]
            events = len(rows)
            tokens = sum(int(e.get("input_tokens") or 0) + int(e.get("output_tokens") or 0)
                         for e in rows)
        except Exception as e:  # noqa: BLE001
            error = "could not read logs (%s)" % type(e).__name__
        finally:
            history.close()
    if not error and not events:
        error = "logs were read but contained no token usage"
    return {"ok": not error, "error": error,
            "reading": {"files": files, "events": events, "tokens": tokens}}


def _check_grok(cfg: Dict[str, Any]) -> Dict[str, Any]:
    from omnimonitor import billing
    usage, error = billing.fetch_usage_safely(dict(cfg, source="grok_cli_billing"))
    return {"ok": usage is not None, "error": error or "",
            "reading": usage.to_dict() if usage is not None else {}}


def run(cfg: Dict[str, Any], pids: Optional[List[str]] = None, http: Optional[Http] = None,
        out_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    out_dir = out_dir or default_dir()
    config_mod.secure_mkdir(out_dir)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    results = []
    for pid in pids or targets(cfg):
        info = catalog.BY_ID.get(pid)
        if info is None or info.kind == "none":
            results.append({"pid": pid, "ok": False, "saved": "",
                            "error": "unknown or unsupported source"})
            continue
        if info.kind in ("local_logs", "billing"):
            got = _check_local(cfg, pid) if info.kind == "local_logs" else _check_grok(cfg)
            results.append(_save(out_dir, stamp, dict(got, pid=pid, calls=[]), []))
            continue
        rec = RecordingHttp(http or _default_http(pid))
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
            from omnimonitor import accounts
            r = accounts.fetch(pid, cfg, http=rec)
        for n in info.env:  # keys and account / team ids
            from omnimonitor import apikeys
            secrets.append(apikeys.get(n))
        secrets += rec.secrets
        results.append(_save(out_dir, stamp, {"pid": pid, "ok": r.ok, "error": r.error,
                                              "reading": r.to_dict(), "calls": rec.calls},
                             secrets))
    return results


def _save(out_dir: Path, stamp: str, raw: Dict[str, Any], secrets: List[str]
          ) -> Dict[str, Any]:
    """Scrub, write one result file, and return its summary row."""
    doc = redact.scrub(raw, secrets)
    name = "%s-%s.json" % (raw["pid"], stamp)
    path = out_dir / name
    path.write_text(json.dumps(doc, indent=2, sort_keys=True, default=str), "utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return {"pid": raw["pid"], "ok": bool(raw["ok"]), "error": doc["error"], "saved": name}
