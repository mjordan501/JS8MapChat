#!/usr/bin/env python3
"""Relay engine shared state + timing helpers.

Extracted from JS8Map.py. The [None]/[0]/[False] holders are single-element
mutable containers deliberately shared BY REFERENCE across JS8Map, the web
server, and (later) JS8CallClient: they are only ever mutated in place
(ref[0] = ...), never rebound, so every importer sees the same live object.
Leaf module — imports only stdlib + js8map_util."""
from __future__ import annotations

import threading
import time

from js8map_util import _age_seconds


def _relay_confidence(logged_ts: str) -> str:
    """HIGH / MED / LOW based on age of relay path."""
    try:
        age = _age_seconds(logged_ts)
    except Exception:
        return 'LOW'
    if age < 300:   return 'HIGH'
    if age < 1800:  return 'MED'
    return 'LOW'

def _relay_age_min(logged_ts: str) -> int:
    """Age of a relay path in whole minutes."""
    try:
        return int(_age_seconds(logged_ts) / 60)
    except Exception:
        return 999

# ── shared relay state (mutated in place; imported by reference) ──
_relay_active_ref       = [False]   # [0]=True while browser relay filter is active → suspend auto-refresh
_armed_relay_ref        = [None]    # [0]=target callsign after Arm pressed — auto-populates map relay filter
_armed_relay_pos_ref    = [None]    # [0]={'lat','lon','grid'} FCC position of armed target, or None
_relay_committed_ref  = [None]   # [0]={'via','target'} set when GO pressed
_relay_anim_ref       = [None]   # [0]={'state','via','target','ts'} or None
_relay_tx_timer          = [None]   # [0]=threading.Timer — fires leg2_fwd after last TX.FRAME
_relay_tx_frame_count    = [0]      # consecutive TX.FRAME count for current relay sequence
_relay_tx_last_frame_ts  = [0.0]    # timestamp of most recent TX.FRAME in relay
_relay_watchdog_timer = [None]   # [0]=threading.Timer — fires timeout if no return after leg2_fwd
_relay_yes_list_ref      = [[]]  # [0] = [{via, snr, offset, ts}, ...] saved from YES phase
_relay_yes_ts_ref        = [0.0] # [0] = epoch when YES list was last populated
_relay_msg_ref           = ['']  # [0] = relay message text stored at /relay_commit
_relay_dest_timer        = [None]# [0] = threading.Timer watching for target response after via goes silent
_relay_return_timer      = [None]# [0] = threading.Timer watching for via→me return after rtn_leg1 fires
_relay_via_last_frag_ts  = [0.0] # [0] = epoch of last frag seen at via's offset — timer resets on each one
_relay_failed_vias_ref   = [[]]  # [0] = [callsign, ...] vias that failed this session
_relay_outcome_ref       = [None]# [0] = {status,via,target,ts} — persists for sticky bar
_RELAY_ANIM_ORDER = {'leg1_tx': 1, 'leg2_fwd': 2, 'rtn_leg1': 3, 'rtn_leg2': 4}

