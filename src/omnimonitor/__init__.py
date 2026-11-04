"""OmniMonitor: local-first tray app that monitors AI usage, limits and spend across many providers."""

__version__ = "0.3.1"
APP_NAME = "omnimonitor"
APP_DISPLAY_NAME = "OmniMonitor"
APP_ID = "com.omnimonitor.tray"

# Names used before the project was renamed to OmniMonitor. Kept so existing installs keep
# working: the old config dir is migrated (or read as a fallback) and old autostart entries
# are cleaned up.
LEGACY_APP_NAME = "grok-usage-tray"
LEGACY_APP_ID = "io.github.grok-usage-tray"
LEGACY_ENV_PREFIX = "GROK_USAGE_TRAY_"
