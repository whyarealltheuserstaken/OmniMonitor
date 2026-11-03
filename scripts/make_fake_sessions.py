#!/usr/bin/env python3
"""Write SYNTHETIC Grok CLI session logs into a throwaway GROK_HOME (for local testing).

    python scripts/make_fake_sessions.py /tmp/fake-grok-home --days 120
"""

import argparse
import json
import random
import time
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("grok_home")
    ap.add_argument("--days", type=int, default=120)
    a = ap.parse_args()
    rng = random.Random(1)
    now = time.time()
    root = Path(a.grok_home) / "sessions" / "%2Ftmp%2Fproject"
    n = 0
    for d in range(a.days):
        if rng.random() < 0.4 and d > 3:
            continue
        sess = root / ("synthetic-%03d" % d)
        sess.mkdir(parents=True, exist_ok=True)
        t = now - d * 86400 - rng.uniform(0, 8 * 3600)
        with (sess / "updates.jsonl").open("w") as f:
            for turn in range(rng.randint(1, 6)):
                f.write(json.dumps({"params": {"update": {"sessionUpdate": "agent_message_chunk"},
                                               "_meta": {"agentTimestampMs": int(t * 1000),
                                                         "eventId": "c%d-%d" % (d, turn)}}}) + "\n")
                t += rng.uniform(5, 600)
                inp = rng.randint(2000, 200000)
                f.write(json.dumps({"params": {"update": {
                    "sessionUpdate": "turn_completed",
                    "usage": {"modelUsage": {rng.choice(["grok-build", "grok-code-fast"]): {
                        "inputTokens": inp, "cachedReadTokens": int(inp * 0.8),
                        "outputTokens": rng.randint(100, 8000)}}}},
                    "_meta": {"agentTimestampMs": int(t * 1000),
                              "eventId": "t%d-%d" % (d, turn)}}}) + "\n")
                n += 1
                t += rng.uniform(30, 900)
    print("wrote %d synthetic turns under %s" % (n, root))


if __name__ == "__main__":
    main()
