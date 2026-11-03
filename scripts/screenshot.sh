#!/usr/bin/env bash
# Render the stats page (DEMO data) headlessly into docs/screenshot-stats.png.
# Needs google-chrome or chromium. Usage: scripts/screenshot.sh [python]
set -euo pipefail
PY="${1:-python3}"
cd "$(dirname "$0")/.."
TMP="$(mktemp -d)"
OMNIMONITOR_HOME="$TMP" PYTHONPATH=src "$PY" -m omnimonitor serve --demo > "$TMP/url" &
PID=$!
trap 'kill $PID 2>/dev/null; rm -rf "$TMP"' EXIT
for _ in $(seq 50); do [ -s "$TMP/url" ] && break; sleep 0.2; done
URL="$(head -1 "$TMP/url")"
CHROME="$(command -v google-chrome || command -v chromium || command -v chromium-browser)"
sleep 1
"$CHROME" --headless=new --disable-gpu --no-sandbox --hide-scrollbars \
  --window-size=1100,1180 --virtual-time-budget=4000 \
  --screenshot="$PWD/docs/screenshot-stats.png" "$URL" 2>/dev/null
echo "saved docs/screenshot-stats.png"
