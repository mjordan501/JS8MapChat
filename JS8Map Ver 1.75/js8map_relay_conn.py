"""js8map_relay_conn.py — HamMapApp relay + JS8Call-connection mixin (Phase 6 T2c).

The last cohesive group lifted out of the HamMapApp god-class:
  • JS8Call connection lifecycle (_start_client/_reconnect/_on_conn_status/
    _on_my_station/_on_new_spot/_auto_sync_js8_highlights)
  • relay arm/clear flow (_confirm_clear/_clear_map/_on_relay_hint/_arm_relay_inline)
    and the JS8Call-poll refresh triggers (_refresh_from_js8call/_manual_refresh)

Stays methods on _RelayConnMixin; HamMapApp inherits it alongside _AppUIMixin and
_MapGenMixin, so all call sites (__init__, _build_ui) and cross-mixin self-calls
(_generate, _relay_blocks_rebuild in _MapGenMixin; _set_status on base) resolve via
the MRO. The 17 js8map_relay holders/timers are by-ref (in-place [0]= only, proven
never rebound) — imported by-ref, shared with the rest of the app. Plain imports,
no aliases. No import back into the monolith (no cycle).
"""

import json
import sqlite3
import time
from datetime import datetime

from js8map_util import is_valid_grid, grid_to_latlon
from js8map_theme import BLUE, GREEN, MUTED, RED
from js8map_dialogs import themed_confirm
from js8map_js8call_ini import read_js8call_highlights
from js8map_fcc import fcc_fallback_grid, load_fcc_info
from js8map_client import JS8CallClient
from js8map_tx import tx_query
from js8map_web import _data_json_bytes
from js8map_relay import (
    _armed_relay_ref, _armed_relay_pos_ref, _relay_active_ref, _relay_committed_ref,
    _relay_anim_ref, _relay_msg_ref, _relay_outcome_ref, _relay_yes_list_ref,
    _relay_yes_ts_ref, _relay_failed_vias_ref, _relay_dest_timer, _relay_return_timer,
    _relay_tx_timer, _relay_watchdog_timer, _relay_tx_frame_count, _relay_tx_last_frame_ts,
    _relay_via_last_frag_ts,
)


