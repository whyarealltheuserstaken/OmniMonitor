#!/usr/bin/env python3
"""Optional standalone menu bar app using `rumps` (alternative to SwiftBar).

It reuses fetch_usage() / config from usage.5m.py (same folder), so there is
still only ONE data-source function to edit.

Run:  python3 usage_menubar_rumps.py      (needs: pip3 install --user rumps)
"""
import importlib.util
import os
import webbrowser
from datetime import datetime

import rumps

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("usage_core", os.path.join(HERE, "usage.5m.py"))
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)

REFRESH_SECONDS = 300


class UsageApp(rumps.App):
    def __init__(self):
        super(UsageApp, self).__init__("…", quit_button=None)
        self.remaining_item = rumps.MenuItem("Weekly usage remaining: …")
        self.reset_item = rumps.MenuItem("Resets: …")
        self.source_item = rumps.MenuItem("Source: …")
        self.error_item = rumps.MenuItem("")
        self.updated_item = rumps.MenuItem("Last updated: never")
        self.menu = [
            self.remaining_item, self.reset_item, self.source_item, self.error_item,
            self.updated_item, None,
            rumps.MenuItem("Refresh Now", callback=self.on_refresh),
            rumps.MenuItem("Open usage page", callback=self.on_open),
            None,
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]
        self.timer = rumps.Timer(self.on_tick, REFRESH_SECONDS)
        self.timer.start()
        self.refresh()

    def refresh(self):
        cfg = core.load_config()
        self.cfg = cfg
        result, error = core.get_usage_safely(cfg)
        if result is not None:
            pct = result.remaining_percent
            self.title = "%s %d%%%s" % (core.gauge_emoji(pct), round(pct),
                                        " DEMO" if result.is_demo else "")
            self.remaining_item.title = "Weekly usage remaining: %.1f%%" % pct
            self.reset_item.title = "Resets: %s%s" % (core.fmt_local(result.resets_at),
                                                      core.fmt_until(result.resets_at))
            src = result.label + (" - FAKE DATA" if result.is_demo else "")
            self.source_item.title = "Source: " + src
        else:
            self.title = "⚠️ --%"
        self.error_item.title = ("Error: " + error) if error else ""
        self.updated_item.title = "Last updated: " + datetime.now().strftime("%-I:%M:%S %p")

    def on_tick(self, _):
        self.refresh()

    def on_refresh(self, _):
        self.refresh()

    def on_open(self, _):
        webbrowser.open(self.cfg.get("usage_page_url") or core.DEFAULT_USAGE_PAGE)


if __name__ == "__main__":
    UsageApp().run()
