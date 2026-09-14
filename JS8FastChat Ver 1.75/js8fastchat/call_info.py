# call_info.py — per-callsign derived facts for JS8FastChat
#
# Pure computation lifted verbatim from MainWindow (Phase 1 extraction): given a
# callsign and the activity rows, derive its grid, distance, SNR (both directions),
# and cleaned display text. No Tk, no widgets — a domain-layer helper a contributor
# can read and unit-test in isolation, mirroring JS8Map's util/fcc/zip3_latlon layer.
#
# State is read through LIVE getters, never snapshotted: MainWindow rebinds
# self.locator when a fresh /data.json payload arrives (main_window.py, on the
# activity-update path), so a captured copy would go stale. cfg is stable but is
# read live too for symmetry. activity_rows / all_activity_rows are likewise read
# live (rebound on every refresh). theme_var is a tk.StringVar held by reference (only
# .get() is ever called on it here) — the same live object MainWindow owns.
#
# The 23 method bodies below are byte-identical to the originals; only the class
# wrapper and the cfg/locator/theme_var live-property shims are new.

from __future__ import annotations

import math
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .models import ActivityRow
from .data.zip3_latlon import ZIP3_LATLON
from .data.js8call_ini import find_js8call_ini
from .utils import base_call, debug, debug_exc


