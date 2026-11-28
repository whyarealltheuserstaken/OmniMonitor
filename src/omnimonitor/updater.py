"""In-app updater.

Security model: a release can only be installed when its ``update.json`` carries a signature
from a key in ``update_key.PUBLIC_KEYS`` (the private key stays with the publisher, not on
GitHub). The signed text names the version and the download's size and SHA-256, so an altered
file, a re-tagged old release or a downgrade is refused. Addresses are built here from the
version number, never taken from a server response, and only HTTPS GitHub hosts are used.
Nothing installs without a click. One-click install is for the frozen Windows app; everything
else gets a notice with a link.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, NamedTuple, Optional, Tuple

from omnimonitor import __version__, net
from omnimonitor.update_key import PUBLIC_KEYS

log = logging.getLogger("omnimonitor.updater")

REPO_URL = "https://github.com/whyarealltheuserstaken/OmniMonitor"
API_URL = "https://api.github.com/repos/whyarealltheuserstaken/OmniMonitor/releases?per_page=30"
MANIFEST = "update.json"
WINDOWS_ASSET = "omnimonitor-windows-x86_64.zip"
APP_FILE = "omnimonitor.exe"
SIGN_PREFIX = b"omnimonitor-update-v1\n"
HOSTS = frozenset({"api.github.com", "github.com", "objects.githubusercontent.com",
                   "release-assets.githubusercontent.com"})
CHECK_EVERY_S = 24 * 3600
TIMEOUT = 30
MAX_LISTING = 5_000_000
MAX_MANIFEST = 64_000
MAX_ASSET = 150_000_000
MAX_APP = 300_000_000
_VERSION = re.compile(r"(\d{1,5})\.(\d{1,5})\.(\d{1,5})(-beta(?:\.(\d{1,5}))?)?")


class UpdateError(Exception):
    pass


class Release(NamedTuple):
    version: str
    page: str          # release notes / download page
    url: str           # Windows download ("" when this is only a notice)
    size: int
    sha256: str
    installable: bool


# ----------------------------------------------------------------------------- versions
def parse_version(text: str) -> Optional[Tuple[int, int, int, int, int]]:
    """Sortable form of X.Y.Z, X.Y.Z-beta or X.Y.Z-beta.N (a beta sorts before its release)."""
    m = _VERSION.fullmatch(text or "")
    if not m:
        return None
    major, minor, patch = (int(m.group(i)) for i in (1, 2, 3))
    if m.group(4):
        return major, minor, patch, 0, int(m.group(5) or 1)
    return major, minor, patch, 1, 0


def is_beta(version: str) -> bool:
    return "-beta" in version


def channel(cfg: Dict[str, Any], current: str = __version__) -> str:
    """"stable" or "beta"; unset follows the installed build."""
    chosen = (cfg or {}).get("update_channel")
    if chosen in ("stable", "beta"):
        return chosen
    return "beta" if is_beta(current) else "stable"


# ----------------------------------------------------------------------------- network
def allowed_url(url: str) -> bool:
    try:
        u = urllib.parse.urlsplit(url)
        port = u.port
    except ValueError:
        return False
    return (u.scheme == "https" and u.hostname in HOSTS and port in (None, 443)
            and not u.username and not u.password)


class _GitHubOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_url(newurl):
            return None  # urllib then raises HTTPError for the 3xx
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_fetch(url: str, limit: int) -> bytes:
    if not allowed_url(url):
        raise UpdateError("address not allowed")
    try:
        opener = net.build_opener(_GitHubOnly)
        req = urllib.request.Request(url, headers={"User-Agent": "OmniMonitor"})
        with opener.open(req, timeout=TIMEOUT) as response:
            data = response.read(limit + 1)
    except Exception as e:  # noqa: BLE001
        raise UpdateError("could not reach GitHub (%s)" % type(e).__name__) from None
    if len(data) > limit:
        raise UpdateError("response too large")
    return data


def asset_url(version: str, name: str) -> str:
    return "%s/releases/download/v%s/%s" % (REPO_URL, version, name)


# ----------------------------------------------------------------------------- signature
def build_manifest(version: str, files: Dict[str, bytes],
                   sign: Callable[[bytes], bytes]) -> bytes:
    """update.json for these files; used by scripts/sign_release.py."""
    payload = json.dumps({"version": version, "assets": {
        name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(files.items())}}, sort_keys=True)
    signature = base64.b64encode(sign(SIGN_PREFIX + payload.encode("utf-8"))).decode("ascii")
    return json.dumps({"payload": payload, "signature": signature}).encode("utf-8")


def verify_manifest(raw: bytes, keys: Tuple[str, ...] = PUBLIC_KEYS) -> Dict[str, Any]:
    """The signed payload, or UpdateError. Nothing is read from it before the signature."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        doc = json.loads(raw.decode("utf-8"))
        payload = doc["payload"].encode("utf-8")
        signature = base64.b64decode(doc["signature"], validate=True)
        for key in keys:
            try:
                Ed25519PublicKey.from_public_bytes(base64.b64decode(key)).verify(
                    signature, SIGN_PREFIX + payload)
            except InvalidSignature:
                continue
            out = json.loads(payload.decode("utf-8"))
            if isinstance(out, dict):
                return out
    except Exception:  # noqa: BLE001 - any malformed input is just "not signed"
        pass
    raise UpdateError("bad signature")


