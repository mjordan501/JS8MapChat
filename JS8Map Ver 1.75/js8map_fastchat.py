#!/usr/bin/env python3
"""
js8map_fastchat.py  —  JS8MapChat 1.75 ↔ FastChat coordination substrate.

Phase 6 / T0: the shared-mailbox resolver, the outgoing-TX publisher, and the
cross-app TX-pending lease — the files FastChat reads. Extracted verbatim from
JS8Map.py so both the monolith and the coming js8map_tx module import one copy.
Leaf deps only (stdlib + shared_resolver + js8map_runtime); no back-import.
"""

import os
import json
import sys as _sys_early
from datetime import datetime, timezone as _dt_timezone
from js8map_runtime import (APP_DIR as _APP_DIR, DATA_DIR as _DATA_DIR,
                            EXE_DIR as _EXE_DIR, APP_VERSION as _APP_VERSION_STR)


# ── FastChat outgoing-TX publish ────────────────────────────────────────────
# Sends fired from JS8Map go straight to JS8Call and never pass through
# FastChat's own TX path, so FastChat can't show them -- you'd see the other
# station's reply but not the query you sent. We fix that by publishing each
# directed frame WE transmit to a small JSON file that FastChat reads (same
# locked handshake pattern as the intent/raise/server files: JS8Map writes,
# FastChat reads only). Logic is inlined here (not imported) so it works the
# same whether JS8Map runs as a script or as a frozen PyInstaller .exe.
#
# v1.5 LAYOUT (replaces the old pointer-file-as-primary-mechanism design):
#   JS8MapChat\
#     JS8FastChat Ver 1.5\
#     JS8Map Ver 1.5\                  <- script mode: _APP_DIR lands here
#       dist\JS8Map.exe                <- frozen mode: _APP_DIR lands here
#                                          (dirname(sys.executable) is dist\,
#                                          since that's where the exe runs)
#     JS8MapChat Shared Ver 1.5\       <- the shared mailbox (_fc_shared_dir())
#
# Because script mode and frozen mode are NOT the same number of folder
# levels deep (frozen adds the extra dist\ level), we WALK UP from _APP_DIR
# checking each ancestor for the named shared folder, instead of assuming a
# fixed number of ".." hops. This is what makes the resolution work
# correctly from both modes without caring which one is running.
import tempfile as _fc_tempfile

_FC_OBS_FILENAME = 'js8map_outgoing_tx.json'
_FC_PATHFILE     = 'js8map_writer_path.txt'   # legacy escape-hatch only (v1.5)
_FC_MAX_ROWS     = 1500
# v1.6: THE VERSION IS OUT OF THE FOLDER NAME.
# In v1.5 both apps agreed on the shared mailbox by string-matching a folder
# name that had the version baked into it ('JS8MapChat Shared Ver 1.5'). That
# made a version bump a breaking change to the contract between two separately
# edited programs: rename the folder, update one app's constant but not the
# other's, and the handoff dies -- silently, in a --noconsole build.
# The canonical name is now version-free. The old name is still ACCEPTED so an
# existing install keeps working without the operator renaming anything.
# MUST stay identical to constants.py::_SHARED_DIR_NAMES (verify_shared_dir.py
# asserts this and the test suite fails if it ever drifts).
_FC_SHARED_DIR_NAME  = 'JS8MapChat Shared'            # DEAD (#38): nothing reads
                                                      # this, and NOTHING CREATES
                                                      # the folder. Kept only so no
                                                      # import can break. When the
                                                      # sibling folder is absent we
                                                      # use the per-user mailbox --
                                                      # we never create a sibling.
_FC_SHARED_DIR_NAMES = ('JS8MapChat Shared',
                        'JS8MapChat Shared Ver 1.5')  # accepted, in priority order
_FC_WALK_UP_MAX_LEVELS = 5   # safety bound on how far up we'll search
# Phase 10: FastChat is considered dead if its alive-sentinel "ts" is older
# than this many seconds. Must match fastchat_heartbeat.ALIVE_STALE_SECONDS
# (FastChat refreshes every 5 s; 15 s = 3 missed beats before we call it dead).
_FC_ALIVE_STALE_SECONDS = 15


def _fc_base_call(c: str) -> str:
    c = str(c or '').strip().upper()
    return c.split('/')[0] if '/' in c else c

# ── #35: shared-folder resolution now lives in ONE module ───────────────────
# shared_resolver.py (a byte-identical twin of the copy in FastChat's tree; the
# build hash-gate refuses to build if they differ) owns the search ALGORITHM.
# JS8Map keeps only this THIN ADAPTER, which hands the resolver JS8Map's own
# anchors and preserves JS8Map's contract EXACTLY: _fc_shared_dir_impl() still
# returns (path_str, rule_str) with path_str == '' on total failure, so the
# public _fc_shared_dir() wrapper, its #42 logging, and all 7 call sites (which
# do `... or _DATA_DIR` / `if not shared`) are UNCHANGED.
#
# The per-user log line JS8Map used to emit from inside the impl (the "no
# sibling ... using per-user shared mailbox" note) is intentionally dropped
# here: _fc_log_resolution() below already records the final (path, rule) on
# every run (#42), which supersedes it. Net logging is the same or better.
import shared_resolver as _shared_resolver

