import pytest

from omnimonitor import accounts, net, settings, status, statusline
from tests.test_providers_v3 import FakeHttp

NOW = 1_793_800_000.0


def test_status_badges_opt_in_and_cached():
    status.clear_cache()
    http = FakeHttp({"https://status.claude.com/api/v2/status.json": (200, {
        "status": {"indicator": "minor", "description": "Elevated errors"}})})
    assert status.fetch_all({}, {"Claude"}, http=http, now=NOW) == {}
    assert http.calls == []  # off by default: no request at all
    got = status.fetch_all({"status_badges": True}, {"Claude", "Grok"}, http=http, now=NOW)
    assert got["Claude"]["level"] == "minor" and got["Claude"]["url"] == "https://status.claude.com"
    assert "Grok" not in got  # no official Statuspage JSON verified for it: nothing shown
    status.fetch_all({"status_badges": True}, {"Claude"}, http=http, now=NOW + 60)
    assert len(http.calls) == 1  # cached for 5 minutes
    bad = FakeHttp({"https://status.claude.com/": (200, {"status": {"indicator": "weird"}})})
    got = status.fetch_all({"status_badges": True}, {"Claude"}, http=bad, now=NOW + 600)
    assert got["Claude"]["stale"] is True  # unknown indicator never shown as fresh
    status.clear_cache()


def test_status_hosts_allow_listed_only():
    assert accounts._extra_host_ok("https://www.githubstatus.com/api/v2/status.json")
    assert not accounts._extra_host_ok("http://www.githubstatus.com/api/v2/status.json")


def test_proxy_validation_and_settings():
    assert net.validate_proxy("http://proxy.local:3128/") == "http://proxy.local:3128"
    for bad in ("socks5://x:1", "http://u:p@x:1", "http://x:1/path", "http://x:99999"):
        with pytest.raises(ValueError):
            net.validate_proxy(bad)
    cfg = {}
    clean, errors = settings.apply(cfg, {"proxy_url": "http://p:8080"}, web=True)
    assert "proxy_url" in errors  # CLI / config file only
    clean, errors = settings.apply(cfg, {"proxy_url": "http://p:8080"}, web=False)
    assert not errors and clean["proxy_url"] == "http://p:8080"
    net.configure({"proxy_url": "http://p:8080"})
    try:
        assert net.proxy_url() == "http://p:8080"
        h = [x for x in net.build_opener().handlers if x.__class__.__name__ == "ProxyHandler"]
        assert h and h[0].proxies["https"] == "http://p:8080"
    finally:
        net.configure({})
    assert net.proxy_url() == ""


def test_statusline_render():
    p = statusline.parts(
        {"ts": NOW - 7200, "used_percent": 41.6, "period_end": "2026-11-06T00:00:00+00:00"},
        {"tokens": 3_400_000, "usd": 5.2},
        {"tokens": 1_250_000, "cost_usd": 3.1, "remaining_s": 8040}, NOW)
    line = statusline.render(p, NOW)
    assert line.startswith("Grok 42% used, resets in ")
    assert "(as of 2h 0m ago)" in line
    assert "≈5h block 1.2M tok $3.10, 2h 14m left" in line  # estimate marked
    assert line.endswith("today 3.4M tok $5.20")
    assert statusline.render(statusline.parts(None, None, None, NOW), NOW) == \
        "OmniMonitor: no data yet"
    assert "$" not in statusline.render(statusline.parts(None, {"tokens": 5, "usd": None},
                                                         None, NOW), NOW)
