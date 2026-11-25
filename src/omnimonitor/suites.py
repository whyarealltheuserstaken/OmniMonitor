"""Suites: the top-level grouping of providers by vendor (Anthropic, OpenAI, Google, ...).

Pure data, like :mod:`omnimonitor.catalog`. Every catalog provider belongs to exactly one
suite, by its ``family``. Nothing here is special-cased for any one vendor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from omnimonitor import catalog
from omnimonitor.config import DEFAULT_USAGE_PAGE


@dataclass(frozen=True)
class Suite:
    id: str
    name: str
    families: Tuple[str, ...]
    usage_url: str = ""


SUITES: Tuple[Suite, ...] = (
    Suite("anthropic", "Anthropic", ("Claude",), "https://claude.ai/settings/usage"),
    Suite("openai", "OpenAI", ("ChatGPT",), "https://chatgpt.com/codex/settings/usage"),
    Suite("google", "Google", ("Gemini",), "https://aistudio.google.com/"),
    Suite("xai", "Grok & Cursor", ("Grok", "Cursor"), DEFAULT_USAGE_PAGE),
    Suite("deepseek", "DeepSeek", ("DeepSeek",), "https://platform.deepseek.com/usage"),
    Suite("qwen", "Qwen", ("Qwen",), "https://bailian.console.aliyun.com/"),
    Suite("kimi", "Kimi", ("Kimi",), "https://platform.moonshot.ai/console"),
    Suite("github", "GitHub Copilot", ("Copilot",), "https://github.com/settings/billing"),
    Suite("gateways", "API gateways",
          ("OpenRouter", "Vercel", "LiteLLM", "DeepInfra", "Fireworks", "Venice", "Ollama")),
    Suite("agents", "Coding agents",
          ("opencode", "Pi", "OpenClaw", "Block", "Nous Research", "Amp", "Factory", "Cline",
           "Roo Code", "Kilo Code")),
    Suite("other", "Other", ()),
)

SUITE_IDS: Tuple[str, ...] = tuple(s.id for s in SUITES)
TRAY_CHOICES: Tuple[str, ...] = SUITE_IDS + ("tightest", "budget")

_BY_ID: Dict[str, Suite] = {s.id: s for s in SUITES}
_BY_FAMILY: Dict[str, str] = {f: s.id for s in SUITES for f in s.families}


def get(sid: str) -> Optional[Suite]:
    return _BY_ID.get(sid)


def suite_of(pid: str) -> str:
    info = catalog.get(pid)
    return _BY_FAMILY.get(info.family, "other") if info else "other"


def members(sid: str) -> List[str]:
    return [p.id for p in catalog.CATALOG if suite_of(p.id) == sid]


def map_legacy_primary(pid: str) -> str:
    """Map an old ``primary_provider`` value (or a tray choice) to a ``tray_suite``."""
    if pid in TRAY_CHOICES:
        return pid
    if catalog.get(pid):
        return suite_of(pid)
    return "tightest"
