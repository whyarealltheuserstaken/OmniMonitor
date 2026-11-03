# legacy/

`swiftbar/` is the original macOS-only SwiftBar plugin (`usage.5m.py`) this project grew
out of. It is kept for reference and for people who already use SwiftBar. It is **not
maintained**; new features land in `src/omnimonitor/`.

Differences from the new app: SwiftBar-only (macOS), no token stats, 5-minute refresh,
and the auth file is read only when `grok_auth_file` is set. The billing parsing rules are
the same (missing `creditUsagePercent` = 0 % used; values over 100 clamp to 100 % used).
