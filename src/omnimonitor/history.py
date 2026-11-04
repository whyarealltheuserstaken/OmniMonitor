"""Local history (SQLite, stdlib). Lives in the per-user config dir, chmod 600.

Only token counts, timestamps, model names, session ids and billing percentages are
stored - never prompts, responses, file contents or credentials.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from omnimonitor.config import secure_file, secure_mkdir

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS token_events (
    event_id TEXT NOT NULL, model TEXT NOT NULL, ts REAL NOT NULL, session_id TEXT,
    input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0, cache_creation_tokens INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0, cost_usd REAL, source TEXT,
    PRIMARY KEY (event_id, model));
CREATE INDEX IF NOT EXISTS token_events_ts ON token_events(ts);
CREATE TABLE IF NOT EXISTS turns (
    event_id TEXT PRIMARY KEY, session_id TEXT, ts REAL NOT NULL,
    duration_s REAL, duration_source TEXT);
CREATE TABLE IF NOT EXISTS scan_state (
    path TEXT PRIMARY KEY, size INTEGER, mtime REAL, offset INTEGER, turn_start_ms REAL);
CREATE TABLE IF NOT EXISTS billing_snapshots (
    ts REAL NOT NULL, used_percent REAL, raw_used_percent REAL,
    period_start TEXT, period_end TEXT, products_json TEXT);
"""


