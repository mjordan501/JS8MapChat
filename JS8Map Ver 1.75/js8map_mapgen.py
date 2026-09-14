"""js8map_mapgen.py — HamMapApp map-generation mixin for JS8MapChat (Phase 6 T2b).

The map build/refresh pipeline lifted out of the HamMapApp god-class:
  • debounce + scheduled/auto refresh, time-filter + cutoff helpers
  • _generate (thread launcher) and _generate_thread (the render orchestration)

Stays methods on _MapGenMixin; HamMapApp inherits it alongside _AppUIMixin, so every
call site (_build_ui, _clear_map, _on_new_spot, _manual_refresh, _refresh_from_js8call)
and the two base calls it makes (_save_config, _set_status) resolve through the MRO.
Imports mirror the monolith exactly (all plain, no aliases). No import back (no cycle).
"""

import threading
from datetime import datetime, timedelta

from js8map_util import _base, _utc_now, _utc_cutoff, is_valid_grid, grid_to_latlon
from js8map_theme import GREEN, MUTED, RED
from js8map_dialogs import themed_confirm, themed_message
from js8map_activity_store import build_stations_from_db, build_directed_maps_from_db
from js8map_fcc import _find_fcc_db, fcc_fallback_grid, load_fcc_info
from js8map_relay import _relay_active_ref, _relay_committed_ref, _relay_age_min, _relay_confidence
from js8map_tx import _TX_HALT_ENABLED
from js8map_web import render_html, open_in_browser