def _signed_release(version: str, fetch, keys) -> Release:
    manifest = verify_manifest(fetch(asset_url(version, MANIFEST), MAX_MANIFEST), keys)
    try:
        asset = manifest["assets"][WINDOWS_ASSET]
        size, digest = asset["size"], asset["sha256"]
    except (KeyError, TypeError):
        raise UpdateError("signed file lists no Windows download") from None
    if manifest.get("version") != version:
        raise UpdateError("signature is for another version")
    if not (isinstance(size, int) and 0 < size <= MAX_ASSET and isinstance(digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", digest)):
        raise UpdateError("signed file is malformed")
    return Release(version, "%s/releases/tag/v%s" % (REPO_URL, version),
                   asset_url(version, WINDOWS_ASSET), size, digest, True)


def can_self_update(platform: str = sys.platform, frozen: Optional[bool] = None,
                    executable: str = sys.executable) -> bool:
    """Only the downloaded Windows app, in a folder it can write to."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    folder = os.path.dirname(os.path.abspath(executable))
    return platform == "win32" and frozen and os.path.isdir(folder) \
        and os.access(folder, os.W_OK)


def check(current: str = __version__, chan: str = "stable", fetch=http_fetch,
          keys: Tuple[str, ...] = PUBLIC_KEYS, installable: Optional[bool] = None
          ) -> Optional[Release]:
    """The newest release to offer, or None when up to date. Raises UpdateError."""
    here = parse_version(current)
    try:
        listing = json.loads(fetch(API_URL, MAX_LISTING).decode("utf-8"))
        if not isinstance(listing, list):
            raise ValueError("not a list")
        rows =[r for r in listing if isinstance(r, dict) and not r.get("draft")]
    except (ValueError, TypeError):
        raise UpdateError("unreadable release list") from None
    newer = []
    for row in rows:
        tag = row.get("tag_name")
        version = tag[1:] if isinstance(tag, str) and tag.startswith("v") else ""
        order = parse_version(version)
        if not order or not here or order <= here:
            continue
        if bool(row.get("prerelease")) != is_beta(version):
            continue
        if chan != "beta" and is_beta(version):
            continue
        newer.append((order, version))
    if not newer:
        return None
    newer.sort(reverse=True)
    installable = can_self_update() if installable is None else installable
    if not (installable and keys):
        version = newer[0][1]
        return Release(version, "%s/releases/tag/v%s" % (REPO_URL, version), "", 0, "", False)
    for _order, version in newer[:5]:
        try:
            return _signed_release(version, fetch, keys)
        except UpdateError as e:
            log.warning("update %s skipped: %s", version, e)
    raise UpdateError("a newer version exists, but its signature could not be verified")


# ----------------------------------------------------------------------------- install
def download(release: Release, dest: Path, fetch=http_fetch) -> Path:
    """Write the app from the verified zip to ``dest``. Raises before writing on any mismatch."""
    data = fetch(release.url, release.size)
    if len(data) != release.size or hashlib.sha256(data).hexdigest() != release.sha256:
        raise UpdateError("the download does not match the signed release")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if z.namelist() != [APP_FILE] or z.infolist()[0].file_size > MAX_APP:
                raise UpdateError("unexpected files in the download")
            app = z.read(APP_FILE)
        Path(dest).write_bytes(app)
    except (zipfile.BadZipFile, OSError) as e:
        raise UpdateError("could not unpack the update (%s)" % type(e).__name__) from None
    return Path(dest)


def _sibling(exe: Path, suffix: str) -> Path:
    return Path(str(exe) + suffix)


def install(new: Path, exe: Path, version: str, run=subprocess.run,
            popen=subprocess.Popen) -> None:
    """Self-test ``new``, swap it in for ``exe`` and start it. On any failure the old app
    stays (or is put back) and UpdateError is raised. The caller then quits."""
    new, exe = Path(new), Path(exe)
    old = _sibling(exe, ".old")
    # A one-file build must unpack itself again, not reuse this process's temp folder.
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    try:
        try:
            result = run([str(new), "--version"], capture_output=True, text=True,
                         timeout=120, env=env)
            works = result.returncode == 0 \
                and result.stdout.strip() == "omnimonitor %s" % version
        except (OSError, subprocess.SubprocessError):
            works = False
        if not works:
            raise UpdateError("the new version did not pass its self-test")
        try:
            if old.exists():
                old.unlink()
            os.replace(exe, old)
        except OSError as e:
            raise UpdateError("could not replace the app (%s)" % type(e).__name__) from None
        try:
            os.replace(new, exe)
            flags = 0
            if sys.platform == "win32":
                flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            popen([str(exe), "run"], env=env, close_fds=True, creationflags=flags)
        except OSError as e:
            os.replace(old, exe)
            raise UpdateError("could not start the new version (%s)"
                              % type(e).__name__) from None
    finally:
        if new.exists():
            new.unlink()


def update_app(release: Release, exe: str = sys.executable) -> None:
    """Download, verify, swap and start. The new file sits beside the app (same drive)."""
    if not (release.installable and can_self_update(executable=exe)):
        raise UpdateError("this install can't update itself")
    new = _sibling(Path(exe), ".new")
    download(release, new)
    install(new, Path(exe), release.version)


def cleanup(exe: Path) -> None:
    """Remove what an earlier update left behind. Never raises."""
    for suffix in (".old", ".new"):
        try:
            _sibling(Path(exe), suffix).unlink()
        except OSError:
            pass


# ----------------------------------------------------------------------------- schedule
class Updater:
    """What the tray shows: the newest release found, plus the daily check."""

    def __init__(self, cfg: Dict[str, Any], current: str = __version__, check=None):
        self.cfg = cfg
        self.current = current
        self._check = check or globals()["check"]
        self.available: Optional[Release] = None
        self.error = ""
        self._announced = ""
        self._last = 0.0
        self._lock = threading.Lock()

    def refresh(self) -> Optional[Release]:
        """Check now. Raises UpdateError (and keeps what was found before)."""
        with self._lock:
            try:
                found = self._check(self.current, channel(self.cfg, self.current))
            except UpdateError as e:
                self.error = str(e)
                raise
            self.available, self.error = found, ""
            return found

    def automatic(self, now: float) -> Optional[Release]:
        """Run the daily check if due; returns a release only the first time it is seen."""
        if not self.cfg.get("update_check", True) or self.cfg.get("source", "demo") == "demo":
            return None
        if self._last and now - self._last < CHECK_EVERY_S:
            return None
        self._last = now
        try:
            found = self.refresh()
        except Exception as e:  # noqa: BLE001 - never disturbs monitoring
            log.info("update check failed: %s", e)
            return None
        if not found or found.version == self._announced:
            return None
        self._announced = found.version
        return found

    def start(self, announce: Callable[[Release], None], stop: threading.Event) -> None:
        def loop():
            wait = 60.0  # let the app settle first
            while not stop.wait(wait):
                found = self.automatic(time.time())
                if found:
                    try:
                        announce(found)
                    except Exception:  # noqa: BLE001
                        log.exception("update notice")
                wait = 3600.0
        threading.Thread(target=loop, name="omnimonitor-update", daemon=True).start()
