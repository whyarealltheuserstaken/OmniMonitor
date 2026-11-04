"""Setup wizard back end: detect the CLIs enabled providers depend on, and install them
with each vendor's *official* installer script, one explicit click per dependency.

Safety rules (enforced here, covered by tests/test_deps.py):

* official sources only: every URL is https on an allow-listed host, and every redirect
  must stay on that dependency's allow-list;
* never ``curl | sh``: the script is downloaded to a private temp file first, its SHA256
  and the exact command are shown, and nothing runs until the user confirms *that* hash;
* the file is re-hashed right before running (it must not have changed);
* never sudo / never root: the command never contains sudo, and we refuse to run as root;
* runs as the current user with shell=False and stdin closed; results are logged.

Dependencies without an official script installer (e.g. Gemini CLI: npm/Homebrew only)
are reported with manual instructions and never executed by OmniMonitor.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("omnimonitor.deps")

MAX_SCRIPT_BYTES = 2 * 1024 * 1024
RUN_TIMEOUT_S = 900
PLAN_TTL_S = 30 * 60


@dataclass(frozen=True)
class Dep:
    id: str
    name: str
    providers: Tuple[str, ...]          # catalog ids that need it
    binaries: Tuple[str, ...]           # names looked up on PATH
    extra_dirs: Tuple[str, ...] = ()    # common install dirs (~ expanded) not always on PATH
    sh_url: str = ""                    # macOS/Linux installer
    ps1_url: str = ""                   # Windows installer
    sh_interpreter: str = "/bin/bash"
    hosts: Tuple[str, ...] = ()         # allowed hosts for the URL and any redirect
    docs: str = ""                      # official page documenting the installer
    manual: str = ""                    # shown when there is no script installer
    notes: str = ""
    extra: Dict[str, str] = field(default_factory=dict)


# Every URL below was checked on 2026-11-04 against the vendor's own README/docs (``docs``).
DEPS: Tuple[Dep, ...] = (
    Dep("grok", "Grok CLI", ("grok", "grok_sessions"), ("grok",), ("~/.grok/bin", "~/.local/bin"),
        sh_url="https://x.ai/cli/install.sh", ps1_url="https://x.ai/cli/install.ps1",
        hosts=("x.ai",), docs="https://x.ai/cli",
        notes="After installing, run `grok` once and sign in; OmniMonitor reads its login "
              "file for the weekly pool."),
    Dep("claude", "Claude Code", ("claude_code",), ("claude",), ("~/.local/bin", "~/.claude/local"),
        sh_url="https://claude.ai/install.sh", ps1_url="https://claude.ai/install.ps1",
        hosts=("claude.ai", "downloads.claude.ai"),
        docs="https://docs.anthropic.com/en/docs/claude-code/setup"),
    Dep("codex", "Codex CLI", ("codex",), ("codex",), ("~/.local/bin", "~/.codex/bin"),
        sh_url="https://chatgpt.com/codex/install.sh", ps1_url="https://chatgpt.com/codex/install.ps1",
        sh_interpreter="/bin/sh", hosts=("chatgpt.com", "releases.openai.com"),
        docs="https://github.com/openai/codex#readme"),
    Dep("gemini", "Gemini CLI", ("gemini_cli",), ("gemini",),
        docs="https://github.com/google-gemini/gemini-cli#readme",
        manual="No official script installer. Install with `npm install -g @google/gemini-cli` "
               "or `brew install gemini-cli` (OmniMonitor does not run these for you)."),
    Dep("qwen", "Qwen Code", ("qwen_code",), ("qwen",), ("~/.local/bin",),
        sh_url="https://qwen-code-assets.oss-cn-hangzhou.aliyuncs.com/installation/"
               "install-qwen-standalone.sh",
        ps1_url="https://qwen-code-assets.oss-cn-hangzhou.aliyuncs.com/installation/"
                "install-qwen-standalone.ps1",
        hosts=("qwen-code-assets.oss-cn-hangzhou.aliyuncs.com",),
        docs="https://github.com/QwenLM/qwen-code#readme"),
    Dep("kimi", "Kimi Code", ("kimi",), ("kimi", "kimi-code"), ("~/.local/bin",),
        sh_url="https://code.kimi.com/kimi-code/install.sh",
        ps1_url="https://code.kimi.com/kimi-code/install.ps1",
        hosts=("code.kimi.com", "cdn.kimi.com"), docs="https://github.com/MoonshotAI/kimi-cli#readme"),
    Dep("opencode", "opencode", ("opencode",), ("opencode",), ("~/.opencode/bin",),
        sh_url="https://opencode.ai/v2/install", hosts=("opencode.ai",),
        docs="https://github.com/anomalyco/opencode#readme",
        manual="Windows: no official script installer; use `npm i -g @opencode/cli@latest`."),
)
BY_ID = {d.id: d for d in DEPS}


def _is_windows() -> bool:
    return sys.platform == "win32"


def installer_url(dep: Dep) -> str:
    return dep.ps1_url if _is_windows() else dep.sh_url


def host_allowed(url: str, dep: Dep) -> bool:
    u = urllib.parse.urlsplit(url)
    return u.scheme == "https" and (u.hostname or "").lower() in dep.hosts and not u.username


def find_binary(dep: Dep, which: Callable[[str], Optional[str]] = shutil.which) -> Optional[str]:
    for b in dep.binaries:
        p = which(b)
        if p:
            return p
    exts = (".exe", ".cmd", "") if _is_windows() else ("",)
    for d in dep.extra_dirs:
        base = Path(os.path.expanduser(d))
        for b in dep.binaries:
            for e in exts:
                c = base / (b + e)
                if c.is_file() and os.access(str(c), os.X_OK):
                    return str(c)
    return None


def needed_deps(cfg: dict) -> List[Dep]:
    """Only dependencies of providers the user enabled (Grok pool counts when it's the source)."""
    from omnimonitor import catalog

    on = set(catalog.enabled_ids(cfg))
    if cfg.get("source", "grok_cli_billing") == "grok_cli_billing":
        on.add("grok")
    if (cfg.get("providers") or {}).get("grok_sessions", {}).get("enabled", True) is not False:
        on.add("grok_sessions")
    return [d for d in DEPS if on & set(d.providers)]


def status(cfg: dict, which: Callable[[str], Optional[str]] = shutil.which) -> Dict[str, Any]:
    rows = []
    for d in needed_deps(cfg):
        path = find_binary(d, which)
        url = installer_url(d)
        rows.append({
            "id": d.id, "name": d.name, "providers": list(d.providers),
            "installed": bool(path), "path": path,
            "installer": url or None, "docs": d.docs,
            "manual": d.manual if not url else "", "notes": d.notes,
        })
    return {"deps": rows, "platform": sys.platform, "root": _is_root(),
            "missing": sum(1 for r in rows if not r["installed"])}


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class _SameHostRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, dep: Dep):
        self.dep = dep

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not host_allowed(newurl, self.dep):
            raise urllib.error.HTTPError(newurl, code, "redirect to non-official host refused",
                                         headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_fetch(url: str, dep: Dep) -> Tuple[bytes, str]:
    from omnimonitor import __version__
    from omnimonitor.net import ssl_context

    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context()),
                                         _SameHostRedirects(dep))
    req = urllib.request.Request(url, headers={"User-Agent": "omnimonitor/" + __version__})
    with opener.open(req, timeout=30) as r:
        data = r.read(MAX_SCRIPT_BYTES + 1)
        return data, r.geturl()


