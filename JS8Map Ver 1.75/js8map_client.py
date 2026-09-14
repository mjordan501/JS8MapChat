#!/usr/bin/env python3
"""JS8Call TCP client: connect, receive-loop, frame parsing, spot ingestion.

Extracted from JS8Map.py. Drives relay state via the js8map_relay holders/
helpers (shared by reference). _last_seen_offset is a live dict shared by
reference with the map renderer. Leaf-ish: imports only stdlib + js8map_*."""
from __future__ import annotations

import json
import re
import socket
import threading
import time
from datetime import datetime, timedelta

from js8map_util import _utc_from_epoch_ms, _utc_now, _utc_stamp, is_valid_grid, norm_call, norm_grid
from js8map_activity_store import SpotDatabase
from js8map_relay import (
    _relay_anim_ref, _relay_committed_ref, _relay_dest_timer, _relay_outcome_ref,
    _relay_tx_frame_count, _relay_tx_last_frame_ts, _relay_tx_timer,
    _relay_yes_list_ref, _relay_yes_ts_ref, _restart_dest_timer,
    _restart_return_timer, _set_relay_anim,
)


# ── JS8Call frame-parsing patterns ──
_TX_SNR_RE   = re.compile(                        # outgoing SNR response pattern
    r'(?:[A-Z0-9/]+):\s*([A-Z0-9]{3,}(?:/[A-Z0-9]+)?)\s+SNR\s*([+\-]?\d+)', re.I)
_TX_GRP_SNR_RE = re.compile(                      # outgoing @GROUP SNR? — auto-arm listener
    r'(@[A-Z0-9_\-]+)\s+SNR\?', re.I)
_INT_RE          = re.compile(r'^[+\-]?\d+$')                         # bare signed integer
_DIRECTED_CALL_RE = re.compile(r'^[A-Z0-9]{1,3}[0-9][A-Z]{1,4}', re.I) # directed-frame callsign prefix
_HEARING_RE      = re.compile(r'\bHEARING\b(?!\?)', re.I)             # HEARING keyword (not HEARING?)
_GRID_KW_RE      = re.compile(r'\bGRID\s+([A-R]{2}[0-9]{2}(?:[A-X]{2})?)\b', re.I)  # GRID <locator> in text
_STATUS_CALL_RE  = re.compile(                                          # callsign in STATION.STATUS value
    r'^(?:[A-Z]{1,2}[0-9][A-Z]{0,4}[0-9]?[A-Z]{0,3}|[0-9][A-Z]{2}[0-9][A-Z]{0,3})$', re.I)
GRID_IN_TEXT = re.compile(r'\b([A-R]{2}[0-9]{2}(?:[A-X]{2})?)\b', re.I)
MSG_ID_RE     = re.compile(r'\bMSG\s+ID\s+(\d+)', re.I)   # "MSG ID 211" in heartbeat TEXT

# shared by reference with the map renderer (offset per callsign)
_last_seen_offset        = {}    # {callsign: int(offset_hz)} — populated from every RX.DIRECTED


