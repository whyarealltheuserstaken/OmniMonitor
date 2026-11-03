"""Start-at-login for macOS (LaunchAgent), Windows (HKCU Run key), Linux (XDG autostart)."""

from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

from omnimonitor import APP_DISPLAY_NAME, APP_ID, APP_NAME, LEGACY_APP_ID, LEGACY_APP_NAME

WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_argv() -> List[str]:
    """The command that starts the tray app, for the current install style."""
    if getattr(sys, "frozen", False):  # PyInstaller binary
        return [sys.executable]
    # On Windows prefer the gui-script launcher so no console window opens at login.
    names = [APP_NAME + "-gui", APP_NAME] if sys.platform == "win32" else [APP_NAME]
    exe = next((e for e in (shutil.which(n) for n in names) if e), None)
    if exe:
        return [exe]
    py = sys.executable
    if sys.platform == "win32":
        pyw = Path(py).with_name("pythonw.exe")  # no console window
        if pyw.exists():
            py = str(pyw)
    return [py, "-m", "omnimonitor"]


def mac_plist_path(app_id: str = APP_ID) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / ("%s.plist" % app_id)


def linux_desktop_path(app_name: str = APP_NAME) -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / ("%s.desktop" % app_name)


def _remove_legacy() -> List[str]:
    """Remove autostart entries created under the pre-rename name (grok-usage-tray), so the
    app doesn't start twice after an upgrade. Never raises."""
    removed: List[str] = []
    try:
        if sys.platform == "darwin":
            p = mac_plist_path(LEGACY_APP_ID)
            if p.exists():
                subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
                p.unlink()
                removed.append(str(p))
        elif sys.platform == "win32":
            import winreg  # type: ignore[import-not-found]
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_RUN_KEY, 0,
                                    winreg.KEY_SET_VALUE) as k:
                    winreg.DeleteValue(k, LEGACY_APP_NAME)
                removed.append("HKCU\\%s\\%s" % (WIN_RUN_KEY, LEGACY_APP_NAME))
            except FileNotFoundError:
                pass
        else:
            p = linux_desktop_path(LEGACY_APP_NAME)
            if p.exists():
                p.unlink()
                removed.append(str(p))
    except Exception:  # noqa: BLE001
        pass
    return removed


def _with_legacy_note(msg: str, removed: List[str]) -> str:
    return msg + ("" if not removed else " (removed old entry %s)" % ", ".join(removed))


def _desktop_quote(arg: str) -> str:
    if not any(c in arg for c in ' \t\n"\'\\$`'):
        return arg
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")\
        .replace("`", "\\`") + '"'


def desktop_entry(argv: List[str]) -> str:
    return ("[Desktop Entry]\nType=Application\nName=%s\n"
            "Comment=Unofficial Grok weekly usage in your tray\n"
            "Exec=%s\nTerminal=false\nX-GNOME-Autostart-enabled=true\n"
            "X-GNOME-Autostart-Delay=10\n") % (APP_DISPLAY_NAME,
                                                " ".join(_desktop_quote(a) for a in argv))


def mac_plist(argv: List[str]) -> bytes:
    return plistlib.dumps({
        "Label": APP_ID, "ProgramArguments": argv, "RunAtLoad": True,
        "KeepAlive": False, "ProcessType": "Interactive",
        "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null",
    })


def install() -> Tuple[bool, str]:
    ok, msg = _install()
    return ok, _with_legacy_note(msg, _remove_legacy() if ok else [])


def uninstall() -> Tuple[bool, str]:
    removed = _remove_legacy()
    ok, msg = _uninstall()
    if removed and msg == "not installed":
        msg = "removed old entry %s" % ", ".join(removed)
        removed = []
    return ok, _with_legacy_note(msg, removed)


def _install() -> Tuple[bool, str]:
    argv = launch_argv()
    try:
        if sys.platform == "darwin":
            p = mac_plist_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(mac_plist(argv))
            # Load now too (ignore errors: it's loaded at next login anyway).
            subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
            subprocess.run(["launchctl", "load", str(p)], capture_output=True)
            return True, "installed LaunchAgent %s" % p
        if sys.platform == "win32":
            import winreg  # type: ignore[import-not-found]
            cmd = subprocess.list2cmdline(argv)
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
            return True, "added HKCU\\%s\\%s = %s" % (WIN_RUN_KEY, APP_NAME, cmd)
        p = linux_desktop_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(desktop_entry(argv), encoding="utf-8")
        return True, "installed %s (Exec=%s)" % (p, shlex.join(argv))
    except Exception as e:  # noqa: BLE001
        return False, "could not install autostart: %s" % e


def _uninstall() -> Tuple[bool, str]:
    try:
        if sys.platform == "darwin":
            p = mac_plist_path()
            if p.exists():
                subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
                p.unlink()
                return True, "removed %s" % p
            return True, "not installed"
        if sys.platform == "win32":
            import winreg  # type: ignore[import-not-found]
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_RUN_KEY, 0,
                                    winreg.KEY_SET_VALUE) as k:
                    winreg.DeleteValue(k, APP_NAME)
                return True, "removed Run key value"
            except FileNotFoundError:
                return True, "not installed"
        p = linux_desktop_path()
        if p.exists():
            p.unlink()
            return True, "removed %s" % p
        return True, "not installed"
    except Exception as e:  # noqa: BLE001
        return False, "could not remove autostart: %s" % e
