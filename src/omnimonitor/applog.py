"""Log file + crash capture so a GUI launch that dies silently leaves a trace.

macOS: ~/Library/Logs/OmniMonitor/omnimonitor.log; elsewhere: <config dir>/logs/.
"""

from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Optional

from omnimonitor import config as config_mod

log = logging.getLogger("omnimonitor")
_FAULT_FILE = None


def log_dir() -> Path:
    override = config_mod.env("LOG_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin" and not config_mod.env("HOME"):
        return Path.home() / "Library" / "Logs" / "OmniMonitor"
    return config_mod.config_dir() / "logs"


def log_path() -> Path:
    return log_dir() / "omnimonitor.log"


def setup(level: int = logging.INFO) -> Optional[Path]:
    """Idempotent. Never raises: logging must not be the reason the app fails to start."""
    global _FAULT_FILE
    if getattr(log, "_omni_ready", False):
        return log_path()
    try:
        d = log_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = log_path()
        h = logging.handlers.RotatingFileHandler(str(path), maxBytes=1_000_000, backupCount=3,
                                                 encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] "
                                         "%(name)s: %(message)s"))
        log.addHandler(h)
        log.setLevel(level)
        # Hard crashes (SIGSEGV/SIGABRT/SIGBUS/SIGILL, and SIGTRAP from AppKit assertions)
        # get the Python stacks of all threads written to a separate file.
        _FAULT_FILE = open(str(d / "crash.log"), "a", encoding="utf-8")  # noqa: SIM115
        faulthandler.enable(_FAULT_FILE, all_threads=True)
        if hasattr(signal, "SIGTRAP"):
            try:
                faulthandler.register(signal.SIGTRAP, _FAULT_FILE, all_threads=True, chain=True)
            except (ValueError, RuntimeError, OSError):
                pass
    except Exception:  # noqa: BLE001
        return None

    def hook(exc_type, exc, tb):
        log.critical("uncaught exception", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    def thread_hook(a):
        log.critical("uncaught exception in thread %s", a.thread and a.thread.name,
                     exc_info=(a.exc_type, a.exc_value, a.exc_traceback))

    sys.excepthook = hook
    threading.excepthook = thread_hook
    log._omni_ready = True  # type: ignore[attr-defined]
    log.info("start pid=%s argv=%s frozen=%s", os.getpid(), sys.argv,
             getattr(sys, "frozen", False))
    return path