@dataclass
class Plan:
    token: str
    dep: str
    url: str
    final_url: str
    path: str
    sha256: str
    size: int
    command: List[str]
    created: float
    preview: str

    def public(self) -> Dict[str, Any]:
        return {"token": self.token, "dep": self.dep, "url": self.url, "final_url": self.final_url,
                "path": self.path, "sha256": self.sha256, "size": self.size,
                "command": self.command, "command_str": " ".join(_q(c) for c in self.command),
                "preview": self.preview}


def _q(s: str) -> str:
    return s if s and all(c.isalnum() or c in "/._-:\\" for c in s) else '"%s"' % s


class Installer:
    """Two-step installer. ``prepare`` downloads + hashes; ``run`` executes a confirmed plan."""

    def __init__(self, fetch=_default_fetch, runner=subprocess.run, log_path: Optional[Path] = None,
                 is_root: Callable[[], bool] = _is_root):
        self.fetch, self.runner, self.is_root = fetch, runner, is_root
        self.log_path = log_path
        self.plans: Dict[str, Plan] = {}
        self.lock = threading.Lock()

    def prepare(self, dep_id: str) -> Dict[str, Any]:
        dep = BY_ID.get(dep_id)
        if dep is None:
            return {"ok": False, "error": "unknown dependency"}
        url = installer_url(dep)
        if not url:
            return {"ok": False, "error": "no official script installer on this OS",
                    "manual": dep.manual, "docs": dep.docs}
        if not host_allowed(url, dep):
            return {"ok": False, "error": "installer URL is not on the official allow-list"}
        try:
            data, final = self.fetch(url, dep)
        except Exception as e:  # noqa: BLE001
            self._log(dep_id, "download_failed", error=str(e)[:200])
            return {"ok": False, "error": "download failed: %s" % str(e)[:200]}
        if not host_allowed(final, dep):
            return {"ok": False, "error": "download ended on a non-official host"}
        if len(data) > MAX_SCRIPT_BYTES or not data:
            return {"ok": False, "error": "unexpected installer size"}
        ps1 = _is_windows()
        d = tempfile.mkdtemp(prefix="omnimonitor-install-")
        path = os.path.join(d, "install-%s.%s" % (dep.id, "ps1" if ps1 else "sh"))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if ps1:
            cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-File", path]
        else:
            cmd = [dep.sh_interpreter, path]
        plan = Plan(secrets.token_urlsafe(16), dep.id, url, final, path, sha256_file(path),
                    len(data), cmd, time.time(),
                    "\n".join(data.decode("utf-8", "replace").splitlines()[:12]))
        with self.lock:
            self._expire()
            self.plans[plan.token] = plan
        self._log(dep.id, "downloaded", url=final, sha256=plan.sha256, size=plan.size)
        out = plan.public()
        out["ok"] = True
        return out

    def run(self, token: str, sha256: str, dry_run: bool = False) -> Dict[str, Any]:
        with self.lock:
            self._expire()
            plan = self.plans.get(token or "")
        if plan is None:
            return {"ok": False, "error": "no prepared installer (expired?); download again"}
        if not sha256 or sha256.lower() != plan.sha256:
            return {"ok": False, "error": "SHA256 confirmation does not match"}
        try:
            now = sha256_file(plan.path)
        except OSError:
            now = ""
        if now != plan.sha256:
            self._discard(plan)
            return {"ok": False, "error": "installer file changed after download; refused"}
        if any(os.path.basename(c).lower() in ("sudo", "doas", "su") for c in plan.command):
            return {"ok": False, "error": "refused: elevated command"}
        if self.is_root():
            return {"ok": False, "error": "refused: OmniMonitor is running as root; "
                                          "installers run as your normal user only"}
        if dry_run:
            self._log(plan.dep, "dry_run", sha256=plan.sha256, command=plan.command)
            return {"ok": True, "dry_run": True, "command": plan.command, "sha256": plan.sha256}
        env = {k: v for k, v in os.environ.items() if not k.startswith("SUDO_")}
        env["CI"] = env.get("CI", "")
        t0 = time.time()
        try:
            r = self.runner(plan.command, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, timeout=RUN_TIMEOUT_S, env=env, shell=False)
            rc, out, err = r.returncode, r.stdout or "", r.stderr or ""
        except subprocess.TimeoutExpired:
            rc, out, err = -1, "", "timed out"
        except OSError as e:
            rc, out, err = -1, "", str(e)
        finally:
            self._discard(plan)
        tail = (out + "\n" + err).strip()[-4000:]
        self._log(plan.dep, "ran", rc=rc, sha256=plan.sha256, seconds=round(time.time() - t0, 1))
        dep = BY_ID[plan.dep]
        return {"ok": rc == 0, "rc": rc, "output": tail, "installed_path": find_binary(dep)}

    # ------------------------------------------------------------------ helpers
    def _discard(self, plan: Plan) -> None:
        with self.lock:
            self.plans.pop(plan.token, None)
        try:
            os.remove(plan.path)
            os.rmdir(os.path.dirname(plan.path))
        except OSError:
            pass

    def _expire(self) -> None:
        for t, p in list(self.plans.items()):
            if time.time() - p.created > PLAN_TTL_S:
                self.plans.pop(t, None)

    def _log(self, dep: str, event: str, **kw) -> None:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "dep": dep, "event": event}
        rec.update(kw)
        log.info("setup %s", json.dumps(rec))
        if self.log_path:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec) + "\n")
            except OSError:
                pass

    def history(self, limit: int = 30) -> List[Dict[str, Any]]:
        if not self.log_path or not self.log_path.exists():
            return []
        lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
        return out
