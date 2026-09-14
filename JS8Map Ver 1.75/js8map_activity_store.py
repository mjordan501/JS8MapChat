#!/usr/bin/env python3
"""JS8Map activity persistence and database-derived station queries.

This module owns the complete ``js8_spots.db`` responsibility that previously
lived in ``JS8Map.py``: schema creation, migrations, locking, writes, cleanup,
and the two database-query builders used by the map composition layer.

FastChat temporarily reads the same SQLite schema directly.  During that
compatibility stage, ``READ_CONTRACT_VERSION`` identifies the schema contract
that must remain readable until the local API replaces those direct reads.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone as _dt_timezone

READ_CONTRACT_VERSION = 1

_DB_TS_FMT = '%Y-%m-%d %H:%M:%S UTC'
GRID_RE = re.compile(r'^[A-Ra-r]{2}[0-9]{2}([A-Xa-x]{2})?$')
GRID_IN_TEXT = re.compile(r'\b([A-R]{2}[0-9]{2}(?:[A-X]{2})?)\b', re.I)
SNR_RE_GLOBAL = re.compile(r'\bSNR\s*([+\-]?\d+)', re.I)
_RSNR_RE = re.compile(r'\[RSNR:([+\-]?\d+)\]')
_HEARING_CALL_RE = re.compile(
    r'\b([A-Z0-9]{1,3}[0-9][A-Z]{1,4}(?:/[A-Z0-9]+)?)\b'
)


class ActivityStoreError(RuntimeError):
    """Base error for activity-store initialization and compatibility failures."""


class UnsupportedSchemaError(ActivityStoreError):
    """Raised when a database was written by a newer unsupported schema."""


def _utc_now():
    return datetime.now(_dt_timezone.utc)


def _utc_stamp():
    return _utc_now().strftime(_DB_TS_FMT)


def _utc_cutoff(delta):
    return (_utc_now() - delta).strftime(_DB_TS_FMT)


def _base(call: str) -> str:
    return call.split('/')[0] if '/' in call else call


def norm_call(value) -> str:
    return str(value or '').upper().strip()


def is_valid_grid(grid: str) -> bool:
    return bool(grid and GRID_RE.match(grid.strip()))


def is_precise_grid(grid: str) -> bool:
    grid = (grid or '').strip().upper()
    return bool(grid and len(grid) == 6 and GRID_RE.match(grid))


class SpotDatabase:
    """
    Self-managed SQLite database for JS8Call activity data.
    The composition root supplies the database path explicitly.
    Thread-safe via an internal lock.
    """

    DB_FILENAME  = 'js8_spots.db'
    SCHEMA_VERSION = 4   # increment when adding new tables/columns
    READ_CONTRACT_VERSION = READ_CONTRACT_VERSION

    # ── SQL constants ─────────────────────────────────────────────────────
    SQL_INSERT_SPOT = (
        "INSERT INTO spots "
        "(callsign,grid,snr,freq,offset,speed,timestamp,source)"
        " VALUES (?,?,?,?,?,?,?,?)"
    )
    SQL_INSERT_DIRECTED = (
        "INSERT INTO directed "
        "(from_call,to_call,text,snr,freq,offset,timestamp)"
        " VALUES (?,?,?,?,?,?,?)"
    )
    SQL_INSERT_DIRECTED_NO_OFFSET = (
        "INSERT INTO directed "
        "(from_call,to_call,text,snr,freq,timestamp)"
        " VALUES (?,?,?,?,?,?)"
    )
    SQL_INSERT_BAND_ACTIVITY = (
        "INSERT INTO band_activity "
        "(callsign,snr,freq,offset,speed,text,timestamp)"
        " VALUES (?,?,?,?,?,?,?)"
    )
    SQL_UPSERT_MY_CALLSIGN = (
        "INSERT OR REPLACE INTO my_station(key,value) VALUES ('callsign',?)"
    )
    SQL_UPSERT_MY_GRID = (
        "INSERT OR REPLACE INTO my_station(key,value) VALUES ('grid',?)"
    )
    SQL_INSERT_INBOX_MSG = (
        "INSERT OR IGNORE INTO inbox_msgs(msg_id, from_call, first_seen)"
        " VALUES (?,?,?)"
    )
    SQL_INSERT_API_LOG = (
        "INSERT INTO api_log(direction,msg_type,payload,timestamp)"
        " VALUES (?,?,?,?)"
    )

    def __init__(self, db_path: str):
        if not db_path:
            raise ValueError('SpotDatabase requires an explicit database path')
        self.path = str(db_path)
        self._lock = threading.Lock()
        self._init_db()
        self._migrate_db()
        self.cleanup_old_data()
        self.vacuum_if_needed()

    # ── Internal helpers ──────────────────────────────────────

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _get_schema_version(self, conn) -> int:
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_meta "
                         "(key TEXT PRIMARY KEY, value TEXT)")
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key='version'").fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0

    def _set_schema_version(self, conn, version: int):
        conn.execute("INSERT OR REPLACE INTO schema_meta (key,value) VALUES ('version',?)",
                     (str(version),))

    def _migrate_db(self):
        """Apply schema migrations — safe to run on every startup."""
        with self._connect() as conn:
            ver = self._get_schema_version(conn)
            if ver > self.SCHEMA_VERSION:
                raise UnsupportedSchemaError(
                    f'Database schema {ver} is newer than supported schema '
                    f'{self.SCHEMA_VERSION}'
                )

            if ver < 1:   # original columns
                for sql in [
                    "ALTER TABLE band_activity ADD COLUMN text TEXT DEFAULT ''",
                    "ALTER TABLE directed ADD COLUMN offset REAL DEFAULT 0",
                ]:
                    try: conn.execute(sql)
                    except Exception: pass
                try:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS inbox_msgs (
                            msg_id     TEXT PRIMARY KEY,
                            from_call  TEXT NOT NULL,
                            first_seen TEXT NOT NULL
                        )""")
                except Exception: pass

            if ver < 4:   # group_activity table
                try:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS group_activity (
                            call       TEXT NOT NULL,
                            group_name TEXT NOT NULL,
                            last_ts    TEXT NOT NULL,
                            PRIMARY KEY (call, group_name)
                        )""")
                except Exception:
                    pass

            if ver < 2:   # pending_queries for relay feature
                try:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS pending_queries (
                            target_call TEXT NOT NULL,
                            asked_at    TEXT NOT NULL,
                            expires_at  TEXT NOT NULL
                        )""")
                except Exception: pass

            if ver < 3:   # future migrations go here
                pass

            self._set_schema_version(conn, self.SCHEMA_VERSION)
            conn.commit()

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS spots (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    callsign  TEXT    NOT NULL,
                    grid      TEXT    DEFAULT '',
                    snr       REAL    DEFAULT 0,
                    freq      REAL    DEFAULT 0,
                    offset    REAL    DEFAULT 0,
                    speed     INTEGER DEFAULT 0,
                    timestamp TEXT    NOT NULL,
                    source    TEXT    DEFAULT 'RX.SPOT'
                );
                CREATE INDEX IF NOT EXISTS idx_spots_call ON spots(callsign);
                CREATE INDEX IF NOT EXISTS idx_spots_ts   ON spots(timestamp);

                CREATE TABLE IF NOT EXISTS directed (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_call TEXT    NOT NULL,
                    to_call   TEXT    NOT NULL,
                    text      TEXT    DEFAULT '',
                    snr       REAL    DEFAULT 0,
                    freq      REAL    DEFAULT 0,
                    offset    REAL    DEFAULT 0,
                    timestamp TEXT    NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_dir_from ON directed(from_call);
                CREATE INDEX IF NOT EXISTS idx_dir_to   ON directed(to_call);
                CREATE INDEX IF NOT EXISTS idx_dir_ts   ON directed(timestamp);

                CREATE TABLE IF NOT EXISTS band_activity (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    callsign  TEXT    NOT NULL,
                    snr       REAL    DEFAULT 0,
                    freq      REAL    DEFAULT 0,
                    offset    REAL    DEFAULT 0,
                    speed     INTEGER DEFAULT 0,
                    text      TEXT    DEFAULT '',
                    timestamp TEXT    NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ba_call ON band_activity(callsign);
                CREATE INDEX IF NOT EXISTS idx_ba_ts   ON band_activity(timestamp);

                CREATE TABLE IF NOT EXISTS my_station (
                    key   TEXT PRIMARY KEY,
                    value TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS watched_calls (
                    callsign TEXT PRIMARY KEY
                );

                CREATE TABLE IF NOT EXISTS inbox_msgs (
                    msg_id     TEXT PRIMARY KEY,
                    from_call  TEXT NOT NULL,
                    first_seen TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS api_log (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    direction TEXT,
                    msg_type  TEXT,
                    payload   TEXT,
                    timestamp TEXT NOT NULL
                );
            """)

    # ── Write methods ─────────────────────────────────────────

    def add_spot(self, callsign: str, grid: str, snr: float,
                 freq: float, offset: float, speed: int,
                 timestamp: str, source: str = 'RX.SPOT'):
        with self._lock:
            with self._connect() as conn:
                conn.execute(self.SQL_INSERT_SPOT,
                    (callsign.upper().strip(),
                     grid.upper()[:6] if grid else '',
                     snr, freq, offset, speed, timestamp, source)
                )

    def add_directed(self, from_call: str, to_call: str,
                     text: str, snr: float, freq: float, timestamp: str,
                     offset: float = 0):
        with self._lock:
            with self._connect() as conn:
                try:
                    conn.execute(self.SQL_INSERT_DIRECTED,
                        (from_call.upper().strip(), to_call.upper().strip(),
                         text or '', snr, freq, float(offset or 0), timestamp)
                    )
                except Exception:
                    # Fallback for existing DBs where offset column may not
                    # exist yet (migration runs at startup but connection
                    # pooling can race). Write without offset rather than drop
                    # the record entirely.
                    try:
                        conn.execute(self.SQL_INSERT_DIRECTED_NO_OFFSET,
                            (from_call.upper().strip(), to_call.upper().strip(),
                             text or '', snr, freq, timestamp)
                        )
                    except Exception:
                        pass  # never crash the message handler

    def add_band_activity(self, callsign: str, snr: float, freq: float,
                          offset: float, speed: int, timestamp: str, text: str = ''):
        with self._lock:
            with self._connect() as conn:
                conn.execute(self.SQL_INSERT_BAND_ACTIVITY,
                    (callsign.upper().strip(), snr, freq, offset, speed,
                     text or '', timestamp)
                )

    def set_my_station(self, callsign: str, grid: str):
        with self._lock:
            with self._connect() as conn:
                conn.execute(self.SQL_UPSERT_MY_CALLSIGN,
                    (callsign.upper().strip(),))
                conn.execute(self.SQL_UPSERT_MY_GRID,
                    (grid.upper()[:6] if grid else '',))


    def add_inbox_msg(self, msg_id: str, from_call: str, timestamp: str):
        """
        Record a MSG ID notification (e.g. 'MSG ID 211' embedded in heartbeat).
        INSERT OR IGNORE preserves first_seen — repeated heartbeats carrying the
        same MSG ID do NOT update the timestamp, so the inbox flag only fires
        when first_seen >= session_start (i.e. it is genuinely new this session).
        """
        with self._lock:
            with self._connect() as conn:
                conn.execute(self.SQL_INSERT_INBOX_MSG,
                    (str(msg_id), from_call.upper().strip(), timestamp)
                )

    def clear_inbox_poll_msgs(self):
        """Remove all INBOX_ entries — called before repopulating from a fresh
        INBOX.GET_MESSAGES poll so read messages clear off the map."""
        with self._lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM inbox_msgs WHERE msg_id LIKE 'INBOX_%'")

    def add_log_entry(self, direction: str, msg_type: str, payload: str):
        ts = _utc_stamp()
        with self._lock:
            with self._connect() as conn:
                conn.execute(self.SQL_INSERT_API_LOG,
                    (direction, msg_type, payload[:2000], ts)
                )
                # Keep only last 500 log entries
                conn.execute(
                    "DELETE FROM api_log WHERE id NOT IN "
                    "(SELECT id FROM api_log ORDER BY id DESC LIMIT 500)"
                )

    # ── Read methods ──────────────────────────────────────────

    def get_my_station(self) -> dict:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM my_station").fetchall()
        return {r[0]: r[1] for r in rows}

    def get_api_log(self, limit: int = 200) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT direction, msg_type, payload, timestamp"
                " FROM api_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return rows

    def get_spot_count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM spots").fetchone()[0]

    # ── Watched calls ─────────────────────────────────────────

    def load_watched_calls(self) -> set:
        with self._connect() as conn:
            rows = conn.execute("SELECT callsign FROM watched_calls").fetchall()
        return {r[0] for r in rows}

    def upsert_group_activity(self, call: str, group_name: str, ts: str):
        """Log that call sent a directed message to group_name at ts."""
        with self._lock:
            with self._connect() as conn:
                conn.execute("""
                    INSERT INTO group_activity(call, group_name, last_ts)
                    VALUES(?,?,?)
                    ON CONFLICT(call, group_name) DO UPDATE SET last_ts=excluded.last_ts
                """, (call, group_name, ts))

    def load_group_active_calls(self, group_colors: dict, cutoff_ts: str) -> dict:
        """Return {call: {color, group, all_groups}} for stations active on
        watched groups since cutoff_ts. Most-recent group is primary."""
        if not group_colors:
            return {}
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT call, group_name, last_ts FROM group_activity "
                    "WHERE last_ts >= ? ORDER BY last_ts DESC",
                    (cutoff_ts,)
                ).fetchall()
        result = {}
        for call, group_name, _ts in rows:
            if group_name not in group_colors:
                continue
            color = group_colors[group_name]
            if call not in result:
                result[call] = {
                    'color':      color,
                    'group':      group_name,
                    'all_groups': {group_name: color},   # dict: {name → color}
                }
            elif group_name not in result[call]['all_groups']:
                result[call]['all_groups'][group_name] = color
        return result

    def set_watched_calls(self, callsigns: set):
        """Replace the entire watched calls list."""
        with self._lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM watched_calls")
                for c in callsigns:
                    conn.execute(
                        "INSERT OR IGNORE INTO watched_calls(callsign) VALUES (?)",
                        (c.upper().strip(),)
                    )

    def clear_spots(self, clear_directed: bool = True, clear_log: bool = True):
        """Wipe spot/band_activity data. Directed cleared only when explicitly requested.
        clear_log=False preserves the api_log across restarts for post-session review."""
        with self._lock:
            with self._connect() as conn:
                conn.execute('DELETE FROM spots')
                conn.execute('DELETE FROM band_activity')
                if clear_directed:
                    conn.execute('DELETE FROM directed')
                if clear_log:
                    conn.execute('DELETE FROM api_log')

    def cleanup_old_data(self, days: int = 7):
        """Remove data older than `days` days to keep the DB from growing forever.

        7 days, not months: spot/band rows describe propagation at the moment
        they were heard and are worthless once conditions move on, and `directed`
        is only read back as recent context (the FastChat callsign popup's chat
        pane and history search), so a week is as far back as anything looks.
        """
        cutoff = _utc_cutoff(timedelta(days=days))
        with self._lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM spots         WHERE timestamp < ?", (cutoff,))
                conn.execute("DELETE FROM directed      WHERE timestamp < ?", (cutoff,))
                conn.execute("DELETE FROM band_activity WHERE timestamp < ?", (cutoff,))
                inbox_cutoff_30d = _utc_cutoff(timedelta(days=30))
                conn.execute("DELETE FROM inbox_msgs    WHERE first_seen < ?", (inbox_cutoff_30d,))

    def vacuum_if_needed(self, min_free_pages: int = 16) -> bool:
        """Reclaim disk space left behind by DELETEs and return it to the OS.

        cleanup_old_data, clear_spots and the api_log trim in add_log_entry all
        DELETE rows, but SQLite only moves those pages onto the file's free-list
        and reuses them internally -- the file on disk never shrinks. Without an
        explicit VACUUM the .db only ever grows. Rewriting the file is not free,
        so only do it once enough free pages have accumulated to be worth it.
        Also truncates the WAL sidecar, which otherwise keeps its high-water
        mark for the life of the file. Returns True if a VACUUM actually ran.
        """
        with self._lock:
            conn = sqlite3.connect(self.path, timeout=15)
            try:
                # VACUUM cannot run inside a transaction; isolation_level=None
                # stops the driver opening one implicitly.
                conn.isolation_level = None
                if conn.execute("PRAGMA freelist_count").fetchone()[0] < min_free_pages:
                    return False
                conn.execute("VACUUM")
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                return True
            finally:
                conn.close()


def build_stations_from_db(db: SpotDatabase, my_callsign: str,
                            cutoff_dt: datetime,
                            time_filter_label: str = 'All time',
                            my_freq: float = 0,
                            session_start: str = ""):
    """
    Returns (stations_heard, stations_hearing_me, via_heartbeat,
             no_grid_active, snr_of_me, inbox_senders, pending_msg_senders, debug_log).
    All data comes from our own SQLite — no JS8Spotter needed.
    """
    my_call    = norm_call(my_callsign)
    cutoff_str = cutoff_dt.strftime(_DB_TS_FMT)
    debug_log  = []

    with sqlite3.connect(db.path, timeout=15) as conn:
        conn.execute("PRAGMA journal_mode=WAL")

        # ── Phase 1: All-time grid registry ──────────────────
        # Collect the most recent VALID grid for every callsign, no time filter.
        grids: dict = {}   # callsign → grid

        # Grids registry: best non-empty grid per callsign across ALL sources.
        # WHERE grid != '' ensures empty BAND_ACTIVITY spots never overwrite
        # a real grid from CALL_ACTIVITY (which carries JS8Call's decoded grid).
        # CALL_ACTIVITY grids are filtered to 6-digit only at storage time so
        # 4-digit grids never reach the registry — FCC ZIP3 is more accurate.
        rows = conn.execute(
            "SELECT callsign, grid"
            " FROM spots"
            " WHERE grid != '' AND grid IS NOT NULL"
            " GROUP BY callsign"
            " ORDER BY MAX(timestamp)"
        ).fetchall()
        for callsign, grid in rows:
            if is_valid_grid(str(grid or '')):
                grids[callsign] = grid.upper()[:6]
                base = _base(callsign)
                if base != callsign:
                    grids.setdefault(base, grid.upper()[:6])

        # Also mine grids from directed message text.
        # Exclude HEARING responses — they list callsigns after the HEARING
        # keyword which the grid regex would misidentify as Maidenhead squares.
        # Limit to 30 days to keep query fast on large DBs.
        _grid_cutoff = _utc_cutoff(timedelta(days=30))
        rows = conn.execute(
            "SELECT from_call, text, MAX(timestamp) as ts"
            " FROM directed"
            " WHERE UPPER(text) NOT LIKE '%HEARING%'"
            "   AND timestamp >= ?"
            " GROUP BY from_call ORDER BY ts",
            (_grid_cutoff,)
        ).fetchall()
        for from_call, text, _ in rows:
            gm = GRID_IN_TEXT.search(str(text or ''))
            if gm and is_valid_grid(gm.group(1)):
                g = gm.group(1).upper()[:6]
                grids[from_call] = g
                base = _base(from_call)
                if base != from_call:
                    grids.setdefault(base, g)

        # Also scan band_activity TEXT for embedded grid squares.
        # IMPORTANT: only use HEARTBEAT frames — the grid in a heartbeat
        # belongs to the SENDER of that heartbeat.  Directed messages like
        # "KA2YNT: KW3KW GRID FM29" contain ANOTHER station's grid, not
        # KA2YNT's.  Extracting from those gives the wrong callsign a wrong
        # grid.  HEARTBEAT frames like "W3BFO/P: @ALLCALL HEARTBEAT FM18"
        # always carry the sender's own grid.
        rows = conn.execute(
            "SELECT callsign, text, MAX(timestamp) as ts"
            " FROM band_activity"
            " WHERE text != '' AND UPPER(text) LIKE '%HEARTBEAT%'"
            " GROUP BY callsign ORDER BY ts"
        ).fetchall()
        for callsign, text, _ in rows:
            if callsign in grids:
                continue  # already have a reliable grid
            gm = GRID_IN_TEXT.search(str(text or ''))
            if gm and is_valid_grid(gm.group(1)):
                g = gm.group(1).upper()[:6]
                grids[callsign] = g
                base = _base(callsign)
                if base != callsign:
                    grids.setdefault(base, g)

        debug_log.append(f"ℹ BUILD Grid registry: {len(grids)} callsigns with known grids")

        # ── Phase 2: Active stations in time window ───────────
        # Get the most recent row per callsign from the spots table.
        # Only autonomous transmissions count for 'active' / time filtering.
        # RX.DIRECTED = SNR responses to our queries (not independent band TX).
        # RX.CALL_ACTIVITY = JS8Call tracks all decodes incl. directed replies;
#                     its UTC reflects the last ANY decode, not last broadcast.
        # Both inflate recency vs JS8Call's Age column. Only RX.SPOT and
        # RX.BAND_ACTIVITY use the true autonomous-decode UTC timestamp.
        rows = conn.execute("""
            SELECT s.callsign, s.snr, s.timestamp, s.freq, s.offset, s.speed, c.cnt, c.min_ts
            FROM spots s
            JOIN (
                SELECT callsign,
                       MAX(timestamp) AS max_ts,
                       MIN(timestamp) AS min_ts,
                       COUNT(*)       AS cnt
                FROM spots
                WHERE timestamp >= ? AND callsign != ?
                  AND source NOT IN ('RX.CALL_ACTIVITY')
                  AND (? = 0 OR freq = 0 OR ABS(freq - ?) <= 500000)
                GROUP BY callsign
            ) c ON s.callsign = c.callsign
                AND s.timestamp = c.max_ts
                AND s.source NOT IN ('RX.CALL_ACTIVITY')
            GROUP BY s.callsign
        """, (cutoff_str, my_call, my_freq, my_freq)).fetchall()

        active: dict = {}
        for row in rows:
            call = row[0]
            active[call] = {
                'snr':          float(row[1] or 0),
                'last_seen':    str(row[2] or ''),
                'freq':         float(row[3] or 0),
                'offset':       float(row[4] or 0),
                'speed':        int  (row[5] or 0),
                'count':        int  (row[6] or 1),
                'first_seen_ts': str(row[7] or ''),
            }

        # Supplement with band_activity (some stations only appear there)
        rows = conn.execute("""
            SELECT b.callsign, b.snr, b.timestamp, b.freq, b.offset, b.speed, c.cnt, c.min_ts
            FROM band_activity b
            JOIN (
                SELECT callsign,
                       MAX(timestamp) AS max_ts,
                       MIN(timestamp) AS min_ts,
                       COUNT(*)       AS cnt
                FROM band_activity
                WHERE timestamp >= ? AND callsign != ?
                  AND (? = 0 OR freq = 0 OR ABS(freq - ?) <= 500000)
                GROUP BY callsign
            ) c ON b.callsign = c.callsign
                AND b.timestamp = c.max_ts
            GROUP BY b.callsign
        """, (cutoff_str, my_call, my_freq, my_freq)).fetchall()

        for row in rows:
            call = row[0]
            ts   = str(row[2] or '')
            if call not in active or ts > active[call]['last_seen']:
                active[call] = {
                    'snr':           float(row[1] or 0),
                    'last_seen':     ts,
                    'freq':          float(row[3] or 0),
                    'offset':        float(row[4] or 0),
                    'speed':         int  (row[5] or 0),
                    'count':         int  (row[6] or 1),
                    'first_seen_ts': str(row[7] or ''),
                }
            elif call in active and not active[call].get('first_seen_ts'):
                active[call]['first_seen_ts'] = str(row[7] or '')

        # first_seen_ts is now built directly into active dict from MIN(timestamp)
        # in the spots and band_activity queries above — no separate query needed.
        first_seen_map: dict = {call: info.get('first_seen_ts', '')
                                for call, info in active.items()}

        src_counts = {}
        if active:
            try:
                src_rows = conn.execute(
                    "SELECT source, COUNT(DISTINCT callsign) FROM spots"
                    " WHERE timestamp >= ? AND callsign != ?"
                    " GROUP BY source", (cutoff_str, my_call)
                ).fetchall()
                src_counts = {r[0]: r[1] for r in src_rows}
            except Exception:
                pass
        src_summary = '  '.join(
            f"{str(s).replace('RX.','').replace('_ACTIVITY','_CA')}:{n}"
            for s, n in sorted(
                ((k or 'UNKNOWN', v) for k, v in src_counts.items()),
                key=lambda x: x[0]
            )
        )
        debug_log.append(
            f"ℹ BUILD Active in window: {len(active)} stations"
            + (f"  [{src_summary}]" if src_summary else "")
        )

        # ALL-TIME FALLBACK REMOVED -- it lied about recency.
        # This used to run when `active` came back empty: it re-queried spots
        # with NO time filter and plotted every station ever recorded, while
        # the Show dropdown still read "Last 30 minutes". Nothing on screen
        # said the fallback had fired -- fallback_used only ever reached an
        # internal debug line the operator never sees.
        # It was invisible for as long as JS8Map wiped `spots` at startup:
        # "all time" could only mean "this session", which was empty. Once
        # 270ae90 kept history across restarts, "all time" became up to 7 days
        # and stale stations started appearing inside short windows. Reported
        # from the field: two callsigns from 14 hours earlier shown under a
        # 30-minute filter.
        # An EMPTY MAP is the truthful answer to "what have I heard in the last
        # 30 minutes". For an operator reading band conditions, a false signal
        # is worse than no signal. Retained history is what the fallback was
        # really compensating for, and that now exists.
        fallback_used = False

        # ── Supplement active with CALL_ACTIVITY-only stations ──────────
        # RX.CALL_ACTIVITY spots are excluded from the active query above
        # (their timestamps can be stale — JS8Call keeps stations in its
        # list long after they were last decoded).  But some stations ONLY
        # appear in CALL_ACTIVITY and never in BAND_ACTIVITY within the
        # window (e.g. weak stations decoded once then gone quiet).
        # Without this supplement they are silently invisible — active is
        # empty for them, no_grid is never populated, FCC fallback never
        # runs.  Solution: add any CALL_ACTIVITY station NOT already in
        # active, using the most recent CALL_ACTIVITY timestamp for that
        # callsign.  Mark them with a sentinel so FCC fallback can run.
        ca_rows = conn.execute("""
            SELECT callsign, snr, MAX(timestamp) as ts, freq, offset, speed, grid
            FROM spots
            WHERE source = 'RX.CALL_ACTIVITY'
              AND callsign != ?
              AND timestamp >= ?
            GROUP BY callsign
        """, (my_call, cutoff_str)).fetchall()
        for row in ca_rows:
            call = row[0]
            if call and call not in active:
                ca_grid = str(row[6] or '').upper().strip()
                # Only accept 6-digit grids from CALL_ACTIVITY — 4-digit grids
                # (~150mi accuracy) are less useful than FCC ZIP3 (~50mi).
                resolved_grid = ca_grid if is_precise_grid(ca_grid) else (
                    grids.get(call) or grids.get(_base(call)) or '')
                active[call] = {
                    'snr':       float(row[1] or 0),
                    'last_seen': str(row[2] or ''),
                    'freq':      float(row[3] or 0),
                    'offset':    float(row[4] or 0),
                    'speed':     int(row[5] or 0),
                    'count':     1,
                }
                # If we have a real grid, put them straight into stations_heard
                # and grids registry — skip the no_grid path entirely
                if resolved_grid:
                    grids[call] = resolved_grid
                    base = _base(call)
                    if base != call:
                        grids.setdefault(base, resolved_grid)

        # ── Heartbeat detection ───────────────────────────────
        via_heartbeat: set = set()
        rows = conn.execute(
            "SELECT DISTINCT from_call FROM directed"
            " WHERE (to_call LIKE '@HB%' OR text LIKE '%@HB%') AND timestamp >= ?",
            (cutoff_str,)
        ).fetchall()
        for row in rows:
            via_heartbeat.add(row[0])

        # ── Hears me (directed to MY_CALL in window) ─────────
        # Also captures last_seen timestamp from directed for proper popup display.
        hears_me:       set  = set()
        hears_last_ts:  dict = {}   # callsign -> last directed timestamp to me
        snr_of_me:      dict = {}
        # SNR proofs always look back at least 2 hours regardless of the active
        # time filter.  A short filter like "Last 15 min" controls which stations
        # appear as *heard* (blue); it must NOT prune SNR reports that arrived
        # earlier in the same session or those mutual contacts silently disappear.
        # Formula: min() chooses the earlier datetime — so if the filter is already
        # wider than 2h (e.g. "Last 30 days") it stays wide; if narrower (e.g. "Last
        # 15 min") it expands to 2h.
        hears_cutoff    = min(cutoff_dt, _utc_now() - timedelta(hours=2))
        hears_cutoff_str = hears_cutoff.strftime(_DB_TS_FMT)
        if my_call:
            rows = conn.execute(
                "SELECT from_call, text, snr, MAX(timestamp) as ts"
                " FROM directed WHERE to_call = ? AND timestamp >= ?"
                " GROUP BY from_call",
                (my_call, hears_cutoff_str)
            ).fetchall()
            for row in rows:
                from_c  = norm_call(row[0])
                text    = str(row[1] or '')
                last_ts = str(row[3] or '')
                hears_me.add(from_c)
                hears_last_ts[from_c] = last_ts
                # Parse the REPORTED SNR: [RSNR:xx] marker first, then text regex
                rsnr_m = _RSNR_RE.search(text)
                if rsnr_m:
                    snr_val = int(rsnr_m.group(1))
                elif 'SNR' in text.upper() and 'HEARING' not in text.upper() and 'SNR?' not in text.upper():
                    snr_m = SNR_RE_GLOBAL.search(text)
                    snr_val = int(snr_m.group(1)) if snr_m else None
                else:
                    snr_val = None
                if snr_val is not None:
                    snr_of_me[from_c] = snr_val
                    if _base(from_c) != from_c:
                        snr_of_me[_base(from_c)] = snr_val

            # ── They-Hear-Me SNR proof (latest SNR-BEARING directed row) ──
            # The GROUP BY above returns only the most-recent directed row's
            # text per callsign. Mid-QSO that newest frame is usually a plain
            # message, so its text has no SNR token and snr_of_me is left unset
            # even though an earlier in-window frame reported our SNR -- the
            # station then shows MUTUAL (hears_me is added unconditionally
            # above) but the popup's "They Hear Me" row stays blank. Restricting
            # this query to SNR-bearing rows guarantees the grouped winner per
            # callsign actually carries the reported value. The directed `snr`
            # column is OUR rx of them (wrong direction), so the value must come
            # from the text, exactly as Path A and the band_activity scan do.
            snr_rows = conn.execute(
                "SELECT from_call, text, MAX(timestamp) FROM directed"
                " WHERE to_call = ? AND timestamp >= ?"
                "   AND UPPER(text) LIKE '%SNR%'"
                "   AND UPPER(text) NOT LIKE '%SNR?%'"
                "   AND UPPER(text) NOT LIKE '%HEARING%'"
                " GROUP BY from_call",
                (my_call, hears_cutoff_str)
            ).fetchall()
            for row in snr_rows:
                from_c = norm_call(row[0])
                text   = str(row[1] or '')
                rsnr_m = _RSNR_RE.search(text)
                if rsnr_m:
                    val = int(rsnr_m.group(1))
                else:
                    snr_m = SNR_RE_GLOBAL.search(text)
                    val = int(snr_m.group(1)) if snr_m else None
                if val is not None:
                    hears_me.add(from_c)
                    snr_of_me[from_c] = val
                    if _base(from_c) != from_c:
                        hears_me.add(_base(from_c))
                        snr_of_me[_base(from_c)] = val

            # Also check outgoing SNR reports we sent (from_call=my_call)
            # 'KW3KW: W3BFO/P SNR -09' proves W3BFO/P heard us (we responded)
            # This is mutual even without an incoming SNR report from them
            out_rows = conn.execute(
                "SELECT to_call, text, timestamp FROM directed"
                " WHERE from_call = ? AND timestamp >= ?"
                "   AND UPPER(text) LIKE '%SNR%'"
                "   AND UPPER(text) NOT LIKE '%SNR?%'",
                (my_call, hears_cutoff_str)
            ).fetchall()
            for to_c, text, _ in out_rows:
                if not to_c or to_c == my_call:
                    continue
                # We sent them an SNR report (outgoing TX auto-response to their HB).
                # This proves WE heard THEM — a reasonable proxy for mutual contact —
                # but we do NOT know what SNR they received from us.
                # Rule: add to hears_me (marks orange on map) but do NOT update
                # snr_of_me.  snr_of_me must only be populated from *incoming*
                # reports where the remote station explicitly tells us what SNR
                # they heard us at.  Storing our own RX SNR there would show the
                # wrong direction in the "They Hear Me" popup row.
                snr_m = SNR_RE_GLOBAL.search(text)
                if snr_m:
                    hears_me.add(to_c)
                    if _base(to_c) != to_c:
                        hears_me.add(_base(to_c))
                    # Intentionally NOT writing to snr_of_me — value would be
                    # "we heard them at X dB", not "they heard us at X dB".

            # Also scan band_activity TEXT history for SNR reports we may have missed.
            # Each poll entry stores the full TEXT so we can find older SNR reports
            # even after a station sent a different message later.
            ba_rows = conn.execute(
                "SELECT callsign, text, snr, MAX(timestamp)"
                " FROM band_activity"
                " WHERE timestamp >= ?"
                "   AND UPPER(text) LIKE ?"
                "   AND UPPER(text) LIKE '%SNR%'"
                "   AND UPPER(text) NOT LIKE '%SNR?%'"
                " GROUP BY callsign",
                (hears_cutoff_str, f'%{my_call}%')
            ).fetchall()
            for row in ba_rows:
                from_c = norm_call(row[0])
                text   = str(row[1] or '')
                if from_c and from_c not in snr_of_me:
                    rsnr_m = _RSNR_RE.search(text)
                    if rsnr_m:
                        snr_of_me[from_c] = int(rsnr_m.group(1))
                    else:
                        snr_m = SNR_RE_GLOBAL.search(text)
                        if snr_m:
                            snr_of_me[from_c] = int(snr_m.group(1))
                    hears_me.add(from_c)

        # ── Inbox senders ─────────────────────────────────────
        inbox: set = set()
        pending_msg: set = set()
        if my_call:
            cutoff_24h = _utc_cutoff(timedelta(days=30))
            inbox_cutoff = max(session_start, cutoff_24h) if session_start else cutoff_24h

            # Path 1a: Messages delivered to KW3KW's inbox (from INBOX.GET_MESSAGES)
            rows = conn.execute(
                "SELECT DISTINCT from_call FROM inbox_msgs"
                " WHERE first_seen >= ? AND msg_id LIKE 'INBOX_%'",
                (inbox_cutoff,)
            ).fetchall()
            for row in rows:
                if row[0]: inbox.add(row[0])

            # Path 1b: MSG ID from heartbeat — pending on their side
            rows = conn.execute(
                "SELECT DISTINCT from_call FROM inbox_msgs"
                " WHERE first_seen >= ? AND msg_id NOT LIKE 'INBOX_%'",
                (inbox_cutoff,)
            ).fetchall()
            for row in rows:
                if row[0]: pending_msg.add(row[0])

            # Path 2: Direct MSG delivery with no embedded ID.
            # Group messages (@AMRRON etc) are already stored in inbox_msgs
            # by the RX.DIRECTED handler (Path 1) — do NOT include them here
            # or any station sending to any group gets falsely flagged.
            rows = conn.execute(
                "SELECT DISTINCT from_call FROM directed"
                " WHERE to_call = ? AND timestamp >= ?"
                "   AND ("
                "     UPPER(text) LIKE '% MSG %'"
                "     OR UPPER(text) LIKE '%: MSG %'"
                "     OR UPPER(text) LIKE '% MSG'"
                "   )"
                "   AND UPPER(text) NOT LIKE '%MSG ID%'"
                "   AND UPPER(text) NOT LIKE '%MSG?%'"
                "   AND UPPER(text) NOT LIKE '%ACK MSG%'"
                "   AND UPPER(text) NOT LIKE '%HEARTBEAT%'"
                "   AND UPPER(text) NOT LIKE '%RELAY%'"
                "   AND UPPER(text) NOT LIKE '%SNR%'",
                (my_call, inbox_cutoff)
            ).fetchall()
            for row in rows:
                if row[0]:
                    inbox.add(row[0])

        # ── Build output dicts ────────────────────────────────────
        stations_heard:      dict = {}
        stations_hearing_me: dict = {}
        no_grid_active:      set  = set()

        for call, info in active.items():
            if call == my_call:
                continue
            grid = grids.get(call) or grids.get(_base(call))
            # Prefer 6-digit grids. If we only have a 4-digit grid from JS8Call,
            # keep it in raw_grid as a last resort but send station through FCC
            # fallback first — FCC ZIP3 6-digit is more accurate (~50mi vs ~150mi).
            # If FCC also fails, raw_grid (4-digit) is used so station still appears.
            raw_grid = grid or ''
            if grid and len(grid) < 6:
                grid = ''   # try FCC first
            rec = info.copy()
            rec['grid']     = grid or ''
            rec['raw_grid'] = raw_grid   # preserve for fallback
            # is_new disabled — new-ring animation caused white halo on dots
            # (ring boundary visible at dot edge before scaling out). Re-enable
            # when animation is tuned to start clearly outside dot boundary.
            rec['is_new'] = False
            # try:
            #     last_dt = datetime.strptime(info['last_seen'], '%Y-%m-%d %H:%M:%S')
            #     rec['is_new'] = (datetime.now() - last_dt).total_seconds() < 60
            # except Exception:
            #     rec['is_new'] = False
            stations_heard[call] = rec
            if not grid:
                no_grid_active.add(call)

        # ── Batch freq/offset pre-fetch for hears_me stations ────────────
        # Single query per table instead of N queries in the loop below.
        hears_me_bases = set()
        for call in hears_me:
            hears_me_bases.add(call)
            hears_me_bases.add(_base(call))
        _batch_freq: dict = {}
        if hears_me_bases:
            phs = ','.join('?' * len(hears_me_bases))
            for tbl in ('spots', 'band_activity'):
                col = 'from_call' if tbl == 'directed' else 'callsign'
                try:
                    rows_f = conn.execute(
                        f"SELECT {col}, freq, offset FROM {tbl}"
                        f" WHERE {col} IN ({phs}) AND freq>0 AND offset>0"
                        f" ORDER BY timestamp DESC",
                        list(hears_me_bases)
                    ).fetchall()
                    for r in rows_f:
                        if r[0] not in _batch_freq:
                            _batch_freq[r[0]] = (float(r[1] or 0), float(r[2] or 0))
                except Exception:
                    pass
            try:
                rows_f = conn.execute(
                    f"SELECT from_call, freq, offset FROM directed"
                    f" WHERE from_call IN ({phs}) AND freq>0"
                    f" ORDER BY timestamp DESC",
                    list(hears_me_bases)
                ).fetchall()
                for r in rows_f:
                    if r[0] not in _batch_freq:
                        _batch_freq[r[0]] = (float(r[1] or 0), float(r[2] or 0))
            except Exception:
                pass

        for call in hears_me:
            if call == my_call:
                continue
            base_call = _base(call)
            # Guard: station must be in active (decoded in current window).
            # directed SNR proofs survive restarts (not cleared) — without this
            # guard, old pre-restart mutuals appear as ghost dots on a fresh map.
            if call not in active and base_call not in active:
                continue
            grid = grids.get(call) or grids.get(_base(call))
            # Same 6-digit preference — try FCC first, 4-digit as last resort
            raw_grid = grid or ''
            if grid and len(grid) < 6:
                grid = ''
            # Build the best info record we have for this station.
            base = dict(active.get(call) or active.get(base_call) or stations_heard.get(call, {}))
            # Use batch-fetched freq/offset
            if not base.get('freq') or not base.get('offset'):
                fq = _batch_freq.get(call) or _batch_freq.get(base_call)
                if fq:
                    if not base.get('freq'):   base['freq']   = fq[0]
                    if not base.get('offset'): base['offset'] = fq[1]
            rec = {
                'grid':      grid or '',
                'snr':       base.get('snr', 0),
                # Prefer band_activity last_seen (actual decode time).
                # Only fall back to directed timestamp if no spot data exists.
                'last_seen': base.get('last_seen', '') or hears_last_ts.get(call, ''),
                'snr_proof_ts': hears_last_ts.get(call) or hears_last_ts.get(_base(call), ''),
                'freq':   base.get('freq', 0) or 0,
                'offset': base.get('offset', 0) or 0,
                'speed':  base.get('speed', 0) or 0,
                'count':  base.get('count', 1) or 1,
            }
            stations_hearing_me[call] = rec
            # Ensure station is also in stations_heard so FCC fallback can run on it
            if call not in stations_heard:
                stations_heard[call] = rec
                if not grid:
                    no_grid_active.add(call)   # mark for FCC fallback

        debug_log.append(
            f"ℹ BUILD Final: {len(stations_heard)} plotted "
            f"({'all-time fallback' if fallback_used else 'in window'}), "
            f"{len(stations_hearing_me)} heard you"
        )
        debug_log.append(
            f"ℹ BUILD hears_me: {len(hears_me)} → {sorted(hears_me)[:6]}"
            f"  snr_of_me: {len(snr_of_me)}"
        )
        return (stations_heard, stations_hearing_me, via_heartbeat,
                no_grid_active, snr_of_me, inbox, pending_msg, debug_log,
                first_seen_map)


def build_directed_maps_from_db(db: SpotDatabase, cutoff_dt: datetime, my_callsign: str = "") -> tuple:
    """
    Single pass through the directed table returning BOTH:
      heard_by     {target: [(reporter, snr, ts), ...]}  — who heard each station
      station_hears {from_call: [(target, snr, ts), ...]} — who each station hears

    Sources (processed in timestamp order so later messages win):
      SNR report: "K8DDD: KW3KW SNR -01"  → K8DDD heard KW3KW
      HEARING reply: "KD9DSS: KW3KW HEARING KG5PVF KV4S…"
                     → KD9DSS hears each listed station (SNR unknown → None)
    """
    _CALL_RE   = _HEARING_CALL_RE
    _SKIP_CALLS = {'@AMRRON', '@HRMS', '@MAGNET', '@PREPNET', '@CQ'}
    window_30  = _utc_now() - timedelta(minutes=30)
    cutoff_str = max(cutoff_dt, window_30).strftime(_DB_TS_FMT)

    hby_raw:    dict = {}   # target  → {reporter: (snr|None, ts)}
    shrs_raw:   dict = {}   # from_c  → {target:   (snr|None, ts)}
    hearing_given: set = set()  # stations that gave an explicit HEARING list

    with sqlite3.connect(db.path, timeout=15) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        # ORDER BY timestamp so later messages overwrite earlier ones naturally
        rows = conn.execute(
            "SELECT from_call, to_call, text, snr, timestamp"
            " FROM directed WHERE timestamp >= ?"
            " ORDER BY timestamp",
            (cutoff_str,)
        ).fetchall()

        for from_c, to_c, text, raw_snr, ts in rows:
            if not from_c or from_c == to_c:
                continue
            text_up = str(text or '').upper()

            # ── HEARING reply ─────────────────────────────────────
            if 'HEARING' in text_up and 'HEARING?' not in text_up:
                idx   = text_up.find('HEARING')
                after = str(text or '')[idx + 7:].strip()
                found = False
                for cs in _CALL_RE.findall(after.upper()):
                    if len(cs) < 3 or cs in _SKIP_CALLS:
                        continue
                    # from_c is hearing cs (SNR unknown)
                    shrs_raw.setdefault(from_c, {})[cs] = (None, ts)
                    # cs was heard by from_c (SNR unknown)
                    hby_raw.setdefault(cs, {})[from_c]  = (None, ts)
                    found = True
                if found:
                    hearing_given.add(from_c)

            # ── SNR report ────────────────────────────────────────
            elif 'SNR' in text_up and 'SNR?' not in text_up:
                snr_m = SNR_RE_GLOBAL.search(text)
                if snr_m:
                    snr_val = int(snr_m.group(1))
                elif raw_snr is not None:
                    try:
                        snr_val = int(raw_snr)
                    except Exception:
                        continue
                else:
                    continue
                # station_hears: from_c hears to_c
                # Don't overwrite an explicit HEARING list with inferred SNR data.
                # Skip if to_c is the operator — every mutual hears us by
                # definition; showing KW3KW in every station's Hearing list
                # floods the badge and misleads the operator.
                if from_c not in hearing_given and to_c != my_callsign:
                    shrs_raw.setdefault(from_c, {})[to_c] = (snr_val, ts)
                # heard_by: to_c was heard by from_c (keep for operator's own popup)
                hby_raw.setdefault(to_c, {})[from_c] = (snr_val, ts)

    # Build final sorted results
    heard_by: dict = {
        target: sorted(
            [(r, s, t) for r, (s, t) in reporters.items()],
            key=lambda x: x[2], reverse=True
        )[:6]
        for target, reporters in hby_raw.items()
    }
    station_hears: dict = {
        fc: sorted(
            [(to, s, t) for to, (s, t) in targets.items()],
            key=lambda x: x[2], reverse=True
        )[:4]
        for fc, targets in shrs_raw.items()
    }
    return heard_by, station_hears


__all__ = [
    'ActivityStoreError',
    'UnsupportedSchemaError',
    'READ_CONTRACT_VERSION',
    'SpotDatabase',
    'build_stations_from_db',
    'build_directed_maps_from_db',
]
