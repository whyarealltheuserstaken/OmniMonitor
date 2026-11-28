"""In-app updater: only a newer, signed release can ever be installed."""
import base64
import io
import json
import zipfile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from omnimonitor import updater

ASSET = updater.WINDOWS_ASSET


def keypair():
    key = Ed25519PrivateKey.generate()
    raw = key.public_key().public_bytes(serialization.Encoding.Raw,
                                        serialization.PublicFormat.Raw)
    return key, (base64.b64encode(raw).decode(),)


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def signed(key, version, blob):
    return updater.build_manifest(version, {ASSET: blob}, key.sign)


def releases(*versions, draft=False):
    return json.dumps([{"tag_name": "v" + v, "draft": draft,
                        "prerelease": "-" in v} for v in versions]).encode()


class Web:
    """A fake HTTPS: exact URL -> bytes. Records what was asked for."""

    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def __call__(self, url, limit):
        self.asked.append(url)
        if url not in self.pages:
            raise updater.UpdateError("not found")
        data = self.pages[url]
        if len(data) > limit:
            raise updater.UpdateError("response too large")
        return data


def web_for(key, *versions, blob=b"zip", listing=None):
    pages = {updater.API_URL: listing or releases(*versions)}
    for v in versions:
        pages[updater.asset_url(v, updater.MANIFEST)] = signed(key, v, blob)
    return Web(pages)


def test_versions_sort_beta_before_its_stable_release():
    order = ["0.6.1-beta", "0.7.0-beta", "0.7.0-beta.2", "0.7.0", "0.7.1-beta", "0.10.0"]
    assert sorted(reversed(order), key=updater.parse_version) == order
    for bad in ("0.7", "v0.7.0", "0.7.0-rc1", "0.7.0 ", "../0.7.0", "0.7.0\n", ""):
        assert updater.parse_version(bad) is None


def test_stable_channel_ignores_betas_and_beta_channel_takes_the_highest():
    key, pub = keypair()
    web = web_for(key, "0.7.0", "0.8.0-beta")
    assert updater.check("0.6.1-beta", "stable", web, pub, True).version == "0.7.0"
    assert updater.check("0.6.1-beta", "beta", web, pub, True).version == "0.8.0-beta"


def test_unset_channel_follows_the_installed_build():
    assert updater.channel({}, "0.7.0") == "stable"
    assert updater.channel({"update_channel": "auto"}, "0.7.0-beta") == "beta"
    assert updater.channel({"update_channel": "stable"}, "0.7.0-beta") == "stable"


@pytest.mark.parametrize("version", ["0.6.1", "0.6.0"])
def test_never_offers_the_same_or_an_older_version(version):
    key, pub = keypair()
    assert updater.check("0.6.1", "stable", web_for(key, version), pub, True) is None


def test_drafts_and_mislabelled_prereleases_are_ignored():
    key, pub = keypair()
    web = web_for(key, "0.7.0", listing=releases("0.7.0", draft=True))
    assert updater.check("0.6.1", "stable", web, pub, True) is None
    listing = json.dumps([{"tag_name": "v0.8.0-beta", "draft": False, "prerelease": False}])
    web = web_for(key, "0.8.0-beta", listing=listing.encode())
    assert updater.check("0.6.1", "stable", web, pub, True) is None


@pytest.mark.parametrize("reply", [b'{"message": "rate limited"}', b'"text"', b"<html>"])
def test_an_unexpected_reply_is_an_error_not_up_to_date(reply):
    with pytest.raises(updater.UpdateError):
        updater.check("0.6.1", "stable", Web({updater.API_URL: reply}), (), False)


def test_a_badly_signed_release_is_skipped_for_the_next_good_one():
    key, pub = keypair()
    stranger, _ = keypair()
    web = web_for(key, "0.7.0", "0.8.0")
    web.pages[updater.asset_url("0.8.0", updater.MANIFEST)] = signed(stranger, "0.8.0", b"zip")
    found = updater.check("0.6.1", "stable", web, pub, True)
    assert (found.version, found.installable) == ("0.7.0", True)


