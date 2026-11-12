from __future__ import annotations

import atexit
import gc
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ..constants import CANADIAN_CALL_PREFIXES, GROUP_ALL, TIME_FILTERS
from ..models import ActivityRow
from ..utils import base_call, debug_exc, is_protocol_word, looks_like_callsign, match_watch_words, norm_call, safe_int
from .db_locator import MapDbLocator


# ---- inbox snapshot temp files ---------------------------------------------
# _inbox_snapshot() copies JS8Call's inbox into %TEMP% so we never open (and so
# never lock) JS8Call's live file -- see the long note above _inbox_snapshot.
# Those copies were never deleted, so every run of FastChat left another one
# behind. We are supposed to be the application that does NOT leave fingerprints
# in other people's folders; that goes for %TEMP% as well.
_SNAPSHOT_PREFIX = "js8fastchat_inbox_"
_SNAPSHOT_PATHS: set[str] = set()


def _forget_snapshots() -> None:
    """Delete this process's inbox snapshots. Registered with atexit.

    Windows will not delete a file that is still open. Anything still holding a
    handle -- a SQLite connection nobody closed, say -- makes the unlink fail,
    and because we swallow the error the copies quietly pile up regardless. So
    collect any unreferenced connections first, and try more than once.
    """
    gc.collect()                          # drop connections nothing references
    for path in list(_SNAPSHOT_PATHS):
        for attempt in range(3):
            try:
                os.unlink(path)
                break
            except FileNotFoundError:
                break                     # already gone; fine
            except Exception:
                if attempt == 2:
                    break                 # genuinely stuck; the sweep gets it next run
                gc.collect()
                time.sleep(0.05)
    _SNAPSHOT_PATHS.clear()


atexit.register(_forget_snapshots)


def _sweep_old_snapshots(max_age_secs: float = 86400.0) -> None:
    """Clear out snapshots abandoned by earlier runs (a crash skips atexit).

    Only touches files matching our own prefix, and only ones over a day old,
    so a snapshot belonging to a SECOND copy of FastChat running right now is
    never removed.
    """
    now = time.time()
    try:
        tmp = Path(tempfile.gettempdir())
        for old in tmp.glob(f"{_SNAPSHOT_PREFIX}*.db3"):
            try:
                if (now - old.stat().st_mtime) > max_age_secs:
                    old.unlink()
            except Exception:
                pass
    except Exception:
        pass


def is_canadian_callsign(call: str) -> bool:
    b = base_call(call)
    return any(b.startswith(prefix) for prefix in CANADIAN_CALL_PREFIXES)


