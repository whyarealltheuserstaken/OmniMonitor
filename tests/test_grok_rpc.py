"""grok agent stdio: underscore method first, plain method second, no hangs."""

import json
import time

from omnimonitor import grok_rpc
from omnimonitor.billing import UsageError


class _Proc:
    def __init__(self, respond):
        self.respond = respond
        self.stdin = self
        self.stdout = self
        self.stderr = self
        self.methods = []
        self.killed = False
        self._buf = b""
        self._lines = []
        import queue
        self._q = queue.Queue()

    def write(self, data):
        self._buf += data
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            msg = json.loads(line)
            self.methods.append(msg["method"])
            reply = self.respond(msg)
            if reply is not None:
                self._q.put(reply)

    def flush(self):
        return None

    def readline(self):
        try:
            return self._q.get(timeout=2)
        except Exception:
            return b""

    def read(self, n):
        return b""

    def poll(self):
        return -15 if self.killed else None

    def terminate(self):
        self.killed = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return -15

    def close(self):
        return None


def _ok_billing(msg):
    if msg["method"] == "initialize":
        return json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}).encode() + b"\n"
    if msg["method"] == "_x.ai/billing":
        return json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {
            "code": -32601, "message": "Method not found"}}).encode() + b"\n"
    return json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {
        "config": {"creditUsagePercent": 12, "currentPeriod": {}}}}).encode() + b"\n"


def test_tries_both_billing_method_names(monkeypatch):
    proc = _Proc(_ok_billing)
    monkeypatch.setattr(grok_rpc.subprocess, "Popen", lambda *a, **k: proc)
    body = grok_rpc.fetch_agent_billing("/usr/bin/grok", timeout=3)
    assert json.loads(body)["config"]["creditUsagePercent"] == 12
    assert proc.methods == ["initialize", "_x.ai/billing", "x.ai/billing"]
    assert proc.killed


def test_stops_when_the_underscore_method_works(monkeypatch):
    def respond(msg):
        if msg["method"] == "initialize":
            return json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}).encode() + b"\n"
        return json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "creditUsagePercent": 3, "currentPeriod": {}}}).encode() + b"\n"

    proc = _Proc(respond)
    monkeypatch.setattr(grok_rpc.subprocess, "Popen", lambda *a, **k: proc)
    body = grok_rpc.fetch_agent_billing("/usr/bin/grok", timeout=3)
    assert json.loads(body)["creditUsagePercent"] == 3
    assert proc.methods == ["initialize", "_x.ai/billing"]


def test_timeout_kills_the_process(monkeypatch):
    class Hang(_Proc):
        def write(self, data):
            return None

        def readline(self):
            for _ in range(40):
                if self.killed:
                    return b""
                time.sleep(0.05)
            return b""

    proc = Hang(lambda msg: None)
    monkeypatch.setattr(grok_rpc.subprocess, "Popen", lambda *a, **k: proc)
    try:
        grok_rpc.fetch_agent_billing("/usr/bin/grok", timeout=0.4)
    except UsageError as e:
        assert "timed out" in str(e)
    else:
        raise AssertionError("expected a timeout")
    assert proc.killed