def test_an_old_signed_manifest_cannot_be_replayed_under_a_new_tag():
    key, pub = keypair()
    web = web_for(key, "0.9.0")
    web.pages[updater.asset_url("0.9.0", updater.MANIFEST)] = signed(key, "0.5.0", b"zip")
    with pytest.raises(updater.UpdateError, match="signature"):  # not "up to date"
        updater.check("0.6.1", "stable", web, pub, True)


def test_a_changed_manifest_fails_verification():
    key, pub = keypair()
    doc = json.loads(signed(key, "0.7.0", b"zip"))
    doc["payload"] = doc["payload"].replace("0.7.0", "0.7.1")
    with pytest.raises(updater.UpdateError):
        updater.verify_manifest(json.dumps(doc).encode(), pub)
    for junk in (b"", b"[]", b"{}", b"not json", b'{"payload": 1, "signature": 2}'):
        with pytest.raises(updater.UpdateError):
            updater.verify_manifest(junk, pub)


@pytest.mark.parametrize("can_install, with_key", [(False, True), (True, False)])
def test_without_a_key_or_an_installable_build_it_only_gives_notice(can_install, with_key):
    key, pub = keypair()
    web = web_for(key, "0.7.0")
    found = updater.check("0.6.1", "stable", web, pub if with_key else (), can_install)
    assert (found.version, found.installable) == ("0.7.0", False)
    assert found.page == ("https://github.com/whyarealltheuserstaken/OmniMonitor"
                          "/releases/tag/v0.7.0")


def test_download_addresses_are_built_by_the_app_not_taken_from_the_release_list():
    key, pub = keypair()
    listing = json.dumps([{"tag_name": "v0.7.0", "draft": False, "prerelease": False,
                           "html_url": "https://evil.example/x",
                           "assets": [{"name": ASSET,
                                       "browser_download_url": "https://evil.example/y"}]}])
    web = web_for(key, "0.7.0", listing=listing.encode())
    found = updater.check("0.6.1", "stable", web, pub, True)
    assert "evil" not in found.page + found.url + " ".join(web.asked)


@pytest.mark.parametrize("url", [
    "http://github.com/a", "https://github.com.evil.example/a", "https://evil.example/a",
    "https://user@github.com/a", "https://github.com:8443/a", "file:///etc/passwd",
    "https://[bad/a"])
def test_only_https_github_addresses_are_allowed(url):
    assert not updater.allowed_url(url)


def test_github_addresses_are_allowed():
    for host in ("api.github.com", "github.com", "objects.githubusercontent.com",
                 "release-assets.githubusercontent.com"):
        assert updater.allowed_url("https://%s/x?y=1" % host)


def found_release(blob):
    key, pub = keypair()
    web = web_for(key, "0.7.0", blob=blob)
    found = updater.check("0.6.1", "stable", web, pub, True)
    web.pages[found.url] = blob
    return web, found


def test_download_writes_only_the_verified_exe(tmp_path):
    web, found = found_release(make_zip({"omnimonitor.exe": b"new app"}))
    out = updater.download(found, tmp_path / "omnimonitor.exe.new", web)
    assert out.read_bytes() == b"new app"


@pytest.mark.parametrize("served", [b"other bytes", b""])
def test_download_rejects_bytes_that_do_not_match_the_signature(tmp_path, served):
    web, found = found_release(make_zip({"omnimonitor.exe": b"new app"}))
    web.pages[found.url] = served
    with pytest.raises(updater.UpdateError):
        updater.download(found, tmp_path / "new", web)
    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize("files", [
    {"omnimonitor.exe": b"a", "extra.dll": b"b"}, {"../omnimonitor.exe": b"a"},
    {"other.exe": b"a"}, {}])
def test_download_rejects_a_zip_that_is_not_exactly_the_app(tmp_path, files):
    web, found = found_release(make_zip(files))
    with pytest.raises(updater.UpdateError):
        updater.download(found, tmp_path / "new", web)
    assert not (tmp_path / "new").exists()


class Done:
    def __init__(self, out, code=0):
        self.stdout, self.returncode = out, code


def apps(tmp_path):
    exe, new = tmp_path / "omnimonitor.exe", tmp_path / "omnimonitor.exe.new"
    exe.write_bytes(b"old")
    new.write_bytes(b"new")
    return exe, new