def _set_relay_anim(state: str, via: str, target: str) -> None:
    """Update relay animation state. Thread-safe single-element list assignment.
    Does NOT touch _data_json_bytes — browser reads /relay_anim.json directly."""
    current = _relay_anim_ref[0]
    if current:
        if _RELAY_ANIM_ORDER.get(state, 0) <= _RELAY_ANIM_ORDER.get(current.get('state', ''), 0):
            return  # never revert to an earlier hop
    _relay_anim_ref[0] = {'state': state, 'via': via, 'target': target, 'ts': time.time()}

    # Watchdog: start 4-minute timer after leg2_fwd — if rtn_leg1 never arrives
    # the relay chain broke down; set 'timeout' state so browser shows warning.
    if state == 'leg2_fwd':
        if _relay_watchdog_timer[0]:
            _relay_watchdog_timer[0].cancel()
        def _watchdog_fire(v=via, t=target):
            cur = _relay_anim_ref[0]
            if cur and cur.get('state') == 'leg2_fwd':
                _relay_anim_ref[0] = {'state': 'timeout', 'via': v,
                                      'target': t, 'ts': time.time()}
                _relay_outcome_ref[0] = {'status': 'failed', 'via': v,
                                         'target': t, 'ts': time.time()}
        wd = threading.Timer(240.0, _watchdog_fire)
        wd.daemon = True
        wd.start()
        _relay_watchdog_timer[0] = wd

        # C.18 (updated): destination-offset timeout.
        # Timer is 90s and is RESET each time a fragment arrives at the via's
        # offset — so it measures 90s from when the via STOPS transmitting,
        # not from when leg2_fwd fires.  _relay_via_last_frag_ts tracks the
        # last via-fragment timestamp; the RX.FRAG handler calls
        # _restart_dest_timer() on each new via frag during leg2_fwd.
        if _relay_dest_timer[0]:
            _relay_dest_timer[0].cancel()
        _relay_via_last_frag_ts[0] = time.time()
        def _dest_timeout_fire(v=via, t=target):
            cur = _relay_anim_ref[0]
            if cur and cur.get('state') in ('leg2_fwd',):
                _relay_anim_ref[0] = {
                    'state': 'timeout_dest', 'via': v,
                    'target': t, 'ts': time.time()
                }
                _relay_outcome_ref[0] = {'status': 'failed_dest', 'via': v,
                                         'target': t, 'ts': time.time()}
        dt = threading.Timer(45.0, _dest_timeout_fire)
        dt.daemon = True
        dt.start()
        _relay_dest_timer[0] = dt

    elif state == 'rtn_leg2':
        # Success — relay leg started. Outcome pill is set only when
        # RX.DIRECTED confirms the full decode (not from early frag trigger).
        if _relay_watchdog_timer[0]:
            _relay_watchdog_timer[0].cancel()
            _relay_watchdog_timer[0] = None
        if _relay_dest_timer[0]:
            _relay_dest_timer[0].cancel()
            _relay_dest_timer[0] = None
        if _relay_return_timer[0]:
            _relay_return_timer[0].cancel()
            _relay_return_timer[0] = None

    elif state == 'rtn_leg1':
        # Target responded to the via — the return journey has started, but the
        # via has NOT yet relayed it back to us. Cancel the outbound timers
        # (their job is done) and start a RETURN-leg watchdog: if rtn_leg2 never
        # fires, the via received the target's reply but failed to relay it home.
        # Without this timer the relay would hang silently in rtn_leg1 forever
        # with no failure popup — the asymmetry this fixes.
        if _relay_watchdog_timer[0]:
            _relay_watchdog_timer[0].cancel()
            _relay_watchdog_timer[0] = None
        if _relay_dest_timer[0]:
            _relay_dest_timer[0].cancel()
            _relay_dest_timer[0] = None
        if _relay_return_timer[0]:
            _relay_return_timer[0].cancel()
        def _return_timeout_fire(v=via, t=target):
            cur = _relay_anim_ref[0]
            # Only fire if we're still stuck waiting for the via to relay home.
            if cur and cur.get('state') == 'rtn_leg1':
                _relay_anim_ref[0] = {'state': 'timeout_return', 'via': v,
                                      'target': t, 'ts': time.time()}
                _relay_outcome_ref[0] = {'status': 'failed_return', 'via': v,
                                         'target': t, 'ts': time.time()}
        rt = threading.Timer(90.0, _return_timeout_fire)
        rt.daemon = True
        rt.start()
        _relay_return_timer[0] = rt

    elif state in ('timeout_dest', 'timeout_return'):
        if _relay_watchdog_timer[0]:
            _relay_watchdog_timer[0].cancel()
            _relay_watchdog_timer[0] = None
        if _relay_dest_timer[0]:
            _relay_dest_timer[0].cancel()
            _relay_dest_timer[0] = None
        if _relay_return_timer[0]:
            _relay_return_timer[0].cancel()
            _relay_return_timer[0] = None

def _restart_dest_timer(via: str, target: str) -> None:
    """Reset the 90s dest timeout from now — called each time a frag arrives
    at the via's offset during leg2_fwd so the clock measures from when
    the via STOPS transmitting, not from when leg2_fwd first fired."""
    if _relay_dest_timer[0]:
        _relay_dest_timer[0].cancel()
    _relay_via_last_frag_ts[0] = time.time()
    def _dest_timeout_fire(v=via, t=target):
        cur = _relay_anim_ref[0]
        if cur and cur.get('state') == 'leg2_fwd':
            _relay_anim_ref[0] = {
                'state': 'timeout_dest', 'via': v,
                'target': t, 'ts': time.time()
            }
            _relay_outcome_ref[0] = {'status': 'failed_dest', 'via': v,
                                     'target': t, 'ts': time.time()}
    dt = threading.Timer(45.0, _dest_timeout_fire)
    dt.daemon = True
    dt.start()
    _relay_dest_timer[0] = dt

def _restart_return_timer(via: str, target: str) -> None:
    """Reset the 90s return-leg timeout from now — called each time a frag
    arrives at the TARGET's offset during rtn_leg1, so the clock measures from
    when the target STOPS replying to the via, not from when rtn_leg1 fired.
    If the via then fails to relay the reply home, timeout_return fires."""
    if _relay_return_timer[0]:
        _relay_return_timer[0].cancel()
    def _return_timeout_fire(v=via, t=target):
        cur = _relay_anim_ref[0]
        if cur and cur.get('state') == 'rtn_leg1':
            _relay_anim_ref[0] = {'state': 'timeout_return', 'via': v,
                                  'target': t, 'ts': time.time()}
            _relay_outcome_ref[0] = {'status': 'failed_return', 'via': v,
                                     'target': t, 'ts': time.time()}
    rt = threading.Timer(90.0, _return_timeout_fire)
    rt.daemon = True
    rt.start()
    _relay_return_timer[0] = rt

