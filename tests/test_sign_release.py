"""scripts/sign_release.py: what it creates must be exactly what the app will accept."""
import hashlib
import importlib.util
import io
import zipfile
from pathlib import Path

import pytest

from omnimonitor import updater

SPEC = importlib.util.spec_from_file_location(
    "sign_release", Path(__file__).resolve().parents[1] / "scripts" / "sign_release.py")
sr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sr)

PASSWORD = "a long test password"


def answers(*values):
    left = list(values)
    return lambda prompt="": left.pop(0)


def make_key(tmp_path):
    key, module = tmp_path / "keys" / "key.pem", tmp_path / "update_key.py"
    sr.keygen(key, module, ask=answers(PASSWORD, PASSWORD))
    return key, sr.read_public_keys(module)


def app_zip(name="omnimonitor.exe"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, b"the app")
    return buf.getvalue()


class GitHub:
    """Stands in for the `gh` command."""

    def __init__(self, blob, sums=None, draft=True, prerelease=True):
        self.blob, self.draft, self.prerelease = blob, draft, prerelease
        self.sums = sums or "%s  %s\n" % (hashlib.sha256(blob).hexdigest(),
                                          updater.WINDOWS_ASSET)
        self.uploaded, self.published = None, False

    def __call__(self, *args):
        if args[:2] == ("release", "view"):
            return '{"isDraft": %s, "isPrerelease": %s}' % (str(self.draft).lower(),
                                                            str(self.prerelease).lower())
        if args[:2] == ("release", "download"):
            folder = Path(args[args.index("--dir") + 1])
            (folder / updater.WINDOWS_ASSET).write_bytes(self.blob)
            (folder / "SHA256SUMS.txt").write_text(self.sums, "utf-8")
        elif args[:2] == ("release", "upload"):
            self.uploaded = Path(args[3]).read_bytes()
        elif args[:2] == ("release", "edit"):
            assert self.uploaded, "never publish before the signature is attached"
            self.published = "--draft=false" in args
        return ""


def test_keygen_stores_only_an_encrypted_private_key(tmp_path):
    key, public = make_key(tmp_path)
    assert b"ENCRYPTED PRIVATE KEY" in key.read_bytes()
    module = (tmp_path / "update_key.py").read_text("utf-8")
    assert len(public) == 1 and public[0] in module and "PRIVATE KEY" not in module


def test_keygen_never_overwrites_a_key(tmp_path):
    key, _ = make_key(tmp_path)
    before = key.read_bytes()
    with pytest.raises(sr.Refused):
        sr.keygen(key, tmp_path / "update_key.py", ask=answers(PASSWORD, PASSWORD))
    assert key.read_bytes() == before


@pytest.mark.parametrize("first, second", [("short", "short"), (PASSWORD, PASSWORD + "x")])
def test_keygen_refuses_a_weak_or_mistyped_password(tmp_path, first, second):
    with pytest.raises(sr.Refused):
        sr.keygen(tmp_path / "key.pem", tmp_path / "update_key.py",
                  ask=answers(first, second))
    assert not (tmp_path / "key.pem").exists()


def test_signed_release_is_accepted_by_the_app_and_then_published(tmp_path):
    key, public = make_key(tmp_path)
    blob = app_zip()
    gh = GitHub(blob)
    sr.sign("v0.7.0-beta", key, public, ask=answers(PASSWORD), confirm=answers("yes"), gh=gh)
    manifest = updater.verify_manifest(gh.uploaded, public)
    assert manifest["version"] == "0.7.0-beta"
    assert manifest["assets"][updater.WINDOWS_ASSET] == {
        "size": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
    assert gh.published


@pytest.mark.parametrize("change", [
    {"gh": {"draft": False}}, {"gh": {"prerelease": False}},
    {"gh": {"sums": "0" * 64 + "  " + updater.WINDOWS_ASSET + "\n"}},
    {"zip": "other.exe"}, {"confirm": "no"}, {"ask": "the wrong password!"},
    {"tag": "0.7.0-beta"}, {"public": ("A" * 43 + "=",)}])
def test_sign_refuses_and_uploads_nothing(tmp_path, change):
    key, public = make_key(tmp_path)
    gh = GitHub(app_zip(change.get("zip", "omnimonitor.exe")), **change.get("gh", {}))
    with pytest.raises(sr.Refused):
        sr.sign(change.get("tag", "v0.7.0-beta"), key, change.get("public", public),
                ask=answers(change.get("ask", PASSWORD)),
                confirm=answers(change.get("confirm", "yes")), gh=gh)
    assert gh.uploaded is None and not gh.published


def test_release_workflow_creates_drafts_so_nothing_unsigned_goes_out():
    root = Path(__file__).resolve().parents[1]
    text = (root / ".github" / "workflows" / "release.yml").read_text("utf-8")
    assert "draft: true" in text and "draft: false" not in text
