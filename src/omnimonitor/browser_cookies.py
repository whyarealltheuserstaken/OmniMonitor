"""Read session cookies for specific provider domains from installed browsers.

Off by default. Only the cookie *names* listed for a provider on the listed *hosts*
are returned — never the whole cookie jar. Chromium Safe Storage / Safari Full Disk
Access requirements are documented in docs/cookies/browser-import.md.

Supported readers (best-effort; each returns a clear error when unavailable):

* Firefox — cookies.sqlite (unencrypted) on macOS, Windows, Linux
* Chromium family (Chrome, Edge, Brave, Arc) — Cookies SQLite + OS key decryption
  (macOS Keychain "… Safe Storage", Windows DPAPI, Linux libsecret/kwallet via
  the optional ``cryptography`` package for AES)
* Safari — Cookies.binarycookies on macOS (needs Full Disk Access for Terminal/app)

Citations for which cookies matter: CodexBar docs (MIT), see docs/cookies/codexbar.md.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

# (browser_id, display name)
BROWSERS: Tuple[Tuple[str, str], ...] = (
    ("firefox", "Firefox"),
    ("chrome", "Google Chrome"),
    ("edge", "Microsoft Edge"),
    ("brave", "Brave"),
    ("arc", "Arc"),
    ("safari", "Safari"),
)

# provider_id -> (hosts to match, cookie names to keep; empty names = all cookies on host)
PROVIDER_COOKIE_FILTER: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    # CodexBar docs/claude.md — Domain claude.ai, cookie sessionKey (sk-ant-…)
    "claude_plan": (("claude.ai",), ("sessionKey",)),
    # CodexBar docs/codex.md — domains chatgpt.com, openai.com; no name filter there.
    # We keep a short allow-list of session-ish names to avoid shipping unrelated cookies.
    "chatgpt_web": (("chatgpt.com", "openai.com", "auth.openai.com"),
                    ("__Secure-next-auth.session-token", "next-auth.session-token",
                     "__Secure-authjs.session-token", "authjs.session-token",
                     "oai-did", "_account", "cf_clearance", "__cf_bm",
                     "__Secure-next-auth.callback-url", "__Host-next-auth.csrf-token",
                     "session-token")),
    # CodexBar docs/cursor.md — WorkosCursorSessionToken and next-auth variants
    "cursor": (("cursor.com", "www.cursor.com", "cursor.sh", "authenticator.cursor.sh"),
               ("WorkosCursorSessionToken", "__Secure-next-auth.session-token",
                "next-auth.session-token", "wos-session", "__Secure-wos-session",
                "authjs.session-token", "__Secure-authjs.session-token")),
}


@dataclass
class Cookie:
    host: str
    name: str
    value: str


class CookieError(Exception):
    """User-presentable reason a browser import failed (never includes cookie values)."""


def available_browsers() -> List[Dict[str, str]]:
    """Browsers we can at least attempt on this OS, with a short readiness note."""
    out = []
    for bid, label in BROWSERS:
        ok, note = _browser_ready(bid)
        out.append({"id": bid, "name": label, "ready": ok, "note": note})
    return out


def read_for_provider(pid: str, browser: str = "auto") -> str:
    """Return a Cookie header for ``pid`` from ``browser`` (or first that works).

    Raises CookieError with a clear, value-free message on failure.
    """
    if pid not in PROVIDER_COOKIE_FILTER:
        raise CookieError("provider %s does not use browser cookies" % pid)
    hosts, names = PROVIDER_COOKIE_FILTER[pid]
    order = [browser] if browser and browser != "auto" else [b for b, _ in BROWSERS]
    errors: List[str] = []
    for bid in order:
        try:
            cookies = _read_browser(bid, hosts, names)
        except CookieError as e:
            errors.append("%s: %s" % (bid, e))
            continue
        if cookies:
            return _header(cookies)
        errors.append("%s: no matching cookies for %s" % (bid, pid))
    raise CookieError("no usable session found (%s)" % "; ".join(errors[:4]))


def _header(cookies: Sequence[Cookie]) -> str:
    # Last value wins per name (common when several profiles match)
    by_name: Dict[str, str] = {}
    for c in cookies:
        by_name[c.name] = c.value
    return "; ".join("%s=%s" % (k, v) for k, v in by_name.items())


def _host_ok(cookie_host: str, allowed: Iterable[str]) -> bool:
    h = (cookie_host or "").lstrip(".").lower()
    for a in allowed:
        a = a.lstrip(".").lower()
        if h == a or h.endswith("." + a):
            return True
    return False


def _name_ok(name: str, allowed: Sequence[str]) -> bool:
    return (not allowed) or name in allowed


def _browser_ready(bid: str) -> Tuple[bool, str]:
    if bid == "safari":
        if sys.platform != "darwin":
            return False, "Safari cookies are only on macOS"
        p = Path.home() / "Library/Cookies/Cookies.binarycookies"
        if not p.is_file():
            return False, "no Safari cookie file (sign in, or grant Full Disk Access)"
        try:
            p.read_bytes()[:4]
            return True, "Cookies.binarycookies readable"
        except PermissionError:
            return False, "needs Full Disk Access for this app (System Settings → Privacy)"
        except OSError as e:
            return False, "unreadable (%s)" % type(e).__name__
    paths = _profile_roots(bid)
    if not paths:
        return False, "not installed (or profile path unknown on this OS)"
    return True, str(paths[0])


def _profile_roots(bid: str) -> List[Path]:
    home = Path.home()
    if bid == "firefox":
        if sys.platform == "darwin":
            base = home / "Library/Application Support/Firefox/Profiles"
        elif sys.platform == "win32":
            base = Path(os.environ.get("APPDATA", home / "AppData/Roaming")) / "Mozilla/Firefox/Profiles"
        else:
            base = home / ".mozilla/firefox"
        if not base.is_dir():
            return []
        return [p for p in base.iterdir() if p.is_dir() and (p / "cookies.sqlite").is_file()]
    # Chromium family
    mapping = {
        "chrome": {
            "darwin": ["Library/Application Support/Google/Chrome"],
            "win32": ["AppData/Local/Google/Chrome/User Data"],
            "linux": [".config/google-chrome", ".config/chromium"],
        },
        "edge": {
            "darwin": ["Library/Application Support/Microsoft Edge"],
            "win32": ["AppData/Local/Microsoft/Edge/User Data"],
            "linux": [".config/microsoft-edge"],
        },
        "brave": {
            "darwin": ["Library/Application Support/BraveSoftware/Brave-Browser"],
            "win32": ["AppData/Local/BraveSoftware/Brave-Browser/User Data"],
            "linux": [".config/BraveSoftware/Brave-Browser"],
        },
        "arc": {
            "darwin": ["Library/Application Support/Arc/User Data"],
            "win32": [],
            "linux": [],
        },
    }.get(bid, {})
    key = "darwin" if sys.platform == "darwin" else ("win32" if sys.platform == "win32" else "linux")
    roots: List[Path] = []
    for rel in mapping.get(key, []):
        base = home / rel
        if not base.is_dir():
            continue
        for name in ("Default", "Profile 1", "Profile 2", "Profile 3"):
            p = base / name
            if (p / "Cookies").is_file() or (p / "Network/Cookies").is_file():
                roots.append(p)
        # also any Profile *
        for p in base.glob("Profile *"):
            if p.is_dir() and ((p / "Cookies").is_file() or (p / "Network/Cookies").is_file()):
                if p not in roots:
                    roots.append(p)
    return roots


def _read_browser(bid: str, hosts: Sequence[str], names: Sequence[str]) -> List[Cookie]:
    ok, note = _browser_ready(bid)
    if not ok and bid != "safari":
        raise CookieError(note)
    if bid == "firefox":
        return _read_firefox(hosts, names)
    if bid == "safari":
        return _read_safari(hosts, names)
    if bid in ("chrome", "edge", "brave", "arc"):
        return _read_chromium(bid, hosts, names)
    raise CookieError("unknown browser %r" % bid)


def _copy_db(src: Path) -> Path:
    """Copy a live SQLite DB (and -wal/-shm if present) so we don't lock the browser."""
    td = Path(tempfile.mkdtemp(prefix="omni-ck-"))
    dst = td / src.name
    shutil.copy2(src, dst)
    for suf in ("-wal", "-shm"):
        side = Path(str(src) + suf)
        if side.is_file():
            try:
                shutil.copy2(side, Path(str(dst) + suf))
            except OSError:
                pass
    return dst


