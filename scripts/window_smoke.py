"""CI: run the native-window smoke test with a time limit.

GitHub's Windows runners sometimes never finish starting WebView2 (no page is requested).
That case, and only that one, gets a single retry.

    python scripts/window_smoke.py --view mini
"""
from __future__ import annotations

import subprocess
import sys

TIMEOUT_S = 240


def _run(cmd, timeout):
    try:
        p = subprocess.run(cmd, timeout=timeout, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, errors="replace")
    except subprocess.TimeoutExpired:
        return 1, "window smoke timed out after %d s" % timeout
    return p.returncode, p.stdout or ""


def main(argv, run=_run, platform=sys.platform) -> int:
    cmd = [sys.executable, "-m", "omnimonitor", "window", "--smoke"] + list(argv)
    for attempt in (1, 2):
        code, out = run(cmd, TIMEOUT_S)
        print(out, flush=True)
        if code == 0:
            return 0
        if attempt == 2 or platform != "win32" or "'served': False" not in out:
            break
        print("::warning::WebView2 did not start; retrying the window smoke once", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
