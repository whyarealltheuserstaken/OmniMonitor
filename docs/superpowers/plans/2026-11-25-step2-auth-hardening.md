# Step 2: Auth hardening + verify — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every stored secret (web session, API key) lives in an encrypted vault keyed from the OS keyring; sign-in is one hardened flow run in its own process; `omnimonitor verify` checks sources and saves scrubbed responses.

**Architecture:** `vault.py` keeps a random 256-bit key in the OS keyring and AES-GCM blobs on disk. `sessions.py` and `apikeys.py` sit on top of it with one-way migration of the old plaintext files. `login_specs.py` describes each provider's sign-in; `session_login.py` runs it in a child process (`omnimonitor login <pid>`). `redact.py` scrubs secrets/PII; `verify.py` wraps the HTTP function to record and scrub responses.

**Tech Stack:** Python 3.9+, `keyring>=24`, `cryptography>=41` (now core deps), pywebview, pytest.

**Spec:** `docs/superpowers/specs/2026-11-25-suites-rework-design.md` (Auth design, Builder's calls)

## Findings that shape this step

- Release builds install `pip install .` without the `cookies` extra, so **keyring and cryptography are never bundled** and every shipped session is a plaintext file.
- `session_login` calls `webview.start()` on a background thread inside the tray process (fails on macOS: GUI must be on the main thread). App windows already use a child process (`window.py`); sign-in will too.
- `_cookie_header` keeps a cookie whose domain doesn't match when its name is in `required` (logic error); success URL `claude.ai/` also matches the login page.

## Global Constraints

- No secret (cookie, bearer, API key) in logs, exceptions, exports, diagnostics, the web page, URLs, or plaintext files (except `secrets.json` API keys when no keyring exists — Builder's call).
- No keyring → web sign-in refused with: `Sign-in needs the system keychain (Windows Credential Manager, macOS Keychain or a Secret Service on Linux). API keys and CLI logins still work.`
- Migration is one-way; legacy plaintext session files are moved into the vault, or deleted if no vault, and the source then shows "Sign in again".
- Tests never touch the real OS keyring: the keyring backend is injectable (`vault.set_backend(obj)`), tests use an in-memory fake.

## Review Focus

- Keyring present but locked / raising on read → `vault.available()` False, sign-in refused, nothing crashes, nothing written in plaintext. (Task 1 test)
- Tampered or truncated blob → `get()` returns None (InvalidTag), source shows signed out. (Task 1 test)
- Key lost (keyring entry deleted) while blobs exist → old blobs unreadable → treated as signed out; a new key is created on next `put`; stale blobs removed. (Task 1 test)
- A cookie for `evil.com` named `sessionKey` delivered by the webview → dropped. (Task 4 test)
- Scrubber: emails, JWTs, `sk-…` keys, long tokens, and values under `id`/`email`/`org`/`account`/`user` keys are replaced; numbers/percentages/timestamps kept. (Task 5 test)

---

### Task 1: `vault.py`

**Files:** Create `src/omnimonitor/vault.py`, `tests/test_vault.py`; modify `pyproject.toml` (move `keyring>=24`, `cryptography>=41` into `dependencies`; keep a `cookies` extra as empty alias for old install commands).

**Interfaces (produces):**
- `class VaultUnavailable(Exception)`
- `set_backend(kr) -> None` (tests; None = real `keyring`)
- `available() -> bool` (a keyring backend exists and a key can be read or created; not the `fail`/`null` backends)
- `put(name: str, data: bytes) -> None` (raises `VaultUnavailable`)
- `get(name: str) -> Optional[bytes]` (None if missing, unreadable, wrong key or tampered)
- `delete(name: str) -> bool`
- `names() -> List[str]`
- Storage: key under keyring service `omnimonitor.vault`, user `key-v1`, base64; blobs `<config_dir>/vault/<name>.bin` = `b"OMV1" + nonce(12) + AESGCM(ct)`, AAD = name; atomic write, chmod 600; names must match `^[a-z0-9_.:-]{1,64}$`.

- [ ] Tests: `test_roundtrip`, `test_no_backend_unavailable`, `test_locked_backend_unavailable` (fake raises on get), `test_tampered_blob_is_none`, `test_lost_key_reads_none_then_recovers`, `test_bad_name_rejected`, `test_blob_has_no_plaintext` (secret bytes not in file).
- [ ] Fail → implement → pass → commit `feat: encrypted vault keyed from the OS keyring`.

### Task 2: sessions + API keys on the vault, with migration

**Files:** Modify `sessions.py`, `apikeys.py`; Test `tests/test_vault.py`, update `tests/test_web_sessions.py` / `tests/test_providers_v3.py` where they assert `file`.

**Interfaces:**
- `sessions.save(pid, header, meta=None) -> str` returns `"vault"`; raises `sessions.SessionStoreError(<constraint message>)` if vault unavailable.
- `sessions.load(pid) -> Optional[str]`, `sessions.meta(pid) -> Dict[str,str]`, `sessions.forget(pid) -> bool`, `sessions.status() -> Dict[str, str]` (`"vault"` / `""`), `sessions.migrate_legacy() -> Dict[str, str]` (pid → `"moved"|"deleted"`), called once from `load`/`status` per process.
- Vault names: `session.<pid>`, `apikey.<NAME>`.
- `apikeys.get(name)`: env → vault → `secrets.json`; `apikeys.where(name)` → `"env"|"vault"|"file"|""`; `set_secret` writes vault when available, else file; `migrate_file() -> int` moves all file keys to vault then deletes `secrets.json` (only after every put succeeded).
- `SESSION_PROVIDERS` comes from `login_specs.ids()` (Task 3) — until then keep the tuple.

- [ ] Tests: `test_session_save_load_forget_vault`, `test_session_refused_without_vault` (message equals constraint text), `test_legacy_session_file_moved`, `test_legacy_session_file_deleted_without_vault`, `test_apikey_file_migrated_then_deleted`, `test_apikey_file_kept_without_vault`, `test_env_still_wins`.
- [ ] Fail → implement → full suite → commit `feat: sessions and API keys in the vault, one-way migration`.

### Task 3: `login_specs.py` + fixed cookie filtering

**Files:** Create `src/omnimonitor/login_specs.py`; modify `session_login.py`, `browser_cookies.py` (`PROVIDER_COOKIE_FILTER` derived from specs); Test `tests/test_login_specs.py`.

**Interfaces:**
- `LoginSpec(pid, login_url, hosts: Tuple[str,...], cookie_names: Tuple[str,...], required: Tuple[str,...], signed_in: Callable[[str], bool], webview_ok: bool = True, bearer_js: str = "")`
- `SPECS: Dict[str, LoginSpec]` for `claude_plan`, `chatgpt_web`, `cursor` (existing values, tightened `signed_in`: host in hosts AND path not starting with `/login`, `/auth`, `/sign`, `/oauth`).
- `filter_cookies(spec, cookies: Iterable[Tuple[name, value, domain]]) -> List[Tuple[name, value]]`: keep only name in `cookie_names` AND (domain matches `hosts`, or domain empty and name in `required`).
- `ids() -> Tuple[str, ...]`.

- [ ] Tests: `test_foreign_domain_dropped` (evil.com sessionKey dropped), `test_empty_domain_only_required_names`, `test_signed_in_rejects_login_page` (`https://claude.ai/login` False, `https://claude.ai/new` True, `https://evil.com/claude.ai/` False), `test_browser_filter_matches_specs`.
- [ ] Fail → implement → commit `fix: strict cookie filtering and signed-in detection from one spec table`.

### Task 4: sign-in in its own process

**Files:** Modify `session_login.py` (`start_login` spawns `omnimonitor login <pid>` via `window.child_command()` pattern; `run_login(pid) -> int` runs webview on the main thread of the child, saves via `sessions.save`, exit 0 ok / 3 cancelled / 4 store refused / 5 webview missing), `cli.py` (`login` subcommand), `appactions.py` (refuse early with the constraint message when `not vault.available()`); Test `tests/test_login_specs.py`.

**Interfaces:** `start_login(pid, on_done=None, popen=subprocess.Popen) -> str`; child never prints cookies; parent reads only the exit code and maps it to a message.

- [ ] Tests: `test_start_login_spawns_child` (fake popen gets `[... "login", "claude_plan"]`), `test_exit_codes_map_to_messages`, `test_login_refused_without_vault`.
- [ ] Fail → implement → commit `fix: run sign-in in a child process (main-thread GUI on every OS)`.

### Task 5: `redact.py` + `omnimonitor verify`

**Files:** Create `src/omnimonitor/redact.py`, `src/omnimonitor/verify.py`; modify `cli.py`; Test `tests/test_verify.py`.

**Interfaces:**
- `redact.scrub(obj: Any, secrets: Iterable[str] = ()) -> Any` (deep copy; strings: given secrets, emails → `"<email>"`, JWT `eyJ…` → `"<jwt>"`, `sk-…`/`xai-…`/`ghp_…` → `"<key>"`, any `[A-Za-z0-9_\-]{32,}` → `"<token>"`; dict values under keys matching `(?i)(^|_)(id|uuid|email|org|organization|account|user|team|name|login)s?$` → `"<redacted>"`; numbers/bools untouched).
- `verify.RecordingHttp(inner)` records `(method, url_without_query, status, json)`.
- `verify.run(cfg, pids=None, out_dir=None) -> List[dict]` (`{pid, ok, error, saved}`); writes `<config_dir>/verify/<pid>-<YYYYmmdd-HHMMSS>.json` (scrubbed, chmod 600).
- CLI `omnimonitor verify [ids...] [--json]` prints `✔/✘ <pid> …` and the folder.

- [ ] Tests: `test_scrub_patterns`, `test_scrub_keeps_numbers`, `test_verify_records_and_scrubs` (fake http with an email + bearer in the payload → saved file contains neither), `test_verify_cli_lists_results`.
- [ ] Fail → implement → commit `feat: omnimonitor verify with scrubbed response capture`.

### Task 6: builds, docs, security review, PR

- `release.yml`: add `--collect-submodules keyring --hidden-import keyring.backends.Windows --hidden-import keyring.backends.macOS --hidden-import keyring.backends.SecretService` and `--collect-submodules cryptography` on each OS.
- README Security section + `docs/cookies/`: vault, no plaintext, sign-in process, verify.
- Run ECC `security-reviewer` on the branch; fix Critical/Important with a failing test first.
- [ ] Push `feat/auth-hardening`, PR, CI green, squash-merge.