class _RelayConnMixin:
    # ── JS8Call connection ────────────────────────────────────

    def _start_client(self):
        host = self.js8_host.get().strip() or '127.0.0.1'
        port = 2442
        try:
            port = int(self.js8_port.get().strip())
        except ValueError:
            pass
        cfg_call = self.callsign.get().replace(' ', '').upper()
        if cfg_call and not self._client._my_callsign:
            self._client._my_callsign = cfg_call
        self._client._relay_db      = self._relay_db
        self._client._on_relay_hint = self._on_relay_hint
        cfg_call = self.callsign.get().replace(' ', '').upper()
        if cfg_call:
            self._relay_db._my_call = cfg_call
        self._client.configure(host, port)
        self._client.start()

    def _reconnect(self):
        """Stop and restart the JS8Call client with current settings."""
        self._client.stop()
        time.sleep(0.3)
        # Rebuild client and re-wire ALL required state — group detection
        # goes dark if any of these are missing after a reconnect.
        self._client = JS8CallClient(
            db            = self._spot_db,
            on_spot       = self._on_new_spot,
            on_status     = self._on_conn_status,
            on_my_station = self._on_my_station,
        )
        self._client._group_config          = self._group_config
        self._client._qsy_callback          = lambda f: setattr(self, '_pending_qsy', f)
        self._client._my_callsign           = self.callsign.get().replace(' ', '').upper()
        self._client._relay_db              = self._relay_db
        self._client._on_relay_hint         = self._on_relay_hint
        self._set_status("⏳  Reconnecting…", MUTED)
        self.root.after(400, self._start_client)

    # ── Callbacks from background thread ─────────────────────

    def _on_conn_status(self, msg: str, is_connected: bool):
        """Called from JS8CallClient thread — must use root.after()."""
        def _update():
            color = GREEN if is_connected else '#e84060'
            self.conn_dot.config(fg=color)
            short = "Connected" if is_connected else "Not connected"
            self.conn_lbl.config(text=short, fg=color)
            self._set_status(msg, color)
            if is_connected:
                # Auto-sync JS8Call highlight list into watched calls
                self.root.after(1000, self._auto_sync_js8_highlights)
        self.root.after(0, _update)

    def _on_my_station(self, callsign: str, grid: str):
        """Called when JS8Call reports our callsign / grid."""
        def _update():
            if callsign and not is_valid_grid(callsign):
                if not self.callsign.get():
                    self.callsign.set(callsign)
            if grid and is_valid_grid(grid):
                self.my_grid.set(grid)
            cur_call = self.callsign.get()
            display_call = (cur_call if cur_call and not is_valid_grid(cur_call) else '') or callsign or '?'
            display_grid = self.my_grid.get() or grid or '?'
            # Keep relay DB in sync so the live endpoint always excludes the operator
            if display_call and display_call != '?':
                self._relay_db._my_call = display_call.upper()
            if display_grid != '?':
                self._set_status(
                    f"✓  JS8Call: {display_call}  Grid: {display_grid}", GREEN)
        self.root.after(0, _update)

    def _on_new_spot(self):
        """Called from JS8CallClient thread every time a new spot arrives."""
        self.root.after(0, self._debounce_regen)

    def _auto_sync_js8_highlights(self):
        """Read JS8Call.ini and merge secondary highlight callsigns into watched_calls."""
        try:
            highlights = read_js8call_highlights()
            if not highlights:
                return
            existing = self._spot_db.load_watched_calls()
            new_ones = highlights - existing
            if new_ones:
                merged = existing | highlights
                self._spot_db.set_watched_calls(merged)
                self._set_status(
                    f"✓  Synced {len(new_ones)} callsign(s) from JS8Call highlights  "
                    f"({len(merged)} total watched)", GREEN)
        except Exception:
            pass

    def _confirm_clear(self):
        """Ask confirmation then clear — used by button and keyboard shortcut."""
        if themed_confirm(
            self.root,
            'Clear Map — QSY',
            'Clear all spots and reset the map?\n\n'
            'Use this after changing frequency.\n\n'
            'Tip: JS8Call\'s own "Clear All" only clears JS8Call\'s\n'
            'display — it does NOT clear this app\'s data.',
            kind='warning', yes='Yes', no='No', safe_no=True
        ):
            self._clear_map()

    def _clear_map(self):
        """Wipe all spot data including directed — full clean slate for QSY.
        Also performs a full relay teardown: clears all in-memory relay refs
        and cancels all relay timers so a stale armed target cannot snap back
        after a frequency change. Mirrors the /relay_arm_clear HTTP endpoint."""
        self._spot_db.clear_spots(clear_directed=True)
        # Watermark 'now' so the snapshot polls below (and every later refresh)
        # do NOT re-import the old-frequency stations JS8Call still holds in its
        # CALL_ACTIVITY / BAND_ACTIVITY buffers -- those carry their original
        # decode UTCs, which still fall inside the time filter and would re-plot
        # the very stations we just cleared. Only decodes AFTER this moment pass.
        self._client.set_clear_watermark()
        self._relay_db.clear_all()          # also wipe relay paths — stale on QSY
        self._relay_db._my_call = self.callsign.get().replace(' ', '').upper()
        self._client._last_ba_text.clear()
        self._unmapped_calls.clear()
        self._regen_pending = False
        # ── Relay teardown (v1.82_A fix) ──────────────────────────────────
        # Clear all module-level relay refs so a stale armed target cannot
        # reassert after QSY (the "KC1PHY snap-back" symptom).
        _armed_relay_ref[0]     = None
        _armed_relay_pos_ref[0] = None
        _relay_committed_ref[0] = None
        _relay_anim_ref[0]      = None
        for _timer_ref in (_relay_tx_timer, _relay_watchdog_timer,
                           _relay_dest_timer, _relay_return_timer):
            if _timer_ref[0]:
                try:
                    _timer_ref[0].cancel()
                except Exception:
                    pass
                _timer_ref[0] = None
        _relay_tx_frame_count[0]   = 0
        _relay_tx_last_frame_ts[0] = 0.0
        _relay_yes_list_ref[0]     = []
        _relay_yes_ts_ref[0]       = 0.0
        _relay_msg_ref[0]          = ''
        _relay_failed_vias_ref[0]  = []
        _relay_via_last_frag_ts[0] = 0.0
        # Strip armed_target from the in-memory data.json so the browser
        # drops the relay overlay on the very next poll — no stale map lines.
        try:
            d = json.loads(_data_json_bytes[0])
            d['armed_target']     = None
            d['armed_target_pos'] = None
            _data_json_bytes[0] = json.dumps(d, separators=(',', ':')).encode()
        except Exception:
            pass
        # ──────────────────────────────────────────────────────────────────
        self._set_status('🗑  Cleared — waiting for new decodes…', MUTED)
        # Stagger API polls with delays so JS8Call doesn't freeze.
        # Never send multiple commands at once to JS8Call's TCP queue.
        self.root.after(500,  lambda: self._client.send('RIG.GET_FREQ'))
        self.root.after(1500, lambda: self._client.send('RX.GET_BAND_ACTIVITY'))
        self.root.after(2500, lambda: self._client.send('RX.GET_CALL_ACTIVITY'))
        self.root.after(4000, lambda: self._generate(silent=False))

    # ── Map generation ────────────────────────────────────────

    def _on_relay_hint(self, from_call: str):
        """Fires when a YES response arrives but no relay target is armed."""
        self._relay_hint_count += 1
        def _flash():
            self._set_status(
                f'🔁  {from_call} said YES — enter target in 🔁 field and press Search'
                f'  ({self._relay_hint_count} YES response(s) received)', '#29b6f6')
        self.root.after(0, _flash)

    def _arm_relay_inline(self):
        """Arm relay detection for the callsign in the inline 🔁 entry field.
        Sets a 5-minute YES detection window AND retroactively scans the
        directed table for YES responses already in the last 10 minutes.
        Copies QUERY CALL TARGET? to clipboard ready to paste into JS8Call."""
        # If the placeholder is showing, the field is effectively empty.
        if getattr(self, '_relay_ph_active', [False])[0]:
            self._set_status('🔁  Enter a callsign in the relay field first', MUTED)
            return
        raw = self._relay_entry_var.get().strip()
        # Defense in depth: strip any placeholder remnant, then keep only
        # callsign-legal chars. Prevents a mangled 'KC1E.G. KD4E' from ever
        # reaching tx_query / JS8Call (root cause of relay-not-firing).
        for variant in ('e.g. KD4E', 'E.G. KD4E', 'E.G. KI'):
            raw = raw.replace(variant, '')
        tgt = ''.join(ch for ch in raw if ch.isalnum() or ch in '/').upper().split('/')[0]
        # A real callsign is 3–10 chars and contains at least one digit.
        if (not tgt or tgt in ('EGKD4E', 'EGKI')
                or len(tgt) < 3 or len(tgt) > 10
                or not any(c.isdigit() for c in tgt)):
            self._set_status('🔁  Enter a valid callsign in the relay field first', MUTED)
            return
        # Full clean slate — clear relay DB, all state refs, and any
        # lingering timers so re-arming never inherits a previous relay.
        self._relay_db.clear_for_target(tgt)
        _relay_committed_ref[0]   = None
        _relay_anim_ref[0]        = None
        _relay_outcome_ref[0]     = None
        _relay_yes_list_ref[0]    = []
        _relay_yes_ts_ref[0]      = 0.0
        _relay_failed_vias_ref[0] = []
        for _tmr in (_relay_dest_timer, _relay_watchdog_timer, _relay_tx_timer):
            if _tmr[0]:
                try: _tmr[0].cancel()
                except Exception: pass
                _tmr[0] = None
        # Arm 5-minute window
        self._client._pending_relay_target = (tgt, datetime.now())
        # Retroactively scan directed table
        logged = 0
        try:
            my_call = self._client._my_callsign or self.callsign.get().upper()
            with sqlite3.connect(self._spot_db.path, timeout=10) as conn:
                rows = conn.execute(
                    "SELECT from_call, snr, timestamp FROM directed"
                    " WHERE UPPER(text) LIKE '% YES%'"
                    "   AND UPPER(to_call) = UPPER(?)"
                    "   AND timestamp >= datetime('now', '-10 minutes')"
                    " ORDER BY timestamp DESC",
                    (my_call,)
                ).fetchall()
            for from_c, snr_val, ts in rows:
                self._relay_db.log_path(from_c, tgt, snr_val, ts)
                logged += 1
            self._spot_db.add_log_entry('⊕', 'RELAY',
                f'Armed for {tgt} — {logged} existing YES path(s) logged')
            # Auto-copy QUERY CALL to clipboard, addressed to the chosen audience
            aud = (self._relay_aud_var.get().strip().upper()
                   if hasattr(self, '_relay_aud_var') else '@ALLCALL') or '@ALLCALL'
            if not aud.startswith('@'):
                aud = '@' + aud
            # v1.82_C: remember the audience so YES responders to a group-directed
            # QUERY CALL get tagged to that group (their box turns the group color).
            self._client._pending_relay_audience = aud
            query_cmd = f'{aud} QUERY CALL {tgt}?'
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(query_cmd)
                clip_note = f'  ·  "{query_cmd}" copied to clipboard'
            except Exception:
                clip_note = ''
            # Resolve target FCC position for chain leg-2 drawing in browser
            t_pos = None
            try:
                fcc = load_fcc_info({tgt})
                if tgt in fcc:
                    grid = fcc_fallback_grid(fcc[tgt])
                    if grid:
                        pos = grid_to_latlon(grid)
                        if pos:
                            t_pos = {'lat': pos[0], 'lon': pos[1], 'grid': grid}
            except Exception:
                pass
            _armed_relay_ref[0]     = tgt
            _armed_relay_pos_ref[0] = t_pos
            # Mode-aware TX. Relay path-discovery query (analog of group Go).
            #   live   → transmits QUERY CALL now
            #   manual → fills JS8Call's box (operator sends)
            #   shadow → logs only
            _tx_result = tx_query(self._client, self._spot_db, query_cmd, trigger='relay-arm')
            # Patch data.json immediately — browser auto-populates relay filter within 5s
            try:
                d = json.loads(_data_json_bytes[0])
                d['armed_target']     = tgt
                d['armed_target_pos'] = t_pos
                _data_json_bytes[0] = json.dumps(d, separators=(',', ':')).encode()
            except Exception:
                pass
            # Status reflects what actually happened with the TX, not just clipboard.
            _tx_note = {
                'sent':   '  ·  📡 transmitted to JS8Call (LIVE)',
                'staged': '  ·  📝 filled JS8Call box — press send there (MANUAL)',
                'shadow': f'{clip_note}  ·  SHADOW: logged only, no TX',
                'skip':   '  ·  ⚠ not sent — JS8Call not connected',
                'empty':  '',
            }.get(_tx_result, clip_note)
            msg = f'🔁  {tgt} armed{_tx_note}'
            if logged:
                msg += f'  ·  {logged} path(s) found. Map updates in ~10s.'
            self._set_status(msg, '#29b6f6')
            self._relay_hint_count = 0
        except Exception as ex:
            self._set_status(f'✗  Relay arm error: {ex}', RED)

    def _refresh_from_js8call(self):
        """R — Ask JS8Call for its current snapshot then regenerate the map.
        Useful when the app was started after JS8Call was already running,
        or when the map looks stale.  Staggered so the TCP queue never
        floods — data arrives ~3s later, map generates at +4.5s."""
        _relay_active_ref[0] = False   # resume auto-refresh if suspended
        self._set_status('⏳  Refreshing from JS8Call…', BLUE)
        self.root.after(0,    lambda: self._client.send('RIG.GET_FREQ'))
        self.root.after(500,  lambda: self._client.send('STATION.GET_CALLSIGN'))
        self.root.after(1000, lambda: self._client.send('STATION.GET_GRID'))
        self.root.after(1500, lambda: self._client.send('RX.GET_BAND_ACTIVITY'))
        self.root.after(2500, lambda: self._client.send('RX.GET_CALL_ACTIVITY'))
        self.root.after(3000, lambda: self._client.send('INBOX.GET_MESSAGES'))
        self.root.after(4500, lambda: self._generate(silent=False))

    def _manual_refresh(self):
        """R-hotkey / manual pull. Skips the forced regen only while a relay
        path is COMMITTED (TX/leg overlay must not be disturbed). During relay
        discovery the rebuild is allowed so newly-found vias plot. Mirrors the
        committed-relay guard in _auto_refresh; resumes fully on Done."""
        if self._relay_blocks_rebuild():
            return
        self._generate(silent=True)
