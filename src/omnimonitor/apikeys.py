"""API keys for account providers: environment variables first, then a chmod-600 file.

Keys are NEVER stored in config.json, never accepted from the web UI and never returned
by any API: callers only learn *whether* a key is set and where it comes from.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from omnimonitor import catalog
from omnimonitor import config as config_mod


def allowed_names() -> List[str]:
    names: List[str] = []
    for p in catalog.CATALOG:
        for n in p.env:
            if n not in names:
                names.append(n)
    return names


def secrets_path() -> Path:
    raw = config_mod.env("SECRETS_FILE")
    return Path(raw).expanduser() if raw.strip() else config_mod.config_dir() / "secrets.json"


def file_problem(path: Optional[Path] = None) -> str:
    """'' when the file is absent or private; otherwise why it is ignored."""
    path = path or secrets_path()
    try:
        st = path.stat()
    except OSError:
        return ""
    if os.name != "nt" and st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        return "%s is readable by other users; run: chmod 600 %s" % (path, path)
    return ""


def _load(path: Optional[Path] = None) -> Dict[str, str]:
    path = path or secrets_path()
    if not path.is_file() or file_problem(path):
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in allowed_names() and isinstance(v, str) and v}


def get(name: str) -> str:
    """The key value, for the HTTP client only. Never log or display it."""
    v = os.environ.get(name, "").strip()
    if v:
        return v
    return _load().get(name, "").strip()


def where(name: str) -> str:
    """'env', 'file' or '' (not set)."""
    if os.environ.get(name, "").strip():
        return "env"
    return "file" if _load().get(name) else ""


def status() -> Dict[str, str]:
    return {n: where(n) for n in allowed_names()}


def _write(data: Dict[str, str], path: Path) -> None:
    config_mod.secure_mkdir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".secrets-", suffix=".tmp")
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, str(path))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    config_mod.secure_file(path)


def set_secret(name: str, value: str, path: Optional[Path] = None) -> Path:
    if name not in allowed_names():
        raise ValueError("unknown key name %r (allowed: %s)" % (name, ", ".join(allowed_names())))
    value = (value or "").strip()
    if not value or len(value) > 4096 or any(c in value for c in "\r\n\x00"):
        raise ValueError("empty or malformed key")
    path = path or secrets_path()
    data = _load(path) if path.is_file() else {}
    data[name] = value
    _write(data, path)
    return path


def unset_secret(name: str, path: Optional[Path] = None) -> bool:
    path = path or secrets_path()
    data = _load(path)
    if name not in data:
        return False
    del data[name]
    _write(data, path)
    return True