class ActivityReader:
    """Read JS8Map SQLite data safely. No Tk calls and no UI decisions here."""

    # Safety ceiling on RAW rows pulled for the main display when a time
    # window is active. The window (cutoff) is the real bound on volume; this
    # only stops a pathological pull. It is deliberately far above an hour of
    # the busiest realistic band traffic so it never truncates stations that
    # are still inside the selected window. See display_rows() for why the
    # caller-facing `limit` must NOT be used as the raw-row cap.
    _DISPLAY_RAW_CEILING = 25000

    def __init__(self, locator: MapDbLocator, msg_watch_words: Optional[Sequence[str]] = None):
        self.locator = locator
        self.msg_watch_words = [str(x).upper().strip() for x in (msg_watch_words or []) if str(x).strip()]
        self._lookup_cache: Dict[Tuple[str, str], bool] = {}
        self._lookup_tables_cache: Dict[str, List[Tuple[str, str]]] = {}
        # Clear/QSY watermark. When set to a UTC timestamp string
        # ("YYYY-MM-DD HH:MM:SS UTC"), read_activity raises its effective cutoff
        # to this value, so stations JS8Call keeps re-reporting from BEFORE a
        # frequency change (Clear Activity) are dropped at the SQL level and
        # never reach the Incoming Activity list. Same mechanism as the normal
        # time-window cutoff -- a pure lower bound on row age, NOT a merge or a
        # synthesized row, so it cannot blank the list the way the reverted
        # Phase 9 merge did. None = no watermark (normal behavior).
        self.clear_watermark: Optional[str] = None

    def set_clear_watermark(self, ts: Optional[str]) -> None:
        """Set (or clear, with None) the QSY/clear watermark timestamp.

        ts must be in the same "YYYY-MM-DD HH:MM:SS UTC" form the spot DB
        stores, so it compares correctly against the timestamp column.
        """
        self.clear_watermark = ts or None

    @staticmethod
    def _max_cutoff(*values: Optional[str]) -> Optional[str]:
        """Return the latest of the given cutoff strings (ignoring None/empty).

        The DB timestamps are zero-padded 'YYYY-MM-DD HH:MM:SS UTC', so a plain
        string comparison is chronological. Returns None only if all inputs are
        empty (meaning 'no lower bound').
        """
        present = [v for v in values if v]
        if not present:
            return None
        return max(present)

    def _connect_spots(self):
        """Return a CONTEXT MANAGER, not a bare connection. Use only as
        `with self._connect_spots() as con:`.

        `with sqlite3.connect(...)` commits or rolls back the TRANSACTION; it
        does NOT close the CONNECTION. Every caller here used the bare form, so
        read-only handles accumulated for the life of the process -- and on
        Windows a leaked handle is what defeats the snapshot cleanup (see the
        note in read_js8call_inbox). closing() adds the close; the read-only
        URI means there is no transaction to lose.
        """
        return closing(sqlite3.connect(f"file:{self.locator.spot_db}?mode=ro", uri=True, timeout=2))

    def _connect_relay(self):
        """Context manager, not a bare connection. See _connect_spots."""
        return closing(sqlite3.connect(f"file:{self.locator.relay_db}?mode=ro", uri=True, timeout=2))

    def newest_db_time(self) -> Optional[datetime]:
        if not self.locator.spot_db or not Path(self.locator.spot_db).exists():
            return None
        newest = None
        try:
            with self._connect_spots() as con:
                for table, col in (("spots", "timestamp"), ("directed", "timestamp"), ("band_activity", "timestamp")):
                    try:
                        row = con.execute(f"SELECT MAX({col}) FROM {table}").fetchone()
                        raw = str(row[0] if row else "")[:19].replace("T", " ")
                        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S") if raw else None
                        if dt and (newest is None or dt > newest):
                            newest = dt
                    except Exception:
                        pass
        except Exception:
            pass
        return newest

    def cutoff_string(self, time_filter: str) -> Optional[str]:
        hours = TIME_FILTERS.get(time_filter, 0.25)
        if hours is None:
            return None
        # UTC wall-clock window (Phase 3 migration). JS8Map now stores activity
        # timestamps in UTC ("... UTC"), so anchoring the cutoff to UTC
        # wall-clock -- NOT the newest DB row -- makes both apps share one clock.
        # The old newest-row anchor advanced as new traffic arrived and silently
        # dropped stations whose last activity fell behind it; a fixed wall-clock
        # window cannot do that. The 19-char (no-suffix) cutoff compares
        # correctly against the suffixed rows: lexical ">=", with the " UTC"
        # suffix only acting as a same-second tiebreaker toward inclusion.
        # NOTE: requires the fresh all-UTC DB (cutover order). A mixed
        # local+UTC DB would mis-window the stale local rows.
        base = datetime.now(timezone.utc)
        return (base - timedelta(hours=float(hours))).strftime("%Y-%m-%d %H:%M:%S")

    def group_activity_map(self, cutoff: Optional[str] = None) -> Dict[str, set[str]]:
        out: Dict[str, set[str]] = {}
        if not self.locator.spot_db or not Path(self.locator.spot_db).exists():
            return out
        try:
            with self._connect_spots() as con:
                queries = []
                if cutoff:
                    queries.append(("SELECT call, group_name FROM group_activity WHERE last_ts >= ?", (cutoff,)))
                    queries.append(("SELECT from_call, to_call FROM directed WHERE timestamp >= ? AND to_call LIKE '@%'", (cutoff,)))
                else:
                    queries.append(("SELECT call, group_name FROM group_activity", ()))
                    queries.append(("SELECT from_call, to_call FROM directed WHERE to_call LIKE '@%'", ()))
                for sql, params in queries:
                    try:
                        for call, group in con.execute(sql, params):
                            b = norm_call(str(call or ""))
                            g = norm_call(group)
                            if b and g.startswith("@"):
                                out.setdefault(b, set()).add(g)
                    except Exception:
                        pass
        except Exception:
            pass
        return out


    def _is_real_inbox_text(self, text: object) -> bool:
        """Return True only for an operator message body, not JS8 metadata.

        JS8Map/JS8Call databases can contain heartbeat/SNR/RSNR metadata rows
        that mention callsigns and sometimes pass through inbox-like tables.
        Those rows must not create the ⚑ message flag in FastChat.
        """
        raw = str(text or "").strip()
        if not raw:
            return False
        up = raw.upper().strip()
        padded = f" {up} "
        if up == "SPOT" or up.startswith("SPOT "):
            return False
        if "HEARTBEAT" in up or "[RSNR" in up or " RSNR" in padded:
            return False
        if " QUERY MSG" in padded or " QUERY MSGS" in padded:
            return False
        if up.startswith("ID ") or up.startswith("MSG ID ") or " MSG ID" in padded:
            return False
        # Plain SNR report/status traffic is not an inbox message. A true MSG
        # frame is allowed below even if the body happens to mention SNR.
        if " MSG " not in padded:
            if " SNR?" in padded or " SNR " in padded or re.search(r"\bSNR\s*[+\-]?\d+", up):
                return False
            if " STATUS" in padded or " GRID" in padded or " INFO" in padded or " HEARING" in padded:
                return False
        if " MSG " in padded:
            body = raw[up.find(" MSG ") + 5:].strip() if " MSG " in up else raw
            body_up = body.upper().strip()
            if not body_up:
                return False
            if body_up.startswith("ID ") or body_up.startswith("MSG ID ") or "[RSNR" in body_up:
                return False
        return True

    def inbox_counts_by_call(self, my_call: str = "") -> Dict[str, int]:
        """Return cleaned message-inbox counts by sender.

        Count only real message rows addressed to this station. Do not let
        heartbeat/SNR/RSNR/status metadata produce the JS8Call-style ⚑ flag.

        SCHEMA NOTE (PROVEN 2026-07-19, Bug Testing Phase 2):
        `inbox_msgs` as written by JS8Map.py (CREATE at 1330 and 1418, INSERT at
        1278) has exactly THREE columns: msg_id, from_call, first_seen. There is
        no `text`, no `to_call`, and no deleted/cleared/archived. JS8Map's own
        reads select from_call only. It is a lightweight "who has mail waiting"
        index by design.

        This function previously required a `text` column and returned an empty
        dict when it was absent -- which was ALWAYS. Every ActivityRow therefore
        got inbox_count = 0, so main_window._has_message_for_me() line 1733 could
        never fire and the ⚑ never rendered in the Flag column, while the popup
        (counting from its own source) correctly showed Inbox (1). Verified
        against the live DB at %LOCALAPPDATA%\\JS8Map\\js8_spots.db, which held
        INBOX_KO4BIA_2026-07-17 while the Flag column showed a bare star.

        Only `from_call` is required now. The optional-column handling below is
        retained so a future richer schema still filters correctly; on today's
        three-column table those branches are simply inert.
        """
        out: Dict[str, int] = {}
        if not self.locator.spot_db or not Path(self.locator.spot_db).exists():
            return out
        try:
            with self._connect_spots() as con:
                try:
                    cols = {str(x[1]).lower() for x in con.execute("PRAGMA table_info(inbox_msgs)").fetchall()}
                except Exception:
                    cols = set()
                # No from_call means no table (or an unrecognisable one): there is
                # nothing to attribute a count to, so an empty dict is the only
                # honest answer. This is the ONLY schema shape that bails now.
                if "from_call" not in cols:
                    return out

                select_cols = ["from_call"]
                if "text" in cols:
                    select_cols.append("text")
                if "to_call" in cols:
                    select_cols.append("to_call")
                if "deleted" in cols:
                    select_cols.append("deleted")
                if "cleared" in cols:
                    select_cols.append("cleared")
                if "archived" in cols:
                    select_cols.append("archived")

                where_parts = []
                params: list[object] = []
                if my_call and "to_call" in cols:
                    where_parts.append("UPPER(to_call) LIKE ?")
                    params.append(base_call(my_call) + "%")
                where = "WHERE " + " AND ".join(where_parts) if where_parts else ""
                sql = f"SELECT {', '.join(select_cols)} FROM inbox_msgs {where}"
                for row in con.execute(sql, tuple(params)):
                    data = dict(zip(select_cols, row))
                    if "deleted" in data and safe_int(data.get("deleted"), 0):
                        continue
                    if "cleared" in data and safe_int(data.get("cleared"), 0):
                        continue
                    if "archived" in data and safe_int(data.get("archived"), 0):
                        continue
                    if my_call and "to_call" in data and base_call(data.get("to_call")) != base_call(my_call):
                        continue
                    # Content filtering is only possible when the row carries text.
                    # On the three-column schema there is nothing to inspect, and
                    # every inbox_msgs row is by construction a real stored message.
                    if "text" in data and not self._is_real_inbox_text(data.get("text")):
                        continue
                    b = norm_call(str(data.get("from_call") or ""))
                    if b:
                        out[b] = out.get(b, 0) + 1
        except Exception:
            debug_exc("inbox_counts_by_call failed")
            return out
        return out

    def read_activity(self, time_filter: str, group_filter: str = GROUP_ALL, limit: int = 350, include_counts: bool = False) -> List[ActivityRow]:
        if not self.locator.spot_db or not Path(self.locator.spot_db).exists():
            return []
        cutoff = self.cutoff_string(time_filter)
        # Fold in the clear/QSY watermark: if set, it raises the effective lower
        # bound so pre-clear stations JS8Call keeps re-reporting are excluded at
        # the SQL level. max() of the two cutoffs = the later (more restrictive)
        # of "time window start" and "moment of last clear".
        cutoff = self._max_cutoff(cutoff, self.clear_watermark)
        my_call = base_call(self.locator.callsign())
        watched = {norm_call(w) for w in (self.locator.watched_calls() or [])}
        group_map = self.group_activity_map(cutoff)
        inbox_counts = self.inbox_counts_by_call(my_call)
        # @ALLCALL is the JS8 universal-broadcast group every station implicitly
        # belongs to; as a FILTER it means "everyone", so treat it like All Groups
        # (never blanks, keeps dropdown parity with JS8Map).
        gf = group_filter
        if gf and gf.strip().upper() == "@ALLCALL":
            gf = GROUP_ALL
        rows: List[ActivityRow] = []
        try:
            with self._connect_spots() as con:
                if cutoff:
                    spot_where = band_where = dir_where = "WHERE timestamp >= ?"
                    spot_params: Sequence[object] = (cutoff, limit)
                    band_params: Sequence[object] = (cutoff, limit)
                    dir_params: Sequence[object] = (cutoff, limit)
                else:
                    spot_where = band_where = dir_where = ""
                    spot_params = band_params = dir_params = (limit,)

                try:
                    sql = f"SELECT callsign, grid, snr, freq, offset, timestamp, source FROM spots {spot_where} ORDER BY timestamp DESC LIMIT ?"
                    for call, grid, snr, freq, offset, ts, src in con.execute(sql, spot_params):
                        c = norm_call(call)
                        if not c or c.startswith("@") or is_protocol_word(c) or not looks_like_callsign(c):
                            continue
                        groups_for_call = sorted(group_map.get(c, set()))
                        if gf != GROUP_ALL and gf not in groups_for_call:
                            continue
                        rows.append(ActivityRow(call=c, group=groups_for_call[0] if groups_for_call else "", snr=snr, freq=freq, offset=offset, grid=str(grid or "").upper(), timestamp=str(ts or ""), source=str(src or "spot"), text=f"SPOT {str(grid or '').upper()}" if grid else "SPOT"))
                except Exception:
                    pass

                try:
                    sql = f"SELECT callsign, snr, freq, offset, text, timestamp FROM band_activity {band_where} ORDER BY timestamp DESC LIMIT ?"
                    for call, snr, freq, offset, text, ts in con.execute(sql, band_params):
                        c = norm_call(call)
                        if not c or c.startswith("@") or is_protocol_word(c) or not looks_like_callsign(c):
                            continue
                        groups_for_call = sorted(group_map.get(c, set()))
                        if gf != GROUP_ALL and gf not in groups_for_call:
                            continue
                        rows.append(ActivityRow(call=c, group=groups_for_call[0] if groups_for_call else "", snr=snr, freq=freq, offset=offset, text=str(text or ""), timestamp=str(ts or ""), source="band"))
                except Exception:
                    pass

                try:
                    sql = f"SELECT from_call, to_call, text, snr, freq, offset, timestamp FROM directed {dir_where} ORDER BY timestamp DESC LIMIT ?"
                    for from_call, to_call, text, snr, freq, offset, ts in con.execute(sql, dir_params):
                        c = norm_call(from_call)
                        to = norm_call(to_call)
                        if not c or c.startswith("@") or is_protocol_word(c) or not looks_like_callsign(c):
                            continue
                        groups_for_call = sorted(group_map.get(c, set()))
                        group = to if to.startswith("@") else (groups_for_call[0] if groups_for_call else "")
                        if gf != GROUP_ALL:
                            if to.startswith("@"):
                                if to != gf:
                                    continue
                            elif gf not in groups_for_call:
                                continue
                        category = "hearing_me" if my_call and base_call(to) == my_call else "heard"
                        rows.append(ActivityRow(call=c, to_call=to, group=group, snr=snr, freq=freq, offset=offset, text=str(text or ""), timestamp=str(ts or ""), source="directed", category=category))
                except Exception:
                    pass
        except Exception:
            debug_exc("read_activity failed")

        evidence: Dict[str, set[str]] = {}
        for r in rows:
            ev = evidence.setdefault(r.call, set())
            ev.add("heard")
            txt_up = (r.text or "").upper()
            to_base = base_call(r.to_call)
            if r.category == "hearing_me" or "[RSNR:" in txt_up or (my_call and f"{my_call} SNR" in txt_up) or (my_call and to_base == my_call):
                ev.add("hearing_me")
        for r in rows:
            ev = evidence.get(r.call, set())
            # No `elif "hearing_me"` case: every row here came from OUR receiver
            # decoding a frame, so "heard" is true by construction and is added
            # unconditionally above. That makes hearing-me-without-hearing-them
            # impossible from this data source -- you cannot learn a station
            # heard you except by decoding something they sent. The old elif was
            # therefore dead code, and so was the "hearing_me" tier in _CAT_RANK.
            # This would change only if third-party reports (PSKReporter, a relay
            # hearing them on your behalf) were ever ingested; that is not on the
            # roadmap, so it is deleted rather than left as a branch nobody trusts.
            if "heard" in ev and "hearing_me" in ev:
                r.category = "both"
            r.watched = r.call in watched
            r.watch_hit = match_watch_words(r.text or "", self.msg_watch_words)
            # Message flagging must be sender-specific. Do this from a single
            # inbox summary query instead of treating every directed MSG-like
            # line as an inbox message.
            r.inbox_count = inbox_counts.get(r.call, 0)
            if include_counts:
                r.relay_count = self.relay_count_for(r.call)
        rows.sort(key=lambda r: r.timestamp, reverse=True)
        return rows[:limit]

    @staticmethod
    def _activity_core(text: object, base: str) -> Optional[str]:
        """Normalize a decoded frame to a content signature for the Activity
        count, or return None if the row is not a countable activity.

        The same physical frame is logged across the spots / band_activity /
        directed tables in slightly different forms -- sometimes with a leading
        "SENDER: " prefix (band_activity), sometimes as the bare payload
        (directed). Stripping a leading callsign-colon prefix and collapsing
        whitespace makes those forms hash to the SAME signature, so one frame is
        counted once no matter how many tables logged it. Two genuinely
        different frames (different target / SNR / body) keep different
        signatures and are counted separately -- even when they share a second.

        Bare signal spots ("SPOT" / "SPOT <grid>") are not posted information and
        return None so they never inflate the count.

        Heartbeats (HB / HEARTBEAT) and pure signal-report traffic (SNR reports,
        SNR? requests, RSNR metadata) are automatic housekeeping, not posted
        activity, so they also return None and are excluded from the Activity
        count. A real MSG whose body merely mentions "SNR" is NOT excluded --
        the exclusion targets frames whose whole payload IS the heartbeat/report.
        """
        up = " ".join(str(text or "").upper().split())
        if not up or up == "SPOT" or up.startswith("SPOT "):
            return None
        # Strip a single leading "SENDER:" prefix (a callsign-like token, with an
        # optional /P style suffix, before the first colon). Handles both
        # "KE4ZDJ: KW3KW HEARTBEAT SNR +05" and "KE4ZDJ/P: ..." regardless of
        # whether `base` carries the suffix. Done before the HB/SNR test so the
        # test sees the actual payload, not the sender prefix.
        head, sep, rest = up.partition(":")
        if sep and rest.strip():
            token = head.replace("/", "").strip()
            if token and token.replace("-", "").isalnum() and any(ch.isdigit() for ch in token):
                up = rest.strip()
        if ActivityReader._is_hb_or_snr_only(up):
            return None
        return up or None

    @staticmethod
    def _is_hb_or_snr_only(up: str) -> bool:
        """True if the normalized payload `up` is a heartbeat or a pure signal
        report/request -- the 'garbage' traffic the operator asked to keep out of
        the Activity count. `up` is already uppercased and whitespace-collapsed,
        with any leading "SENDER:" prefix removed.

        Excluded:
          * Heartbeats:   "... HEARTBEAT ...", or a bare/directed "HB" command
                          ("KW3KW HB", "HB", "@ALLCALL HB HEARTBEAT SNR +05").
          * Signal reports/requests: a payload that is only an SNR report
                          ("KW3KW SNR -07"), an SNR? request ("KW3KW SNR?"),
                          or RSNR metadata ("[RSNR: -12]", "... RSNR ...").

        NOT excluded: a MSG or other real frame that happens to contain "SNR"
        among other words (e.g. "KW3KW MSG PSE SEND SNR LATER") -- those carry
        posted content and stay counted. The test keys on the frame being a
        report/heartbeat in whole, not on the mere presence of the token.
        """
        if not up:
            return False
        # Heartbeats in any position.
        if "HEARTBEAT" in up:
            return True
        # RSNR reciprocal-SNR metadata in any position (never posted content).
        if "[RSNR" in up or " RSNR " in f" {up} " or up.startswith("RSNR "):
            return True
        # Tokenize and drop a leading directed target (callsign or @GROUP) so
        # "KW3KW HB" and "@ALLCALL SNR -07" reduce to their command payload.
        toks = up.split()
        if toks and (toks[0].startswith("@") or looks_like_callsign(toks[0])):
            toks = toks[1:]
        if not toks:
            return False
        # Bare HB command (possibly with trailing SNR): "HB", "HB SNR +05".
        if toks[0] == "HB":
            return True
        # Pure SNR report or request: the remaining payload is just "SNR ..."
        # ("SNR -07", "SNR +05 ...numbers", "SNR?"). If anything other than the
        # SNR token + its numeric value/`?` remains, it's a real frame and stays.
        if toks[0] == "SNR" or toks[0] == "SNR?":
            tail = toks[1:]
            if not tail:                      # "SNR" / "SNR?" alone
                return True
            # allow only a signed number after SNR (the report value)
            if len(tail) == 1 and re.fullmatch(r"[+\-]?\d{1,3}", tail[0]):
                return True
            return False
        return False

    def display_rows(self, time_filter: str, group_filter: str = GROUP_ALL, limit: int = 350) -> List[ActivityRow]:
        # `limit` here caps DISTINCT CALLSIGNS shown on the main screen, not raw
        # DB rows. band_activity logs every decode, so a busy band produces many
        # rows per station. The selected time window (cutoff) is the real bound
        # on how much traffic to consider; within it we must see EVERY station's
        # rows so the per-call dedup below can collapse them.
        #
        # FIELD BUG (dropped rows): the old code passed `limit` straight into
        # read_activity, which applies it as a RAW-row cap (ORDER BY timestamp
        # DESC LIMIT). On a busy band the newest `limit` raw rows can span only
        # ~10 minutes, so stations whose last activity was older than that --
        # but still well inside the 1-hour window -- were silently dropped,
        # while JS8Map (no such raw cap) kept showing them. Because the raw cap
        # was applied BEFORE this dedup, whole callsigns disappeared, not just
        # duplicate frames.
        #
        # Fix: when a window is active, read under a high raw safety ceiling
        # (the window bounds real volume), then cap DISTINCT CALLS after dedup.
        # For "All available" (no cutoff) there is no time bound, so we keep the
        # caller's `limit` as the raw cap to avoid loading the entire DB; that
        # path is a deliberate, documented exception.
        windowed = self.cutoff_string(time_filter) is not None
        raw_limit = max(limit, self._DISPLAY_RAW_CEILING) if windowed else limit
        all_rows = self.read_activity(time_filter, group_filter=group_filter, limit=raw_limit, include_counts=False)
        # Activity-event count per station (Phase 8 "Activity" column). Every raw
        # row from read_activity is bounded by the ACTIVE TIME WINDOW (the cutoff
        # is already applied), so the count re-scopes automatically when the
        # operator changes the window (2 hr -> 1 hr counts only the last hour).
        #
        # We count DISTINCT decoded frames per station, keyed by MESSAGE CONTENT
        # -- not timestamp. Timestamp keying was wrong both ways: the same
        # physical frame is logged in more than one table (spots + band_activity
        # + directed) at slightly skewed seconds, which inflated the count (a
        # station with 2 frames showed 4); and two genuinely different frames can
        # share the same second on different offsets (e.g. KE4ZDJ sending
        # "ND6Q HEARTBEAT SNR -17" and "KW3KW HEARTBEAT SNR +05" in the same
        # decode cycle), which a same-second key would have wrongly collapsed.
        # Content keying fixes both: cross-table copies of one frame carry the
        # same content and collapse to one; two different frames stay distinct.
        # Bare signal spots ("SPOT" / "SPOT <grid>") carry no posted information
        # and are not counted. This matches JS8Call's own per-station frame list.
        activity_events: Dict[str, set] = {}
        for row in all_rows:
            core = self._activity_core(row.text, row.call)
            if core is None:
                continue
            activity_events.setdefault(row.call, set()).add(core)
        # Bug #5 fix: while we still choose ONE representative row per callsign
        # for display (newest-with-text, as before), that choice must NOT lose
        # the directed-message info (to_call, group, and the "both"/"hearing_me"
        # category) that may live on a DIFFERENT row for the same callsign.
        #
        # After a QSY, a station's newest row is often a bare band_activity
        # heartbeat (text but no to_call, category "heard"), which would win the
        # dedup and strip the TO column + Mutual status that its earlier directed
        # SNR-reply row carried. So we track, per callsign, the best directed
        # info seen across ALL its rows and back-fill the winner with it.
        # "hearing_me" is absent by design -- it can never survive display_rows;
        # see the note in the evidence loop of display_rows().
        _CAT_RANK = {"": 0, "heard": 1, "both": 2}
        best_to: Dict[str, str] = {}
        best_group: Dict[str, str] = {}
        best_cat: Dict[str, str] = {}
        for row in all_rows:
            b = row.call
            if row.to_call and not best_to.get(b):
                best_to[b] = row.to_call
            if row.group and not best_group.get(b):
                best_group[b] = row.group
            if _CAT_RANK.get(row.category, 0) > _CAT_RANK.get(best_cat.get(b, ""), 0):
                best_cat[b] = row.category

        by_call: Dict[str, ActivityRow] = {}
        for row in all_rows:
            existing = by_call.get(row.call)
            if not existing:
                by_call[row.call] = row
                continue
            row_has_text = bool(row.text and row.text.strip() and row.text.strip() != "SPOT")
            existing_has_text = bool(existing.text and existing.text.strip() and existing.text.strip() != "SPOT")
            if row.watch_hit and not existing.watch_hit:
                by_call[row.call] = row
            elif row_has_text and not existing_has_text:
                by_call[row.call] = row
            elif row.timestamp > existing.timestamp and (row.watch_hit or row_has_text or not existing_has_text):
                by_call[row.call] = row
        rows = list(by_call.values())
        for r in rows:
            # Back-fill the directed info the winning row may have lacked, so the
            # TO column, group tag, and Mutual status survive dedup even when a
            # newer bare heartbeat row was chosen for display.
            if not r.to_call and best_to.get(r.call):
                r.to_call = best_to[r.call]
            if not r.group and best_group.get(r.call):
                r.group = best_group[r.call]
            if _CAT_RANK.get(best_cat.get(r.call, ""), 0) > _CAT_RANK.get(r.category, 0):
                r.category = best_cat[r.call]
            # Set as a plain attribute, mirroring how inbox_count / relay_count
            # are attached in read_activity(). The display side reads it with a
            # getattr default, so manual/session-only rows (which never pass
            # through here) safely show 0.
            r.activity_count = len(activity_events.get(r.call, ()))
        rows.sort(key=lambda r: r.timestamp, reverse=True)
        # Cap DISTINCT callsigns (newest-first), never raw rows.
        return rows[:limit]

    def inbox_count_for(self, callsign: str) -> int:
        b = norm_call(callsign)
        if not b:
            return 0
        return int(self.inbox_counts_by_call(self.locator.callsign()).get(b, 0))

    def relay_count_for(self, callsign: str) -> int:
        if not self.locator.relay_db or not Path(self.locator.relay_db).exists():
            return 0
        b = base_call(callsign)
        try:
            with self._connect_relay() as con:
                row = con.execute("SELECT COUNT(*) FROM relay_paths WHERE UPPER(target_call) LIKE ? OR UPPER(via_call) LIKE ?", (b + "%", b + "%")).fetchone()
                return int(row[0] if row else 0)
        except Exception:
            return 0

    def fcc_info(self, callsign: str) -> dict:
        if not self.locator.fcc_db or not Path(self.locator.fcc_db).exists():
            return {}
        b = base_call(callsign)
        try:
            with closing(sqlite3.connect(f"file:{self.locator.fcc_db}?mode=ro", uri=True, timeout=2)) as con:
                con.execute("PRAGMA query_only=ON")
                table = "callsigns"
                try:
                    names = {str(x[0]).lower(): str(x[0]) for x in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                    table = names.get("callsigns", table)
                except Exception:
                    pass
                cols = {str(x[1]).lower(): str(x[1]) for x in con.execute(f'PRAGMA table_info("{str(table).replace(chr(34), chr(34)+chr(34))}")').fetchall()}
                call_col = cols.get("callsign") or cols.get("call") or cols.get("call_sign")
                wanted_keys = []
                wanted_cols = []
                aliases = (
                    ("callsign", ("callsign", "call", "call_sign")),
                    ("name", ("name", "fullname", "full_name")),
                    ("city", ("city",)),
                    ("state", ("state",)),
                    ("class", ("class", "license_class")),
                    ("zip", ("zip", "zipcode", "zip_code", "postal_code")),
                )
                for key, names in aliases:
                    for name in names:
                        if name in cols:
                            wanted_keys.append(key)
                            wanted_cols.append(cols[name])
                            break
                if not call_col or not wanted_cols:
                    return {}
                def q(name: str) -> str:
                    return '"' + str(name).replace('"', '""') + '"'
                sql = f"SELECT {','.join(q(c) for c in wanted_cols)} FROM {q(table)} WHERE {q(call_col)}=UPPER(?) LIMIT 1"
                row = con.execute(sql, (b,)).fetchone()
                return dict(zip(wanted_keys, row)) if row else {}
        except Exception:
            return {}

    # ---- JS8Call's REAL message inbox -------------------------------------
    # JS8Call stores actual messages in its own DB: <JS8Call dir>/inbox.db3,
    # table inbox_v1 (id INTEGER, blob TEXT). Each blob is JSON:
    #   {"type":"UNREAD"|"READ"|..., "params":{"FROM":..,"TO":..,"TEXT":..,
    #    "UTC":"YYYY-MM-DD HH:MM:SS","FREQ":..,"CMD":" MSG ",...}}
    # NOTE: js8_spots.db's `inbox_msgs` table is NOT this -- it only holds
    # (msg_id, from_call, first_seen), i.e. heard MSG-ID advertisements, with no
    # recipient and no message text. Reading it for inbox content always failed
    # (missing columns) and silently produced an empty inbox. We read JS8Call
    # directly instead. READ-ONLY: opened with mode=ro; we never write to
    # JS8Call's DB.
    # How long we wait before looking for inbox.db3 again after failing to find
    # it. Only a MISS is retried; see below.
    INBOX_PATH_RETRY_SECS = 10.0

    def js8call_inbox_db(self) -> str:
        # A FOUND path is cached for good: the file does not move while we run.
        #
        # A MISS IS NOT. It used to be. The guard was `if cached is not None`,
        # and a miss caches "" -- which is not None -- so the miss stuck for the
        # life of the process.
        #
        # That is not a hypothetical. If the operator starts FASTCHAT BEFORE
        # JS8CALL, inbox.db3 does not exist yet. JS8Call then starts, creates it
        # and takes mail all evening -- and FastChat never looks again. No
        # inbox, no red flag, no green counter, no Store Msg button, for the
        # whole session, and the "JS8Call inbox not found" message confidently
        # tells him the inbox is missing while it sits on his disk. Restarting
        # FastChat is the only cure and nothing on screen says so.
        #
        # Launch order alone decided it. So we now re-look, throttled so that a
        # genuinely-absent JS8Call does not cost us a directory scan every tick.
        cached = getattr(self, "_js8call_inbox_path", "") or ""
        if cached:
            return cached
        now = time.monotonic()
        last_miss = getattr(self, "_js8call_inbox_miss_at", 0.0)
        if last_miss and (now - last_miss) < self.INBOX_PATH_RETRY_SECS:
            return ""
        candidates: list[Path] = []
        ini = str(getattr(self.locator.app_config, "js8call_ini_path", "") or "")
        if ini:
            try:
                candidates.append(Path(ini).expanduser().parent / "inbox.db3")
            except Exception:
                pass
        for env in ("LOCALAPPDATA", "APPDATA"):
            raw = os.environ.get(env, "")
            if raw:
                candidates.append(Path(raw) / "JS8Call" / "inbox.db3")
        candidates.append(Path.home() / ".local" / "share" / "JS8Call" / "inbox.db3")
        candidates.append(Path.home() / ".config" / "JS8Call" / "inbox.db3")
        found = ""
        for c in candidates:
            try:
                if c and c.exists():
                    found = str(c)
                    break
            except Exception:
                pass
        if found:
            self._js8call_inbox_path = found
            self._js8call_inbox_miss_at = 0.0
            # JS8Call has appeared since we last looked. Anything we cached
            # while it was missing (an empty inbox) is now a lie -- drop it so
            # the next tick reads his real mail instead of serving the blank.
            self.invalidate_inbox_cache()
        else:
            self._js8call_inbox_path = ""
            self._js8call_inbox_miss_at = now
        return found

    # How long a read of JS8Call's inbox is reused before we touch the file
    # again. The refresh pipeline repaints buttons on every tick, and each
    # button would otherwise re-open inbox.db3 -- a lot of contact with a
    # database JS8Call is actively using. Messages arrive on the order of
    # minutes, so a few seconds of staleness costs nothing and keeps us well
    # out of JS8Call's way.
    INBOX_CACHE_SECS = 5.0

    # ---- why we never open JS8Call's live inbox.db3 -------------------------
    #
    # JS8Call keeps inbox.db3 in ROLLBACK-JOURNAL mode -- `PRAGMA journal_mode`
    # returns "delete" (confirmed on the operator's station, 2026-07-11). In
    # that mode a READER holds a SHARED lock, and a writer cannot COMMIT while
    # any SHARED lock is held: JS8Call's write comes back SQLITE_BUSY.
    #
    # The button-repaint path above calls in here on every refresh tick, so
    # FastChat was opening JS8Call's live database roughly every 5 seconds, for
    # as long as it was running. Sooner or later one of those reads overlapped a
    # JS8Call write -- e.g. JS8Call logging an incoming heartbeat before firing
    # the auto-reply. That is the best explanation we have for the three
    # otherwise unexplained JS8Call crashes, all of which happened with no
    # delete, no store, and no API call anywhere near them.
    #
    # The fix is not to read less often. It is to stop touching the file at all:
    # copy it, read the copy. shutil.copy2 is a plain OS file read -- it opens
    # no SQLite connection and takes no SQLite lock, so it is completely
    # invisible to JS8Call. The database is ~64 KB; the copy costs about a
    # millisecond. FastChat can now poll as hard as it likes and remains
    # physically incapable of blocking, locking, or disturbing JS8Call.
    #
    # A copy taken mid-write may be torn, and SQLite will refuse it. That is
    # expected and handled: we keep serving the last good read and try again on
    # the next tick. A few seconds of stale inbox costs nothing. Crashing the
    # operator's radio program costs everything.
    def _inbox_snapshot(self, db: str) -> str:
        """Private copy of JS8Call's inbox. Raises if the copy fails.

        The copy is only RE-taken when JS8Call has actually changed the file.
        Before, every cache miss re-copied it: 64 KB every 5 seconds, forever,
        which on a station left running is about a gigabyte a day written to
        %TEMP% to re-read bytes we already had. Mail arrives every few minutes,
        not every five seconds.
        """
        dst = Path(tempfile.gettempdir()) / f"{_SNAPSHOT_PREFIX}{os.getpid()}.db3"

        if not getattr(self, "_snapshot_swept", False):
            self._snapshot_swept = True
            _sweep_old_snapshots()

        st = os.stat(db)                      # plain stat: no SQLite, no lock
        sig = (st.st_mtime_ns, st.st_size)
        have = getattr(self, "_inbox_snapshot_sig", None)

        if have == sig and dst.exists():
            return str(dst)                   # unchanged since we last looked

        # A rollback journal on disk means JS8Call is mid-transaction. Copying
        # anyway is SAFE -- SQLite writes the file header last, so the copy
        # reads back as the last COMMITTED state (tested; see the phase-4
        # notes). But there is no point copying a file that is being rewritten
        # under us, so if we already hold a good snapshot we simply keep it and
        # look again next tick. We never REFUSE on account of the journal: a
        # stale journal left by a JS8Call crash would then blank the operator's
        # inbox indefinitely, and a silently empty inbox is the one failure this
        # application must never have.
        if have is not None and dst.exists():
            try:
                if os.path.exists(db + "-journal"):
                    return str(dst)
            except Exception:
                pass

        shutil.copy2(db, str(dst))
        self._inbox_snapshot_sig = sig
        _SNAPSHOT_PATHS.add(str(dst))         # so atexit can clean it up
        return str(dst)

    def read_js8call_inbox(self, limit: int = 500) -> list[dict]:
        """All messages JS8Call is holding, newest first. Cached briefly (see
        INBOX_CACHE_SECS). Empty list if the JS8Call inbox cannot be read.

        Reads a COPY of inbox.db3, never JS8Call's live file -- see above.
        """
        now = time.monotonic()
        cached = getattr(self, "_inbox_cache", None)
        cached_at = getattr(self, "_inbox_cache_at", 0.0)
        if cached is not None and (now - cached_at) < self.INBOX_CACHE_SECS:
            return cached

        out: list[dict] = []
        db = self.js8call_inbox_db()
        if not db:
            self._inbox_cache = out
            self._inbox_cache_at = now
            return out
        try:
            # Snapshot first: JS8Call's file is never opened by SQLite here.
            snapshot = self._inbox_snapshot(db)
            # `with sqlite3.connect(...)` does NOT close the connection -- it only
            # commits or rolls back the transaction. The handle stays open. On
            # Linux nobody notices, because you can delete an open file. On
            # WINDOWS you cannot, so the leaked handle kept our own snapshot
            # locked and the cleanup-on-exit silently failed: the temp copies
            # piled up anyway, which is the exact thing the cleanup was added to
            # stop. Close it properly.
            con = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True, timeout=2)
            try:
                sql = "SELECT id, blob FROM inbox_v1 ORDER BY id DESC LIMIT ?"
                for rid, blob in con.execute(sql, (int(limit),)):
                    try:
                        if isinstance(blob, (bytes, bytearray)):
                            blob = blob.decode("utf-8", errors="replace")
                        obj = json.loads(blob)
                    except Exception:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    p = obj.get("params") or {}
                    if not isinstance(p, dict):
                        continue
                    mtype = str(obj.get("type") or "").strip().upper()
                    out.append({
                        "id": rid,
                        "timestamp": str(p.get("UTC") or "").strip(),
                        "freq": p.get("FREQ") or p.get("DIAL") or "",
                        "from_call": norm_call(p.get("FROM") or ""),
                        "to_call": norm_call(p.get("TO") or ""),
                        "text": str(p.get("TEXT") or "").strip(),
                        "msg_type": mtype,
                        # JS8Call's own read state -- authoritative, and matches
                        # the flag shown in JS8Call's Message Inbox window.
                        "unread": mtype == "UNREAD",
                    })
            finally:
                try:
                    con.close()          # the whole point of this rewrite
                except Exception:
                    pass
        except Exception:
            debug_exc("read_js8call_inbox")
            # On failure reuse the last good read rather than flapping to empty.
            if cached is not None:
                return cached
        self._inbox_cache = out
        self._inbox_cache_at = now
        return out

    def invalidate_inbox_cache(self) -> None:
        """Force the next inbox read to hit the file (e.g. when the user opens
        an inbox and wants it current)."""
        self._inbox_cache_at = 0.0

    # Types JS8Call uses once an outgoing stored message is no longer waiting.
    # Row observed in the wild: {"type":"DELIVERED", FROM: me, TO: recipient} --
    # JS8Call flips the type itself when the recipient pulls the message, so a
    # live count of the non-delivered ones self-decrements with no pickup
    # detection on our side. Anything NOT in this set is still waiting.
    OUTBOX_DONE_TYPES = {"DELIVERED", "EXPIRED", "SENT", "READ", "UNREAD"}

    def outbox_rows_for(self, callsign: str, my_call: str = "", limit: int = 200) -> list[dict]:
        """Messages I am holding LOCALLY for `callsign`, still waiting to be
        picked up (they sent me no QUERY MSGS yet). FROM = me, TO = them."""
        b = norm_call(callsign)
        me = base_call(my_call)
        if not b or not me:
            return []
        rows: list[dict] = []
        for m in self.read_js8call_inbox():
            if base_call(m.get("from_call", "")) != me:
                continue
            if norm_call(str(m.get("to_call", "") or "")) != b:
                continue
            if str(m.get("msg_type", "")).upper() in self.OUTBOX_DONE_TYPES:
                continue  # already picked up / no longer waiting
            rows.append(m)
            if len(rows) >= limit:
                break
        return rows

    def outbox_waiting_count(self, callsign: str, my_call: str = "") -> int:
        return len(self.outbox_rows_for(callsign, my_call))

    # ---- DELETING messages from JS8Call's inbox ---------------------------
    # JS8Call exposes no delete over its API (probed: INBOX.DELETE_MESSAGE and
    # every plausible variant are ignored), so removing a message means writing
    # to its live SQLite file. We do that only with a safety net:
    #   * a timestamped backup copy is made BEFORE any delete, and
    #   * if the backup cannot be made, we refuse to delete at all.
    # JS8Call may keep its own in-memory view of the inbox, so its Message Inbox
    # window can still show a deleted message until JS8Call is restarted.
    # FastChat reflects the deletion immediately.
    # ---- THE DELETE KILL-SWITCH (ticket #2b) -------------------------------
    # Set False to remove message deletion from the product entirely.
    #
    # WHY IT IS OFF. Every READ of JS8Call's inbox now goes through a COPY, so
    # FastChat cannot lock or block JS8Call (that was the crash). Delete cannot
    # be fixed the same way. Removing a row means WRITING to JS8Call's live
    # inbox.db3, which is in journal_mode=delete, which means taking an
    # EXCLUSIVE lock on a file JS8Call has open. You cannot copy your way out of
    # a write. It is the last thing in FastChat that touches JS8Call's live
    # database, and it is more aggressive than the read that was already
    # crashing it.
    #
    # This flag is the SINGLE SOURCE OF TRUTH. The UI asks the reader (see
    # main_window: _delete_enabled) rather than keeping its own copy, so the
    # buttons cannot say one thing while the database layer does another. It is
    # enforced HERE as well as in the UI: gating only the buttons would leave a
    # live write path one stray call away.
    DELETE_ENABLED = False

    def backup_inbox_db(self) -> str:
        """Copy JS8Call's inbox DB next to itself, timestamped. Returns the
        backup path. Raises on failure (callers must refuse to delete)."""
        db = self.js8call_inbox_db()
        if not db:
            raise RuntimeError("JS8Call inbox not found.")
        src = Path(db)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = src.with_name(f"{src.stem}_backup_{stamp}{src.suffix}")
        shutil.copy2(str(src), str(dst))
        return str(dst)

    def delete_inbox_messages(self, ids: Sequence[object]) -> Tuple[int, str, str]:
        """Delete rows from JS8Call's inbox by inbox_v1.id.

        Returns (deleted_count, backup_path, error). On any failure nothing is
        deleted. A backup is always taken first; if it fails, we do not proceed.
        """
        # Refuse BEFORE we go anywhere near JS8Call's file -- no locate, no
        # backup, no connection. See DELETE_ENABLED above (ticket #2b).
        if not self.DELETE_ENABLED:
            return 0, "", ("Deleting messages is turned off in this release. "
                           "It is the one operation that must write to JS8Call's "
                           "own database, and doing that while JS8Call is running "
                           "can stop JS8Call from saving incoming messages.")

        db = self.js8call_inbox_db()
        if not db:
            return 0, "", "JS8Call inbox database not found."
        wanted: list[int] = []
        for i in ids:
            try:
                wanted.append(int(i))
            except Exception:
                continue
        if not wanted:
            return 0, "", "No messages selected."

        try:
            backup = self.backup_inbox_db()
        except Exception as e:
            return 0, "", f"Backup failed, refusing to delete: {type(e).__name__}: {e}"

        try:
            qmarks = ",".join("?" * len(wanted))
            # closing() not the bare form: the explicit con.commit() below
            # already handles the transaction, so nothing is lost by closing.
            with closing(sqlite3.connect(db, timeout=5.0)) as con:
                con.execute("PRAGMA busy_timeout=5000")
                # Collect JS8Call's own message ids so we can clean up the
                # group-recipient side table too.
                mids: list[str] = []
                for _rid, blob in con.execute(
                    f"SELECT id, blob FROM inbox_v1 WHERE id IN ({qmarks})", wanted
                ):
                    try:
                        if isinstance(blob, (bytes, bytearray)):
                            blob = blob.decode("utf-8", errors="replace")
                        p = (json.loads(blob) or {}).get("params") or {}
                        mid = p.get("_ID")
                        if mid:
                            mids.append(str(mid))
                    except Exception:
                        pass
                cur = con.execute(
                    f"DELETE FROM inbox_v1 WHERE id IN ({qmarks})", wanted
                )
                deleted = int(cur.rowcount or 0)
                if mids:
                    try:
                        qm2 = ",".join("?" * len(mids))
                        con.execute(
                            f"DELETE FROM inbox_group_recip_v1 WHERE msg_id IN ({qm2})",
                            mids,
                        )
                    except Exception:
                        pass  # side table is optional
                con.commit()
        except Exception as e:
            return 0, backup, f"{type(e).__name__}: {e}"

        self.invalidate_inbox_cache()
        return deleted, backup, ""

    def inbox_rows_for(self, callsign: str, to_me_only: bool = True, my_call: str = "", limit: int = 200) -> list[dict]:
        b = norm_call(callsign)
        me = base_call(my_call)
        if to_me_only and not me:
            # "To Me Only" requested but the operator callsign is unknown. Fail
            # CLOSED -- show nothing -- rather than falling open to every message
            # from this station, which would expose mail addressed to OTHERS.
            return []

        # Primary source: JS8Call's own inbox (the messages you actually see in
        # JS8Call's Message Inbox window).
        rows: list[dict] = []
        for m in self.read_js8call_inbox():
            if norm_call(str(m.get("from_call", "") or "")) != b:
                continue
            if to_me_only and base_call(m.get("to_call", "")) != me:
                continue
            rows.append(m)
            if len(rows) >= limit:
                break
        if rows:
            return rows
        if self.js8call_inbox_db():
            # JS8Call inbox is readable and simply has nothing from this call.
            return rows
        return self._inbox_rows_for_legacy(callsign, to_me_only, my_call, limit)

    def _inbox_rows_for_legacy(self, callsign: str, to_me_only: bool = True, my_call: str = "", limit: int = 200) -> list[dict]:
        """Old spot-DB path, kept only as a fallback when JS8Call's inbox.db3
        cannot be located.

        DO NOT reintroduce a SELECT against `inbox_msgs` here. This function
        used to run
            SELECT id, timestamp, freq, from_call, to_call, text FROM inbox_msgs
        against a table that has exactly THREE columns -- msg_id, from_call,
        first_seen (see the SCHEMA NOTE on inbox_counts_by_call). It therefore
        threw on EVERY call and reached the derivation below via the except
        handler, which meant a genuine DB error was indistinguishable from
        normal operation, and rows came back with id = "" so they could not be
        tracked or deleted. Same defect class as the Phase 2 inbox-flag fix;
        this fallback was missed at the time.

        There is no correct rewrite of that query: this function needs `text`
        and `to_call`, and the three-column table has neither by design -- it is
        a lightweight "who has mail waiting" index. So the derivation from
        directed traffic IS the implementation, not a fallback from one.
        """
        rows: list[dict] = []
        b = norm_call(callsign)
        me = base_call(my_call)
        if to_me_only and not me:
            return rows
        # Derive from directed rows containing MSG, avoiding MSG ID / RSNR metadata.
        try:
            for r in self.read_activity("All available", limit=limit):
                if r.call != b:
                    continue
                if to_me_only and base_call(r.to_call) != me:
                    continue
                text_up = (r.text or "").upper()
                if " MSG " in f" {text_up} " and "MSG ID" not in text_up and "[RSNR" not in text_up:
                    rows.append({"id": "", "timestamp": r.timestamp, "freq": r.freq, "from_call": r.call, "to_call": r.to_call, "text": r.text, "msg_type": "", "unread": True})
                    if len(rows) >= limit:
                        break
        except Exception:
            debug_exc("_inbox_rows_for_legacy")
        return rows

    def diagnostics(self) -> str:
        lines = []
        lines.extend(self.locator.summary_lines())
        lines.append("")
        if not self.locator.spot_db or not Path(self.locator.spot_db).exists():
            lines.append("Spot DB not found.")
            return "\n".join(lines)
        try:
            with self._connect_spots() as con:
                for table in ("spots", "directed", "band_activity", "watched_calls", "inbox_msgs", "group_activity", "api_log"):
                    try:
                        count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                        lines.append(f"{table}: {count}")
                    except Exception as e:
                        lines.append(f"{table}: {type(e).__name__}: {e}")
                lines.append("")
                lines.append(f"Newest DB time: {self.newest_db_time()}")
        except Exception as e:
            lines.append(f"Diagnostics failed: {type(e).__name__}: {e}")
        return "\n".join(lines)
