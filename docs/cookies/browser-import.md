# Browser cookie import

Off by default. Per provider you can choose **Import from browser** and which browser.

## What is read

Only cookies whose **host** is in that provider’s allow-list and whose **name** is in
that provider’s allow-list (see `browser_cookies.PROVIDER_COOKIE_FILTER`). The rest of
the jar is never opened into OmniMonitor’s session store.

## Browsers

| Browser | macOS | Windows | Linux | Notes |
|---|---|---|---|---|
| Firefox | yes | yes | yes | `cookies.sqlite` is unencrypted — easiest path |
| Chrome | yes* | yes* | partial* | Needs `cryptography`. macOS: Keychain “Chrome Safe Storage” prompt |
| Edge | yes* | yes* | partial* | “Microsoft Edge Safe Storage” |
| Brave | yes* | yes* | partial* | “Brave Safe Storage” |
| Arc | yes* | – | – | “Arc Safe Storage” (macOS) |
| Safari | yes† | – | – | `Cookies.binarycookies`; needs **Full Disk Access** for the app |

\* Chromium decryption uses the OS secret store. If the prompt is denied, import fails
with a clear error — use Firefox or **in-app sign-in** instead.  
† Without Full Disk Access, macOS returns a permission error; we surface that text.

## Storage after import

Cookies are saved via `omnimonitor.sessions`: OS keyring when the optional `keyring`
package works, else `~/<config>/sessions/<provider>.cookie` with mode `0600`.
They are never logged, exported, shown in the UI, or included in Report a problem
(only `claude_plan=keyring`-style presence flags).
