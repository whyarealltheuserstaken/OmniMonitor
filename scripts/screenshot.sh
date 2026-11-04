#!/usr/bin/env bash
# Render the web pages (DEMO data) into docs/*.png.
# Uses scripts/screenshot.py (Playwright). Usage: scripts/screenshot.sh [python-with-playwright]
# Set CHROME=/usr/bin/google-chrome to reuse an installed Chrome instead of Playwright's.
set -euo pipefail
PY="${1:-python3}"
cd "$(dirname "$0")/.."
exec "$PY" scripts/screenshot.py