def _read_firefox(hosts: Sequence[str], names: Sequence[str]) -> List[Cookie]:
    out: List[Cookie] = []
    for profile in _profile_roots("firefox"):
        db = profile / "cookies.sqlite"
        try:
            copy = _copy_db(db)
            con = sqlite3.connect(str(copy))
            try:
                rows = con.execute(
                    "SELECT host, name, value FROM moz_cookies").fetchall()
            finally:
                con.close()
            shutil.rmtree(copy.parent, ignore_errors=True)
        except OSError as e:
            raise CookieError("could not read Firefox cookies (%s)" % type(e).__name__) from e
        for host, name, value in rows:
            if _host_ok(str(host), hosts) and _name_ok(str(name), names) and value:
                out.append(Cookie(str(host), str(name), str(value)))
    return out


def _chromium_cookie_paths(profile: Path) -> List[Path]:
    return [p for p in (profile / "Network/Cookies", profile / "Cookies") if p.is_file()]


def _read_chromium(bid: str, hosts: Sequence[str], names: Sequence[str]) -> List[Cookie]:
    key = _chromium_key(bid)
    out: List[Cookie] = []
    for profile in _profile_roots(bid):
        for db in _chromium_cookie_paths(profile):
            try:
                copy = _copy_db(db)
                con = sqlite3.connect(str(copy))
                try:
                    rows = con.execute(
                        "SELECT host_key, name, encrypted_value, value FROM cookies"
                    ).fetchall()
                finally:
                    con.close()
                shutil.rmtree(copy.parent, ignore_errors=True)
            except OSError as e:
                raise CookieError("could not read %s cookies (%s)" % (bid, type(e).__name__)) from e
            for host, name, enc, plain in rows:
                if not (_host_ok(str(host), hosts) and _name_ok(str(name), names)):
                    continue
                val = ""
                if plain:
                    val = plain if isinstance(plain, str) else plain.decode("utf-8", "replace")
                elif enc:
                    try:
                        val = _decrypt_chromium(enc if isinstance(enc, (bytes, bytearray))
                                                else bytes(enc), key)
                    except CookieError:
                        raise
                    except Exception as e:  # noqa: BLE001
                        raise CookieError(
                            "could not decrypt %s cookie (install cryptography; on macOS "
                            "approve Keychain access for Safe Storage): %s"
                            % (bid, type(e).__name__)) from e
                if val:
                    out.append(Cookie(str(host), str(name), val))
    return out


