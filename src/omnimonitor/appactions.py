"""Native actions the app window asks the tray process to do (WKWebView/WebView2 safe).

Embedded web views ignore ``<a download>``, send ``target=_blank`` / ``window.open`` to the
system browser (or nowhere) and often block clipboard access. So the page POSTs small,
CSRF-protected requests (webui ``api/app/<action>``) and the tray process does the work:
save an export to ~/Downloads and reveal it, open an https link in the browser, build a
secret-free diagnostics report, and open/focus the native full or mini window.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from omnimonitor import __version__

EXPORT_FORMATS = ("csv", "json")


def reveal_path(path: str, popen: Callable = subprocess.Popen) -> None:
    """Show a saved file in Finder / Explorer (Linux: open its folder). Never via a shell."""
    try:
        if sys.platform == "darwin":
            popen(["open", "-R", path])
        elif sys.platform == "win32":
            popen(["explorer", "/select,", path])
        else:
            popen(["xdg-open", str(Path(path).parent)])
    except Exception:  # noqa: BLE001
        pass


def downloads_dir() -> Path:
    d = Path.home() / "Downloads"
    return d if d.is_dir() else Path.home()


def _unique(folder: Path, name: str) -> Path:
    out = folder / name
    stem, suf, i = out.stem, out.suffix, 1
    while out.exists():
        out = folder / ("%s-%d%s" % (stem, i, suf))
        i += 1
    return out


def save_export(ctrl, kind: str, fmt: str, folder: Optional[Path] = None,
                reveal: Callable[[str], None] = reveal_path) -> Dict[str, Any]:
    from omnimonitor.webui import EXPORT_KINDS
    if kind not in EXPORT_KINDS or fmt not in EXPORT_FORMATS:
        return {"ok": False, "error": "unknown export"}
    body, _ct, fname = ctrl.export(kind, fmt)
    out = _unique(folder or downloads_dir(), os.path.basename(fname))
    out.write_bytes(body)
    reveal(str(out))
    return {"ok": True, "path": str(out), "bytes": len(body)}


def safe_external_url(url: Any) -> bool:
    return isinstance(url, str) and len(url) < 2048 and bool(
        re.match(r"^https://[A-Za-z0-9.\-]+(:\d+)?(/[^\s\"'<>]*)?$", url))


def open_external(body: Any, browser: Callable[[str], Any] = webbrowser.open) -> Dict[str, Any]:
    url = (body or {}).get("url") if isinstance(body, dict) else None
    if not safe_external_url(url):
        return {"ok": False, "error": "only https:// links can be opened"}
    browser(url)
    return {"ok": True}


_SECRETISH = re.compile(r"(?i)\b(?:xai-|sk-|sk_|ghp_|gho_|eyJ)[A-Za-z0-9_\-.]{8,}|"
                        r"(token|secret|password|api[_-]?key)(\"?\s*[:=]\s*\"?)[^\s\",]+")
_URL_SECRET = re.compile(r"/s/[A-Za-z0-9_\-]{16,}/")


def redact(text: str) -> str:
    text = _URL_SECRET.sub("/s/<secret>/", text)
    text = _SECRETISH.sub(lambda m: (m.group(1) + m.group(2) + "<redacted>") if m.group(1)
                          else "<redacted>", text)
    home = str(Path.home())
    return text.replace(home, "~") if len(home) > 1 else text


def diagnostics(ctrl, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Plain-text report for bug reports. No tokens, keys, URLs secrets or cookies."""
    from omnimonitor import doctor
    lines = ["OmniMonitor %s" % __version__,
             "Python %s on %s (%s)" % (platform.python_version(), platform.platform(),
                                       platform.machine()),
             "frozen build: %s" % bool(getattr(sys, "frozen", False))]
    try:
        st = ctrl.status_dict()
        st.pop("usage_page_url", None)
        lines.append("status: " + json.dumps(st, default=str, sort_keys=True))
    except Exception as e:  # noqa: BLE001
        lines.append("status: unavailable (%s)" % type(e).__name__)
    if getattr(ctrl, "token_note", ""):
        lines.append("token note: %s" % ctrl.token_note)
    for k, v in (extra or {}).items():
        lines.append("%s: %s" % (k, v))
    lines.append("")
    lines.append("doctor (offline checks):")
    try:
        for status, name, detail in doctor.checks(ctrl.cfg, online=False):
            lines.append("  [%s] %s: %s" % (status, name, detail))
    except Exception as e:  # noqa: BLE001
        lines.append("  doctor failed: %s" % type(e).__name__)
    text = redact("\n".join(lines) + "\n")
    return {"ok": True, "text": text}


def save_diagnostics(ctrl, folder: Optional[Path] = None,
                     reveal: Callable[[str], None] = reveal_path,
                     extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    d = diagnostics(ctrl, extra)
    out = _unique(folder or downloads_dir(), "omnimonitor-diagnostics.txt")
    out.write_text(d["text"], encoding="utf-8")
    reveal(str(out))
    return {"ok": True, "path": str(out), "text": d["text"]}


def window_actions(launcher, base_url: Callable[[], str], cfg_get: Callable[[str], Any],
                   browser: Callable[[str], Any] = webbrowser.open
                   ) -> Dict[str, Callable[[Any], Dict[str, Any]]]:
    """Tray-only actions: open/focus the native full or mini window, or (explicitly) the
    browser. Registered on the stats server by the tray process."""
    from omnimonitor.window import app_url

    def _page(body) -> str:
        page = str((body or {}).get("page") or "") if isinstance(body, dict) else ""
        return page if re.match(r"^[a-z\-]{0,20}$", page) else ""

    def window(body):
        view = (body or {}).get("view") if isinstance(body, dict) else None
        view = "mini" if view == "mini" else "full"
        mode = launcher.open(app_url(base_url(), view, _page(body)), view=view,
                             on_top=bool(view == "mini" and cfg_get("mini_on_top")))
        return {"ok": True, "view": view, "mode": mode}

    def open_browser(body):
        browser(base_url() + _page(body))
        return {"ok": True}
    return {"window": window, "browser": open_browser}