class JS8CallClient:
    """
    Persistent TCP connection to JS8Call's API server (default port 2442).
    Reconnects automatically on disconnect.
    Thread-safe — all callbacks arrive on the background thread;
    GUI code must use root.after() to act on them.
    """

    RECONNECT_DELAY = 12   # seconds between reconnect attempts
    RECV_TIMEOUT    = 30   # socket recv timeout seconds

    def __init__(self, db: SpotDatabase,
                 on_spot=None, on_status=None, on_my_station=None):
        self.db            = db
        self.on_spot       = on_spot        # () → called when new data arrives
        self.on_status     = on_status      # (msg, is_connected) → GUI update
        self.on_my_station = on_my_station  # (callsign, grid) → GUI update

        self.host = '127.0.0.1'
        self.port = 2442

        self._sock         = None
        self._running      = False
        self._connected    = False
        self._thread       = None
        self._my_callsign  = ''    # filled from STATION.CALLSIGN
        self._my_freq      = 0     # current dial frequency Hz (from BAND_ACTIVITY)
        # Clear watermark (QSY / Clear-Map). After a clear we must NOT re-import
        # the stations JS8Call still retains in its CALL_ACTIVITY / BAND_ACTIVITY
        # buffers: JS8Call keeps them for hours (its own list wasn't cleared) and
        # stamps them with their ORIGINAL decode UTC, which still falls inside the
        # active time filter -- so they would re-plot and undo the clear. Any
        # incoming station whose decode UTC (ms epoch) is older than this
        # watermark is dropped at ingestion. A still-active station reappears the
        # instant JS8Call decodes it again (its UTC advances past the mark).
        # 0 = no watermark set yet (import everything).
        self._clear_epoch_ms = 0
        self._last_ba_text          = {}   # callsign -> last TEXT seen in BAND_ACTIVITY
        self._group_config              = None # set by HamMapApp after construction
        self._pending_group_queries     = {}   # {group_name: arm_timestamp float} — kept for manual arm button
        self._last_group_query          = None # (group_name, timestamp) — auto-set by Path A, used by Path B
        self._pending_3rd_party_queries = {}   # {querying_callsign: (group_name, timestamp)}
        self._relay_db                  = None  # RelayDatabase — wired by HamMapApp
        self._pending_relay_target      = None  # (target_call, query_datetime) or None
        self._pending_relay_audience    = None  # '@GROUP' or '@ALLCALL' the QUERY CALL was sent to (v1.82_C)
        self._on_relay_hint             = None  # callback(from_call) — YES with no target
        # Tracks TEXT changes to detect new autonomous transmissions.
        # JS8Call's BAND_ACTIVITY UTC updates on ANY decode (incl. directed),
        # but the TEXT field only changes when a new autonomous frame is decoded.
        # A changed TEXT = new autonomous broadcast → update our timestamp.
        # Unchanged TEXT = same old frame, station just still tracked → keep old ts.

    # ── Public interface ──────────────────────────────────────

    def configure(self, host: str, port: int):
        self.host = host
        self.port = port

    def set_clear_watermark(self, when_ms: int = 0) -> None:
        """Mark 'now' (or a given ms-epoch) as the clear point. Stations JS8Call
        still retains from before this moment are dropped at ingestion so a
        Clear-Map / QSY is not immediately undone by the next snapshot poll."""
        self._clear_epoch_ms = int(when_ms) if when_ms else int(_utc_now().timestamp() * 1000)

    def _is_prewatermark(self, utc_ms) -> bool:
        """True if a station's decode UTC predates the clear watermark and must
        be skipped. A missing/zero UTC is treated as a fresh decode (kept), since
        those fall back to poll time, which is after any clear."""
        try:
            return bool(self._clear_epoch_ms) and bool(utc_ms) and int(utc_ms) < self._clear_epoch_ms
        except (TypeError, ValueError):
            return False

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name='JS8CallClient')
        self._thread.start()

    def stop(self):
        self._running = False
        self._connected = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass

    def send(self, msg_type: str, value: str = '', params: dict = None) -> bool:
        """Send a JSON command to JS8Call. Returns True on success."""
        if not self._connected or not self._sock:
            return False
        try:
            payload = json.dumps({
                'type': msg_type, 'value': value, 'params': params or {}
            }) + '\n'
            self._sock.sendall(payload.encode('utf-8'))
            self.db.add_log_entry('→', msg_type, payload.strip())
            return True
        except Exception:
            return False

    # ── Background thread ─────────────────────────────────────

    def _run(self):
        while self._running:
            try:
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._sock.settimeout(self.RECV_TIMEOUT)
                self._sock.connect((self.host, self.port))
                self._connected = True

                if self.on_status:
                    self.on_status(
                        f"✓  Connected to JS8Call  ({self.host}:{self.port})", True)

                # Request station info immediately after connecting
                self.send('STATION.GET_INFO')
                self.send('STATION.GET_GRID')
                self.send('STATION.GET_CALLSIGN')  # explicit callsign query
                self.send('INBOX.GET_MESSAGES')    # pull pending inbox messages on connect
                # Fetch current stations already visible in JS8Call.
                # RX.SPOT only fires for NEW decodes — if we connected after
                # stations were already decoded we'd get nothing without these.
                self._last_ba_text.clear()   # reset TEXT baseline on (re)connect
                self._my_freq = 0              # reset frequency on (re)connect
                self.send('RIG.GET_FREQ')       # get current dial frequency reliably
                self.send('RX.GET_BAND_ACTIVITY')
                self.send('RX.GET_CALL_ACTIVITY')
                # Note: INBOX.GET_MESSAGES already sent above at connect time

                buf = ''
                _last_poll = time.time()
                _last_inbox_poll = time.time()   # throttle inbox to every 5 min
                while self._running:
                    try:
                        chunk = self._sock.recv(8192).decode('utf-8', errors='replace')
                        if not chunk:
                            break       # server closed connection
                        buf += chunk
                        # Process complete lines (JS8Call sends one JSON per line)
                        while '\n' in buf:
                            line, buf = buf.split('\n', 1)
                            line = line.strip('\r ')
                            if line:
                                self._handle_line(line)
                    except socket.timeout:
                        # No data — send a band activity poll every ~60s to
                        # keep the DB fresh even when JS8Call is quiet
                        now = time.time()
                        if now - _last_poll >= 30:
                            _last_poll = now
                            self.send('RIG.GET_FREQ')
                            self.send('RX.GET_BAND_ACTIVITY')
                            self.send('RX.GET_CALL_ACTIVITY')
                            # Inbox only every 5 min — messages don't change rapidly
                            if now - _last_inbox_poll >= 60:   # poll every 60s so read msgs clear promptly
                                _last_inbox_poll = now
                                self.send('INBOX.GET_MESSAGES')
                        continue
                    except (ConnectionResetError, BrokenPipeError, OSError):
                        break

            except (ConnectionRefusedError, OSError) as e:
                pass   # JS8Call not running — will retry
            except Exception:
                pass
            finally:
                self._connected = False
                try:
                    if self._sock:
                        self._sock.close()
                except Exception:
                    pass
                if self._running and self.on_status:
                    self.on_status(
                        f"⚠  Not connected  —  JS8Call on {self.host}:{self.port} "
                        f"not responding.  Retrying in {self.RECONNECT_DELAY}s…", False)

            if self._running:
                time.sleep(self.RECONNECT_DELAY)

    def _handle_line(self, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = str(msg.get('type', ''))
        params   = msg.get('params', {}) or {}
        value    = str(msg.get('value', '') or '')
        ts       = _utc_stamp()

        # Log to DB (sample: only certain types to avoid DB spam)
        if msg_type in ('RX.SPOT', 'RX.DIRECTED', 'STATION.INFO', 'STATION.GRID',
                       'STATION.CALLSIGN', 'RX.CALL_ACTIVITY', 'RX.BAND_ACTIVITY',
                       'TX.FRAME', 'TX.TEXT', 'TX.SENT',
                       'RIG.FREQ', 'STATION.STATUS', 'INBOX.MESSAGES', 'CLOSE'):
            self.db.add_log_entry('←', msg_type, raw[:500])

        # ── RX.SPOT ───────────────────────────────────────────
        # Fired for every decoded station. Contains CALL, GRID, SNR, DIAL, OFFSET, SPEED.
        if msg_type == 'RX.SPOT':
            call   = norm_call(params.get('CALL', params.get('CALLSIGN', '')))
            grid   = norm_grid(params.get('GRID', params.get('LOCATOR', '')))
            snr    = float(params.get('SNR',    0) or 0)
            freq   = float(params.get('DIAL',   params.get('FREQ', 0)) or 0)
            offset = float(params.get('OFFSET', 0) or 0)
            speed  = int  (params.get('SPEED',  0) or 0)
            if call:
                self.db.add_spot(call, grid if is_valid_grid(grid) else '',
                                 snr, freq, offset, speed, ts, 'RX.SPOT')
                if self.on_spot:
                    self.on_spot()

        # ── RX.DIRECTED ───────────────────────────────────────
        # A message directed from one station to another (or a group).
        elif msg_type == 'RX.DIRECTED':
            from_call = norm_call(params.get('FROM', ''))
            to_call   = norm_call(params.get('TO', ''))
            text      = str(params.get('TEXT', value) or '').strip()
            snr       = float(params.get('SNR',  0) or 0)  # reception SNR (how well KW3KW hears sender)
            freq      = float(params.get('DIAL', params.get('FREQ', 0)) or 0)
            offset_hz = float(params.get('OFFSET', 0) or 0)
            # EXTRA = the reported SNR value the sender is broadcasting
            # e.g. 'K8DDD: KW3KW SNR -01' -> EXTRA='-01' (how K8DDD hears KW3KW)
            # This is DIFFERENT from snr (which is how KW3KW hears K8DDD).
            extra = str(params.get('EXTRA', '') or '').strip()
            cmd   = str(params.get('CMD',   '') or '').strip()
            # Embed EXTRA into text with a reliable marker for later retrieval
            if extra and _INT_RE.match(extra):
                text_stored = text + f' [RSNR:{extra}]'
            else:
                text_stored = text
            if from_call:
                self.db.add_directed(from_call, to_call, text_stored, snr, freq, ts, offset_hz)
                # C.18: remember this station's TX offset for relay dest timeout watch
                if offset_hz:
                    _last_seen_offset[from_call] = int(offset_hz)
                # Parse GRID keyword first: 'KA2YNT: KW3KW GRID FM29'
                # IMPORTANT: skip grid extraction entirely for HEARING responses.
                # "KO4HKX: KW3KW HEARING KO4JNH KQ4QZX KQ4DNM KU4B" lists
                # callsigns after HEARING, and the grid regex [A-R]{2}[0-9]{2}[A-X]{2}
                # matches callsigns like KO4JNH perfectly — assigning "Finland" as
                # the station's grid and making them invisible on a US-focused map.
                _is_hearing = bool(_HEARING_RE.search(text))
                if _is_hearing:
                    grid = ''   # callsign list after HEARING is not a grid source
                else:
                    grid_kw = _GRID_KW_RE.search(text)
                    if grid_kw and is_valid_grid(grid_kw.group(1)):
                        grid = grid_kw.group(1).upper()[:6]
                    else:
                        gm   = GRID_IN_TEXT.search(text)
                        grid = gm.group(1) if gm and is_valid_grid(gm.group(1)) else ''
                self.db.add_spot(from_call, grid if is_valid_grid(grid) else '', snr, freq, offset_hz, 0, ts, 'RX.DIRECTED')
                mid_m = MSG_ID_RE.search(text)
                # Only store inbox entries for messages directly addressed to
                # my callsign. Group messages (@AMRRON etc) are broadcast and
                # do NOT appear in JS8Call's inbox — do not flag them.
                _to_me = (self._my_callsign and to_call == self._my_callsign)
                if mid_m and _to_me:
                    self.db.add_inbox_msg(mid_m.group(1), from_call, ts)
                # ── Group activity detection — wrapped to never crash client thread ──
                try:
                    # Path A: station addressed ANY @GROUP directly — always tag, no config needed
                    # e.g. "W4ABC: @AMRRON SNR?" → tag W4ABC as @AMRRON member
                    # Also sets _last_group_query so Path B can correlate replies to KW3KW
                    if (to_call.startswith('@')
                            and to_call not in ('@ALLCALL', '@HB')
                            and from_call):
                        self.db.upsert_group_activity(from_call, to_call, ts)
                        self.db.add_log_entry('⊕', 'GRP-A',
                            f'Auto-tagged {from_call} -> {to_call}')
                        # Path C setup: if this was an SNR query (empty EXTRA),
                        # watch for other stations responding TO from_call
                        if not extra and cmd and 'SNR' in cmd.upper():
                            self._pending_3rd_party_queries[from_call] = (to_call, time.time())
                            # Track last group queried so Path B can correlate replies
                            self._last_group_query = (to_call, time.time())

                    # Path B: station replied to our @GROUP SNR? query
                    # Their reply is addressed to KW3KW with a bare SNR number.
                    # Always-on: correlate against _last_group_query (10-min window),
                    # no manual arming required.
                    # Guard: HEARTBEAT SNR responses also arrive as TO=KW3KW
                    # with a numeric EXTRA — exclude them or every HB responder
                    # gets wrongly tagged.
                    _is_hb = 'HEARTBEAT' in (cmd or '').upper()
                    if (not _is_hb
                            and self._my_callsign and to_call == self._my_callsign
                            and extra and from_call):
                        _now = time.time()
                        _is_snr = bool(_INT_RE.match(extra.strip()))
                        _lgq = getattr(self, '_last_group_query', None)
                        if _is_snr and _lgq:
                            _lgq_grp, _lgq_ts = _lgq
                            if _now - _lgq_ts <= 600:   # 10-minute correlation window
                                self.db.upsert_group_activity(from_call, _lgq_grp, ts)
                                self.db.add_log_entry('⊕', 'GRP-B',
                                    f'Auto-tagged {from_call} -> {_lgq_grp}')

                    # Path C: station replied to a THIRD-PARTY @GROUP SNR? query
                    # e.g. W4ABC sent @AMRRON SNR? (Path A tagged W4ABC, opened watch window)
                    # K3XYZ responds K3XYZ: W4ABC SNR -05 — K3XYZ is also an @AMRRON member
                    if (from_call and to_call
                            and not to_call.startswith('@')
                            and to_call != self._my_callsign
                            and extra
                            and to_call in self._pending_3rd_party_queries):
                        _now3 = time.time()
                        _entry = self._pending_3rd_party_queries[to_call]
                        _c_grp, _c_ts = _entry
                        if _now3 - _c_ts <= 180:
                            _is_snr3 = bool(_INT_RE.match(extra.strip()))
                            if _is_snr3 and _c_grp in self._group_config.get_groups():
                                self.db.upsert_group_activity(from_call, _c_grp, ts)
                                self.db.add_log_entry('⊕', 'GRP-C',
                                    f'Tagged {from_call} -> {_c_grp} (via {to_call} query)')
                        else:
                            del self._pending_3rd_party_queries[to_call]

                except Exception:
                    pass  # never let group detection crash the client thread

                # ── Relay YES detection ──────────────────────────────
                # Separate try/except so relay never interferes with group.
                # JS8Call sends TX.FRAME (not TX.TEXT) so auto-arming via
                # outgoing text is not possible — the user arms manually via
                # the inline relay field on the Python screen.
                try:
                    if cmd.upper() == 'YES' and from_call and self._relay_db:
                        if self._pending_relay_target:
                            _rtgt, _rqts = self._pending_relay_target
                            if (datetime.now() - _rqts).total_seconds() <= 300:
                                self._relay_db.log_path(
                                    via_call    = from_call,
                                    target_call = _rtgt,
                                    snr         = snr if snr is not None else None,
                                    query_ts    = _rqts.strftime('%Y-%m-%d %H:%M:%S'),
                                )
                                self.db.add_log_entry('⊕', 'RELAY',
                                    f'{from_call} can reach {_rtgt}'
                                    + (f'  SNR {snr:+.0f}' if snr is not None else ''))
                                # C.18: persist YES entry for Switch Path panel
                                _relay_yes_list_ref[0].append({
                                    'via':    from_call,
                                    'snr':    snr,
                                    'offset': int(offset_hz) if offset_hz else 0,
                                    'ts':     time.time(),
                                })
                                _relay_yes_ts_ref[0] = time.time()
                                # v1.82_C: a YES to a GROUP-directed QUERY CALL
                                # proves the responder received an @GROUP message,
                                # so tag it to that group — its box then shows the
                                # group color. The SNR-based group Path B misses
                                # these because YES replies carry an empty EXTRA
                                # (the SNR sits inside TEXT). Skip @ALLCALL/@HB:
                                # a YES there proves no specific group membership.
                                _aud = getattr(self, '_pending_relay_audience', None)
                                if (_aud and _aud.startswith('@')
                                        and _aud not in ('@ALLCALL', '@HB')):
                                    try:
                                        self.db.upsert_group_activity(from_call, _aud, ts)
                                        self.db.add_log_entry('⊕', 'GRP-Y',
                                            f'Auto-tagged {from_call} -> {_aud} (YES to QUERY CALL)')
                                    except Exception:
                                        pass
                        else:
                            # YES arrived but no target armed — notify operator
                            self.db.add_log_entry('📡', 'RELAY-HINT',
                                f'{from_call} said YES — enter target in 🔁 field and press Search')
                            if self._on_relay_hint:
                                self._on_relay_hint(from_call)
                except Exception:
                    pass

                # Phase 2: relay chain hop detection (CMD=">")
                try:
                    if cmd.strip() == '>':
                        _committed = _relay_committed_ref[0]
                        if _committed and self._my_callsign:
                            my_cs = norm_call(self._my_callsign)
                            _via  = norm_call(_committed['via'])
                            _tgt  = norm_call(_committed['target'])
                            _frm  = norm_call(from_call) if from_call else ''
                            _toc  = norm_call(to_call)   if to_call   else ''
                            if _frm == _via and _toc == _tgt:
                                _set_relay_anim('leg2_fwd', _via, _tgt)
                            elif _frm == _tgt and _toc == _via:
                                _set_relay_anim('rtn_leg1', _via, _tgt)
                            elif _frm == _via and _toc == my_cs:
                                _set_relay_anim('rtn_leg2', _via, _tgt)
                                # Outcome confirmed — set pill only on full decode
                                _relay_outcome_ref[0] = {
                                    'status': 'complete', 'via': _via,
                                    'target': _tgt, 'ts': time.time()}
                                self.db.add_log_entry('\u2713', 'RELAY',
                                    f'Complete \u2014 ACK received from {_tgt} via {_via}')
                except Exception:
                    pass

                if self.on_spot:
                    self.on_spot()

        # ── STATION.INFO / STATION.GRID ───────────────────────
        # Response to STATION.GET_INFO / STATION.GET_GRID.
        # JS8Call versions vary in what they put in params vs value:
        #   Newer:  params.CALLSIGN="KW3KW", params.GRID="FM05SX"
        #   Some:   params.CALLSIGN="FM05SX" (grid misplaced in CALLSIGN field)
        #   Grid-only: value="FM05SX", params={}
        # We must detect and correct all cases.
        elif msg_type in ('STATION.INFO', 'STATION.GRID'):
            raw_cs   = norm_call(params.get('CALLSIGN', params.get('CALL', '')))
            raw_grid = norm_grid(params.get('GRID', ''))

            callsign = ''
            grid     = ''

            # GRID param is unambiguous
            if raw_grid and is_valid_grid(raw_grid):
                grid = raw_grid

            # CALLSIGN param — if it looks like a Maidenhead grid, it IS a grid
            if raw_cs:
                if is_valid_grid(raw_cs):
                    if not grid:
                        grid = raw_cs   # FM05SX was wrongly put in CALLSIGN field
                else:
                    callsign = raw_cs   # looks like a real callsign

            # Scan value string token by token — grid is unambiguous; callsign has
            # digit(s) but does NOT match the Maidenhead pattern.
            _CALL_RE = _STATUS_CALL_RE
            if value and (not callsign or not grid):
                for token in value.upper().split():
                    if not grid and is_valid_grid(token):
                        grid = token
                    elif not callsign and not is_valid_grid(token) and _CALL_RE.match(token):
                        callsign = token

            # Persist and notify — always update if we got at least a grid
            if callsign or grid:
                if not callsign:
                    existing = self.db.get_my_station()
                    callsign = existing.get('callsign', '')
                self.db.set_my_station(callsign, grid)
                if callsign:
                    self._my_callsign = callsign   # used to detect SNR reports in BAND_ACTIVITY
                if self.on_my_station:
                    self.on_my_station(callsign, grid)

        # ── RIG.FREQ ─────────────────────────────────────────
        # Response to RIG.GET_FREQ — the authoritative current dial frequency.
        # Also sent as an async notification when the rig frequency changes.
        elif msg_type == 'RIG.FREQ':
            dial = float(params.get('DIAL', 0) or 0)
            if dial:
                old_freq = self._my_freq
                self._my_freq = dial
                self.db.add_log_entry('←', msg_type, f'DIAL={dial}')
                if old_freq and abs(old_freq - dial) > 1000:   # mid-session QSY
                    if self.on_status:
                        self.on_status(f'✓  Frequency: {dial/1e6:.3f} MHz  ·  Updating map…', True)
                    if self.on_spot:
                        self.on_spot()

        # ── STATION.STATUS ────────────────────────────────────
        # Fires when ANY station attribute changes (frequency QSY, speed, etc.)
        # Use it to detect mid-session frequency changes in real-time.
        elif msg_type == 'STATION.STATUS':
            dial = float(params.get('DIAL', 0) or 0)
            if dial:
                old_freq = self._my_freq
                self._my_freq = dial
                if old_freq and abs(old_freq - dial) > 1000:  # >1kHz = QSY
                    self._last_ba_text.clear()
                    self.db.clear_spots()
                    self.db.add_log_entry('←', msg_type, f'QSY {old_freq}->{dial}')
                    if hasattr(self, '_qsy_callback') and self._qsy_callback:
                        self._qsy_callback(dial)
                    if self.on_status:
                        self.on_status(
                            f'✓  QSY → {dial/1e6:.3f} MHz  ·  '
                            f'Map cleared — waiting for new decodes', True)
                    if self.on_spot:
                        self.on_spot()

        # ── CLOSE ────────────────────────────────────────────
        # JS8Call is shutting down.
        elif msg_type == 'CLOSE':
            self._connected = False
            if self.on_status:
                self.on_status('⚠  JS8Call closed — reconnecting…', False)

        # ── PING ─────────────────────────────────────────────
        # Keepalive from JS8Call — just log it, no response needed.
        elif msg_type == 'PING':
            pass   # connection confirmed alive; logged by the catch-all above

        # ── TX.TEXT / TX.FRAME / TX.SENT ───────────────────
        # TX.FRAME value contains encoded JS8 tones — _TX_SNR_RE can never
        # match it.  TX.TEXT carries the human-readable outgoing message and
        # is the correct event for SNR detection + mutual-dot recording.
        # TX.FRAME is still included so the map-refresh trigger fires on
        # actual transmission even when TX.TEXT has no readable payload.
        elif msg_type in ('TX.TEXT', 'TX.FRAME', 'TX.SENT'):
            tx_text = str(value or params.get('TEXT', '') or '').strip()
            # Detect outgoing 'MY_CALL: STATION SNR +XX' — this auto-response
            # proves KW3KW heard STATION's heartbeat. Trigger map refresh.
            if tx_text and self._my_callsign and self._my_callsign in tx_text.upper():
                if self.on_spot:
                    self.on_spot()
            # Parse 'MY_CALL: STATION SNR +XX' to write a directed record.
            # This lets the map detect the bidirectional HB exchange even if
            # STATION doesn't auto-reply with their own SNR report.
            m = _TX_SNR_RE.search(tx_text)
            if m and self._my_callsign:
                to_cs  = norm_call(m.group(1))
                snr_v  = int(m.group(2))
                # Store as from=my_callsign, to=remote so the outgoing SNR
                # proof query (WHERE from_call=my_call) finds this record
                # and adds to_cs to hears_me → shows orange mutual dot.
                self.db.add_directed(self._my_callsign, to_cs,
                                     f'{tx_text} [TX]', snr_v, 0, ts)
                if self.on_spot:
                    self.on_spot()
            # Auto-detect outgoing @GROUP SNR? — arm listener without manual button.
            # Fires on TX.TEXT only (human-readable payload), never TX.FRAME.
            mg = _TX_GRP_SNR_RE.search(tx_text)
            if mg and msg_type == 'TX.TEXT':
                grp = mg.group(1).upper()
                if not grp.startswith('@'):
                    grp = '@' + grp
                arm_ts = time.time()
                self._pending_group_queries.clear()   # one group at a time
                self._pending_group_queries[grp] = arm_ts
                self.db.add_log_entry('⊕', 'GRP-AUTO',
                    f'Auto-armed {grp} from TX.TEXT')
            # Phase 2: TX.FRAME fires each frame during our transmission.
            # Require 2+ consecutive frames (within 30s of each other) before
            # starting the leg2_fwd timer. A single isolated TX.FRAME — which
            # can appear during relay query or between transmissions — must NOT
            # trigger leg2_fwd prematurely. After the last qualifying frame,
            # 20s timer fires the speculative advance.
            if msg_type == 'TX.FRAME':
                _committed = _relay_committed_ref[0]
                if _committed:
                    _set_relay_anim('leg1_tx', _committed['via'], _committed['target'])
                    _now = time.time()
                    if _now - _relay_tx_last_frame_ts[0] <= 30.0:
                        _relay_tx_frame_count[0] += 1   # consecutive frame
                    else:
                        _relay_tx_frame_count[0] = 1    # isolated frame — reset count
                    _relay_tx_last_frame_ts[0] = _now
                    if _relay_tx_timer[0]:
                        _relay_tx_timer[0].cancel()
                    if _relay_tx_frame_count[0] >= 2:   # only arm after 2+ consecutive
                        def _advance_leg2(via=_committed['via'], tgt=_committed['target']):
                            cur = _relay_anim_ref[0]
                            if cur and cur.get('state') == 'leg1_tx':
                                _set_relay_anim('leg2_fwd', via, tgt)
                        t = threading.Timer(20.0, _advance_leg2)
                        t.daemon = True
                        t.start()
                        _relay_tx_timer[0] = t

        # ── RX.ACTIVITY ──────────────────────────────────────
        # Fires for every decoded frame fragment in real-time.
        # Use it to trigger a map debounce — someone is actively decoding.
        # Patch 12: log fragments to DB only while a relay is committed so we
        # can compare RX.ACTIVITY timing vs RX.DIRECTED without DB spam.
        # Logged as type 'RX.FRAG' with symbol 📡 for easy identification.
        # C.18: also cancel destination timeout if fragment offset matches target.
        elif msg_type == 'RX.ACTIVITY':
            if _relay_committed_ref[0]:
                _frag_from = str(params.get('FROM', '?'))
                _frag_text = str(params.get('TEXT', '') or '').strip()[:80]
                _frag_snr  = params.get('SNR',    '?')
                _frag_off  = int(params.get('OFFSET', 0) or 0)
                self.db.add_log_entry('\U0001f4e1', 'RX.FRAG',
                    f'FROM={_frag_from}  SNR={_frag_snr}  OFF={_frag_off}'
                    f'  TEXT={_frag_text}')
                # C.18 (updated): two-stage offset watch during leg2_fwd:
                # 1) Frag at VIA's offset → via still transmitting → reset 90s
                #    timer so it counts from via's LAST transmission, not first.
                # 2) Frag at TARGET's offset → target IS responding → cancel timer.
                # If neither offset is known, the 90s timer from leg2_fwd stands.
                try:
                    _committed = _relay_committed_ref[0]
                    _cur_anim  = _relay_anim_ref[0]
                    if (_committed and _cur_anim
                            and _cur_anim.get('state') in ('leg2_fwd', 'rtn_leg1')
                            and _frag_off > 0):
                        _via_call   = _committed.get('via', '')
                        _tgt_call   = _committed.get('target', '')
                        _via_off    = _last_seen_offset.get(_via_call, 0)
                        _tgt_off    = _last_seen_offset.get(_tgt_call, 0)
                        _cur_state  = _cur_anim.get('state')
                        if _cur_state == 'leg2_fwd':
                            if _via_off and abs(_frag_off - _via_off) <= 5:
                                # Via still forwarding — slide the 90s dest window
                                _restart_dest_timer(_via_call, _tgt_call)
                            elif _tgt_off and abs(_frag_off - _tgt_off) <= 5:
                                # Target responding — fire rtn_leg1 from first frag
                                if _relay_dest_timer[0]:
                                    _relay_dest_timer[0].cancel()
                                    _relay_dest_timer[0] = None
                                _set_relay_anim('rtn_leg1', _via_call, _tgt_call)
                                self.db.add_log_entry('\U0001f4e1', 'RX.FRAG',
                                    f'Target {_tgt_call} responding '
                                    f'(OFF={_frag_off}) — rtn_leg1 triggered early')
                        elif _cur_state == 'rtn_leg1':
                            if _via_off and abs(_frag_off - _via_off) <= 5:
                                # Via relaying ACK back — fire rtn_leg2 from first frag
                                _set_relay_anim('rtn_leg2', _via_call, _tgt_call)
                                self.db.add_log_entry('\U0001f4e1', 'RX.FRAG',
                                    f'Via {_via_call} relaying ACK '
                                    f'(OFF={_frag_off}) — rtn_leg2 triggered early')
                            elif _tgt_off and abs(_frag_off - _tgt_off) <= 5:
                                # Target still transmitting its reply TO the via —
                                # slide the 90s return window so it measures from
                                # when the target STOPS, not when rtn_leg1 first fired.
                                _restart_return_timer(_via_call, _tgt_call)
                except Exception:
                    pass
            if self.on_spot:
                self.on_spot()

        # ── INBOX.MESSAGES ────────────────────────────────────
        elif msg_type == 'INBOX.MESSAGES':
            messages = params.get('MESSAGES', params.get('messages', []))
            if isinstance(messages, list):
                # Clear all INBOX poll entries first — this is what makes read
                # messages disappear from the map.  We then re-add only the
                # UNREAD ones JS8Call is currently reporting.
                self.db.clear_inbox_poll_msgs()
                for msg in messages:
                    # FROM is nested inside msg['params'], not at msg top level
                    inner     = msg.get('params', msg)
                    from_c    = str(inner.get('FROM', inner.get('from', ''))).strip().upper()
                    utc       = inner.get('UTC', inner.get('utc', 0))
                    msg_state = str(msg.get('type', msg.get('TYPE', 'UNREAD'))).upper()
                    # Only flag UNREAD messages — skip anything already read
                    if msg_state not in ('UNREAD', ''):
                        continue
                    if from_c and from_c != self._my_callsign:
                        ts = _utc_from_epoch_ms(utc) \
                            if isinstance(utc, (int, float)) and utc else _utc_stamp()
                        msg_id = f"INBOX_{from_c}_{utc}"
                        self.db.add_inbox_msg(msg_id, from_c, ts)
                        self.db.add_log_entry('✉', 'INBOX',
                            f'Unread message from {from_c}')
            if self.on_spot:
                self.on_spot()

        # ── STATION.CALLSIGN ──────────────────────────────────
        # Explicit callsign-only response — the most reliable source.
        # value = "KW3KW" (just the callsign, nothing else)
        elif msg_type == 'STATION.CALLSIGN':
            raw_call = norm_call(params.get('CALLSIGN', params.get('CALL', '')) or value)
            if raw_call and not is_valid_grid(raw_call):
                existing = self.db.get_my_station()
                self.db.set_my_station(raw_call, existing.get('grid', ''))
                self.db.add_log_entry('←', msg_type, raw_call)
                self._my_callsign = raw_call   # used to detect SNR reports in BAND_ACTIVITY
                if self.on_my_station:
                    self.on_my_station(raw_call, existing.get('grid', ''))

        # -- RX.BAND_ACTIVITY --
        # CONFIRMED FORMAT (live API log):
        # params = {"1845":{DIAL,FREQ,OFFSET,SNR,TEXT,UTC}, "1897":{...}}
        # Offset Hz is the dict KEY. NO callsign field -- it is in TEXT:
        #   "N4VAD: KW3KW SNR +05 "  ->  split on ':'  ->  "N4VAD"
        elif msg_type == 'RX.BAND_ACTIVITY':
            raw_ba = params if params else {}
            items_ba = list(raw_ba.values()) if isinstance(raw_ba, dict) else list(raw_ba)

            # ── "Clear All Lists" detection ───────────────────────────────
            # JS8Call sends no API notification when the user clicks
            # "Clear All Lists" (or "Clear Entire List").  The next
            # RX.BAND_ACTIVITY poll simply comes back empty.  If we had
            # more than 3 stations tracked and now get zero, treat it as a
            # user-initiated clear and wipe our DB to match — otherwise
            # mutuals (orange dots) linger because their directed-table SNR
            # proofs are never cleared by the time filter.
            # Threshold of 3 avoids false positives on a genuinely quiet band.
            if not items_ba and len(self._last_ba_text) > 3:
                self._last_ba_text.clear()
                self.db.clear_spots(clear_directed=True)   # full wipe — matches manual QSY intent
                self.db.add_log_entry('←', msg_type,
                    'EMPTY — auto-cleared DB to match JS8Call list clear')
                if self.on_status:
                    self.on_status(
                        '⚠  JS8Call list cleared — map cleared to match.  '
                        'Waiting for new decodes…', True)
                if self.on_spot:
                    self.on_spot()
                return   # nothing else to process this poll
            # ─────────────────────────────────────────────────────────────
            added = 0
            for item in items_ba:
                if not isinstance(item, dict):
                    continue
                # Try explicit callsign keys, then parse from TEXT
                call = norm_call(item.get('CALLSIGN', item.get('CALL', item.get('FROM', ''))))
                if not call:
                    txt_raw = str(item.get('TEXT', '') or '')
                    if ':' in txt_raw:
                        call = txt_raw.split(':', 1)[0].strip().upper()
                grid   = norm_grid(item.get('GRID', item.get('LOCATOR', '')))
                snr    = float(item.get('SNR',   0) or 0)
                freq   = float(item.get('DIAL',  item.get('FREQ', 0)) or 0)
                offset = float(item.get('OFFSET', 0) or 0)
                speed  = int  (item.get('SPEED', 0) or 0)
                # Use JS8Call's own UTC timestamp (ms epoch) so spot age
                # reflects when the station was ACTUALLY decoded, not when we polled.
                utc_ms = item.get('UTC', 0) or 0
                spot_ts = (_utc_from_epoch_ms(utc_ms)
                           if utc_ms else ts)
                txt_raw = str(item.get('TEXT', '') or '')
                # Track current dial frequency from BAND_ACTIVITY DIAL field.
                # This is the most reliable QSY detection — BAND_ACTIVITY
                # always contains the actual current DIAL frequency.
                if freq and freq != self._my_freq:
                    old_ba_freq = self._my_freq
                    self._my_freq = freq
                    # QSY detected if frequency changed by more than 1kHz
                    if old_ba_freq and abs(old_ba_freq - freq) > 1000:
                        self._last_ba_text.clear()
                        self.db.clear_spots()
                        if hasattr(self, '_qsy_callback') and self._qsy_callback:
                            self._qsy_callback(freq)
                        if self.on_status:
                            self.on_status(
                                f'\u2713  QSY \u2192 {freq/1e6:.3f} MHz  \u00b7  '
                                f'Map cleared \u2014 waiting for new decodes', True)
                        if self.on_spot:
                            self.on_spot()
                elif freq:
                    self._my_freq = freq
                if call and len(call) <= 12 and not self._is_prewatermark(utc_ms):
                    # sanity: callsigns are short; and skip stations JS8Call still
                    # retains from before a Clear/QSY (freq detection above still
                    # ran, so QSY tracking is unaffected).
                    # Only write a new spot/band_activity entry when the TEXT has
                    # CHANGED — that indicates a new autonomous transmission.
                    # If TEXT is the same as last time, JS8Call is just re-reporting
                    # the same old frame (possibly with a directed-response UTC),
                    # so we keep the old timestamp to match JS8Call's Age column.
                    prev_text      = self._last_ba_text.get(call)
                    is_first_sight = (prev_text is None)  # never seen this station before
                    txt_changed    = (txt_raw.strip() != (prev_text or '').strip())
                    # Detect directed frame: 'SENDER: CALLSIGN CMD' (not autonomous).
                    # Only suppress timestamp update for UPDATES (not first sight).
                    # On first sight we ALWAYS write — we need the station in our DB
                    # even if their last text happened to be a directed SNR response.
                    after_colon = txt_raw.split(':', 1)[1].strip() if ':' in txt_raw else ''
                    first_token = after_colon.split()[0] if after_colon.split() else ''
                    # Directed frame: token after colon matches callsign pattern
                    is_directed_frame = (
                        bool(_DIRECTED_CALL_RE.match(first_token)) and
                        not first_token.startswith('@')
                    )
                    # Write to band_activity only when text changes (avoids redundant text rows).
                    # Write to spots ALWAYS — even repeated identical heartbeats must refresh
                    # the timestamp so the station stays visible under any active time filter.
                    # Without this decoupling, a station sending the same heartbeat for 3h
                    # gets a 3-hour-old spot timestamp and disappears from "Last 2 hours" view.
                    if txt_changed and (is_first_sight or not is_directed_frame):
                        self._last_ba_text[call] = txt_raw
                        self.db.add_band_activity(call, snr, freq, offset, speed, spot_ts, txt_raw)
                    self.db.add_spot(call, grid if is_valid_grid(grid) else '',
                                     snr, freq, offset, speed, spot_ts, 'RX.BAND_ACTIVITY')
                    added += 1
                    # Detect SNR reports directed at us: 'CALL: MY_CALL SNR +xx'
                    if (self._my_callsign and
                            self._my_callsign in txt_raw.upper() and
                            'SNR' in txt_raw.upper() and
                            'SNR?' not in txt_raw.upper()):
                        extra = str(item.get('EXTRA', '') or '').strip()
                        if extra and _INT_RE.match(extra):
                            txt_stored = txt_raw + f' [RSNR:{extra}]'
                        else:
                            txt_stored = txt_raw
                        self.db.add_directed(
                            call, self._my_callsign, txt_stored, snr, freq, spot_ts)
            if added and self.on_spot:
                self.on_spot()

        # -- RX.CALL_ACTIVITY --
        # CONFIRMED FORMAT (live API log):
        # params = {"AA0DY":{"GRID":"","SNR":-7,"UTC":...}, "K8DDD":{...}}
        # Callsign IS the dict KEY -- must use .items(), not .values().
        elif msg_type == 'RX.CALL_ACTIVITY':
            raw_ca = params if params else {}
            added = 0
            if isinstance(raw_ca, dict):
                for cs_key, meta in raw_ca.items():
                    if cs_key.startswith('_'):  # skip _ID and internal keys
                        continue
                    call = norm_call(cs_key)
                    if not call or not isinstance(meta, dict):
                        continue
                    grid   = norm_grid(meta.get('GRID', meta.get('LOCATOR', '')))
                    snr    = float(meta.get('SNR',   0) or 0)
                    freq   = float(meta.get('DIAL',  meta.get('FREQ', 0)) or 0)
                    offset = float(meta.get('OFFSET', 0) or 0)
                    speed  = int  (meta.get('SPEED', 0) or 0)
                    # Use JS8Call's UTC timestamp so age reflects actual decode time
                    utc_ms = meta.get('UTC', 0) or 0
                    # Drop stations JS8Call still retains from before a Clear/QSY.
                    if self._is_prewatermark(utc_ms):
                        continue
                    spot_ts = (_utc_from_epoch_ms(utc_ms)
                               if utc_ms else ts)
                    self.db.add_spot(call, grid if is_valid_grid(grid) else '',
                                     snr, freq, offset, speed, spot_ts, 'RX.CALL_ACTIVITY')
                    added += 1
            if added and self.on_spot:
                self.on_spot()
