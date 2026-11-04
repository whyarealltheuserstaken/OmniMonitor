"""Startup robustness: main-thread marshalling, log file, TLS CA bundle, Finder argv."""

import logging
import os
import ssl
import sys
import threading

import pytest

os.environ.setdefault("PYSTRAY_BACKEND", "dummy")

from omnimonitor import applog, cli, net, tray  # noqa: E402


def test_psn_argument_is_dropped():
    assert cli._clean_argv(["-psn_0_12345", "status"]) == ["status"]


def test_update_from_worker_thread_is_marshalled_on_macos(monkeypatch):
    calls = []
    fake = type(sys)("PyObjCTools")
    helper = type(sys)("PyObjCTools.AppHelper")
    helper.callAfter = lambda fn, *a: calls.append((fn, a))
    fake.AppHelper = helper
    monkeypatch.setitem(sys.modules, "PyObjCTools", fake)
    monkeypatch.setitem(sys.modules, "PyObjCTools.AppHelper", helper)
    monkeypatch.setattr(tray.sys, "platform", "darwin")
    ran = []
    t = threading.Thread(target=tray.on_main_thread, args=(lambda: ran.append(1),))
    t.start()
    t.join()
    assert ran == [] and len(calls) == 1      # not run on the worker thread
    calls[0][0](*calls[0][1])                 # what the main run loop would do
    assert ran == [1]


def test_main_thread_runs_directly(monkeypatch):
    monkeypatch.setattr(tray.sys, "platform", "darwin")
    ran = []
    tray.on_main_thread(lambda: ran.append(1))
    assert ran == [1]


def test_on_main_thread_logs_instead_of_raising():
    def boom():
        raise RuntimeError("x")
    tray.on_main_thread(boom)  # must not raise


def test_ca_source_prefers_certifi(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    pytest.importorskip("certifi")
    kind, path = net.ca_source()
    assert kind == "certifi" and os.path.isfile(path)
    net.reset_for_tests()
    ctx = net.ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert ctx.cert_store_stats()["x509_ca"] > 0
    net.reset_for_tests()


def test_ca_source_falls_back_to_system(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setattr(net, "_certifi_file", lambda: None)
    net.reset_for_tests()
    assert net.ca_source()[0] == "system"
    assert net.ssl_context().verify_mode == ssl.CERT_REQUIRED
    net.reset_for_tests()


def test_log_file_written(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIMONITOR_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(applog.log, "_omni_ready", False, raising=False)
    old = (sys.excepthook, threading.excepthook, list(applog.log.handlers))
    try:
        assert applog.setup() == tmp_path / "omnimonitor.log"
        applog.log.info("hello")
        for h in applog.log.handlers:
            h.flush()
        assert "hello" in (tmp_path / "omnimonitor.log").read_text()
        assert (tmp_path / "crash.log").exists()
    finally:
        for h in applog.log.handlers[len(old[2]):]:
            applog.log.removeHandler(h)
            h.close()
        sys.excepthook, threading.excepthook = old[0], old[1]
        applog.log._omni_ready = False
        applog.log.setLevel(logging.NOTSET)


def test_first_run_hint_once(tmp_path, monkeypatch):
    pytest.importorskip("pystray")
    from omnimonitor import alerts
    from omnimonitor import config as config_mod
    from omnimonitor.app import Controller
    monkeypatch.setenv("OMNIMONITOR_HOME", str(tmp_path))
    monkeypatch.setenv("OMNIMONITOR_NO_BROWSER", "1")
    sent = []
    monkeypatch.setattr(alerts, "deliver", lambda t, b, *a: sent.append(t) or True)
    cfg = config_mod.load_config()
    cfg["source"] = "demo"
    app = tray.TrayApp(Controller(cfg))
    try:
        assert app.first_run_hint() is True
        assert app.first_run_hint() is False
        assert sent == ["OmniMonitor is running"]
    finally:
        if app.server:
            app.server.stop()