def test_install_swaps_the_app_keeps_the_old_one_and_starts_the_new_one(tmp_path):
    exe, new = apps(tmp_path)
    started = []
    updater.install(new, exe, "0.7.0",
                    run=lambda *a, **k: Done("omnimonitor 0.7.0\n"),
                    popen=lambda argv, **k: started.append((argv, k["env"])))
    assert exe.read_bytes() == b"new"
    assert (tmp_path / "omnimonitor.exe.old").read_bytes() == b"old"
    assert started[0][0] == [str(exe), "run"]
    # a one-file build must unpack itself again, not reuse the old app's temp folder
    assert started[0][1]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


@pytest.mark.parametrize("result", [Done("omnimonitor 0.6.1\n"), Done("", 1),
                                    Done("omnimonitor 0.7.0\n", 3)])
def test_install_leaves_the_old_app_alone_when_the_self_test_fails(tmp_path, result):
    exe, new = apps(tmp_path)
    with pytest.raises(updater.UpdateError):
        updater.install(new, exe, "0.7.0", run=lambda *a, **k: result,
                        popen=lambda *a, **k: pytest.fail("must not start"))
    assert exe.read_bytes() == b"old"
    assert not new.exists()


def test_install_puts_the_old_app_back_when_the_new_one_cannot_start(tmp_path):
    exe, new = apps(tmp_path)

    def cannot_start(*a, **k):
        raise OSError("blocked")

    with pytest.raises(updater.UpdateError):
        updater.install(new, exe, "0.7.0", run=lambda *a, **k: Done("omnimonitor 0.7.0\n"),
                        popen=cannot_start)
    assert exe.read_bytes() == b"old"
    assert not (tmp_path / "omnimonitor.exe.old").exists()


def test_cleanup_removes_leftovers_only(tmp_path):
    exe, _new = apps(tmp_path)
    (tmp_path / "omnimonitor.exe.old").write_bytes(b"x")
    updater.cleanup(exe)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["omnimonitor.exe"]


def state(cfg, results):
    calls = []

    def check(current, chan):
        calls.append(chan)
        out = results.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    return updater.Updater(cfg, current="0.7.0", check=check), calls


def rel(version):
    return updater.Release(version, "page", "url", 1, "0" * 64, True)


def test_automatic_checks_run_once_a_day():
    up, calls = state({"source": "none"}, [None, None])
    for now in (1000.0, 2000.0, 1000.0 + updater.CHECK_EVERY_S):
        up.automatic(now)
    assert calls == ["stable", "stable"]


@pytest.mark.parametrize("cfg", [{"source": "none", "update_check": False},
                                 {"source": "demo"}])
def test_no_automatic_check_when_switched_off_or_in_demo_mode(cfg):
    up, calls = state(cfg, [])
    assert up.automatic(1000.0) is None
    assert calls == []


def test_each_new_version_is_announced_once():
    up, _ = state({"source": "none"}, [rel("0.8.0"), rel("0.8.0"), rel("0.9.0")])
    seen = [up.automatic(i * updater.CHECK_EVERY_S + 1) for i in range(3)]
    assert [r and r.version for r in seen] == ["0.8.0", None, "0.9.0"]
    assert up.available.version == "0.9.0"


def test_a_failed_check_keeps_what_was_found_and_never_raises():
    up, _ = state({"source": "none"}, [rel("0.8.0"), updater.UpdateError("offline")])
    up.automatic(1.0)
    assert up.automatic(1.0 + updater.CHECK_EVERY_S) is None
    assert up.available.version == "0.8.0"
    assert up.error == "offline"


def test_only_the_frozen_windows_app_installs_updates(tmp_path):
    exe = tmp_path / "omnimonitor.exe"
    exe.write_bytes(b"x")
    assert updater.can_self_update("win32", True, str(exe))
    assert not updater.can_self_update("darwin", True, str(exe))
    assert not updater.can_self_update("win32", False, str(exe))
    assert not updater.can_self_update("win32", True, str(tmp_path / "no" / "omnimonitor.exe"))
