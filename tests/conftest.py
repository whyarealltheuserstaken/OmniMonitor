import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """Don't let the developer's own OMNIMONITOR_* / legacy GROK_USAGE_TRAY_* vars leak in."""
    for k in list(os.environ):
        if k.startswith(("OMNIMONITOR_", "GROK_USAGE_TRAY_")):
            monkeypatch.delenv(k, raising=False)
