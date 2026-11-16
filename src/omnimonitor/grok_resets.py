"""SuperGrok usage-reset coupons from grok.com.

``POST https://grok.com/prod_mc_billing.ConsumerUiSvc/GetRemainingResets``
with the existing Grok CLI bearer token (grpc-web, empty request frame
``00 00 00 00 00``). Token identifiers in the protobuf are counted and then
discarded. They are never returned, logged, or shown.

A failed call is "not available". A parsed empty inventory is a real zero.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

ENDPOINT = "https://grok.com/prod_mc_billing.ConsumerUiSvc/GetRemainingResets"
_EMPTY_FRAME = bytes((0, 0, 0, 0, 0))


class ResetParseError(Exception):
    pass


def fetch_remaining_resets(token: str, timeout: float = 2.0, now: Optional[float] = None,
                           transport=None) -> Dict[str, Any]:
    """Return ``{count, next_expires_at, reason}``. ``count`` is None when unread.

    ``transport(url, body, timeout) -> (status, bytes)`` replaces the network call
    in tests. Redirects are not followed.
    """
    if not token or not str(token).strip():
        return {"count": None, "next_expires_at": None,
                "reason": "not available: no Grok login token for GetRemainingResets"}
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    try:
        if transport is not None:
            status, body = transport(ENDPOINT, _EMPTY_FRAME, timeout)
        else:
            status, body = _post(str(token).strip(), timeout)
    except Exception:  # noqa: BLE001 - never raise into the billing poll
        return {"count": None, "next_expires_at": None,
                "reason": "not available: GetRemainingResets request failed"}
    if status != 200:
        return {"count": None, "next_expires_at": None,
                "reason": "not available: GetRemainingResets HTTP %s" % status}
    try:
        expirations = parse_remaining_resets(body, now)
    except ResetParseError:
        return {"count": None, "next_expires_at": None,
                "reason": "not available: GetRemainingResets response could not be read"}
    nxt = expirations[0] if expirations else None
    return {"count": len(expirations), "next_expires_at": nxt, "reason": ""}


def _post(token: str, timeout: float):
    req = urllib.request.Request(ENDPOINT, data=_EMPTY_FRAME, method="POST", headers={
        "Authorization": "Bearer " + token,
        "Origin": "https://grok.com",
        "Referer": "https://grok.com/?_s=usage",
        "Accept": "*/*",
        "Content-Type": "application/grpc-web+proto",
        "x-grpc-web": "1",
        "x-user-agent": "connect-es/2.1.1",
        "User-Agent": "omnimonitor",
    })
    from omnimonitor.accounts import _NoRedirect
    from omnimonitor.net import build_opener
    try:
        with build_opener(_NoRedirect).open(req, timeout=timeout) as resp:
            return getattr(resp, "status", 200), resp.read(1_000_000)
    except urllib.error.HTTPError as e:
        return e.code, b""


def parse_remaining_resets(data: bytes, now: float) -> List[float]:
    """Unexpired reset times, soonest first. Raises ResetParseError on a bad payload.

    The returned list has no token identifiers.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise ResetParseError("not bytes")
    payloads, trailer_bad = _frames(bytes(data))
    if trailer_bad:
        raise ResetParseError("grpc status")
    if not payloads and not data:
        return []
    if not payloads:
        if _looks_like_protobuf(bytes(data)):
            payloads = [bytes(data)]
        else:
            raise ResetParseError("no frames")
    expirations: List[float] = []
    saw_record = False
    for payload in payloads:
        if not payload:
            continue
        fields = _fields(payload)
        if fields is None:
            raise ResetParseError("payload")
        for number, kind, value in fields:
            if number != 10 or kind != "bytes":
                continue
            saw_record = True
            exp = _token_expiry(value, now)
            if exp is False:
                raise ResetParseError("token")
            if isinstance(exp, float):
                expirations.append(exp)
    if not saw_record and payloads and any(payloads):
        # A decoded message with no token field is an empty inventory.
        if any(_fields(p) is None for p in payloads if p):
            raise ResetParseError("payload")
    expirations.sort()
    return expirations


def _token_expiry(blob: bytes, now: float):
    """A timestamp, False when the record is malformed, or None when it is expired."""
    fields = _fields(blob)
    if fields is None:
        return False
    token_id = ""
    expires = None
    for number, kind, value in fields:
        if kind != "bytes":
            continue
        if number == 10:
            token_id = bytes(value).decode("utf-8", "replace")
        elif number == 30:
            expires = _timestamp(value)
    if not token_id or expires is None:
        return False
    if expires <= now:
        return None
    return expires


def _timestamp(blob: bytes) -> Optional[float]:
    fields = _fields(blob)
    if not fields:
        return None
    for number, kind, value in fields:
        if number == 1 and kind == "varint" and 1_700_000_000 <= int(value) <= 2_100_000_000:
            return float(value)
    return None


def _frames(data: bytes) -> Tuple[List[bytes], bool]:
    if len(data) < 5:
        return [], False
    payloads: List[bytes] = []
    i = 0
    while i + 5 <= len(data):
        flag = data[i]
        length = int.from_bytes(data[i + 1:i + 5], "big")
        if length < 0 or i + 5 + length > len(data):
            return [], False
        chunk = data[i + 5:i + 5 + length]
        i += 5 + length
        if flag & 0x80:
            text = chunk.decode("utf-8", "replace")
            if "grpc-status:" in text and "grpc-status:0" not in text and "grpc-status: 0" not in text:
                return payloads, True
            continue
        if flag != 0:
            return [], False
        payloads.append(chunk)
    if i != len(data):
        return [], False
    return payloads, False


def _looks_like_protobuf(data: bytes) -> bool:
    return bool(data) and _fields(data) is not None


def _read_varint(buf: bytes, i: int):
    n = 0
    shift = 0
    while i < len(buf) and shift <= 63:
        b = buf[i]
        i += 1
        n |= (b & 0x7F) << shift
        if not (b & 0x80):
            return n, i
        shift += 7
    return None


def _fields(buf: bytes):
    i = 0
    out = []
    while i < len(buf):
        got = _read_varint(buf, i)
        if got is None:
            return None
        tag, i = got
        number, wire = tag >> 3, tag & 7
        if number <= 0:
            return None
        if wire == 0:
            got = _read_varint(buf, i)
            if got is None:
                return None
            val, i = got
            out.append((number, "varint", val))
        elif wire == 2:
            got = _read_varint(buf, i)
            if got is None:
                return None
            length, i = got
            if length < 0 or i + length > len(buf):
                return None
            out.append((number, "bytes", buf[i:i + length]))
            i += length
        elif wire == 5:
            if i + 4 > len(buf):
                return None
            i += 4
        elif wire == 1:
            if i + 8 > len(buf):
                return None
            i += 8
        else:
            return None
    return out