class History:
    def __init__(self, path: Path):
        self.path = Path(path)
        secure_mkdir(self.path.parent)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        secure_file(self.path)
        with self._lock:
            self._db.executescript(SCHEMA)
            self._migrate()
            self._db.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('installed_at', ?)",
                             (str(time.time()),))
            self._db.commit()

    def _migrate(self) -> None:
        """0.2.0: add token_events.project and scan_state.ctx_json to older databases."""
        def cols(table):
            return {r[1] for r in self._db.execute("PRAGMA table_info(%s)" % table)}
        if "project" not in cols("token_events"):
            self._db.execute("ALTER TABLE token_events ADD COLUMN project TEXT")
        if "ctx_json" not in cols("scan_state"):
            self._db.execute("ALTER TABLE scan_state ADD COLUMN ctx_json TEXT")
        self._db.execute("UPDATE token_events SET source='grok_sessions' WHERE source IS NULL")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ----------------------------------------------------------------- meta
    def meta(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, value))
            self._db.commit()

    # ----------------------------------------------------------------- scan state
    def scan_state(self, path: str) -> Optional[Tuple[int, float, int, Optional[float]]]:
        with self._lock:
            row = self._db.execute(
                "SELECT size, mtime, offset, turn_start_ms FROM scan_state WHERE path=?",
                (path,)).fetchone()
        return tuple(row) if row else None  # type: ignore[return-value]

    def set_scan_state(self, path: str, size: int, mtime: float, offset: int,
                       turn_start_ms: Optional[float]) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO scan_state(path, size, mtime, offset,"
                             " turn_start_ms, ctx_json) VALUES(?,?,?,?,?,"
                             " (SELECT ctx_json FROM scan_state WHERE path=?))",
                             (path, size, mtime, offset, turn_start_ms, path))
            self._db.commit()

    def scan_ctx(self, path: str) -> Dict[str, Any]:
        """Per-file parser context for adapters that need state across lines (e.g. Codex)."""
        with self._lock:
            row = self._db.execute("SELECT ctx_json FROM scan_state WHERE path=?",
                                   (path,)).fetchone()
        try:
            v = json.loads(row[0]) if row and row[0] else {}
        except ValueError:
            v = {}
        return v if isinstance(v, dict) else {}

    def set_scan_ctx(self, path: str, size: int, mtime: float, offset: int,
                     ctx: Dict[str, Any]) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO scan_state(path, size, mtime, offset,"
                             " turn_start_ms, ctx_json) VALUES(?,?,?,?,NULL,?)",
                             (path, size, mtime, offset, json.dumps(ctx)))
            self._db.commit()

    # ----------------------------------------------------------------- inserts
    def add_events(self, events: Iterable[Any], replace: bool = False) -> int:
        """Insert TokenEvent-like objects; duplicates (same event id + model) are ignored,
        or overwritten when ``replace`` (for logs where a later record supersedes an earlier
        one with the same id, e.g. a streamed message rewritten with final counts)."""
        n = 0
        verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
        with self._lock:
            cur = self._db.cursor()
            for e in events:
                new = True
                if replace:  # count only ids we had not stored yet
                    new = cur.execute("SELECT 1 FROM token_events WHERE event_id=? AND model=?",
                                      (e.event_id, e.model)).fetchone() is None
                cur.execute(
                    verb + " INTO token_events(event_id, model, ts, session_id,"
                    " input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,"
                    " reasoning_tokens, cost_usd, source, project)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (e.event_id, e.model, e.ts, e.session_id, e.input_tokens, e.output_tokens,
                     e.cache_read_tokens, e.cache_creation_tokens, e.reasoning_tokens,
                     e.cost_usd, e.source, getattr(e, "project", "") or None))
                n += 1 if (cur.rowcount > 0 and new) else 0
            self._db.commit()
        return n

    def upsert_events(self, events: Iterable[Any]) -> int:
        return self.add_events(events, replace=True)

    def add_turns(self, turns: Iterable[Any]) -> None:
        with self._lock:
            self._db.executemany(
                "INSERT OR IGNORE INTO turns VALUES(?,?,?,?,?)",
                [(t.event_id, t.session_id, t.ts, t.duration_s, t.duration_source)
                 for t in turns])
            self._db.commit()

    def add_billing_snapshot(self, used: Optional[float], raw: Optional[float],
                             start: Optional[str], end: Optional[str],
                             products: List[Tuple[str, float]], ts: Optional[float] = None
                             ) -> None:
        with self._lock:
            self._db.execute("INSERT INTO billing_snapshots VALUES(?,?,?,?,?,?)",
                             (ts or time.time(), used, raw, start, end, json.dumps(products)))
            # keep the table small: ~1 row per poll, prune older than 400 days
            self._db.execute("DELETE FROM billing_snapshots WHERE ts < ?",
                             (time.time() - 400 * 86400,))
            self._db.commit()

    # ----------------------------------------------------------------- queries
    def events(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, session_id, model, input_tokens, output_tokens, cache_read_tokens,"
                " cache_creation_tokens, reasoning_tokens, cost_usd, source, project"
                " FROM token_events ORDER BY ts").fetchall()
        keys = ("ts", "session_id", "model", "input_tokens", "output_tokens",
                "cache_read_tokens", "cache_creation_tokens", "reasoning_tokens", "cost_usd",
                "source", "project")
        return [dict(zip(keys, r)) for r in rows]

    def turns(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, session_id, duration_s, duration_source FROM turns").fetchall()
        return [dict(zip(("ts", "session_id", "duration_s", "duration_source"), r))
                for r in rows]

    def last_billing(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._db.execute(
                "SELECT ts, used_percent, raw_used_percent, period_start, period_end,"
                " products_json FROM billing_snapshots ORDER BY ts DESC LIMIT 1").fetchone()
        if not row:
            return None
        return dict(zip(("ts", "used_percent", "raw_used_percent", "period_start",
                         "period_end", "products_json"), row))

    def billing_snapshots(self, since_ts: float = 0.0) -> List[Dict[str, Any]]:
        """Real snapshots only (demo values are never stored), oldest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, used_percent, raw_used_percent, period_start, period_end"
                " FROM billing_snapshots WHERE ts >= ? ORDER BY ts", (since_ts,)).fetchall()
        return [dict(zip(("ts", "used_percent", "raw_used_percent", "period_start",
                          "period_end"), r)) for r in rows]
