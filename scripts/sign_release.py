"""Sign and publish a release so the in-app updater will accept it.

Run these yourself, in your own terminal, from the repository folder:

    python scripts/sign_release.py keygen          # once: creates your signing key
    python scripts/sign_release.py sign v0.7.0-beta  # each release (needs `gh` signed in)

The private key is encrypted with your password and stays outside the repository
(~/.omnimonitor-signing/key.pem). Back it up: without it, users must install the next
version by hand. Never paste the password into a chat, a command line or a file.
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from omnimonitor import updater  # noqa: E402

KEY_PATH = Path.home() / ".omnimonitor-signing" / "key.pem"
KEY_MODULE = ROOT / "src" / "omnimonitor" / "update_key.py"
MIN_PASSWORD = 12

MODULE_TEMPLATE = '''"""Public keys that may sign updates (base64 Ed25519). Written by
``python scripts/sign_release.py keygen``. Public keys only: the private key never goes here.

While this is empty the app only says that a newer version exists; it installs nothing.
"""

PUBLIC_KEYS: tuple = (%s)
'''


class Refused(Exception):
    pass


def read_public_keys(module: Path) -> tuple:
    scope: dict = {}
    exec(compile(Path(module).read_text("utf-8"), str(module), "exec"), scope)  # noqa: S102
    return tuple(scope["PUBLIC_KEYS"])


def keygen(key_path: Path = KEY_PATH, module: Path = KEY_MODULE,
           ask=getpass.getpass) -> None:
    from cryptography.hazmat.primitives import serialization as ser
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key_path = Path(key_path)
    if key_path.exists():
        raise Refused("a key already exists at %s; it was not touched" % key_path)
    password = ask("New password for the signing key (at least %d characters): "
                   % MIN_PASSWORD)
    if len(password) < MIN_PASSWORD:
        raise Refused("the password is too short")
    if ask("Type it again: ") != password:
        raise Refused("the passwords do not match")
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(ser.Encoding.PEM, ser.PrivateFormat.PKCS8,
                            ser.BestAvailableEncryption(password.encode("utf-8")))
    key_path.parent.mkdir(parents=True, exist_ok=True)
    with open(key_path, "xb") as f:
        f.write(pem)
    public = base64.b64encode(key.public_key().public_bytes(
        ser.Encoding.Raw, ser.PublicFormat.Raw)).decode("ascii")
    Path(module).write_text(MODULE_TEMPLATE % ('"%s",' % public), "utf-8")
    print("Private key: %s  (back it up somewhere offline)" % key_path)
    print("Public key written to %s. Commit that file." % module)


def run_gh(*args: str) -> str:
    done = subprocess.run(["gh", *args], cwd=str(ROOT), capture_output=True, text=True)
    if done.returncode:
        raise Refused("gh %s failed: %s" % (" ".join(args[:2]), done.stderr.strip()))
    return done.stdout


def _load_key(key_path: Path, password: str):
    from cryptography.hazmat.primitives import serialization as ser
    try:
        return ser.load_pem_private_key(Path(key_path).read_bytes(), password.encode("utf-8"))
    except (OSError, ValueError, TypeError):
        raise Refused("could not open the signing key (wrong password or no key)") from None


def sign(tag: str, key_path: Path = KEY_PATH, public=None, ask=getpass.getpass,
         confirm=input, gh=run_gh) -> None:
    version = tag[1:] if tag.startswith("v") else ""
    if not updater.parse_version(version):
        raise Refused("the tag must look like v1.2.3 or v1.2.3-beta")
    public = read_public_keys(KEY_MODULE) if public is None else public
    info = json.loads(gh("release", "view", tag, "--json", "isDraft,isPrerelease"))
    if not info.get("isDraft"):
        raise Refused("%s is not a draft release" % tag)
    if bool(info.get("isPrerelease")) != updater.is_beta(version):
        raise Refused("only -beta versions may be marked as prereleases")
    with tempfile.TemporaryDirectory() as folder:
        gh("release", "download", tag, "--dir", folder,
           "--pattern", updater.WINDOWS_ASSET, "--pattern", "SHA256SUMS.txt")
        blob = (Path(folder) / updater.WINDOWS_ASSET).read_bytes()
        digest = hashlib.sha256(blob).hexdigest()
        sums = (Path(folder) / "SHA256SUMS.txt").read_text("utf-8")
        if "%s  %s" % (digest, updater.WINDOWS_ASSET) not in sums.splitlines():
            raise Refused("the download does not match SHA256SUMS.txt")
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                names = z.namelist()
        except zipfile.BadZipFile:
            names = []
        if names != [updater.APP_FILE]:
            raise Refused("the zip must contain only %s" % updater.APP_FILE)
        key = _load_key(key_path, ask("Signing key password: "))
        manifest = updater.build_manifest(version, {updater.WINDOWS_ASSET: blob}, key.sign)
        try:
            updater.verify_manifest(manifest, public)
        except updater.UpdateError:
            raise Refused("this key does not match the public key in update_key.py") from None
        print("About to sign and publish %s\n  %s\n  %d bytes, sha256 %s"
              % (tag, updater.WINDOWS_ASSET, len(blob), digest))
        if confirm("Type yes to publish: ").strip().lower() != "yes":
            raise Refused("not confirmed")
        out = Path(folder) / updater.MANIFEST
        out.write_bytes(manifest)
        gh("release", "upload", tag, str(out), "--clobber")
        gh("release", "edit", tag, "--draft=false")
    print("Published %s." % tag)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        if argv == ["keygen"]:
            keygen()
        elif len(argv) == 2 and argv[0] == "sign":
            sign(argv[1])
        else:
            print(__doc__)
            return 2
    except (Refused, KeyboardInterrupt, EOFError) as e:
        print("Stopped: %s" % (e or "cancelled"), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
