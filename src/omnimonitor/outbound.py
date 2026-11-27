"""Optional second delivery for alerts: a webhook you own (ntfy, Discord, Slack or JSON).

Off until you save a URL with ``omnimonitor secrets set OMNIMONITOR_ALERT_WEBHOOK`` (or set
that environment variable). The URL is treated as a secret, since Discord and Slack URLs
carry their own token: it is stored like an API key, is never accepted from the web UI and
never appears in a message or log. Only an alert's title and text are sent. HTTPS only, no
redirects.

Formats are picked from the address: ``discord.com/api/webhooks/…``, ``hooks.slack.com``,
``ntfy.sh`` (``ntfy+https://…`` for your own ntfy server); anything else gets JSON
``{"app", "title", "body"}``.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

from omnimonitor import net

NAME = "OMNIMONITOR_ALERT_WEBHOOK"
TIMEOUT = 10
NTFY_PREFIX = "ntfy+"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # noqa: D401 - never follow redirects
        return None


def configured_url() -> str:
    """The saved webhook URL, or "". Never log or display it."""
    from omnimonitor import apikeys
    return apikeys.get(NAME)


def _request(url: str, title: str, body: str) -> Optional[urllib.request.Request]:
    """The POST for this address, or None when the address isn't a plain HTTPS URL."""
    ntfy = url.startswith(NTFY_PREFIX)
    if ntfy:
        url = url[len(NTFY_PREFIX):]
    try:
        u = urllib.parse.urlsplit(url)
        host = (u.hostname or "").lower()
        _ = u.port
    except ValueError:
        return None
    if u.scheme != "https" or not host or u.username or u.password:
        return None
    headers: Dict[str, str] = {"User-Agent": "OmniMonitor", "Content-Type": "application/json"}
    payload: Any
    if ntfy or host == "ntfy.sh":
        # Header values: one line of ASCII.
        headers["Title"] = " ".join(title.split()).encode("ascii", "replace").decode("ascii")
        headers["Content-Type"] = "text/plain; charset=utf-8"
        data = body.encode("utf-8")
    else:
        if host in ("discord.com", "discordapp.com") and u.path.startswith("/api/webhooks/"):
            payload = {"content": ("**%s**\n%s" % (title, body))[:1900],
                       "allowed_mentions": {"parse": []}}  # a name can't ping anyone
        elif host == "hooks.slack.com":
            text = "%s\n%s" % (title, body)
            for a, b in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;")):
                text = text.replace(a, b)
            payload = {"text": text}
        else:
            payload = {"app": "OmniMonitor", "title": title, "body": body}
        data = json.dumps(payload).encode("utf-8")
    try:
        return urllib.request.Request(url, data=data, method="POST", headers=headers)
    except ValueError:
        return None


def send(title: str, body: str, url: Optional[str] = None, opener: Any = None
         ) -> Tuple[bool, str]:
    """-> (sent, why not). Never raises; the reason never contains the URL."""
    try:
        url = configured_url() if url is None else url
        if not url:
            return False, "no webhook configured"
        req = _request(url.strip(), title, body)
        if req is None:
            return False, "the webhook must be a plain https:// address"
        opener = opener or net.build_opener(_NoRedirect)
        with opener.open(req, timeout=TIMEOUT) as response:
            status = response.status
    except urllib.error.HTTPError as e:
        return False, "webhook answered HTTP %d" % e.code
    except Exception as e:  # noqa: BLE001 - error text can repeat the URL
        return False, "could not send (%s)" % type(e).__name__
    return (True, "sent") if 200 <= status < 300 else (False, "webhook answered HTTP %s" % status)