# Kept as thin pass-throughs so any other reference in this file still resolves.
_fc_find_shared_by_walking_up = _shared_resolver.find_shared_by_walking_up


def _fc_per_user_shared_dir() -> str:
    """JS8Map's per-user mailbox. Delegates to the shared resolver (which CREATES
    the folder, as JS8Map always did here). Returns '' on failure, unchanged."""
    return _shared_resolver.per_user_shared_dir()


def _fc_shared_dir_impl():
    """Resolve the shared mailbox and report which rule decided it.

    #35: the algorithm is now shared_resolver.resolve_shared_dir(). JS8Map passes
    its own anchors (_EXE_DIR primary, then _APP_DIR, _DATA_DIR, cwd -- the same
    four it always used). Contract is unchanged: returns (path, rule), path '' on
    total failure so every caller's no-op-on-empty behaviour still applies.
    """
    return _shared_resolver.resolve_shared_dir(_EXE_DIR, _APP_DIR, _DATA_DIR)


# ── #42: ALWAYS record where the shared mailbox resolved ────────────────────
_FC_RESOLVE_LOGGED = None      # last (path, rule) written; suppresses repeats


def _fc_log_resolution(path, rule):
    """Write ONE line saying which shared folder we picked and WHICH RULE picked
    it -- on EVERY run, success or failure.

    WHY (#42). The old code logged ONLY when the walk-up failed and it fell
    through to the per-user mailbox. A wrong-but-SUCCESSFUL resolution was
    therefore completely silent -- and that is the failure that actually bites:

      The F: backup tree is a FULL COPY, so it has its own sibling
      'JS8MapChat Shared Ver 1.5'. A ghost JS8Map.exe launched from F: walks up,
      FINDS that folder, succeeds, and writes every handoff file into F:'s
      mailbox -- while FastChat reads C:'s. Every button dies, and this log
      stays EMPTY, because the resolver never failed. It just succeeded at the
      wrong answer.

    That invisibility cost a full session. Two programs whose entire contract is
    "we both independently compute the same folder" must each say out loud which
    folder they computed. Silence on success is not an option.

    Logged once per (path, rule) per process: _fc_shared_dir() has ~9 call sites
    and is not memoized, so unconditional logging would spam the file.
    """
    global _FC_RESOLVE_LOGGED
    key = (path, rule)
    if _FC_RESOLVE_LOGGED == key:
        return
    _FC_RESOLVE_LOGGED = key
    try:
        _stamp = datetime.now(_dt_timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        _frozen = bool(getattr(_sys_early, 'frozen', False))
        with open(os.path.join(_DATA_DIR, 'shared_resolve.log'), 'a',
                  encoding='utf-8') as _lh:
            _lh.write(
                f"{_stamp}  [JS8Map] SHARED DIR = {path or '(NONE)'}\n"
                f"{_stamp}  [JS8Map]   rule    : {rule}\n"
                f"{_stamp}  [JS8Map]   version : {_APP_VERSION_STR}  frozen={_frozen}\n"
                f"{_stamp}  [JS8Map]   _EXE_DIR: {_EXE_DIR}\n"
            )
    except Exception:
        pass


def _fc_shared_dir():
    """The shared mailbox, resolved AND recorded. See _fc_shared_dir_impl() for
    the resolution order and _fc_log_resolution() for why we always log (#42).

    Signature is unchanged -- returns a path string, '' on failure -- so every
    existing caller and its no-op-on-empty behaviour works exactly as before.
    """
    try:
        _path, _rule = _fc_shared_dir_impl()
    except Exception as _e:
        _fc_log_resolution('', f'EXCEPTION {type(_e).__name__}: {_e}')
        return ''
    _fc_log_resolution(_path, _rule)
    return _path


def record_js8map_outgoing(my_call: str, target_call: str, full_frame: str,
                           context: str = 'TX') -> None:
    """Append one outgoing directed frame to the file FastChat reads.

    No-ops quietly on any error -- logging a send must NEVER interfere with
    transmitting. Timestamp is UTC with a literal ' UTC' suffix to match
    FastChat's own TX log and keep its chat interleave correct.
    """
    try:
        frame  = str(full_frame or '').strip().upper()
        target = _fc_base_call(target_call)
        if not frame or not target:
            return
        shared = _fc_shared_dir()
        if not shared:
            return
        row = {
            'timestamp': datetime.now(_dt_timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
            'from': _fc_base_call(my_call) or 'KW3KW',
            'to': target,
            'context': str(context or 'TX'),
            'text': frame,
        }
        path = os.path.join(shared, _FC_OBS_FILENAME)
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            rows = data.get('rows', []) if isinstance(data, dict) else []
            if not isinstance(rows, list):
                rows = []
        except Exception:
            rows = []
        rows.append(row)
        rows = rows[-_FC_MAX_ROWS:]
        # atomic write so a concurrent FastChat read never sees a half file
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = _fc_tempfile.mkstemp(dir=os.path.dirname(path),
                                       prefix='.obs_tx_', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                json.dump({'rows': rows}, fh, ensure_ascii=False)
            os.replace(tmp, path)
        finally:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
    except Exception:
        pass  # never let history-logging break TX
# ────────────────────────────────────────────────────────────────────────────

# ── Phase 2: TX-pending lease (FastChat cross-app lockout) ──────────────────
# JS8Map writes this lease at the instant a send is committed in tx_query();
# FastChat's tx_lock.py reads it as an additional lock source on top of its
# own in-process lock, so FastChat won't fire TX.SEND_MESSAGE into a JS8Call
# outgoing box JS8Map already staged (TX.SEND_MESSAGE silently no-ops if the
# box isn't empty). Sole-writer direction preserved: JS8Map writes, FastChat
# only ever reads. Reuses _fc_shared_dir() / _fc_base_call() / _fc_tempfile
# already defined above -- no new imports, no sibling module.
#
# Gated the same way tx_query() itself is gated: shadow=no lease, manual=
# lease at TX.SET_TEXT, live=lease at TX.SEND_MESSAGE. Deliberately NOT
# called from tx_stage() (relay-hop staging) -- that's a human-paced window
# waiting on the operator to type, leasing against it would block FastChat
# for an unbounded time with no matching safety gain (decided this session).
#
# est_duration_sec is a FIXED CEILING, not the actual hold: it only bounds how
# long FastChat's cross-app lock can last if nothing else releases it. For
# FastChat's OWN sends, Phase 1's completion poll (RIG.GET_PTT +
# TX.GET_QUEUE_DEPTH) releases the instant JS8Call unkeys; this ceiling is just
# the backstop for the cross-app (JS8Map lease) case, which can't observe the
# real unkey.
#
# Set to 30s (operator decision): covers a normal-speed directed send with
# margin and stays out of the way for rapid querying. NOTE the tradeoff -- if a
# real over-the-air send ever runs LONGER than this (e.g. a long message in a
# slow JS8 speed), the lock can expire mid-transmission and FastChat could fire
# into a still-busy TX window (the silent-no-op failure this lease prevents). 30
# is comfortable for short directed frames (HEARING?/SNR?/INFO?); raise it if
# long/slow-mode sends start slipping through. JS8Map has no reliable read of
# its own outgoing TX speed (RX.SPOT SPEED is the decoded SENDER's speed, not
# ours), so this stays a fixed conservative number rather than per-send.
_FC_LEASE_FILENAME = 'js8map_tx_lease.json'
_FC_LEASE_DEFAULT_DURATION_SEC = 30

def write_js8map_tx_lease(my_call: str, target_call: str, tx_mode: str,
                          trigger: str = 'user',
                          est_duration_sec: int = _FC_LEASE_DEFAULT_DURATION_SEC) -> None:
    """Write the TX-pending lease. No-ops quietly on any error -- exactly
    like record_js8map_outgoing, a lease write must NEVER interfere with
    the actual transmit."""
    try:
        shared = _fc_shared_dir()
        if not shared:
            return
        row = {
            'active': True,
            'acquired_utc': datetime.now(_dt_timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
            'from': _fc_base_call(my_call) or 'KW3KW',
            'to': _fc_base_call(target_call),
            'tx_mode': str(tx_mode or 'manual'),
            'est_duration_sec': int(est_duration_sec or _FC_LEASE_DEFAULT_DURATION_SEC),
            'trigger': str(trigger or 'user'),
        }
        path = os.path.join(shared, _FC_LEASE_FILENAME)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = _fc_tempfile.mkstemp(dir=os.path.dirname(path),
                                       prefix='.tx_lease_', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                json.dump(row, fh, ensure_ascii=False)
            os.replace(tmp, path)
        finally:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
    except Exception:
        pass  # never let a lease write break TX


def clear_js8map_tx_lease() -> None:
    """Mark the lease inactive (active=false; file is overwritten, not
    deleted). Call right after the send call returns -- success or failure
    -- so a failed/skipped send doesn't leave FastChat locked out. FastChat's
    own TTL remains the backstop if this is ever skipped by an exception."""
    try:
        shared = _fc_shared_dir()
        if not shared:
            return
        path = os.path.join(shared, _FC_LEASE_FILENAME)
        row = {
            'active': False,
            'released_utc': datetime.now(_dt_timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = _fc_tempfile.mkstemp(dir=os.path.dirname(path),
                                       prefix='.tx_lease_', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                json.dump(row, fh, ensure_ascii=False)
            os.replace(tmp, path)
        finally:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
    except Exception:
        pass  # never let a lease clear break TX
# ────────────────────────────────────────────────────────────────────────────
