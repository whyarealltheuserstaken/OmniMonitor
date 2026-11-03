"""Configuration: location per OS, defaults, safe load/save (chmod 600 where supported)."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from omnimonitor import APP_NAME, LEGACY_APP_NAME, LEGACY_ENV_PREFIX

DEFAULT_BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
DEFAULT_USAGE_PAGE = "https://grok.com/dashboard"
MIN_POLL_SECONDS = 60
DEFAULT_POLL_SECONDS = 120

DEFAULT_CONFIG: Dict[str, Any] = {
    # Where the weekly % comes from:
    #   "demo"             -> FAKE numbers, clearly labeled DEMO (default until you run --setup)
    #   "grok_cli_billing" -> UNOFFICIAL endpoint the Grok CLI's /usage panel calls
    #   "command"          -> your own command printing {"remaining_percent": .., "resets_at": ..}
    "source": "demo",
    # Where token counts come from:
    #   "auto"         -> "demo" while source is demo, otherwise "grok_sessions"
    #   "grok_sessions"-> read-only scan of the Grok CLI's local session logs
    #   "demo"         -> FAKE token history (labeled)
    #   "none"         -> no token stats (everything shows "not available")
    "token_source": "auto",
    "poll_interval_seconds": DEFAULT_POLL_SECONDS,
    "usage_page_url": DEFAULT_USAGE_PAGE,
    # Grok CLI locations. Empty = auto ($GROK_HOME or ~/.grok; $GROK_AUTH_PATH).
    "grok_home": "",
    "grok_auth_file": "",
    # "auto" = run `<grok> models` (found on PATH or in ~/.grok/bin) when the token is
    # stale or rejected, so the official CLI refreshes its own token. "" = never run.
    # Or a list like ["/path/to/grok", "models"].
    "grok_refresh_command": "auto",
    "refresh_timeout_seconds": 60,
    "refresh_skew_seconds": 300,
    "timeout_seconds": 15,
    # Manual token instead of the CLI auth file (expires after ~6 h). Prefer the auth file.
    "token": "",
    "command": "",
    # Only https://*.grok.com or http://127.0.0.1/localhost (for tests) are accepted.
    "grok_billing_url": DEFAULT_BILLING_URL,
    # Local stats page. 0 = random free port each run (recommended).
    "web_port": 0,
    # "≈Y tokens" weekly-limit estimate (derived, clearly flagged). Never shown below the
    # minimum used percent, because tiny percentages make the ratio meaningless.
    "estimate_weekly_limit": True,
    "estimate_min_used_percent": 5.0,
    "heatmap_weeks": 53,
    # ---- 0.2.0 ------------------------------------------------------------------------
    # Extra local token logs to read, besides the Grok CLI (opt-in, read-only):
    #   "claude_code" -> ~/.claude/projects/**/*.jsonl ($CLAUDE_CONFIG_DIR)
    #   "codex"       -> ~/.codex/sessions/**/rollout-*.jsonl ($CODEX_HOME)
    "extra_token_sources": [],
    "claude_home": "",
    "codex_home": "",
    # Your own prices, USD per 1M tokens, for COST ESTIMATES. Keys are model names or
    # glob patterns ("grok-code-*"). Fields: input, cached_input, cache_write, output.
    # Nothing is bundled: models without a price show "not available".
    "prices": {},
    # Desktop notifications.
    "notifications": True,
    "notify_thresholds": [80, 95],          # % USED of the weekly pool
    "notify_on_reset": True,
    "notify_on_pace": False,                # "on pace to run out before reset"
    # Personalization.
    "icon_style": "ring",                   # ring | pie | bar | number
    "icon_value": "remaining",              # remaining | used
    "theme": "dark",                        # dark | light | auto
    "accent_color": "#2a8bd8",
    "display_name": "",
    "hidden_cards": [],                     # e.g. ["projects", "cost"]
}


def env(name: str) -> str:
    """Read OMNIMONITOR_<name>, falling back to the pre-rename GROK_USAGE_TRAY_<name>."""
    return os.environ.get("OMNIMONITOR_" + name) or os.environ.get(LEGACY_ENV_PREFIX + name) or ""


def _platform_config_dir(app_name: str) -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / app_name
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / app_name


def default_config_dir() -> Path:
    return _platform_config_dir(APP_NAME)


def legacy_config_dir() -> Path:
    """Where releases before the OmniMonitor rename kept their config."""
    return _platform_config_dir(LEGACY_APP_NAME)


def migrate_legacy_config(new: Path, old: Path) -> Path:
    """Return the dir to use. If only the old dir exists, copy it to the new location (the old
    dir is left untouched). If the copy fails, keep reading the old dir instead."""
    if new.exists() or not old.is_dir():
        return new
    try:
        shutil.copytree(str(old), str(new))
        secure_mkdir(new)
        for child in new.iterdir():
            if child.is_file():
                secure_file(child)
        return new
    except Exception:  # noqa: BLE001 - never crash on migration; fall back to the old dir
        shutil.rmtree(str(new), ignore_errors=True)
        return old


def config_dir() -> Path:
    """Per-OS config directory (no third-party deps), migrating the pre-rename dir once."""
    override = env("HOME")
    if override:
        return Path(override).expanduser()
    return migrate_legacy_config(default_config_dir(), legacy_config_dir())


def config_path() -> Path:
    return config_dir() / "config.json"


def history_path() -> Path:
    return config_dir() / "history.sqlite3"


def secure_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        try:
            os.chmod(str(path), 0o700)
        except OSError:
            pass


def secure_file(path: Path) -> None:
    """chmod 600 on POSIX. On Windows files under %APPDATA% are already per-user."""
    if os.name == "posix":
        try:
            os.chmod(str(path), 0o600)
        except OSError:
            pass


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Never raises: a broken file is reported via cfg['_config_error']."""
    path = path or config_path()
    cfg = dict(DEFAULT_CONFIG)
    cfg["_config_path"] = str(path)
    cfg["_configured"] = False
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                user = json.load(f)
            if isinstance(user, dict):
                if user.get("token"):
                    secure_file(path)  # holds a secret: keep it owner-only
                cfg.update({k: v for k, v in user.items() if not str(k).startswith("_")})
                cfg["_configured"] = True
            else:
                cfg["_config_error"] = "config.json is not a JSON object"
        except Exception as e:  # noqa: BLE001 - never crash on bad config
            cfg["_config_error"] = "config.json unreadable: %s" % e
    if env("SOURCE"):
        cfg["source"] = env("SOURCE")
    if env("TOKEN"):
        cfg["token"] = env("TOKEN")
    cfg["poll_interval_seconds"] = clamp_poll(cfg.get("poll_interval_seconds"))
    return cfg


