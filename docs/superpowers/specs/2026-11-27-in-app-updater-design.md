# In-app updater: design

Date: 2026-11-27. The owner asked for "a simple in-app updater that is light and secure and is
fast", so the longer first draft was cut to this.

## Approach
The owner signs each release with a key that is not on GitHub. Rejected: a key stored in GitHub
(whoever controls the repository could sign) and notify-only (updates stay manual).

## Behaviour
- Channels: `X.Y.Z` stable, `X.Y.Z-beta[.N]` beta. `update_channel` = `auto` (follow the
  installed build), `stable` or `beta`. Only a strictly higher version is offered.
- Check: daily by default (`update_check`), not in demo mode, plus on demand. Reads the public
  release list of the hard-coded repository.
- Signature: `update.json` = signed text (version, asset size and SHA-256) + Ed25519
  signature. Public keys are compiled in (`update_key.py`). With none, notice only.
- Addresses are built from the version number. HTTPS GitHub hosts only, redirects included.
- Install (Windows download only, on a menu click): verify, require a zip holding only
  `omnimonitor.exe`, self-test `--version`, rename the running exe to `.old`, move the new one
  in, start it, quit. If the swap or launch fails, restore. The new app deletes `.old` once its
  tray is up.
- Elsewhere: the menu item opens the release page.
- No new dependencies.

## Publishing
`release.yml` creates drafts. `scripts/sign_release.py sign TAG` checks the download against
`SHA256SUMS.txt`, signs with the password-encrypted key, uploads `update.json`, publishes.

## Deliberately left out
Backup key, waiting for the new app to report healthy before dropping the old one, install
button in the web page, silent installs, delta updates, code-signing certificate.

## Limits
- If the new app starts but then fails before its tray appears, the user renames `.old` back.
- A signature proves who released a build, not that its code is safe.
- Losing the key means one manual install for everyone.
- A real update through two signed releases cannot be tried until both exist.
