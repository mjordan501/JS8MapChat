"""js8map_tx.py — Transmit gate for JS8MapChat.

The single place in the app permitted to transmit to JS8Call. Owns the TX-mode
and TX-halt state holders (_TX_MODE / _TX_HALT_ENABLED, mutated in place, never
rebound) and the four gate functions: _coerce_tx_mode, tx_halt, tx_query,
tx_stage. Extracted from JS8Map.py (Phase 6 T1).

Depends only on siblings below it — js8map_runtime (DATA_DIR) and js8map_fastchat
(outgoing-TX publisher + cross-app TX lease). Never imports back into the monolith.
"""

import os
from datetime import datetime

from js8map_runtime import DATA_DIR as _DATA_DIR
from js8map_fastchat import (
    record_js8map_outgoing,
    write_js8map_tx_lease,
    clear_js8map_tx_lease,
)


# ── Transmit gate ──────────────────────────────────────────────────────────
# The SINGLE place in this app permitted to transmit to JS8Call.
# Mode is held in _TX_MODE[0] so the GUI thread and the HTTP/relay threads all
# read one shared value. Valid modes:
#   'shadow'  → log what we WOULD send to tx_shadow.log; touch JS8Call NOT at all.
#   'manual'  → fill JS8Call's outgoing box via TX.SET_TEXT (NO RF); operator sends.
#   'live'    → actually transmit via TX.SEND_MESSAGE.
# Startup mode comes from config ('tx_mode'); coerced to 'manual' if unrecognized
# so a missing/corrupt config can NEVER silently start live. Set by HamMapApp at
# boot and whenever the user changes mode. Relay Phase-2 GO is exempt from this
# gate entirely — it ALWAYS stages via tx_stage regardless of _TX_MODE.
_TX_MODE = ['manual']
_TX_HALT_ENABLED = [False]   # set from config; True only on JS8Call Improved API 3.0+

def _coerce_tx_mode(val) -> str:
    v = (val or '').strip().lower()
    return v if v in ('shadow', 'manual', 'live') else 'manual'

def tx_halt(client, spot_db):
    """Send RIG.TX_HALT to stop transmission immediately (JS8Call Improved API
    3.0+ only). Goes out over the EXISTING JS8Map→JS8Call connection — same
    JSON-over-TCP API as every other command. On older JS8Call 2.x there is no
    halt command and this is silently ignored by JS8Call; the operator's
    guaranteed stop is JS8Call's own Halt Tx button (JS8Call is always open).
    Gated by _TX_HALT_ENABLED so a 2.x user isn't given a dead button.
    Returns True if the command was sent (not a guarantee the radio obeyed)."""
    if not _TX_HALT_ENABLED[0]:
        return False
    if not (client and getattr(client, '_connected', False)):
        try: spot_db.add_log_entry('\u26a0', 'HALT-SKIP', 'not connected')
        except Exception: pass
        return False
    ok = client.send('RIG.TX_HALT')   # {"type":"RIG.TX_HALT","value":"","params":{}}
    try:
        spot_db.add_log_entry('\U0001f6d1' if ok else '\u26a0',
                              'TX-HALT' if ok else 'HALT-FAIL', 'RIG.TX_HALT sent')
    except Exception:
        pass
    return ok