def _chromium_key(bid: str) -> bytes:
    """Derive the AES key used for Chromium cookie values on this OS."""
    safe = {
        "chrome": ("Chrome", "Chrome Safe Storage"),
        "edge": ("Microsoft Edge", "Microsoft Edge Safe Storage"),
        "brave": ("Brave", "Brave Safe Storage"),
        "arc": ("Arc", "Arc Safe Storage"),
    }.get(bid, ("Chrome", "Chrome Safe Storage"))
    if sys.platform == "darwin":
        try:
            pw = subprocess.check_output(
                ["security", "find-generic-password", "-w", "-s", safe[1], "-a", safe[0]],
                stderr=subprocess.DEVNULL, timeout=30)
            password = pw.decode("utf-8", "replace").rstrip("\n")
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
            raise CookieError(
                "macOS Keychain denied or missing %r — approve the Safe Storage prompt "
                "(or use Firefox / in-app sign-in)" % safe[1]) from e
        try:
            from hashlib import pbkdf2_hmac
        except ImportError as e:  # pragma: no cover
            raise CookieError("hashlib missing") from e
        return pbkdf2_hmac("sha1", password.encode("utf-8"), b"saltysalt", 1003, dklen=16)
    if sys.platform == "win32":
        # DPAPI-unwrapped key from Local State
        import base64
        import json
        roots = {
            "chrome": Path.home() / "AppData/Local/Google/Chrome/User Data/Local State",
            "edge": Path.home() / "AppData/Local/Microsoft/Edge/User Data/Local State",
            "brave": Path.home() / "AppData/Local/BraveSoftware/Brave-Browser/User Data/Local State",
        }
        state = roots.get(bid)
        if not state or not state.is_file():
            raise CookieError("%s Local State not found" % bid)
        data = json.loads(state.read_text("utf-8"))
        enc = base64.b64decode(data["os_crypt"]["encrypted_key"])
        if not enc.startswith(b"DPAPI"):
            raise CookieError("unexpected Chromium key format")
        enc = enc[5:]
        try:
            import ctypes
            import ctypes.wintypes

            class DATA_BLOB(ctypes.Structure):
                _fields_ = [("cbData", ctypes.wintypes.DWORD),
                            ("pbData", ctypes.POINTER(ctypes.c_char))]

            blob_in = DATA_BLOB(len(enc), ctypes.create_string_buffer(enc, len(enc)))
            blob_out = DATA_BLOB()
            if not ctypes.windll.crypt32.CryptUnprotectData(  # type: ignore[attr-defined]
                    ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
                raise CookieError("DPAPI refused to unwrap the Chromium key")
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        except CookieError:
            raise
        except Exception as e:  # noqa: BLE001
            raise CookieError("Windows DPAPI decrypt failed (%s)" % type(e).__name__) from e
    # Linux: try empty password v10 key (many distros) — often wrong; tell user clearly
    from hashlib import pbkdf2_hmac
    return pbkdf2_hmac("sha1", b"peanuts", b"saltysalt", 1, dklen=16)


def _decrypt_chromium(blob: bytes, key: bytes) -> str:
    if not blob:
        return ""
    # v10/v11 AES-CBC (macOS/older) or v10 AES-GCM (Linux/Windows modern)
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as e:
        raise CookieError(
            "Chromium cookie decryption needs the cryptography package "
            "(`pip install cryptography`), or use Firefox / in-app sign-in") from e
    if blob.startswith(b"v10") or blob.startswith(b"v11"):
        payload = blob[3:]
        if sys.platform == "darwin":
            iv = b" " * 16
            decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
            data = decryptor.update(payload) + decryptor.finalize()
            # PKCS7 strip
            pad = data[-1]
            if 1 <= pad <= 16:
                data = data[:-pad]
            return data.decode("utf-8", "replace")
        # AES-GCM: 12-byte nonce + ciphertext + 16-byte tag
        if len(payload) < 28:
            return ""
        nonce, ct = payload[:12], payload[12:]
        return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8", "replace")
    # Legacy plaintext DPAPI blob on older Windows — try as latin-1
    return blob.decode("utf-8", "replace")


def _read_safari(hosts: Sequence[str], names: Sequence[str]) -> List[Cookie]:
    if sys.platform != "darwin":
        raise CookieError("Safari cookies are only on macOS")
    path = Path.home() / "Library/Cookies/Cookies.binarycookies"
    try:
        data = path.read_bytes()
    except PermissionError as e:
        raise CookieError(
            "Safari cookie file needs Full Disk Access "
            "(System Settings → Privacy & Security → Full Disk Access)") from e
    except OSError as e:
        raise CookieError("Safari cookies unreadable (%s)" % type(e).__name__) from e
    if not data.startswith(b"cook"):
        raise CookieError("unrecognised Safari cookie file format")
    return _parse_binarycookies(data, hosts, names)


def _parse_binarycookies(data: bytes, hosts: Sequence[str], names: Sequence[str]) -> List[Cookie]:
    """Minimal Apple binarycookies reader (page → cookie structs)."""
    out: List[Cookie] = []
    try:
        n_pages = struct.unpack(">i", data[4:8])[0]
        offs = struct.unpack(">%di" % n_pages, data[8:8 + 4 * n_pages])
        page_start = 8 + 4 * n_pages + 4  # skip header checksum
        pages = []
        cur = page_start
        for size in offs:
            pages.append(data[cur:cur + size])
            cur += size
        for page in pages:
            if page[0:4] != b"\x00\x00\x01\x00":
                continue
            n = struct.unpack("<i", page[4:8])[0]
            cookie_offs = struct.unpack("<%di" % n, page[8:8 + 4 * n])
            for off in cookie_offs:
                c = page[off:]
                if len(c) < 32:
                    continue
                flags = struct.unpack("<i", c[0:4])[0]  # noqa: F841
                url_off, name_off, path_off, value_off = struct.unpack("<iiii", c[4:20])
                # strings are null-terminated at those offsets into this cookie record
                def cstr(o: int, buf: bytes = c) -> str:
                    if o <= 0 or o >= len(buf):
                        return ""
                    end = buf.index(b"\x00", o)
                    return buf[o:end].decode("utf-8", "replace")
                host, name, value = cstr(url_off), cstr(name_off), cstr(value_off)
                if _host_ok(host, hosts) and _name_ok(name, names) and value:
                    out.append(Cookie(host, name, value))
    except (struct.error, ValueError, IndexError) as e:
        raise CookieError("could not parse Safari cookies (%s)" % type(e).__name__) from e
    return out