class CallInfo:
    """Derives per-callsign facts (grid, distance, SNR, display text) from activity
    rows. Constructed once by MainWindow; state is read through live getters so it
    tracks MainWindow's cfg/locator/theme_var without snapshotting."""

    def __init__(self, cfg_getter, locator_getter, theme_var,
                 activity_rows_getter, all_activity_rows_getter):
        self._cfg_getter = cfg_getter
        self._locator_getter = locator_getter
        self.theme_var = theme_var
        self._activity_rows_getter = activity_rows_getter
        self._all_activity_rows_getter = all_activity_rows_getter

    # Live views — the moved method bodies read self.cfg / self.locator unchanged,
    # but these resolve to MainWindow's current objects on every access.
    @property
    def cfg(self):
        return self._cfg_getter()

    @property
    def locator(self):
        return self._locator_getter()

    # Activity rows are rebound on every refresh/filter/clear, so these are read
    # live too. The moved bodies reach them via getattr(self, "activity_rows", []),
    # which resolves against these properties.
    @property
    def activity_rows(self):
        return self._activity_rows_getter()

    @property
    def all_activity_rows(self):
        return self._all_activity_rows_getter()

    def _clean_display_text(self, text: object) -> str:
        out = str(text or "").replace("Â", " ").replace("\ufffd", " ")
        out = re.sub(r"\[RSNR:\s*[+\-]?\d+(?:\.\d+)?\]", " ", out, flags=re.I)
        out = re.sub(r"\s+", " ", out).strip()
        return out


    def operator_display_text(self, row: ActivityRow) -> str:
        """Clean the main-screen Text column without changing raw DB text."""
        raw = str(getattr(row, "text", "") or "")
        up_raw = raw.upper().strip()
        if not up_raw or up_raw == "SPOT" or up_raw.startswith("SPOT "):
            return ""
        # A real HEARTBEAT frame is unambiguous regardless of what else is in
        # the raw text. But "[RSNR" alone is just metadata that can ALSO ride
        # along on a genuine directed SNR reply (e.g. "N4WXI: KW3KW SNR -03
        # [RSNR: ...]") -- bare presence of that bracket must NOT override a
        # real CALL SNR +/-NN reply underneath it. Only fall back to the
        # heartbeat label for "[RSNR" when there's no such SNR-reply pattern.
        has_snr_reply = bool(re.search(r"\bSNR\s*[+\-]?\d+(?:\.\d+)?\b", up_raw)) and "SNR?" not in up_raw
        if "HEARTBEAT" in up_raw or ("[RSNR" in up_raw and not has_snr_reply):
            return "HB acknowledgement"
        text = self._clean_display_text(raw)
        up = text.upper()
        if re.search(r"\bACK\b", up) and len(up.split()) <= 3:
            return "ACK"
        m = re.search(r"\bSNR\s*([+\-]?\d+(?:\.\d+)?)", up)
        if m and "SNR?" not in up:
            try:
                val = float(m.group(1))
                return f"SNR {val:+.0f}"
            except Exception:
                return f"SNR {m.group(1)}"
        # Strip duplicated sender prefixes like "CALL: " for operator readability.
        text = re.sub(r"^[A-Z0-9/]{3,12}:\s*", "", text).strip()
        return text

    def _rows_for_call(self, call: str) -> list[ActivityRow]:
        """Return all known rows for a callsign, preferring operator-facing rows first."""
        base = base_call(call)
        out: list[ActivityRow] = []
        seen: set[tuple[str, str, str, str]] = set()
        for source_rows in (getattr(self, "activity_rows", []) or [], getattr(self, "all_activity_rows", []) or []):
            for row in source_rows:
                if row.base != base:
                    continue
                key = (str(row.timestamp or ""), str(row.source or ""), str(row.text or ""), str(row.grid or ""))
                if key in seen:
                    continue
                seen.add(key)
                out.append(row)
        return out

    def _best_summary_row_for_call(self, call: str) -> ActivityRow | None:
        """Use the same operator-facing row as the Active Callsigns list when possible.

        This avoids raw heartbeat/SNR/spot rows forcing FastChat's HEARD line to
        show 0s ago while the callsign list correctly shows an older age.
        """
        base = base_call(call)
        for row in getattr(self, "activity_rows", []) or []:
            if row.base == base:
                return row
        rows = self._rows_for_call(call)
        if not rows:
            return None
        for row in rows:
            text = str(row.text or "").strip().upper()
            if text and not text.startswith("SPOT") and "HEARTBEAT" not in text:
                return row
        return rows[0]

    def _snr_they_hear_me(self, call: str):
        """Return the SNR (int dB) that `call` most recently reported hearing US
        at, or None. Mirrors JS8Map's "They Hear Me" derivation: query the shared
        js8_spots.db (READ-ONLY) directly, with a >= 2 hour lookback so a narrow
        DB-Time filter (e.g. "Last 15 minutes") doesn't hide an SNR report that
        arrived earlier. This is why the popup can show a number even when the
        report is older than the active time window -- the in-memory activity
        rows are time-filtered, but this query is not. Only *incoming* reports
        (they told us our SNR) count; our own outgoing SNR reports are excluded
        because those say how WE heard THEM, not the reverse.

        Best-effort: any DB/schema problem returns None so the caller falls back
        to the in-memory scan and the ACK/YES/-- semantics. Never writes."""
        my_call = base_call(self.locator.callsign())
        target = base_call(call)
        if not my_call or not target:
            return None
        db_path = getattr(self.locator, "spot_db", "") or ""
        if not db_path or not os.path.exists(db_path):
            return None
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        rsnr_re = re.compile(r"\[RSNR:\s*([+\-]?\d+)\]", re.I)
        snr_re = re.compile(r"\bSNR\s*([+\-]?\d+)", re.I)

        def _extract(text: str):
            m = rsnr_re.search(text)
            if m:
                try:
                    return int(m.group(1))
                except Exception:
                    return None
            up = text.upper()
            if "SNR" in up and "HEARING" not in up and "SNR?" not in up:
                m = snr_re.search(text)
                if m:
                    try:
                        return int(m.group(1))
                    except Exception:
                        return None
            return None

        try:
            with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.5)) as con:
                con.execute("PRAGMA query_only=ON")
                # 1) directed traffic addressed to us -- the authoritative source.
                #    Scan most-recent-first; the latest message may lack an SNR,
                #    so we don't rely on a single GROUP BY row.
                try:
                    rows = con.execute(
                        "SELECT text FROM directed"
                        " WHERE (to_call = ? OR to_call LIKE ?)"
                        "   AND (from_call = ? OR from_call LIKE ?)"
                        "   AND timestamp >= ?"
                        " ORDER BY timestamp DESC LIMIT 50",
                        (my_call, f"{my_call}/%", target, f"{target}/%", cutoff),
                    ).fetchall()
                except Exception:
                    rows = []
                for (text,) in rows:
                    val = _extract(str(text or ""))
                    if val is not None:
                        return val
                # 2) band_activity TEXT history mentioning our call + an SNR --
                #    catches reports that didn't land in `directed`.
                try:
                    rows = con.execute(
                        "SELECT text FROM band_activity"
                        " WHERE (callsign = ? OR callsign LIKE ?)"
                        "   AND timestamp >= ?"
                        "   AND UPPER(text) LIKE ?"
                        "   AND UPPER(text) LIKE '%SNR%'"
                        "   AND UPPER(text) NOT LIKE '%SNR?%'"
                        " ORDER BY timestamp DESC LIMIT 50",
                        (target, f"{target}/%", cutoff, f"%{my_call}%"),
                    ).fetchall()
                except Exception:
                    rows = []
                for (text,) in rows:
                    val = _extract(str(text or ""))
                    if val is not None:
                        return val
        except Exception:
            return None
        return None

    def _parse_reported_snr_for_me(self, call: str) -> str:
        my_call = base_call(self.locator.callsign())
        if not my_call:
            return "--"
        # Prefer a NUMERIC reciprocal SNR pulled straight from the shared DB with
        # a wide (>= 2h) lookback. This matches JS8Map's "They Hear Me" value and
        # survives a narrow DB-Time filter that would otherwise leave only the
        # ACK/YES fallback below (the bug where FastChat showed "YES" while the
        # Map showed e.g. "-16 dB" for the same station).
        snr_val = self._snr_they_hear_me(call)
        if snr_val is not None:
            return f"{snr_val:+.0f} dB"
        ack_seen = False
        directed_seen = False
        for row in self._rows_for_call(call):
            text_raw = str(row.text or "")
            text = text_raw.upper()
            to_base = base_call(getattr(row, "to_call", ""))
            directed_to_me = bool(to_base and to_base == my_call) or bool(my_call and re.search(rf"\b{re.escape(my_call)}\b", text))
            m = re.search(rf"\b{re.escape(my_call)}\s+SNR\s*([+\-]?\d+(?:\.\d+)?)", text)
            if not m:
                m = re.search(r"\[RSNR:\s*([+\-]?\d+(?:\.\d+)?)\]", text, flags=re.I)
            if m:
                try:
                    val = float(m.group(1))
                    return f"{val:+.0f} dB"
                except Exception:
                    return f"{m.group(1)} dB"
            if directed_to_me:
                directed_seen = True
                if re.search(r"\bACK\b", text):
                    ack_seen = True
        if ack_seen:
            return "ACK"
        if directed_seen:
            return "YES"
        return "--"


    def _maidenhead_latlon(self, grid: str):
        g = str(grid or "").strip().upper()
        if len(g) < 4:
            return None
        try:
            lon = -180.0 + (ord(g[0]) - ord('A')) * 20.0 + int(g[2]) * 2.0 + 1.0
            lat = -90.0 + (ord(g[1]) - ord('A')) * 10.0 + int(g[3]) * 1.0 + 0.5
            if len(g) >= 6 and g[4].isalpha() and g[5].isalpha():
                lon += (ord(g[4]) - ord('A')) * (2.0 / 24.0) + (1.0 / 24.0)
                lat += (ord(g[5]) - ord('A')) * (1.0 / 24.0) + (0.5 / 24.0)
            return lat, lon
        except Exception:
            return None

    def _valid_grid(self, value: object) -> str:
        text = str(value or "").strip().upper()
        m = re.search(r"\b([A-R]{2}[0-9]{2}(?:[A-X]{2})?)\b", text)
        return m.group(1) if m else ""

    def _home_grid(self) -> str:
        grid = self._valid_grid(self.locator.grid())
        if grid:
            return grid
        # JS8Map stores the live station grid in js8_spots.db my_station.
        db_path = str(getattr(self.locator, "spot_db", "") or "")
        if db_path and Path(db_path).exists():
            try:
                with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.5)) as con:
                    row = con.execute("SELECT value FROM my_station WHERE key='grid' LIMIT 1").fetchone()
                    grid = self._valid_grid(row[0] if row else "")
                    if grid:
                        return grid
            except Exception:
                pass
        cfg = getattr(self.locator, "map_config", {}) or {}
        keys = ("my_grid", "grid", "home_grid", "station_grid", "operator_grid", "qth_grid", "grid_square", "gridsquare", "maidenhead", "locator")
        for key in keys:
            grid = self._valid_grid(cfg.get(key, ""))
            if grid:
                return grid
        for key, value in cfg.items():
            if "grid" in str(key).lower() or "locator" in str(key).lower():
                grid = self._valid_grid(value)
                if grid:
                    return grid
        try:
            path = self.cfg.js8call_ini_path or find_js8call_ini()
            if path:
                for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
                    if any(word in line.lower() for word in ("grid", "locator", "qth")):
                        grid = self._valid_grid(line)
                        if grid:
                            return grid
        except Exception:
            pass
        return ""

    def _distance_text_from_grid(self, other_grid: str) -> str:
        home = self._maidenhead_latlon(self._home_grid())
        other = self._maidenhead_latlon(other_grid)
        if not home or not other:
            return "--"
        lat1, lon1 = map(math.radians, home)
        lat2, lon2 = map(math.radians, other)
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        km = 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        mi = km * 0.621371
        return f"{mi:.0f} mi / {km:.0f} km"

    # Keep old helper name for any older internal callers.
    def _distance_text(self, other_grid: str) -> str:
        return self._distance_text_from_grid(other_grid)

    def _js8map_grid_for_call(self, call: str) -> str:
        db_path = str(getattr(self.locator, "spot_db", "") or "")
        if not db_path or not Path(db_path).exists():
            return ""
        base = base_call(call)
        call_cols = {"callsign", "call", "from_call", "from", "de", "station", "station_call"}
        grid_cols = {"grid", "grid_square", "gridsquare", "maidenhead", "locator", "qth_grid"}
        time_cols = ("timestamp", "time", "heard", "last_heard", "last_seen", "created_at", "updated_at")
        try:
            with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.5)) as con:
                con.execute("PRAGMA query_only=ON")
                table_rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                preferred = ["spots", "active_callsigns", "call_activity", "stations", "band_activity", "directed"]
                names = [str(x[0]) for x in table_rows]
                ordered = [n for n in preferred if n in names] + [n for n in names if n not in preferred and not n.lower().startswith("sqlite_")]
                for table in ordered:
                    try:
                        info = con.execute(f"PRAGMA table_info({self._quote_sql_ident(table)})").fetchall()
                    except Exception:
                        continue
                    cols = [str(x[1]) for x in info]
                    lower = {c.lower(): c for c in cols}
                    cands_call = [lower[c] for c in call_cols if c in lower]
                    cands_grid = [lower[c] for c in grid_cols if c in lower]
                    if not cands_call or not cands_grid:
                        continue
                    call_col = cands_call[0]
                    grid_col = cands_grid[0]
                    order_col = next((lower[t] for t in time_cols if t in lower), "")
                    order_sql = f" ORDER BY {self._quote_sql_ident(order_col)} DESC" if order_col else ""
                    sql = f"SELECT {self._quote_sql_ident(grid_col)} FROM {self._quote_sql_ident(table)} WHERE UPPER({self._quote_sql_ident(call_col)}) = ? OR UPPER({self._quote_sql_ident(call_col)}) LIKE ?{order_sql} LIMIT 10"
                    try:
                        for (grid_raw,) in con.execute(sql, (base, base + "/%")):
                            grid = self._valid_grid(grid_raw)
                            if grid:
                                return grid
                    except Exception:
                        continue
        except Exception:
            return ""
        return ""

    def _js8map_text_grid_for_call(self, call: str) -> str:
        db_path = str(getattr(self.locator, "spot_db", "") or "")
        if not db_path or not Path(db_path).exists():
            return ""
        base = base_call(call)
        try:
            with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.5)) as con:
                con.execute("PRAGMA query_only=ON")
                # Directed GRID replies: CALL: KW3KW GRID FM29
                try:
                    rows = con.execute("SELECT text FROM directed WHERE UPPER(from_call)=? OR UPPER(from_call) LIKE ? ORDER BY timestamp DESC LIMIT 80", (base, base + "/%"))
                    for (text,) in rows:
                        up = str(text or "").upper()
                        if "HEARING" in up:
                            continue
                        m = re.search(r"\bGRID\s+([A-R]{2}[0-9]{2}(?:[A-X]{2})?)\b", up, flags=re.I)
                        if m:
                            grid = self._valid_grid(m.group(1))
                            if grid:
                                return grid
                except Exception:
                    pass
                # HEARTBEAT text can include the sender's own grid.
                try:
                    rows = con.execute("SELECT text FROM band_activity WHERE UPPER(callsign)=? OR UPPER(callsign) LIKE ? ORDER BY timestamp DESC LIMIT 120", (base, base + "/%"))
                    for (text,) in rows:
                        up = str(text or "").upper()
                        if "HEARTBEAT" not in up:
                            continue
                        grid = self._valid_grid(up)
                        if grid:
                            return grid
                except Exception:
                    pass
        except Exception:
            return ""
        return ""

    def _best_grid_for_call(self, call: str) -> str:
        for row in self._rows_for_call(call):
            grid = self._valid_grid(getattr(row, "grid", ""))
            if grid:
                return grid
        grid = self._js8map_grid_for_call(call)
        if grid:
            return grid
        return self._js8map_text_grid_for_call(call)

    def _quote_sql_ident(self, name: str) -> str:
        return '"' + str(name).replace('"', '""') + '"'

    def _format_distance_pair(self, mi: object = None, km: object = None) -> str:
        try:
            mi_val = float(mi) if mi not in (None, "") else None
        except Exception:
            mi_val = None
        try:
            km_val = float(km) if km not in (None, "") else None
        except Exception:
            km_val = None
        if mi_val is None and km_val is None:
            return "--"
        if mi_val is None and km_val is not None:
            mi_val = km_val * 0.621371
        if km_val is None and mi_val is not None:
            km_val = mi_val / 0.621371
        return f"{mi_val:.0f} mi / {km_val:.0f} km"

    def _js8map_distance_for_call(self, call: str) -> str:
        """Try to use an existing distance value already stored by JS8Map.

        JS8Map versions have used different table/column names over time, so
        this introspects cautiously. If no explicit miles/km column exists, the
        grid fallback below is used instead.
        """
        db_path = str(getattr(self.locator, "spot_db", "") or "")
        if not db_path or not Path(db_path).exists():
            return "--"
        base = base_call(call)
        call_cols = {"callsign", "call", "from_call", "from", "de", "station", "station_call"}
        mi_cols = {"mi", "mile", "miles", "distance_mi", "distance_miles", "dist_mi", "dist_miles", "range_mi", "range_miles"}
        km_cols = {"km", "kilometer", "kilometers", "distance_km", "dist_km", "range_km"}
        time_cols = ("timestamp", "time", "heard", "last_heard", "last_seen", "created_at", "updated_at")
        try:
            with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.5)) as con:
                con.execute("PRAGMA query_only=ON")
                table_rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                # Put common JS8Map activity tables first, then scan the rest.
                preferred = ["spots", "active_callsigns", "call_activity", "stations", "band_activity", "directed"]
                names = [str(x[0]) for x in table_rows]
                ordered = [n for n in preferred if n in names] + [n for n in names if n not in preferred and not n.lower().startswith("sqlite_")]
                for table in ordered:
                    try:
                        info = con.execute(f"PRAGMA table_info({self._quote_sql_ident(table)})").fetchall()
                    except Exception:
                        continue
                    cols = [str(x[1]) for x in info]
                    lower = {c.lower(): c for c in cols}
                    cands_call = [lower[c] for c in call_cols if c in lower]
                    cands_mi = [lower[c] for c in mi_cols if c in lower]
                    cands_km = [lower[c] for c in km_cols if c in lower]
                    if not cands_call or not (cands_mi or cands_km):
                        continue
                    call_col = cands_call[0]
                    mi_col = cands_mi[0] if cands_mi else None
                    km_col = cands_km[0] if cands_km else None
                    select_parts = []
                    if mi_col:
                        select_parts.append(self._quote_sql_ident(mi_col))
                    else:
                        select_parts.append("NULL")
                    if km_col:
                        select_parts.append(self._quote_sql_ident(km_col))
                    else:
                        select_parts.append("NULL")
                    order_col = next((lower[t] for t in time_cols if t in lower), "")
                    order_sql = f" ORDER BY {self._quote_sql_ident(order_col)} DESC" if order_col else ""
                    sql = f"SELECT {', '.join(select_parts)} FROM {self._quote_sql_ident(table)} WHERE UPPER({self._quote_sql_ident(call_col)}) = ? OR UPPER({self._quote_sql_ident(call_col)}) LIKE ?{order_sql} LIMIT 5"
                    try:
                        for mi, km in con.execute(sql, (base, base + "/%")):
                            txt = self._format_distance_pair(mi, km)
                            if txt != "--":
                                return txt
                    except Exception:
                        continue
        except Exception:
            return "--"
        return "--"

    def _distance_text_from_latlon(self, other_lat: object, other_lon: object) -> str:
        home = self._maidenhead_latlon(self._home_grid())
        if not home:
            return "--"
        try:
            other = (float(other_lat), float(other_lon))
        except Exception:
            return "--"
        if other == (0.0, 0.0):
            return "--"
        lat1, lon1 = map(math.radians, home)
        lat2, lon2 = map(math.radians, other)
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        km = 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        mi = km * 0.621371
        return f"{mi:.0f} mi / {km:.0f} km"

    def _fcc_distance_for_call(self, call: str) -> str:
        db_path = str(getattr(self.locator, "fcc_db", "") or "")
        if not db_path or not Path(db_path).exists():
            return "--"
        base = base_call(call)
        try:
            with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.5)) as con:
                con.execute("PRAGMA query_only=ON")
                table = "callsigns"
                try:
                    names = {str(x[0]).lower(): str(x[0]) for x in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                    table = names.get("callsigns", table)
                except Exception:
                    pass
                cols = {str(x[1]).lower(): str(x[1]) for x in con.execute(f"PRAGMA table_info({self._quote_sql_ident(table)})").fetchall()}

                def pick(*names: str) -> str:
                    for name in names:
                        if name.lower() in cols:
                            return cols[name.lower()]
                    return ""

                call_col = pick("callsign", "call", "call_sign")
                lat_col = pick("lat", "latitude")
                lon_col = pick("lon", "lng", "long", "longitude")
                zip_col = pick("zip", "zipcode", "zip_code", "postal", "postal_code")
                if not call_col:
                    return "--"
                selected: list[tuple[str, str]] = []
                if lat_col:
                    selected.append(("lat", lat_col))
                if lon_col:
                    selected.append(("lon", lon_col))
                if zip_col:
                    selected.append(("zip", zip_col))
                if not selected:
                    return "--"
                select_cols = ", ".join(self._quote_sql_ident(col) for _key, col in selected)
                sql = f"SELECT {select_cols} FROM {self._quote_sql_ident(table)} WHERE {self._quote_sql_ident(call_col)}=UPPER(?) LIMIT 1"
                row = con.execute(sql, (base,)).fetchone()
                if not row:
                    return "--"
                data = dict((key, row[idx]) for idx, (key, _col) in enumerate(selected))
                txt = self._distance_text_from_latlon(data.get("lat"), data.get("lon"))
                if txt != "--":
                    return txt
                zip_text = str(data.get("zip", "") or "").strip()
                m = re.search(r"\d{3}", zip_text)
                z3 = m.group(0) if m else ""
                if z3 in ZIP3_LATLON:
                    lat, lon = ZIP3_LATLON[z3]
                    return self._distance_text_from_latlon(lat, lon)
        except Exception:
            debug_exc("fcc distance lookup failed")
            return "--"
        return "--"


    def _distance_text_for_call(self, call: str) -> str:
        from_db = self._js8map_distance_for_call(call)
        if from_db != "--":
            return from_db
        grid = self._best_grid_for_call(call)
        from_grid = self._distance_text_from_grid(grid)
        if from_grid != "--":
            return from_grid
        from_fcc = self._fcc_distance_for_call(call)
        if from_fcc != "--":
            return from_fcc
        try:
            debug(
                "Distance missing for "
                + base_call(call)
                + f": home_grid={self._home_grid() or '(none)'}, "
                + f"station_grid={grid or '(none)'}, "
                + f"spot_db={'yes' if str(getattr(self.locator, 'spot_db', '') or '') else 'no'}, "
                + f"fcc_db={'yes' if str(getattr(self.locator, 'fcc_db', '') or '') else 'no'}"
            )
        except Exception:
            pass
        return "--"

    def _is_hb_ack_text(self, text: str) -> bool:
        up = str(text or "").upper()
        padded = f" {up} "
        # A genuine directed SNR reply (e.g. "N4WXI: KW3KW SNR -03") is never
        # a heartbeat ack, even if heartbeat-ish metadata like "[RSNR" rides
        # along in the same raw string -- the SNR-reply pattern takes
        # priority. "SNR?" is a request, not a reply, so it's excluded here.
        has_snr_reply = bool(re.search(r"\bSNR\s*[+\-]?\d+", up)) and "SNR?" not in up
        if has_snr_reply:
            return False
        if "HEARTBEAT" in up or "[RSNR" in up or " RSNR" in padded:
            return True
        if " HB " in padded or up.startswith("HB ") or " ACK" in padded:
            return True
        return False

    def _latest_display_text_for_call(self, call: str) -> str:
        for row in self._rows_for_call(call):
            raw = str(row.text or "").strip()
            if not raw:
                continue
            raw_up = raw.upper().strip()
            if raw_up == "SPOT" or raw_up.startswith("SPOT "):
                continue
            if re.search(r"\bACK\b", raw, flags=re.I):
                return "ACK"
            if self._is_hb_ack_text(raw):
                return "HB acknowledgement"
            latest = self._clean_display_text(raw)
            latest = re.sub(r"\bHEARTBEAT\b", "", latest, flags=re.I)
            latest = re.sub(r"\bSNR\s*[+\-]?\d+(?:\.\d+)?", "", latest, flags=re.I)
            latest = re.sub(r"\s+", " ", latest).strip(" -:")
            if latest:
                return latest
        return f"No latest decoded reply from {call} yet."

    def _color_from_marker_red(self, widget, marker: str, tag: str = "marker_red") -> None:
        """Color text from the first occurrence of `marker` to the end red.
        Works regardless of widget state."""
        try:
            red = "#ff6b6b" if self.theme_var.get() == "Dark" else "#cc0000"
            widget.tag_configure(tag, foreground=red)
            idx = widget.search(marker, "1.0", stopindex="end")
            if idx:
                widget.tag_add(tag, idx, "end")
        except Exception:
            pass
