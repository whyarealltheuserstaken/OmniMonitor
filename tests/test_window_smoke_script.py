"""CI helper: retry the native-window smoke test once, only for a WebView2 non-start."""
import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "window_smoke", Path(__file__).resolve().parent.parent / "scripts" / "window_smoke.py")
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def runner(*results):
    calls = []

    def run(cmd, timeout):
        calls.append(cmd)
        return results[len(calls) - 1]
    return run, calls


def test_windows_nonstart_is_retried_once():
    run, calls = runner((1, "{'served': False}"), (0, "ok"))
    assert smoke.main(["x"], run=run, platform="win32") == 0 and len(calls) == 2


def test_a_second_nonstart_fails():
    run, calls = runner((1, "'served': False"), (1, "'served': False"))
    assert smoke.main(["x"], run=run, platform="win32") == 1 and len(calls) == 2


def test_other_failures_and_other_systems_are_not_retried():
    for platform, out in (("win32", "'served': True"), ("darwin", "'served': False")):
        run, calls = runner((1, out))
        assert smoke.main(["x"], run=run, platform=platform) == 1 and len(calls) == 1
