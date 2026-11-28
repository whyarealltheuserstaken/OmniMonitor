# In-app updater: design

Date: 2026-11-27. Status: **waiting for the owner's approval**. Nothing here is built.

## Intent
OmniMonitor has no update check. People stay on old builds (0.6.0-beta repeats alerts). Add an
updater that is easy to use and cannot be used to run an altered download.

Owner's words: "add one with necessary security features", "whichever one is most convenient
and secure", releases are the stable versions and betas carry features not tried by many people.

## Approaches
1. **Signed updates, owner holds the key (chosen).** CI drafts a release; the owner signs it on
   their own computer and publishes. Control of the GitHub account alone cannot forge an update.
   Costs one command per release and a key backup.
2. **Signed by CI with a key stored in GitHub.** No release step, but whoever controls the
   repository or its workflows can sign.
3. **Notify only.** Never runs downloaded code; installing stays manual.

The owner asked for the most convenient and secure option. 1 is the stronger installing option;
one signing command keeps it convenient. 3 is still the behaviour where we cannot install.

## What exists today
- Releases: `release.yml` builds four packages and publishes immediately on a `v*` tag.
- No Windows code signing; macOS is ad-hoc signed. `SHA256SUMS.txt` sits beside the downloads,
  so it detects corruption, not tampering.
- Immutable releases are off and `main` is unprotected.
- `net.build_opener` (CA bundle, proxy) and `cryptography` (bundled in builds) can be reused.
- `/releases/latest` is still v0.4.2.

## Scope
- **One-click install: the Windows download only.** It can be tested by hand here.
- **Notice with a link: macOS, Linux and source/pip installs**, and a Windows app in a folder it
  cannot write to (no administrator prompt).
- Not included: silent or automatic installs, partial (delta) updates, downgrades, showing
  release-note HTML inside the app, code-signing certificates.

## Channels
- Version `X.Y.Z` is **stable** (GitHub release). `X.Y.Z-beta` or `X.Y.Z-beta.N` is **beta**
  (GitHub prerelease).
- Setting `update_channel`: `stable` or `beta`. Unset follows the installed build.
- Stable users only get stable versions. Beta users get the highest of either.
- Order: `0.7.0-beta < 0.7.0-beta.2 < 0.7.0 < 0.7.1-beta`. Only a strictly higher version is
  offered. Switching to stable never downgrades; the user waits for the next higher stable.
- First rollout (my proposal): `0.7.0-beta`, then the same features as stable `0.7.0` after the
  owner has used it. Anyone on an older build installs `0.7.0-beta` or later by hand once.

## What gets signed
Two extra release files:
- `update.json`: `schema`, `version`, `channel`, `released`, and per platform the asset `name`,
  `size` and `sha256`.
- `update.json.sig`: Ed25519 signature over a fixed prefix (`omnimonitor-update-v1\n`) plus the
  exact bytes of `update.json`.

The app holds a list of two public keys: the everyday key and a backup key kept offline, so a
lost key does not strand users. Nothing from a release is trusted before the signature passes.
The signed `version` and `channel` must agree with each other, with the release tag and with
GitHub's prerelease flag, so an old or beta release cannot be passed off as a newer stable one.

## Check
- Reads the public release list of the one hard-coded repository over HTTPS. No token, no
  account data, no identifier; User-Agent is `OmniMonitor`. GitHub sees the IP address.
- **On by default, once a day** (my proposal; setting `update_check`, off switch in Settings),
  plus "Check for updates" on demand. Never in demo mode. Honours `proxy_url`.
- A failure is quiet in the background, shown on demand, and never affects monitoring.
- The app tries releases from the highest version down. One with a missing or bad signature is
  skipped and logged, never offered.

## Download and verify
- Redirects are followed only over HTTPS to `github.com`, `objects.githubusercontent.com` and
  `release-assets.githubusercontent.com`.
- Size limits on every response; the asset must match the signed size and SHA-256 exactly.
- Written to a new folder under the app's data directory. The hash is checked again right
  before installing.
- The zip must contain exactly one file, `omnimonitor.exe`. Anything else is rejected.

## Install (Windows)
Only after the user clicks **Update** and confirms.
1. Run the new exe's self-test (`--version` equals the signed version; `vault-check` passes).
2. Rename the running exe to `omnimonitor.exe.old`; move the new one into its place, so
   start-at-login keeps working.
3. Start the new app and wait up to 60 s for it to report that its tray is running.
4. Success: the old app exits; the next start deletes `.old`. Failure: stop the new app,
   restore `.old`, keep running and say why.
5. If the computer stops mid-swap, `.old` stays beside the app and the release notes say how to
   rename it back.

## Where it appears
- Tray menu: "Check for updates", and "Update to X.Y.Z…" when one is available.
- One desktop notification per new version.
- Dashboard banner and a Settings section (channel, automatic check, last checked, release-notes
  link).
- CLI: `omnimonitor update --check`.
- The web page's install action is a POST behind the existing secret, Host, Origin and CSRF
  checks. It takes no address, version or file name: the app installs only what it verified.

## Releasing
1. Push a tag. `release.yml` now creates a **draft** (invisible to users and the app).
2. The owner runs `python scripts/sign_release.py vX.Y.Z[-beta]` in their own terminal, from
   the source checkout with the GitHub CLI signed in. It downloads the draft files, checks them against `SHA256SUMS.txt` and the files from the
   workflow run for that tag, confirms the tagged commit is on `main`, shows what will be
   signed, asks for the key password, uploads the two files and publishes.
- `scripts/update_keygen.py` creates both keys once, password-encrypted, outside the
  repository. Only public keys are committed. **The password is typed only by the owner, never
  in a Claude session or a command line.** The backup key goes offline (USB drive or password
  manager).
- Also: pin the GitHub Actions to full commit hashes, and turn on GitHub's immutable releases
  (a repository setting; approving this design approves it).

## What this does not protect against
- Harmful code that is merged and then built and signed. The signature proves who released it,
  not that it is safe. The script's checks rely on GitHub too.
- A computer where someone can already run programs as the owner (publisher) or the user.
- Someone who can block or hide releases can keep a user on an old version; the app shows when
  it last checked successfully.
- Windows SmartScreen warnings: that needs a paid code-signing certificate.
- Both private keys lost: users must install a new build by hand.

## Testing
Test-first with throwaway keys: bad or missing signature, changed manifest, wrong size or hash,
older or equal version, channel mismatch, off-list or plain-HTTP redirect, oversized response,
bad zip, unwritable folder, failed self-test, new app never reporting healthy (rollback), web
requests without CSRF. Then on this PC: a real update between two built exes, including a forced
rollback, using test keys and a local release source that release builds cannot be pointed at.
macOS and Linux get automated tests only.

## Related fix, same release, separate pull request (my proposal)
The Windows app cannot use the typed `secrets set` prompt (0.6.1-beta known issue). Fix it
before `0.7.0-beta`.

## Left to the builder
Module and function names, exact wording in the interface, and how the new app reports healthy.