def clamp_poll(value: Any) -> int:
    try:
        v = int(float(value))
    except (TypeError, ValueError):
        return DEFAULT_POLL_SECONDS
    return max(MIN_POLL_SECONDS, v)


def save_config(cfg: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Atomic write, then chmod 600. Private keys (leading underscore) are not saved."""
    path = path or config_path()
    secure_mkdir(path.parent)
    data = {k: v for k, v in cfg.items() if not str(k).startswith("_")}
    fd, tmp = tempfile.mkstemp(prefix=".config.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        secure_file(Path(tmp))
        os.replace(tmp, str(path))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    secure_file(path)
    return path


def update_config_file(changes: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Merge `changes` into the config FILE (not the in-memory cfg, which may hold env
    overrides such as a token that must never be written to disk)."""
    path = path or config_path()
    data: Dict[str, Any] = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
        if not isinstance(loaded, dict):
            raise ValueError("config.json is not a JSON object")
        data = loaded
    data.update(changes)
    return save_config(data, path)


def ensure_config_file(path: Optional[Path] = None) -> Path:
    """Create a default (demo) config file if missing, so "Edit config" has something to open."""
    path = path or config_path()
    if not path.exists():
        save_config(dict(DEFAULT_CONFIG), path)
    return path


def grok_home(cfg: Dict[str, Any]) -> Path:
    if (cfg.get("grok_home") or "").strip():
        return Path(cfg["grok_home"]).expanduser()
    if os.environ.get("GROK_HOME"):
        return Path(os.environ["GROK_HOME"]).expanduser()
    return Path.home() / ".grok"


def grok_auth_file(cfg: Dict[str, Any]) -> Path:
    if (cfg.get("grok_auth_file") or "").strip():
        return Path(cfg["grok_auth_file"]).expanduser()
    if os.environ.get("GROK_AUTH_PATH"):
        return Path(os.environ["GROK_AUTH_PATH"]).expanduser()
    return grok_home(cfg) / "auth.json"


def claude_homes(cfg: Dict[str, Any]) -> list:
    raw = (cfg.get("claude_home") or "").strip() or os.environ.get("CLAUDE_CONFIG_DIR", "")
    if raw.strip():
        return [Path(p.strip()).expanduser() for p in raw.split(",") if p.strip()]
    return [Path.home() / ".claude", Path.home() / ".config" / "claude"]


def codex_homes(cfg: Dict[str, Any]) -> list:
    raw = (cfg.get("codex_home") or "").strip() or os.environ.get("CODEX_HOME", "")
    if raw.strip():
        return [Path(p.strip()).expanduser() for p in raw.split(",") if p.strip()]
    return [Path.home() / ".codex"]


def extra_token_sources(cfg: Dict[str, Any]) -> list:
    v = cfg.get("extra_token_sources") or []
    if isinstance(v, str):
        v = [x.strip() for x in v.split(",")]
    return [x for x in v if x in ("claude_code", "codex")]


def effective_token_source(cfg: Dict[str, Any]) -> str:
    ts = (cfg.get("token_source") or "auto").strip()
    if ts == "auto":
        return "demo" if (cfg.get("source") or "demo") == "demo" else "grok_sessions"
    return ts
