#!/usr/bin/env python3
"""Local MOCK of the Grok billing endpoint, for development and tests only.

    python scripts/mock_billing_server.py --port 8765 --used 42.5
    # then in config.json:
    #   "source": "grok_cli_billing",
    #   "grok_billing_url": "http://127.0.0.1:8765/v1/billing?format=credits",
    #   "token": "mock"

Accepts any bearer token. Never contacts the internet. Supports --used values like 0
(field omitted, as the real API does), 100, 137 (over the pool) and --status 412.
"""

import argparse
import json
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer


def payload(used: float):
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0,
                                                          microsecond=0)
    end = start + timedelta(days=7)
    cfg = {"currentPeriod": {"type": "USAGE_PERIOD_TYPE_WEEKLY",
                             "start": start.isoformat().replace("+00:00", "Z"),
                             "end": end.isoformat().replace("+00:00", "Z")},
           "productUsage": [{"product": "GrokBuild"}, {"product": "GrokChat"}],
           "isUnifiedBillingUser": True}
    if used:  # proto-JSON omits zero values
        cfg["creditUsagePercent"] = used
        cfg["productUsage"][0]["usagePercent"] = round(used * 0.7, 2)
        cfg["productUsage"][1]["usagePercent"] = round(used * 0.3, 2)
    return {"config": cfg}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--used", type=float, default=42.5)
    ap.add_argument("--status", type=int, default=200)
    a = ap.parse_args()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if not self.headers.get("Authorization", "").startswith("Bearer "):
                self.send_response(401)
                self.end_headers()
                return
            if a.status == 412:
                body = json.dumps({"error": "No personal team."}).encode()
            else:
                body = json.dumps(payload(a.used)).encode()
            self.send_response(a.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", a.port), H)
    print("mock billing on http://127.0.0.1:%d/v1/billing?format=credits" % srv.server_address[1],
          flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
