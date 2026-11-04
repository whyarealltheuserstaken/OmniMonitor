import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

FIXTURES = Path(__file__).parent / "fixtures"


PROVIDER_ENV = ("ANTHROPIC_ADMIN_KEY", "OPENAI_ADMIN_KEY", "XAI_MANAGEMENT_KEY", "XAI_TEAM_ID",
                "DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "GITHUB_COPILOT_TOKEN", "GITHUB_TOKEN",
                "CURSOR_ADMIN_KEY", "OPENROUTER_API_KEY", "OLLAMA_API_KEY", "GEMINI_CLI_HOME",
                "KIMI_CODE_HOME", "COPILOT_OTEL_FILE_EXPORTER_PATH", "XDG_DATA_HOME",
                "DSH_HOME", "CLAUDE_CONFIG_DIR", "GROK_HOME")


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch, tmp_path_factory):
    """Don't let the developer's own OMNIMONITOR_* / legacy GROK_USAGE_TRAY_* vars, provider
    keys or secrets file leak in."""
    for k in list(os.environ):
        if k.startswith(("OMNIMONITOR_", "GROK_USAGE_TRAY_")) or k in PROVIDER_ENV:
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OMNIMONITOR_SECRETS_FILE",
                       str(tmp_path_factory.mktemp("secrets") / "secrets.json"))
