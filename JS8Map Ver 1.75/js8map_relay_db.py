"""RelayDatabase — extracted verbatim from JS8Map.py.

Moved by tools/extract_class.py. Behavior is byte-identical to the
original class; only its location changed.
"""
from __future__ import annotations

from js8map_util import _utc_stamp
import sqlite3
import threading

class RelayDatabase:
    """Stores relay paths discovered via @ALLCALL / @GROUP QUERY CALL YES responses.
    Kept in js8_relay.db — fully independent of js8_spots.db so relay data
    can be cleared or removed without affecting core spot history.

    Schema:
        relay_paths(id, via_call, target_call, snr, logged_ts, query_ts)
        UNIQUE(via_call, target_call) ON CONFLICT REPLACE — newest path wins.
    """

    def __init__(self, path: str = 'js8_relay.db'):
        self.path    = path
        self._lock   = threading.Lock()
        self._my_call = ''   # set by HamMapApp — used to exclude operator from relay paths
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def _init_db(self):
        with self._lock:
            with self._connect() as conn:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS relay_paths (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        via_call    TEXT NOT NULL,
                        target_call TEXT NOT NULL,
                        snr         REAL,
                        logged_ts   TEXT NOT NULL,
                        query_ts    TEXT,
                        UNIQUE(via_call, target_call) ON CONFLICT REPLACE
                    )''')
                conn.execute(
                    'CREATE INDEX IF NOT EXISTS idx_relay_target'
                    ' ON relay_paths(target_call)')
                conn.execute(
                    'CREATE INDEX IF NOT EXISTS idx_relay_via'
                    ' ON relay_paths(via_call)')

    def log_path(self, via_call: str, target_call: str,
                 snr=None, query_ts: str = None):
        """Store or update a relay path. UNIQUE(via, target) means newest wins."""
        ts = _utc_stamp()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    'INSERT OR REPLACE INTO relay_paths'
                    '(via_call, target_call, snr, logged_ts, query_ts)'
                    ' VALUES (?,?,?,?,?)',
                    (via_call.upper().strip(),
                     target_call.upper().strip(),
                     snr, ts, query_ts))

    def get_all(self) -> list:
        """Return all paths: [(via_call, target_call, snr, logged_ts), ...]"""
        with self._connect() as conn:
            return conn.execute(
                'SELECT via_call, target_call, snr, logged_ts'
                ' FROM relay_paths ORDER BY target_call, snr DESC'
            ).fetchall()

    def clear_for_target(self, target_call: str) -> None:
        """Remove stored paths for one target — called when re-arming so old
        paths don't bleed into the new relay filter view."""
        tc = target_call.upper().strip()
        with self._lock:
            with self._connect() as conn:
                conn.execute('DELETE FROM relay_paths WHERE target_call = ?', (tc,))

    def clear_all(self):
        with self._lock:
            with self._connect() as conn:
                conn.execute('DELETE FROM relay_paths')
