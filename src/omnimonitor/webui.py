"""Local stats page: stdlib HTTP server bound to 127.0.0.1 only.

Security model (see SECURITY.md):
  * binds 127.0.0.1 (never 0.0.0.0), random free port by default;
  * every URL lives under /s/<random-per-run-secret>/ - other local users / web pages
    can't guess it; the secret is regenerated each run and never written to disk;
  * the Host header must be 127.0.0.1:<port> or localhost:<port> (DNS-rebinding guard);
  * read-only: GET only, no endpoint changes anything, no CORS headers;
  * strict Content-Security-Policy; the page loads nothing from the internet.
"""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any, Callable, Dict, Optional, Tuple

CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")


def _static(name: str) -> bytes:
    return resources.files("omnimonitor").joinpath("static", name).read_bytes()


class StatsServer:
    def __init__(self, get_stats: Callable[[], Dict[str, Any]], port: int = 0,
                 refresh: Optional[Callable[[], None]] = None):
        self.secret = secrets.token_urlsafe(24)
        self.get_stats = get_stats
        self.refresh = refresh
        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "omnimonitor"
            sys_version = ""

            def log_message(self, *args):  # keep quiet; never log URLs with the secret
                pass

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", CSP)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _host_ok(self) -> bool:
                host = (self.headers.get("Host") or "").lower()
                port = self.server.server_address[1]
                return host in ("127.0.0.1:%d" % port, "localhost:%d" % port)

            def do_HEAD(self):  # noqa: N802
                self.do_GET()

            def do_GET(self):  # noqa: N802
                if not self._host_ok():
                    return self._send(421, b"bad host", "text/plain")
                prefix = "/s/%s/" % outer.secret
                path = self.path.split("?", 1)[0]
                if not path.startswith(prefix):
                    return self._send(404, b"not found", "text/plain")
                rel = path[len(prefix):]
                try:
                    if rel in ("", "index.html"):
                        return self._send(200, _static("stats.html"), "text/html; charset=utf-8")
                    if rel == "stats.js":
                        return self._send(200, _static("stats.js"),
                                          "application/javascript; charset=utf-8")
                    if rel == "api/stats":
                        body = json.dumps(outer.get_stats(), default=str).encode("utf-8")
                        return self._send(200, body, "application/json")
                except Exception as e:  # noqa: BLE001
                    return self._send(500, ("error: %s" % type(e).__name__).encode(),
                                      "text/plain")
                return self._send(404, b"not found", "text/plain")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", int(port or 0)), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self._thread: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        return "http://127.0.0.1:%d/s/%s/" % (self.port, self.secret)

    def start(self) -> "StatsServer":
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="grok-usage-web",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def start_server(get_stats, port: int = 0) -> Tuple[StatsServer, str]:
    s = StatsServer(get_stats, port).start()
    return s, s.url