class _MapGenMixin:
    def _debounce_regen(self):
        """Schedule a silent map regen 5s after the LAST spot in a burst.
        Uses last-fires debounce: each new spot cancels and restarts the
        timer, so the map generates once the burst has fully settled."""
        if not self._map_opened:
            return
        # Cancel any pending timer and reschedule from now (last-fires).
        if self._regen_job is not None:
            try:
                self.root.after_cancel(self._regen_job)
            except Exception:
                pass
        self._regen_pending = True
        self._regen_job = self.root.after(5000, self._do_debounced_regen)

    def _do_debounced_regen(self):
        self._regen_pending = False
        self._regen_job = None
        # Only suppress rebuilds while a relay path is COMMITTED (TX/animation
        # running). During discovery (armed, not committed) we want rebuilds so
        # newly-discovered YES vias get plotted with map lines. The browser
        # consumes data.json incrementally and re-applies the relay filter, so
        # a discovery-phase rebuild does not tear down the relay overlay.
        if self._relay_blocks_rebuild():
            return
        if self._map_opened:
            self._generate(silent=True)

    # ── Time / Refresh helpers ────────────────────────────────

    def _relay_blocks_rebuild(self) -> bool:
        """True only when a map rebuild must be suppressed for relay safety.

        Relay has two phases:
          • DISCOVERY (armed, not committed) — YES responses are still arriving.
            We WANT rebuilds here so newly-discovered vias get plotted and gain
            map lines that _colorRelayLines can highlight. Allow rebuild.
          • COMMITTED (GO pressed) — a path is locked and the TX/animation
            sequence is running. A full regen here can disturb the leg overlay.
            Suppress rebuild until Done, exactly as before.

        The browser consumes /data.json incrementally (_applyAdditions) and
        re-runs applyRelayFilter on each poll, so a discovery-phase rebuild
        adds the new via markers/lines without tearing down the relay view.
        """
        if not _relay_active_ref[0]:
            return False
        # Committed → protect the overlay. Armed-only (discovery) → allow rebuild.
        return _relay_committed_ref[0] is not None

    def _get_cutoff(self) -> datetime:
        val = self.time_filter.get()
        mapping = {
            "Last 15 minutes": timedelta(minutes=15),
            "Last 30 minutes": timedelta(minutes=30),
            "Last 1 hour":     timedelta(hours=1),
            "Last 2 hours":    timedelta(hours=2),
            "Last 4 hours":    timedelta(hours=4),
            "Last 12 hours":   timedelta(hours=12),
            # "Last 24 hours" was called "Today" until it was renamed for
            # clarity; "Today" normally means since-midnight. Kept in step
            # with FastChat's TIME_FILTERS (constants.py
            # TIME_FILTERS = 24.0), NOT midnight-to-now.
            "Last 24 hours":   timedelta(hours=24),
            # Retired label, still accepted so saved configs keep working.
            "Today":           timedelta(hours=24),
        }
        delta = mapping.get(val)
        return _utc_now() - delta if delta else _utc_now() - timedelta(minutes=30)

    def _get_refresh_secs(self) -> int:
        return {'Every 30 sec': 30, 'Every 1 min': 60, 'Every 2 min': 120,
                'Every 5 min': 300, 'Every 10 min': 600}.get(self.refresh_var.get(), 0)

    def _schedule_refresh(self):
        if self._refresh_job:
            self.root.after_cancel(self._refresh_job)
            self._refresh_job = None
        secs = self._get_refresh_secs()
        if secs > 0:
            self._refresh_job = self.root.after(secs * 1000, self._auto_refresh)

    def _auto_refresh(self):
        self._refresh_job = None
        if self._relay_blocks_rebuild():
            # Relay path committed — skip rebuild to preserve the TX/leg overlay.
            # Will retry after one more interval; Done clears the flag.
            self._schedule_refresh()
            return
        # Poll JS8Call for fresh activity data before rebuilding the map.
        # Without this, auto-refresh queries stale DB data when stations are
        # still responding (e.g. 11 SNR replies arriving across multiple frames).
        try:
            self._client.send('RX.GET_BAND_ACTIVITY')
            self._client.send('RX.GET_CALL_ACTIVITY')
        except Exception:
            pass
        # Wait 3s for the poll responses to arrive, then generate.
        self.root.after(3000, lambda: self._generate(silent=True))

    # ── Event handlers ────────────────────────────────────────

    def _on_time_filter_changed(self, event=None):
        # PERSIST FIRST, and do it whether or not the map window is open.
        # This handler used to ONLY regenerate. Nothing wrote the chosen
        # window to disk, so _save_config() only ever captured it by
        # accident -- when some UNRELATED action (e.g. the JS8Call group
        # sync) happened to save the whole config while this dropdown sat
        # on a particular value. Symptom: pick a window, close, reopen,
        # and the map comes back on whatever was current at the last
        # accidental save. It also made JS8Map LOOK like it defaulted to
        # 30 minutes when in fact that is just _load_config()'s fallback
        # for a missing/invalid value. FastChat has always persisted its
        # own DB Time; this brings the map into line.
        try:
            self._save_config()
        except Exception:
            pass   # a failed save must never block the regen below
        if self._map_opened:
            self._set_status("⏳  Applying new time filter…", MUTED)
            self.root.update()
            self._generate(silent=True)

    def _on_refresh_changed(self, event=None):
        self._schedule_refresh()
        secs = self._get_refresh_secs()
        if secs:
            self._set_status(
                f"✓  Quiet-band rebuild every {self.refresh_var.get().lower()}  "
                f"·  Live spot updates also trigger regen", GREEN)
        else:
            self._set_status("Quiet-band rebuild off  ·  Live spot updates still trigger regen.", MUTED)

    def _generate(self, silent: bool = False):
        # On first-ever Generate Map (map not yet opened, not a silent regen),
        # send a quick JS8Call snapshot poll so data is already in the DB.
        if not silent and not self._map_opened:
            self.root.after(0,    lambda: self._client.send('RIG.GET_FREQ'))
            self.root.after(600,  lambda: self._client.send('RX.GET_BAND_ACTIVITY'))
            self.root.after(1400, lambda: self._client.send('RX.GET_CALL_ACTIVITY'))
        call = self.callsign.get().replace(' ', '').upper()
        grid = self.my_grid.get().replace(' ', '').upper()[:6] if self.my_grid.get() else ''
        out  = self.out_path.get().strip()

        if not call and not silent:
            if not themed_confirm(
                self.root,
                "No Callsign",
                "No callsign entered. Stations 'hearing you' won't be\n"
                "identified. Continue anyway?",
                kind="warning", yes="Continue", no="Cancel", safe_no=True
            ):
                return

        if not out:
            themed_message(self.root, "No Output Path", "Please set a save-to path for the HTML file.", kind="error")
            return

        if not silent:
            self._set_status("⏳  Querying spot database…", MUTED)
            self.root.update()

        self.gen_btn.config(state='disabled')
        threading.Thread(
            target=self._generate_thread,
            args=(call, grid, out, silent),
            daemon=True
        ).start()

    def _generate_thread(self, call: str, grid: str, out: str, silent: bool):
        try:
            cutoff = self._get_cutoff()

            _my_freq = getattr(self._client, '_my_freq', 0)
            (stations_heard, stations_hearing_me, via_hb,
             no_grid, snr_of_me, inbox, pending_msg, debug,
             first_seen_map) = build_stations_from_db(
                self._spot_db, call, cutoff, self.time_filter.get(),
                _my_freq, self._session_start
            )

            hby, shrs = build_directed_maps_from_db(self._spot_db, cutoff,
                                                     my_callsign=call)

            # Surface build diagnostics to 📡 API Log for troubleshooting
            for _dbg in debug:
                self._spot_db.add_log_entry('ℹ', 'BUILD', _dbg)

            watched    = self._spot_db.load_watched_calls()
            _grp_cfg   = self._group_config.get_groups()
            _grp_cutoff = _utc_cutoff(timedelta(hours=2))
            grp_act    = self._spot_db.load_group_active_calls(_grp_cfg, _grp_cutoff)

            # FCC lookup for all active callsigns
            all_calls = set(stations_heard) | set(stations_hearing_me) | no_grid
            fcc_info  = load_fcc_info(all_calls)

            # FCC address fallback for stations with no grid
            # Pre-fetch freq/offset for ALL no_grid stations in one batch query
            _no_grid_calls = set()
            for mc in no_grid:
                _no_grid_calls.add(mc); _no_grid_calls.add(_base(mc))
            _ng_freq: dict = {}
            if _no_grid_calls:
                phs = ','.join('?' * len(_no_grid_calls))
                args = list(_no_grid_calls)
                with self._spot_db._connect() as _dbc:
                    for tbl, col in [('spots','callsign'),('band_activity','callsign')]:
                        try:
                            for r in _dbc.execute(
                                f"SELECT {col},freq,offset FROM {tbl}"
                                f" WHERE {col} IN ({phs}) AND freq>0 AND offset>0"
                                f" ORDER BY timestamp DESC", args
                            ).fetchall():
                                if r[0] not in _ng_freq:
                                    _ng_freq[r[0]] = (float(r[1] or 0), float(r[2] or 0))
                        except Exception:
                            pass
                    try:
                        for r in _dbc.execute(
                            f"SELECT from_call,freq,offset FROM directed"
                            f" WHERE from_call IN ({phs}) AND freq>0"
                            f" ORDER BY timestamp DESC", args
                        ).fetchall():
                            if r[0] not in _ng_freq:
                                _ng_freq[r[0]] = (float(r[1] or 0), float(r[2] or 0))
                    except Exception:
                        pass

            for missing_call in no_grid:
                # Skip if station already has a real grid in stations_heard
                existing = stations_heard.get(missing_call, {})
                if existing.get('grid') and is_valid_grid(existing['grid']):
                    continue
                base_call = _base(missing_call)
                fcc_rec   = fcc_info.get(missing_call) or fcc_info.get(base_call, {})
                fallback  = fcc_fallback_grid(fcc_rec)
                if not fallback or not is_valid_grid(fallback):
                    fallback = existing.get('raw_grid', '')
                if fallback and is_valid_grid(fallback):
                    existing = stations_heard.get(missing_call, {})
                    freq   = existing.get('freq', 0) or 0
                    offset = existing.get('offset', 0) or 0
                    if not freq or not offset:
                        fq = _ng_freq.get(missing_call) or _ng_freq.get(_base(missing_call))
                        if fq:
                            if not freq:   freq   = fq[0]
                            if not offset: offset = fq[1]
                    stations_heard[missing_call] = {
                        'grid':          fallback,
                        'snr':           existing.get('snr', 0),
                        'last_seen':     existing.get('last_seen', ''),
                        'first_seen_ts': existing.get('first_seen_ts', ''),
                        'freq':          freq,
                        'offset':        offset,
                        'speed':         existing.get('speed', 0),
                        'count':         existing.get('count', 0),
                        'approx':        True,
                    }
                    fcc_info.setdefault(missing_call, {})['approx_grid'] = True

            # ── Bulletproof mutual detection ─────────────────────────────
            # Rule: any station that reported our SNR (snr_of_me) is
            # definitively hearing us. If they are already plotted in
            # stations_heard (with any grid incl. FCC fallback), force them
            # into stations_hearing_me so the map marks them MUTUAL/ORANGE.
            # This handles the case where hears_me detection missed them
            # due to timing, table gaps, or grid/sync issues.
            for snr_call in list(snr_of_me.keys()):
                if snr_call in stations_heard and snr_call not in stations_hearing_me:
                    stations_hearing_me[snr_call] = stations_heard[snr_call]

            # Also sync FCC grids back for any hearing_me station still missing a grid
            for hm_call, hm_info in list(stations_hearing_me.items()):
                if not hm_info.get('grid') and hm_call in stations_heard:
                    stations_hearing_me[hm_call] = stations_heard[hm_call]

            # Annotate first_seen_ts after ALL station dict creation/replacement
            for _call in list(set(stations_heard) | set(stations_hearing_me)):
                ts = first_seen_map.get(_call, '')
                if _call in stations_heard:
                    stations_heard[_call].setdefault('first_seen_ts', ts)
                if _call in stations_hearing_me:
                    stations_hearing_me[_call].setdefault('first_seen_ts', ts)

            unplotted_mutuals = []

            if not stations_heard and not stations_hearing_me:
                tf  = self.time_filter.get()
                cnt = self._spot_db.get_spot_count()
                no_grid_cnt = len(no_grid)
                if cnt == 0:
                    msg = ('⚠  No data yet — '
                           'Waiting for JS8Call to decode stations.  '
                           'Check 📡 API Log to confirm data is flowing.')
                elif no_grid_cnt > 0:
                    fcc_note = ('FCC DB active — approximate positions available.'
                                if _find_fcc_db() else
                                'No FCC DB — load AM.dat/EN.dat for approximate positions.')
                    msg = (f'⚠  {cnt} stations heard but no grid data yet.  '
                           f'Waiting for heartbeat beacons (5–15 min).  {fcc_note}')
                else:
                    period = tf.lower().replace('last ', '')
                    msg = (f'⚠  No stations in the last {period}.  '
                           f'Map is blank — select a wider time range.')
                # Render an empty map so browser shows the blank state with overlay
                _host = self.js8_host.get().strip() or '127.0.0.1'
                try:
                    _port = int(self.js8_port.get().strip())
                except ValueError:
                    _port = 2442
                render_html({}, {}, call, grid, out,
                    refresh_secs=self._get_refresh_secs(),
                    js8call_host=_host, js8call_port=_port,
                    tx_halt_enabled=_TX_HALT_ENABLED[0])
                if not self._map_opened:
                    self._map_opened = True
                    self.root.after(0, lambda: open_in_browser(out))
                self.root.after(0, lambda m=msg: self._set_status(m, '#e65100'))
                self.root.after(0, lambda: self.gen_btn.config(state='normal'))
                self._schedule_refresh()
                return

            host = self.js8_host.get().strip() or '127.0.0.1'
            port = 2442
            try:
                port = int(self.js8_port.get().strip())
            except ValueError:
                pass

            _qsy = self._pending_qsy
            self._pending_qsy = 0.0

            # ── Collect unmapped callsigns ────────────────────────────────
            # Any station still missing a placeable grid after all FCC
            # fallback attempts is added to _unmapped_calls.  The set grows
            # across the session and is shown live in 📍 Callsign Unknown.
            # Clears on 🗑 Clear (QSY).  /P portables resolved via base
            # callsign; only appear here if base call also fails.
            _new_unmapped = set()
            for _uc, _ui in {**stations_heard, **stations_hearing_me}.items():
                if not _ui.get('grid') or not grid_to_latlon(_ui.get('grid', '')):
                    _new_unmapped.add(_uc)
            self._unmapped_calls.update(_new_unmapped)

            # ── Build relay_paths ──────────────────────────────────
            # Exclude my own callsign as a via — if I can hear the target
            # directly it's not a relay, it's a direct path.
            relay_paths = {}
            try:
                _my_snr = {vc.upper(): vi['snr']
                           for vc, vi in stations_heard.items()
                           if vi.get('snr') is not None}
                for via, target, snr_r, logged_ts in self._relay_db.get_all():
                    if via.upper() == call.upper():
                        continue   # skip my own callsign
                    snr_to_me = _my_snr.get(via.upper())
                    composite = (min(snr_r, snr_to_me)
                                 if snr_r is not None and snr_to_me is not None
                                 else snr_r)
                    relay_paths.setdefault(target, []).append({
                        'via':        via,
                        'snr':        snr_r,
                        'snr_to_me':  snr_to_me,
                        'composite':  composite,
                        'age_min':    _relay_age_min(logged_ts),
                        'confidence': _relay_confidence(logged_ts),
                    })
                for tgt in relay_paths:
                    relay_paths[tgt].sort(key=lambda r:
                        -(r['composite'] if r['composite'] is not None
                          else r['snr'] if r['snr'] is not None else -99))
            except Exception:
                relay_paths = {}

            count = render_html(
                stations_heard, stations_hearing_me, call, grid, out,
                refresh_secs       = self._get_refresh_secs(),
                fcc_info           = fcc_info,
                via_heartbeat      = via_hb,
                heard_by           = hby,
                station_hears      = shrs,
                snr_of_me          = snr_of_me,
                inbox_senders      = inbox,
                pending_msg_senders= pending_msg,
                watched_calls      = watched,
                group_colors       = grp_act,
                group_filter_names = sorted(self._group_config.get_groups().keys()),
                js8call_host       = host,
                js8call_port       = port,
                qsy_freq           = _qsy,
                time_filter_label  = self.time_filter.get(),
                relay_paths        = relay_paths,
                unplotted_mutuals  = unplotted_mutuals,
                tx_halt_enabled    = _TX_HALT_ENABLED[0],
                fit_exclusions     = self._fit_exclusions,
            )

            hm_list = sorted(stations_hearing_me.keys())[:6]
            hm_str  = ', '.join(hm_list) + ('…' if len(stations_hearing_me) > 6 else '')
            hm_note = f'  ·  Hear you: {hm_str}' if stations_hearing_me else '  ·  None hear you yet'
            hb_note = f'  ·  HB: {len(via_hb)}' if via_hb else ''

            # Unplotted mutuals note
    
            # Breakdown: heard-only (blue) / mutual (orange) / hear-me only (green)
            mutual_set      = set(stations_heard.keys()) & set(stations_hearing_me.keys())
            heard_only_cnt  = len(stations_heard) - len(mutual_set)
            mutual_cnt      = len(mutual_set)
            hear_me_cnt     = len(stations_hearing_me) - len(mutual_set)
            refresh_note = (f'  ·  Rebuild: {self.refresh_var.get().lower()} + live'
                            if self._get_refresh_secs() else '  ·  Live updates active')
            type_note = (f'  ·  👂{heard_only_cnt} 🔄{mutual_cnt}'
                         + (f' 📡{hear_me_cnt}' if hear_me_cnt else ''))

            ts    = datetime.now().strftime('%H:%M')
            msg   = (f"✓  {ts}  —  {count} on map  "
                     f"({len(stations_heard)} in DB, {len(no_grid)} no-grid)"
                     f"{type_note}{hm_note}{hb_note}{refresh_note}")
            color = GREEN if not no_grid else '#e59900'

            self.root.after(0, lambda m=msg, c=color: self._set_status(m, c))
            self.root.after(0, lambda: self.gen_btn.config(state='normal'))

            if not silent:
                self._map_opened = True
                _url = open_in_browser(out)
                self.root.after(0, lambda u=_url: self._set_status(
                    f'🌐  Map ready — if browser did not open, paste into Brave: {u}', GREEN))

            self._save_config()
            self._schedule_refresh()

        except Exception as ex:
            import traceback as _tb
            err = str(ex)
            try:
                self._spot_db.add_log_entry('✗', 'ERROR', _tb.format_exc()[:1900])
            except Exception:
                pass
            self.root.after(0, lambda e=err: self._set_status(f"✗  Error: {e}", RED))
            self.root.after(0, lambda: self.gen_btn.config(state='normal'))
            self._schedule_refresh()
