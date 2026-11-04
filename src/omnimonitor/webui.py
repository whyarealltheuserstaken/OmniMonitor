"""Local stats page: stdlib HTTP server bound to 127.0.0.1 only.

Security model (see SECURITY.md):
  * binds 127.0.0.1 (never 0.0.0.0), random free port by default;
  * every URL lives under /s/<random-per-run-secret>/ - other local users / web pages
    can't guess it; the secret is regenerated each run and never written to disk;
  * the Host header must be 127.0.0.1:<port> or localhost:<port> (DNS-rebinding guard);
  * no CORS headers; strict Content-Security-Policy; nothing loads from the internet;
  * GET/HEAD are read-only. The ONLY write is POST api/settings, which additionally needs
    a JSON body, a per-run CSRF token in the X-OmniMonitor-CSRF header (handed out only via
    the same-origin api/settings response), an Origin (if sent) of this server, and it can
    only change allow-listed, validated keys (settings.py) - never tokens, commands, URLs
    or file paths.
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any, Callable, Dict, Optional, Tuple

MAX_POST_BYTES = 64 * 1024
CSRF_HEADER = "X-OmniMonitor-CSRF"
PAGES = {"": "stats.html", "index.html": "stats.html", "settings": "settings.html",
         "mini": "mini.html", "setup": "setup.html"}
SCRIPTS = ("stats.js", "settings.js", "mini.js", "common.js", "providers.js", "app.js", "setup.js")
EXPORT_KINDS = ("daily", "daily-models", "breakdown", "events", "providers")

CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")


def _static(name: str) -> bytes:
    return resources.files("omnimonitor").joinpath("static", name).read_bytes()


class StatsServer:
    def __init__(self, get_stats: Callable[[], Dict[str, Any]], port: int = 0,
                 refresh: Optional[Callable[[], None]] = None,
                 get_settings: Optional[Callable[[], Dict[str, Any]]] = None,
                 save_settings: Optional[Callable[[Any], Dict[str, Any]]] = None,
                 export: Optional[Callable[[str, str], Tuple[bytes, str, str]]] = None,
                 setup: Optional[Dict[str, Callable[..., Dict[str, Any]]]] = None):
        self.secret = secrets.token_urlsafe(24)
        self.csrf = secrets.token_urlsafe(24)
        self.get_stats = get_stats
        self.refresh = refresh
        self.get_settings = get_settings
        self.save_settings = save_settings
        self.export = export
        # setup wizard: {"status": fn(), "prepare": fn(body), "run": fn(body)}
        self.setup = setup or {}
        # set once a page's script has fetched api/stats (used by the window smoke test)
        self.stats_served = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "omnimonitor"
            sys_version = ""

            def log_message(self, *args):  # keep quiet; never log URLs with the secret
                pass

            def _send(self, code: int, body: bytes, ctype: str,
                      extra: Optional[Dict[str, str]] = None) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
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

            def _json(self, code: int, obj: Any) -> None:
                self._send(code, json.dumps(obj, default=str).encode("utf-8"),
                           "application/json")

            def _rel(self) -> Optional[str]:
                prefix = "/s/%s/" % outer.secret
                path = self.path.split("?", 1)[0]
                if not path.startswith(prefix):
                    return None
                return path[len(prefix):]

            def do_POST(self):  # noqa: N802
                if not self._host_ok():
                    return self._send(421, b"bad host", "text/plain")
                rel = self._rel()
                posts = {"api/settings": outer.save_settings,
                         "api/setup/prepare": outer.setup.get("prepare"),
                         "api/setup/run": outer.setup.get("run")}
                handler = posts.get(rel or "")
                if handler is None:
                    return self._send(404, b"not found", "text/plain")
                port = self.server.server_address[1]
                origin = self.headers.get("Origin")
                if origin is not None and origin.lower() not in (
                        "http://127.0.0.1:%d" % port, "http://localhost:%d" % port):
                    return self._send(403, b"bad origin", "text/plain")
                if not hmac.compare_digest(self.headers.get(CSRF_HEADER) or "", outer.csrf):
                    return self._send(403, b"missing or wrong CSRF token", "text/plain")
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype != "application/json":
                    return self._send(415, b"JSON only", "text/plain")
                try:
                    n = int(self.headers.get("Content-Length") or "0")
                except ValueError:
                    n = -1
                if n <= 0 or n > MAX_POST_BYTES:
                    return self._send(413 if n > 0 else 411, b"bad length", "text/plain")
                try:
                    body = json.loads(self.rfile.read(n).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    return self._json(400, {"ok": False, "errors": {"_": "invalid JSON"}})
                try:
                    res = handler(body)
                except Exception as e:  # noqa: BLE001
                    return self._json(500, {"ok": False, "errors": {"_": type(e).__name__}})
                if rel != "api/settings":
                    return self._json(200, res)
                return self._json(200 if res.get("ok") else 400, res)

            def _export(self, fmt: str):
                if outer.export is None:
                    return self._send(404, b"not found", "text/plain")
                q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path
                                          else "")
                kind = (q.get("kind") or ["daily"])[0]
                if kind not in EXPORT_KINDS:
                    return self._send(400, b"unknown kind", "text/plain")
                body, ctype, fname = outer.export(kind, fmt)
                return self._send(200, body, ctype, {
                    "Content-Disposition": 'attachment; filename="%s"' % fname})

            def do_HEAD(self):  # noqa: N802
                self.do_GET()

            def do_GET(self):  # noqa: N802
                if not self._host_ok():
                    return self._send(421, b"bad host", "text/plain")
                rel = self._rel()
                if rel is None:
                    return self._send(404, b"not found", "text/plain")
                try:
                    if rel in PAGES:
                        return self._send(200, _static(PAGES[rel]), "text/html; charset=utf-8")
                    if rel in SCRIPTS:
                        return self._send(200, _static(rel),
                                          "application/javascript; charset=utf-8")
                    if rel == "api/stats":
                        body = json.dumps(outer.get_stats(), default=str).encode("utf-8")
                        outer.stats_served.set()
                        return self._send(200, body, "application/json")
                    if rel == "api/setup" and outer.setup.get("status"):
                        view = dict(outer.setup["status"]())
                        view["_csrf"] = outer.csrf
                        return self._json(200, view)
                    if rel == "api/settings" and outer.get_settings is not None:
                        view = dict(outer.get_settings())
                        view["_csrf"] = outer.csrf
                        return self._json(200, view)
                    if rel == "api/export.csv":
                        return self._export("csv")
                    if rel == "api/export.json":
                        return self._export("json")
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
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="omnimonitor-web",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def for_controller(ctrl, port: int = 0) -> StatsServer:
    """Server wired to a Controller: stats, settings page and export."""
    from omnimonitor import settings as settings_mod

    return StatsServer(ctrl.stats, port, refresh=ctrl.poke,
                       get_settings=lambda: settings_mod.public_view(ctrl.cfg),
                       save_settings=ctrl.update_settings, export=ctrl.export,
                       setup={"status": ctrl.setup_status, "prepare": ctrl.setup_prepare,
                              "run": ctrl.setup_run})


def start_server(get_stats, port: int = 0) -> Tuple[StatsServer, str]:
    s = StatsServer(get_stats, port).start()
    return s, s.url
