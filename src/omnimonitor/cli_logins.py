"""Read-only access to the logins the official CLIs already hold (Claude Code, Codex,
Gemini CLI), so plan limits can be shown with no extra sign-in.

Rules: files are only ever opened for reading; refresh tokens are never loaded; an expired
access token is reported ("run the CLI once") instead of being refreshed here; token values
never appear in exceptions, logs or ``repr``.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from omnimonitor import config as config_mod


class LoginMissing(Exception):
    """No usable login file for this CLI on this computer."""


class LoginExpired(Exception):
    """The CLI's access token has expired; running the CLI once refreshes it."""


@dataclass(frozen=True)
class CliLogin:
    token: str = field(repr=False)
    expires_at: Optional[float] = None  # unix seconds
    extra: Dict[str, str] = field(default_factory=dict)


MAX_FILE = 1024 * 1024  # a real login file is a few hundred bytes


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        if path.stat().st_size > MAX_FILE:
            raise LoginMissing("unreadable")
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError, RecursionError):
        raise LoginMissing("unreadable")
    if not isinstance(data, dict):
        raise LoginMissing("unreadable")
    return data


def _first_file(homes, name: str, also_under: str = "") -> Optional[Path]:
    for h in homes:
        for p in ([Path(h) / name] + ([Path(h) / also_under / name] if also_under else [])):
            if p.is_file():
                return p
    return None


def _ms_to_s(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        return float(v) / 1000.0 if v > 1e11 else float(v)  # ms vs s
    except (OverflowError, ValueError):
        return None


def _check(token: Any, expires: Optional[float], now: float, cli: str, cmd: str) -> str:
    if not isinstance(token, str) or not token.strip():
        raise LoginMissing("not signed in to %s on this computer" % cli)
    if expires is not None and expires <= now:
        raise LoginExpired("%s login expired - run `%s` once to refresh it" % (cli, cmd))
    return token.strip()


def claude(cfg: Dict[str, Any], now: Optional[float] = None) -> CliLogin:
    now = time.time() if now is None else now
    path = _first_file(config_mod.claude_homes(cfg), ".credentials.json")
    if path is None:
        raise LoginMissing("not signed in to Claude Code on this computer")
    try:
        blob = _read_json(path).get("claudeAiOauth")
    except LoginMissing:
        raise LoginMissing("not signed in to Claude Code on this computer")
    if not isinstance(blob, dict):
        raise LoginMissing("not signed in to Claude Code on this computer")
    exp = _ms_to_s(blob.get("expiresAt"))
    return CliLogin(_check(blob.get("accessToken"), exp, now, "Claude Code", "claude"), exp)


def codex(cfg: Dict[str, Any], now: Optional[float] = None) -> CliLogin:
    now = time.time() if now is None else now
    path = _first_file(config_mod.codex_homes(cfg), "auth.json")
    if path is None:
        raise LoginMissing("not signed in to Codex on this computer")
    try:
        tokens = _read_json(path).get("tokens")
    except LoginMissing:
        raise LoginMissing("not signed in to Codex on this computer")
    if not isinstance(tokens, dict):
        raise LoginMissing("not signed in to Codex on this computer")
    tok = _check(tokens.get("access_token"), None, now, "Codex", "codex")
    extra = {}
    if isinstance(tokens.get("account_id"), str) and tokens["account_id"]:
        extra["account_id"] = tokens["account_id"]
    return CliLogin(tok, None, extra)


def gemini(cfg: Dict[str, Any], now: Optional[float] = None) -> CliLogin:
    now = time.time() if now is None else now
    # GEMINI_CLI_HOME may point at the .gemini folder itself or replace the home directory
    path = _first_file(config_mod.gemini_homes(cfg), "oauth_creds.json", also_under=".gemini")
    if path is None:
        raise LoginMissing("not signed in to Gemini CLI on this computer")
    try:
        data = _read_json(path)
    except LoginMissing:
        raise LoginMissing("not signed in to Gemini CLI on this computer")
    exp = _ms_to_s(data.get("expiry_date"))
    return CliLogin(_check(data.get("access_token"), exp, now, "Gemini CLI", "gemini"), exp)
