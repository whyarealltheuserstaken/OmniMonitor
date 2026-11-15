"""Ask the official ``grok`` CLI for billing over ``grok agent stdio``.

JSON-RPC 2.0, one message per line. ``initialize`` first, then ``_x.ai/billing``,
and if that method is missing, ``x.ai/billing``. The process is killed on timeout.
The caller falls back to the cli-chat-proxy HTTP call when this fails.

Nothing from stdin/stdout is logged: the billing body can be large, and stderr
from the CLI is discarded.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from typing import Any, Optional

from omnimonitor.billing import UsageError


def fetch_agent_billing(binary: str, timeout: float = 8.0) -> str:
    """Return the billing result as a JSON string. Raises UsageError, never hangs."""
    if not binary:
        raise UsageError("grok agent stdio: CLI not found")
    timeout = max(1.0, float(timeout))
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(  # noqa: S603 - binary path, no shell
            [binary, "agent", "stdio"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            **kwargs)
    except OSError as e:
        raise UsageError("grok agent stdio failed to start (%s)" % type(e).__name__) from e
    assert proc.stdin and proc.stdout and proc.stderr
    lines: "queue.Queue[Optional[bytes]]" = queue.Queue()
    reader = threading.Thread(target=_pump, args=(proc.stdout, lines), daemon=True)
    sink = threading.Thread(target=_drain, args=(proc.stderr,), daemon=True)
    reader.start()
    sink.start()
    deadline = time.monotonic() + timeout
    try:
        _rpc(proc, lines, "initialize", {
            "protocolVersion": "1",
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False,
            },
        }, 1, deadline)
        result = None
        last_error = "billing method was not accepted"
        for index, method in enumerate(("_x.ai/billing", "x.ai/billing"), start=2):
            try:
                result = _rpc(proc, lines, method, {}, index, deadline)
                break
            except _MethodMissing as e:
                last_error = str(e) or last_error
                continue
        if not isinstance(result, dict):
            raise UsageError("grok agent stdio: %s" % last_error)
        try:
            body = json.dumps(result)
        except (TypeError, ValueError):
            raise UsageError("grok agent stdio billing result was not JSON") from None
        if len(body) > 2_000_000:
            raise UsageError("grok agent stdio billing result was too large")
        return body
    except UsageError:
        raise
    except Exception as e:  # noqa: BLE001
        raise UsageError("grok agent stdio failed (%s)" % type(e).__name__) from None
    finally:
        _kill(proc)


class _MethodMissing(UsageError):
    pass


def _rpc(proc, lines: "queue.Queue[Optional[bytes]]", method: str, params: dict,
         req_id: int, deadline: float) -> Any:
    payload = json.dumps({
        "jsonrpc": "2.0", "id": req_id, "method": method, "params": params,
    }).encode("utf-8") + b"\n"
    try:
        proc.stdin.write(payload)
        proc.stdin.flush()
    except (OSError, ValueError):
        raise UsageError("grok agent stdio closed while sending %s" % method) from None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise UsageError("grok agent stdio timed out on %s" % method)
        try:
            line = lines.get(timeout=remaining)
        except queue.Empty:
            raise UsageError("grok agent stdio timed out on %s" % method) from None
        if line is None:
            raise UsageError("grok agent stdio closed during %s" % method)
        if len(line) > 2_000_000:
            raise UsageError("grok agent stdio response was too large")
        try:
            msg = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(msg, dict) or msg.get("id") != req_id:
            continue
        if "error" in msg:
            err = msg.get("error")
            code = err.get("code") if isinstance(err, dict) else None
            text = ""
            if isinstance(err, dict) and isinstance(err.get("message"), str):
                text = err["message"][:180]
            if code == -32601 or "method not found" in text.lower():
                raise _MethodMissing(text or "method not found")
            if "authentication required" in text.lower() or "grok login" in text.lower():
                raise UsageError("grok agent stdio: run `grok login`")
            raise UsageError("grok agent stdio %s failed" % method)
        return msg.get("result")


def _pump(stream, lines) -> None:
    try:
        for line in iter(stream.readline, b""):
            lines.put(line)
    except Exception:  # noqa: BLE001
        pass
    finally:
        lines.put(None)


def _drain(stream) -> None:
    try:
        while stream.read(65536):
            pass
    except Exception:  # noqa: BLE001
        pass


def _kill(proc) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
            proc.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        try:
            if pipe:
                pipe.close()
        except OSError:
            pass