def tx_query(client, spot_db, text, trigger='user'):
    """Mode-aware transmit chokepoint.
       shadow → log only (no RF, no JS8Call touch)
       manual → TX.SET_TEXT (fills box, no RF)  ← operator presses send
       live   → TX.SEND_MESSAGE (real RF)
       Returns a status string: 'sent' | 'staged' | 'skip' | 'shadow' | 'empty'."""
    text = (text or '').strip()
    if not text:
        return 'empty'
    mode  = _TX_MODE[0]
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if mode == 'shadow':
        # SHADOW: record intent + trigger, transmit nothing, touch JS8Call not at all.
        try:
            _p = os.path.join(_DATA_DIR, 'tx_shadow.log')
            with open(_p, 'a', encoding='utf-8') as fh:
                fh.write(stamp + '  WOULD TX: ' + text + '   (trigger=' + trigger + ')\n')
        except Exception:
            pass
        try:
            spot_db.add_log_entry('SHADOW', 'TX-WOULD', text + ' (trigger=' + trigger + ')')
        except Exception:
            pass
        return 'shadow'

    if not (client and getattr(client, '_connected', False)):
        try: spot_db.add_log_entry('\u26a0', 'TX-SKIP', 'not connected: ' + text)
        except Exception: pass
        return 'skip'

    def _fc_publish(_txt):
        # Publish this outgoing frame so FastChat shows it on the gold (sent)
        # side. Frames look like "TARGET COMMAND ..." (e.g. "KO4BIA HEARING?");
        # the first token is who we addressed, a keyword gives the context.
        try:
            _parts = _txt.strip().split()
            _to = _parts[0] if _parts else ''
            _ctx = 'TX'
            for _kw in ('HEARING?', 'SNR?', 'STATUS?', 'INFO?', 'GRID?', 'QUERY',
                        'HEARTBEAT', 'MSG', 'ACK'):
                if _kw in _txt.upper():
                    _ctx = _kw
                    break
            record_js8map_outgoing(getattr(client, '_my_callsign', '') or '',
                                   _to, _txt, context=_ctx)
        except Exception:
            pass

    # Phase 2 lease target: same first-token parse _fc_publish already does,
    # reused rather than re-derived twice (manual + live branches).
    _lease_target = (text.strip().split() or [''])[0]

    if mode == 'manual':
        # MANUAL: stage into JS8Call's outgoing box (TX.SET_TEXT). NO RF — the
        # operator reviews and presses send in JS8Call. Logged for the audit trail.
        try:
            _p = os.path.join(_DATA_DIR, 'tx_shadow.log')
            with open(_p, 'a', encoding='utf-8') as fh:
                fh.write(stamp + '  MANUAL STAGE (TX.SET_TEXT): ' + text +
                         '   (trigger=' + trigger + ')\n')
        except Exception:
            pass
        write_js8map_tx_lease(getattr(client, '_my_callsign', '') or '',
                              _lease_target, mode, trigger=trigger)
        ok = client.send('TX.SET_TEXT', text)
        # Do NOT clear on success. TX.SET_TEXT fills JS8Call's outgoing box, and
        # that box stays occupied until the operator sends or clears it in
        # JS8Call. That staged-box window is exactly when FastChat must NOT fire:
        # TX.SEND_MESSAGE silently no-ops into a non-empty box, so FastChat's send
        # would vanish with no error -- the silent broken state this lease exists
        # to prevent. JS8Map can't observe when the operator empties the box, so
        # leave the lease 'active' and let FastChat's own TTL (est_duration_sec
        # since acquired_utc) bound it -- a forgotten stage can't lock FastChat
        # forever. Only clear on a failed SET_TEXT (box was never filled -> nothing
        # to guard), mirroring the live branch.
        if not ok:
            clear_js8map_tx_lease()
        try:
            spot_db.add_log_entry('\U0001f4dd' if ok else '\u26a0',
                                  'TX-STAGED' if ok else 'STAGE-FAIL',
                                  text + ' (trigger=' + trigger + ')')
        except Exception:
            pass
        if ok:
            _fc_publish(text)   # staged from a JS8Map button -> show it in FastChat
        return 'staged' if ok else 'skip'   # never RF in manual mode

    # mode == 'live' — confirm command name 'TX.SEND_MESSAGE' against your JS8Call.
    write_js8map_tx_lease(getattr(client, '_my_callsign', '') or '',
                          _lease_target, mode, trigger=trigger)
    ok = client.send('TX.SEND_MESSAGE', text)
    # Do NOT clear here on success: client.send() returning just means JS8Call's
    # API accepted the command, not that the real over-the-air transmission has
    # finished. Clearing immediately would drop FastChat's lock right as RF is
    # starting -- defeating the cross-app lockout. Leave the lease 'active' and
    # let FastChat's own TTL (est_duration_sec since acquired_utc) expire it.
    # If the send was never actually accepted, there's no TX to guard against,
    # so clear right away rather than leaving FastChat locked for nothing.
    if not ok:
        clear_js8map_tx_lease()
    try:
        spot_db.add_log_entry('\U0001f4e1' if ok else '\u26a0',
                              'TX-SENT' if ok else 'TX-FAIL',
                              text + ' (trigger=' + trigger + ')')
    except Exception:
        pass
    if ok:
        _fc_publish(text)
    return 'sent' if ok else 'skip'


# ── Staging gate (RF-SAFE) ──────────────────────────────────────────────────
# Phase-2 relay staging. Uses TX.SET_TEXT, which fills JS8Call's outgoing
# message box but DOES NOT transmit — the operator types their message and
# presses send manually in JS8Call. Because TX.SET_TEXT physically cannot put
# RF on the air, this path is EXEMPT from the TX_MODE shadow gate: it always
# performs the box-fill when connected, regardless of shadow/live. It still
# writes a 'WOULD STAGE' / 'STAGED' line to tx_shadow.log + api_log so every
# fire is auditable, exactly like tx_query. The real transmit (tx_query →
# TX.SEND_MESSAGE) remains shadow-gated and is the ONLY path that can emit RF.
def tx_stage(client, spot_db, text, trigger='relay-stage'):
    """RF-safe staging chokepoint. Fills JS8Call's TX box via TX.SET_TEXT and
    never transmits. Returns True only if the box was actually set."""
    text = (text or '')
    # Note: do NOT .strip() — a trailing space after 'VIA>TARGET ' is wanted so
    # the operator's cursor lands ready to type the message.
    if not text.strip():
        return False
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    # Always log intent (audit trail), mirroring tx_query's shadow log.
    try:
        _p = os.path.join(_DATA_DIR, 'tx_shadow.log')
        with open(_p, 'a', encoding='utf-8') as fh:
            _label = 'STAGED (TX.SET_TEXT)' if (client and getattr(client, '_connected', False)) \
                     else 'WOULD STAGE (not connected)'
            fh.write(stamp + '  ' + _label + ': ' + text + '   (trigger=' + trigger + ')\n')
    except Exception:
        pass
    if not (client and getattr(client, '_connected', False)):
        try: spot_db.add_log_entry('\u26a0', 'STAGE-SKIP', 'not connected: ' + text)
        except Exception: pass
        return False
    # TX.SET_TEXT — sets the outgoing message box, sends NO RF.
    ok = client.send('TX.SET_TEXT', text)
    try:
        spot_db.add_log_entry('\U0001f4dd' if ok else '\u26a0',
                              'TX-STAGED' if ok else 'STAGE-FAIL',
                              text + ' (trigger=' + trigger + ')')
    except Exception:
        pass
    return ok
