"""Render the web pages (DEMO data) into docs/*.png with Playwright.

Dev-only helper. Needs `pip install playwright` and either `playwright install chromium`
or an existing Chrome (set CHROME=/path/to/chrome). Usage:
    python scripts/screenshot.py
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SHOTS = [  # (page, file, width, height)
    ("", "screenshot-stats.png", 1100, int(os.environ.get("STATS_HEIGHT", "2300"))),
    ("mini", "screenshot-mini.png", 420, 560),
    ("settings", "screenshot-settings.png", 1000, 2300),
]


def main() -> int:
    tmp = tempfile.mkdtemp()
    env = dict(os.environ, OMNIMONITOR_HOME=tmp, PYTHONPATH=str(ROOT / "src"))
    py = os.environ.get("OMNIMONITOR_PYTHON", str(ROOT / ".venv/bin/python"))
    if not Path(py).exists():
        py = sys.executable
    srv = subprocess.Popen([py, "-m", "omnimonitor", "serve", "--demo"], env=env,
                           stdout=subprocess.PIPE, text=True)
    try:
        url = srv.stdout.readline().strip()
        if not url.startswith("http://127.0.0.1"):
            print("server did not start", file=sys.stderr)
            return 1
        time.sleep(1)
        with sync_playwright() as p:
            kw = {"executable_path": os.environ["CHROME"]} if os.environ.get("CHROME") else {}
            browser = p.chromium.launch(args=["--no-sandbox"], **kw)
            for page, name, w, h in SHOTS:
                pg = browser.new_page(viewport={"width": w, "height": h})
                pg.goto(url + page, wait_until="networkidle")
                pg.wait_for_timeout(1500)
                if page == "":  # show the multi-tool breakdown rather than the default tab
                    btn = pg.locator('button[data-range="30d"]')
                    if btn.count():
                        btn.first.click()
                        pg.wait_for_timeout(300)
                pg.screenshot(path=str(ROOT / "docs" / name), full_page=(page == ""))
                pg.close()
                print("saved docs/" + name)
            browser.close()
    finally:
        srv.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
