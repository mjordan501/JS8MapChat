#!/usr/bin/env python3
"""
JS8MapChat  1.75   (map component; formerly JS8Map v1.83)
Connects DIRECTLY to JS8Call's TCP API (port 2442).
No JS8Spotter required — owns its own SQLite spot database.

  BLUE   – Stations you are hearing
  GREEN  – Stations that are hearing you (directed to your callsign)
  ORANGE – Mutual (both)

Requires Python 3.8+, no external packages needed.
Run: python JS8Map.py
"""

import sqlite3
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import json
import webbrowser
# ── JS8Call API Reference URLs ───────────────────────────────────────
# Official JS8Call API docs:  http://files.js8call.com/js8call.pdf
# JS8Call-Improved (latest):  https://github.com/jderricks/JS8Call-improved
# TCP port: 2442  |  Format: JSON  |  Message: {type, value, params}
# Key types: RX.SPOT, RX.DIRECTED, RX.BAND_ACTIVITY, RX.CALL_ACTIVITY,
#            RX.ACTIVITY, TX.FRAME, TX.TEXT, STATION.STATUS, STATION.CALLSIGN,
#            STATION.GRID, RIG.FREQ, RIG.GET_FREQ, RX.GET_BAND_ACTIVITY,
#            RX.GET_CALL_ACTIVITY, CLOSE, PING
# Version history: see `git log` (CHANGELOG.txt no longer exists)


# ─────────────────────────────────────────────────────────────────────

import os
import shutil
import math
import re
import threading
import queue as _queue_mod
import socket
import http.server
import time
from datetime import datetime, timedelta
from datetime import timezone as _dt_timezone

# ── CRASH DIAGNOSTICS ─ install FIRST, above every risky import ───────────────
# In a --noconsole PyInstaller build sys.stderr is None, so an unguarded import
# failure or an exception inside any Tk callback produces NO window, NO dialog
# and NO log line. These hooks are the only reason a future failure is findable.
#
# Deliberately self-contained: no new module (a new module is one more hard
# import that can fall out of the .spec — the disease this cures) and no
# dependency on _APP_DIR / _DATA_DIR, which are defined further down this file
# and would be read-only anyway once installed under Program Files.
import sys as _crash_sys
import traceback as _crash_tb
import threading as _crash_thr
from js8map_util import (
    _DB_TS_FMT, _utc_now, _utc_stamp, _utc_cutoff, _utc_from_epoch_ms, _age_seconds, GRID_RE, _base, norm_call, norm_grid, status, is_valid_grid, is_precise_grid, grid_to_latlon
)

_CRASH_LOG_MAX    = 1000000   # bytes — roll once past this so a looping after()
                              # error cannot fill the disk
_CRASH_DIALOG_CAP = 3         # a repeating callback error must not become a
                              # dialog storm; the log still gets every first-of-kind
_crash_dialogs    = 0
_crash_seen       = set()
_crash_lock       = _crash_thr.Lock()


def _crash_log_path():
    """%LOCALAPPDATA%\\JS8Map\\crash.log — resolved from the environment ONLY."""
    try:
        base = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA')
        if not base:
            import tempfile as _crash_tmp
            base = _crash_tmp.gettempdir()
        d = os.path.join(base, 'JS8Map')
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, 'crash.log')
    except Exception:
        return None


def _crash_write(where, exc_type, exc_value, exc_tb):
    """Append one formatted entry. Returns the traceback text, or None. Never raises."""
    try:
        text = ''.join(_crash_tb.format_exception(exc_type, exc_value, exc_tb))
    except Exception:
        text = None
    path = _crash_log_path()
    if not path:
        return text
    try:
        if os.path.exists(path) and os.path.getsize(path) > _CRASH_LOG_MAX:
            try:
                os.replace(path, path + '.1')
            except Exception:
                pass
        stamp = datetime.now(_dt_timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        with open(path, 'a', encoding='utf-8', errors='replace') as fh:
            fh.write('\n' + '=' * 72 + '\n')
            fh.write('%s  [%s]  frozen=%s  pid=%s\n'
                     % (stamp, where, bool(getattr(_crash_sys, 'frozen', False)),
                        os.getpid()))
            fh.write('=' * 72 + '\n')
            fh.write(text or '(traceback unavailable)\n')
    except Exception:
        pass
    return text


def _crash_tail(text, lines=6):
    """Last few traceback lines — a full traceback in a MessageBox is unreadable."""
    try:
        parts = [ln for ln in (text or '').strip().splitlines() if ln.strip()]
        return '\n'.join(parts[-lines:]) if parts else '(traceback unavailable)'
    except Exception:
        return '(traceback unavailable)'


def _crash_dialog(title, body):
    """Surface a crash notice with NO dependency on a live Tk.

    Windows: native MessageBoxW via ctypes — if Tk is what died, a
    tkinter dialog would die with it, so we talk straight to user32.
    Linux / macOS: no windll exists; write the notice to stderr, which a
    source run (the supported non-Windows path) always has.
    """
    global _crash_dialogs
    try:
        with _crash_lock:
            if _crash_dialogs >= _CRASH_DIALOG_CAP:
                return
            _crash_dialogs += 1
        if _crash_sys.platform.startswith('win'):
            # Windows: native MessageBoxW, no dependency on a live Tk.
            import ctypes as _crash_ctypes
            _crash_ctypes.windll.user32.MessageBoxW(None, str(body), str(title), 0x10)
        else:
            # Linux / macOS: no windll. A source run (the supported non-Windows
            # path) has a real terminal, so the notice belongs on stderr where
            # the user will see it — NOT swallowed. __stderr__ is used in case
            # sys.stderr was reassigned; guarded because a frozen non-Windows
            # build could still have stderr None.
            _err = getattr(_crash_sys, '__stderr__', None) or getattr(_crash_sys, 'stderr', None)
            if _err is not None:
                try:
                    _err.write('\n' + '=' * 60 + '\n'
                               + str(title) + '\n' + '-' * 60 + '\n'
                               + str(body) + '\n' + '=' * 60 + '\n')
                    _err.flush()
                except Exception:
                    pass
    except Exception:
        pass


def _crash_excepthook(exc_type, exc_value, exc_tb):
    """Uncaught on the main thread — this is the silent-startup-failure case."""
    if issubclass(exc_type, KeyboardInterrupt):
        try:
            _crash_sys.__excepthook__(exc_type, exc_value, exc_tb)
        except Exception:
            pass
        return
    text = _crash_write('main', exc_type, exc_value, exc_tb)
    _crash_dialog('JS8Map — crashed',
                  'JS8Map hit an unhandled error and must close.\n\n'
                  + _crash_tail(text)
                  + '\n\nFull details:\n' + (_crash_log_path() or '(log unavailable)'))
    try:
        _crash_sys.__excepthook__(exc_type, exc_value, exc_tb)
    except Exception:
        pass   # noconsole: sys.stderr is None, __excepthook__ itself throws


def _crash_thread_hook(args):
    """Uncaught in a worker thread. Silent thread death is invisible today."""
    try:
        if issubclass(args.exc_type, SystemExit):
            return
        name = getattr(getattr(args, 'thread', None), 'name', '?')
        text = _crash_write('thread:%s' % name,
                            args.exc_type, args.exc_value, args.exc_traceback)
        _crash_dialog('JS8Map — background thread died',
                      'Thread "%s" stopped with an error.\n'
                      'JS8Map is still running but part of it has stopped '
                      'working.\n\n' % name
                      + _crash_tail(text)
                      + '\n\nFull details:\n' + (_crash_log_path() or '(log unavailable)'))
    except Exception:
        pass


def _crash_tk_callback(exc_type, exc_value, exc_tb):
    """Bound to root.report_callback_exception in main().

    The high-traffic hook: today EVERY exception inside a button handler or an
    after() tick vanishes. Deduped on (type, tail-of-traceback) so a repeating
    after() error is written once instead of megabytes of identical entries.
    """
    try:
        sig = (getattr(exc_type, '__name__', '?'),
               ''.join(_crash_tb.format_tb(exc_tb))[-400:])
    except Exception:
        sig = ('?', '?')
    try:
        with _crash_lock:
            first = sig not in _crash_seen
            if first:
                _crash_seen.add(sig)
    except Exception:
        first = True
    if not first:
        return          # repeat of a known error: already on disk, stay quiet
    text = _crash_write('tk-callback', exc_type, exc_value, exc_tb)
    _crash_dialog('JS8Map — internal error',
                  'JS8Map hit an error while handling an action.\n'
                  'The app is still running; this may have left something '
                  'incomplete.\n\n'
                  + _crash_tail(text)
                  + '\n\nFull details:\n' + (_crash_log_path() or '(log unavailable)'))


_crash_sys.excepthook = _crash_excepthook
try:
    _crash_thr.excepthook = _crash_thread_hook      # Python 3.8+
except Exception:
    pass
# ─────────────────────────────────────────────────────────────────────────────

# ── UTC time-standard helpers (Phase 3 migration) ─────────────────────────────
# All activity-DB timestamps are stored as naive-UTC strings with a trailing
# " UTC" suffix, mirroring the convention already used by the shared mailbox and
# TX-lock files (datetime.now(_dt_timezone.utc)...' UTC'). Window/purge cutoffs
# compared against stored rows use the SAME format, so every comparison is a
# uniform lexical string compare. FastChat parses raw[:19], so the suffix is
# harmlessly ignored on the read side. This is the durable fix for the
# "two apps anchor time differently" dropped-rows bug: one unambiguous clock.






# ─────────────────────────────────────────────────────────────
#  Persistent Config  (saved next to this script)
# ─────────────────────────────────────────────────────────────

import sys as _sys_early
import sys as _sys   # was provided by the platform-font block now in js8map_theme.py;
                    # kept module-level here for the ~5 GUI consumers that reference _sys.platform

from js8map_runtime import (
    APP_DIR as _APP_DIR,
    EXE_DIR as _EXE_DIR,
    DATA_DIR as _DATA_DIR,
    CONFIG_PATH,
    DEFAULT_CONFIG,
    load_config,
    save_config,
    APP_VERSION as _APP_VERSION_STR,
    APP_TITLE   as _APP_TITLE_STR,
)

from js8map_activity_store import (
    SpotDatabase,
    build_stations_from_db,
    build_directed_maps_from_db,
)

from js8map_platform import (
    focus_existing_map_window as _focus_existing_map_window,
    launch_map_window as _launch_map_window,
)
from js8map_group_config import GroupConfig
from js8map_relay_db import RelayDatabase
from js8map_tooltip import _ToolTip
from js8map_theme import (
    COLORS, LABEL_SIZE, BG, SURFACE, CARD, BORDER, TEXT, MUTED, BLUE, RED, CALLSIGN, GREEN, BTN_BG, GEN_BG, FONT_MONO, FONT_UI,
    _FONT_SANS, _FONT_MONO_NAME,
)
from js8map_dialogs import (   # themed modal dialogs, extracted from this file
    themed_confirm, themed_message, dismiss_toplevel,
)

# ── Product version (JS8MapChat v1.5 unification) ───────────────────────────


# ─────────────────────────────────────────────────────────────
#  FCC License Database  (AM.dat + EN.dat  →  ham.db)
# ─────────────────────────────────────────────────────────────

from js8map_fcc import (
    FCC_DB_CANDIDATES,
    set_fcc_db_path,
    get_fcc_db_path,
    _dat_files_in,
    update_canadian_db,
    download_and_update_fcc,
    build_fcc_db_from_dats,
    _find_fcc_db,
    fcc_fallback_grid,
    load_fcc_info,
    _zip5_cache,
    _fcc_result_cache,
)

# ─────────────────────────────────────────────────────────────
#  JS8Call Settings File  (read highlight/watched callsigns)
# ─────────────────────────────────────────────────────────────

from js8map_js8call_ini import (
    find_js8call_ini,
    read_js8call_highlights,
    read_js8call_groups,
    _GROUP_AUTO_PALETTE,
)


# Callsign pattern: 1-3 prefix letters/digits, number, 1-4 suffix letters, optional /suffix




# ─────────────────────────────────────────────────────────────
#  Maidenhead Grid → Lat/Lon
# ─────────────────────────────────────────────────────────────

from js8map_client import JS8CallClient, _last_seen_offset

_RSNR_RE         = re.compile(r'\[RSNR:([+\-]?\d+)\]')               # reported-SNR marker in stored text
_HEARING_CALL_RE = re.compile(                                          # callsign tokens in HEARING? text
    r'\b([A-Z0-9]{1,3}[0-9][A-Z]{1,4}(?:/[A-Z0-9]+)?)\b')









# ─────────────────────────────────────────────────────────────
#  Relay helpers + RelayDatabase
# ─────────────────────────────────────────────────────────────

from js8map_relay import (
    _relay_confidence,
    _relay_age_min,
    _set_relay_anim,
    _restart_dest_timer,
    _restart_return_timer,
    _relay_active_ref,
    _armed_relay_ref,
    _armed_relay_pos_ref,
    _relay_committed_ref,
    _relay_anim_ref,
    _relay_tx_timer,
    _relay_tx_frame_count,
    _relay_tx_last_frame_ts,
    _relay_watchdog_timer,
    _relay_yes_list_ref,
    _relay_yes_ts_ref,
    _relay_msg_ref,
    _relay_dest_timer,
    _relay_return_timer,
    _relay_via_last_frag_ts,
    _relay_failed_vias_ref,
    _relay_outcome_ref,
)
from js8map_fastchat import _fc_shared_dir, record_js8map_outgoing, write_js8map_tx_lease, clear_js8map_tx_lease
from js8map_tx import _coerce_tx_mode, tx_halt, tx_query, tx_stage, _TX_MODE, _TX_HALT_ENABLED
from js8map_web import render_html, _data_json_bytes, open_in_browser, wire
from js8map_app_ui import _AppUIMixin
from js8map_mapgen import _MapGenMixin
from js8map_relay_conn import _RelayConnMixin
from js8map_buildui import _BuildUIMixin









# ─────────────────────────────────────────────────────────────
#  JS8CallClient  (TCP connection to JS8Call port 2442)
# ─────────────────────────────────────────────────────────────

SNR_RE_GLOBAL = re.compile(r'\bSNR\s*([+\-]?\d+)', re.I)

# ─────────────────────────────────────────────────────────────────────


# ═══════════════════════════════════════════════════════════════════════
#  FEATURE COLORS — SINGLE SOURCE OF TRUTH
#  Change a color here and it updates everywhere (CSS, JS, and Python UI).
#    primary = dark-mode accent   ·   light = light-mode accent
#    bg      = chip/badge fill background
# ═══════════════════════════════════════════════════════════════════════




# ═══════════════════════════════════════════════════════════════════════
#  LABEL SIZE — zoom-responsive callsign box sizing (single source of truth)
#    Font scales linearly between min/max as you zoom. Padding + notif
#    badges scale proportionally so the whole box shrinks/grows together.
# ═══════════════════════════════════════════════════════════════════════


# ─────────────────────────────────────────────────────────────
#  MAP_HTML  (unchanged from v3 except subtitle label)
# ─────────────────────────────────────────────────────────────



# ─────────────────────────────────────────────────────────────
#  Browser launcher  (persistent localhost server)
# ─────────────────────────────────────────────────────────────

# ── Phase 2: relay animation state ───────────────────────────────────────────

# ── C.18: offset tracking, YES list, destination timeout, Switch Path ────────

# Phase 2 state order — prevents regression to an earlier hop





#  Uses Windows ctypes (built-in, no extra packages needed).
#  Works even when Brave or any other app has focus.
# ─────────────────────────────────────────────────────────────

def _start_global_hotkey(root: 'tk.Tk'):
    """Register Ctrl+Shift+J as a system-wide hotkey.
    Pressing it from anywhere — including inside Brave —
    deiconifies and focuses the JS8Map Python window."""
    import sys
    if sys.platform != 'win32':
        return   # Windows-only; harmless no-op on other platforms
    import ctypes, ctypes.wintypes, threading

    MOD_CONTROL = 0x0002
    MOD_SHIFT   = 0x0004
    VK_J        = 0x4A
    WM_HOTKEY   = 0x0312
    HOTKEY_ID   = 1

    user32 = ctypes.windll.user32

    def _listener():
        if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_SHIFT, VK_J):
            return   # couldn't register — another app may have claimed it
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageA(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                root.after(0, lambda: (
                    root.deiconify(),
                    root.lift(),
                    root.focus_force()
                ))
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageA(ctypes.byref(msg))
        user32.UnregisterHotKey(None, HOTKEY_ID)

    threading.Thread(target=_listener, daemon=True).start()


# ─────────────────────────────────────────────────────────────
#  Tkinter GUI colours & fonts  →  extracted to js8map_theme.py
#  (COLORS, LABEL_SIZE, scalar colours, platform fonts, FONT_MONO/FONT_UI,
#   plus the private _FONT_SANS / _FONT_MONO_NAME families — all imported
#   at the top of this file.)
# ─────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────
#  HamMapApp  (Tkinter GUI)
# ─────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
#  JS8CALL.INI GROUP READER  ·  pull "Callsign Groups" from JS8Call itself
# ─────────────────────────────────────────────────────────────────────────────
# JS8Call's TCP API exposes callsign/grid/info/status but NOT the configured
# Callsign Groups. That field lives only in JS8Call.ini, so to mirror it we read
# the .ini directly (same approach pyjs8call / JS8Spotter-style tools use).
#
#   Windows : %LOCALAPPDATA%\JS8Call\JS8Call.ini
#   Linux   : ~/.config/JS8Call.ini
#   macOS   : ~/Library/Preferences/JS8Call.ini
#
# We parse by VALUE SHAPE (a line whose comma-separated value is all @-tokens)
# rather than depending on the exact INI key name, so a JS8Call version quirk in
# the key spelling can't silently break the sync. A key literally containing
# "group" is preferred when more than one candidate line matches.





#  GROUP CONFIG  ·  js8_groups.json
# ─────────────────────────────────────────────────────────────────────────────
# Palette used to auto-color groups pulled from JS8Call that aren't already in
# the color map. Cycled in order, skipping colors already in use.







class HamMapApp(_AppUIMixin, _MapGenMixin, _RelayConnMixin, _BuildUIMixin):
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(_APP_TITLE_STR)
        # Default size — taller than before to fit the TX-mode readout + HALT
        # settings rows without clipping the bottom buttons. Height is clamped to
        # the screen so it never opens taller than the display (laptops/short
        # monitors); width unchanged.
        _want_w, _want_h = 1260, 880
        try:
            _scr_h = root.winfo_screenheight()
            _h = min(_want_h, max(640, _scr_h - 50))   # leave room for taskbar
        except Exception:
            _h = _want_h
        root.geometry(f"{_want_w}x{_h}")
        root.minsize(1100, min(750, _h))
        root.configure(bg=BG)
        root.resizable(True, True)
        # Windows: load the real .ico so the title-bar AND taskbar show the
        # JS8Map icon instead of the default Python interpreter icon. APP_DIR
        # resolves to the bundled _MEIPASS dir in a frozen exe (where the spec
        # ships JS8Map_icon.ico via datas) and to the source dir otherwise, so
        # this one path is correct both from source and in the built exe.
        # (The taskbar identity is claimed separately by the AppUserModelID set
        # in main() before Tk() is created; without that Windows groups the
        # window under python.exe and shows its icon regardless of this call.)
        try:
            import sys as _icon_sys
            if _icon_sys.platform.startswith('win'):
                _ico = os.path.join(_APP_DIR, 'JS8Map_icon.ico')
                if os.path.isfile(_ico):
                    root.iconbitmap(_ico)
        except Exception:
            pass
        # On Linux, wm_iconphoto with a PNG gives the taskbar/alt-tab icon.
        # Search several locations for JS8Map.png.
        try:
            import sys as _icon_sys
            if _icon_sys.platform.startswith('linux'):
                _icon_candidates = [
                    os.environ.get('APPDIR', '') and os.path.join(os.environ['APPDIR'], 'JS8Map.png'),
                    os.path.join(os.path.expanduser('~'), '.local', 'share', 'icons', 'JS8Map.png'),
                    os.path.join(_APP_DIR, 'JS8Map.png'),
                    os.path.join(_DATA_DIR, 'JS8Map.png'),
                ]
                for _ic in _icon_candidates:
                    if _ic and os.path.isfile(_ic):
                        root._icon_photo = tk.PhotoImage(file=_ic)
                        root.wm_iconphoto(True, root._icon_photo)
                        break
        except Exception:
            pass

        self._map_opened     = False
        self._pending_qsy    = 0.0
        self._session_start  = _utc_stamp()
        self._refresh_job    = None
        self._regen_pending  = False
        self._regen_job      = None   # after() handle for last-fires debounce
        self._unmapped_calls = set()
        self._relay_hint_count = 0
        self._fit_exclusions = set()   # callsigns excluded from Fit All bounding box
        self._relay_db = RelayDatabase(
            os.path.join(_DATA_DIR, 'js8_relay.db'))
        # Relay paths are one-shot observations tied to the band conditions at
        # the moment they were heard. A via that worked before the last restart
        # says nothing about the band now -- _relay_confidence already rates
        # anything older than 30 minutes LOW. Start each session empty rather
        # than presenting stale vias as if they were current.
        self._relay_db.clear_all()
        # Web-server callbacks handed to js8map_web via wire() (single injection point).
        _focus_cb = lambda: self.root.after(0, lambda: [
            self.root.deiconify(),
            self.root.lift(),
            self.root.attributes('-topmost', True),
            self.root.focus_force(),
            self.root.after(400, lambda: self.root.attributes('-topmost', False)),
        ])
        _refresh_cb = lambda: self.root.after(0, self._manual_refresh)
        _relay_stage_cb = lambda text, trigger: self.root.after(
            0, lambda: tx_stage(self._client, self._spot_db, text, trigger=trigger))
        def _do_popup_tx(text, trigger, stage):
            if stage:
                tx_stage(self._client, self._spot_db, text, trigger=trigger)
            else:
                tx_query(self._client, self._spot_db, text, trigger=trigger)
        _popup_cb = lambda text, trigger, stage: self.root.after(
            0, lambda: _do_popup_tx(text, trigger, stage))
        _halt_cb = lambda: tx_halt(self._client, self._spot_db)
        wire(
            relay_db_ref=self._relay_db,
            focus_callback=_focus_cb,
            refresh_callback=_refresh_cb,
            relay_stage_callback=_relay_stage_cb,
            popup_tx_callback=_popup_cb,
            halt_tx_callback=_halt_cb,
            tx_mode_ref=_TX_MODE,
            tx_halt_ref=_TX_HALT_ENABLED,
            shared_dir_provider=_fc_shared_dir,
        )
        _start_global_hotkey(self.root)

        self._spot_db       = SpotDatabase(
            db_path=os.path.join(_DATA_DIR, SpotDatabase.DB_FILENAME)
        )
        self._group_config  = GroupConfig()
        # HISTORY IS NOW RETAINED ACROSS RESTARTS.
        # This line used to call clear_spots(), wiping the `spots` and
        # `band_activity` tables on every launch. The map therefore could only
        # ever plot what it heard since this process started: the Show
        # dropdown's 12h / Today windows were querying a table that had been
        # emptied seconds earlier. No reason for the wipe was ever recorded --
        # the old comment explained only what it SPARED (SNR proofs, api_log).
        # It was not harmless. This file is also FastChat's activity source
        # (js8fastchat/data/db_locator.py walks up and resolves to this very
        # DB), so every JS8Map launch was destroying FastChat's spot and
        # band-activity history too; only `directed` survived, which is why
        # FastChat still appeared to show hours of backlog.
        # Growth stays bounded without the wipe: SpotDatabase.__init__ already
        # runs cleanup_old_data() (7-day trim) and then vacuum_if_needed().
        # The RelayDatabase.clear_all() above is UNRELATED and deliberately
        # kept -- relay vias are one-shot observations of band conditions.

        # JS8Call client (not started until config loaded)
        self._client = JS8CallClient(
            db            = self._spot_db,
            on_spot       = self._on_new_spot,
            on_status     = self._on_conn_status,
            on_my_station = self._on_my_station,
        )
        self._client._group_config          = self._group_config
        self._client._qsy_callback          = lambda f: setattr(self, '_pending_qsy', f)
        self._client._relay_db              = self._relay_db
        self._client._on_relay_hint         = self._on_relay_hint

        self._build_ui()
        self._load_config()
        self._fit_window_to_content()
        # The footer grows after this point (connection status wraps when
        # JS8Call is not answering), so look again once it has settled.
        self.root.after(2500, lambda: self._fit_window_to_content(grow_only=True))
        self.root.after(8000, lambda: self._fit_window_to_content(grow_only=True))
        # Ctrl+L = Clear (QSY shortcut)
        # Ctrl+L = API Log (hidden hotkey — not shown in UI)
        root.bind('<Control-l>', lambda e: self._show_api_log())
        root.bind('<Control-L>', lambda e: self._show_api_log())
        # Start connecting immediately
        self.root.after(500, self._start_client)

    def _fit_window_to_content(self, grow_only=False):
        """Size the main window to what the layout ACTUALLY needs.

        The geometry set in __init__ is a guess made before any widget
        exists (1260x880, clamped to the screen). That guess is wrong
        whenever the real content is taller than 880px -- Windows display
        scaling above 100%, larger UI Scale settings, the TX-mode + HALT
        rows. The window then opens with its bottom row (Generate Map /
        Update FCC DB / Update Canadian DB) below the visible area and a
        new operator cannot reach those controls at all.

        One measurement at startup is NOT enough. The footer grows after
        the window is first sized: with JS8Call not running, the connection
        status wraps to several lines seconds later, and the bottom is
        clipped again. main() therefore re-runs this with grow_only=True a
        few seconds in -- that pass may only make the window taller, never
        shorter and never repositioned, so it cannot fight the operator if
        they have already moved or resized it.

        Measuring is cause-agnostic: whatever made the content tall,
        winfo_reqheight() reports the truth. The result is clamped to the
        usable screen, so this can never open a window taller than the
        display.
        """
        try:
            r = self.root
            r.update_idletasks()          # force geometry calculation

            # Width must cover the WIDEST ROW, not the window's own request.
            # The bottom button rows (Clear/Generate/Groups, and the relay
            # strip) are packed centred and overflow the window rather than
            # widening it, so root.winfo_reqwidth() under-reports and the
            # first and last buttons get clipped at the frame edges. Asking
            # each direct child what it needs finds the true minimum.
            need_w = r.winfo_reqwidth()
            try:
                for _child in r.winfo_children():
                    _cw = _child.winfo_reqwidth()
                    if _cw > need_w:
                        need_w = _cw
                need_w += 40          # side margins
            except Exception:
                pass
            need_h = r.winfo_reqheight()
            scr_w  = r.winfo_screenwidth()
            scr_h  = r.winfo_screenheight()

            # Usable area: leave room for the taskbar and window chrome.
            avail_w = max(800, scr_w - 40)
            avail_h = max(560, scr_h - 90)

            w = max(1100, min(need_w, avail_w))
            h = min(max(need_h, 640), avail_h)

            if grow_only:
                cur_w = r.winfo_width()
                cur_h = r.winfo_height()
                if cur_w > 1 and cur_h > 1:
                    w = max(w, cur_w)
                    h = max(h, cur_h)
                    if w <= cur_w and h <= cur_h:
                        return        # nothing to do; leave the window alone
                w = min(w, avail_w)
                h = min(h, avail_h)
                # Keep where it is, but pull it back on-screen if growing
                # downward would push the bottom under the taskbar.
                x = r.winfo_x()
                y = r.winfo_y()
                if y + h > scr_h - 40:
                    y = max(0, scr_h - 40 - h)
                if x + w > scr_w:
                    x = max(0, scr_w - w)
                x = max(0, x)
                y = max(0, y)
            else:
                # minsize must never exceed what the screen can actually
                # show, or Tk will force a window larger than the display.
                r.minsize(min(1100, avail_w), min(700, avail_h))
                x = max(0, (scr_w - w) // 2)
                y = 0 if h >= (avail_h - 40) else max(0, (scr_h - h) // 3)

            r.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass                          # never block startup over layout

    # ── UI Construction ───────────────────────────────────────

    def _attach_tooltip(self, widget, text):
        """Attach a hover tooltip to a widget. Safe no-op on empty text/failure."""
        if not text:
            return None
        try:
            tip = _ToolTip(widget, text)
            if not hasattr(self, "_tooltips"):
                self._tooltips = []
            self._tooltips.append(tip)   # keep a ref so it isn't GC'd
            return tip
        except Exception:
            return None

    def _tt(self, key):
        """Look up tooltip text by key from the central table."""
        return self.TOOLTIPS.get(key, "")

    # ── Central tooltip text table (JS8Map main Python screen) ──────────────
    # Sourced verbatim from the operator's JS8Map Tool Tips document. Only the
    # main-window (Tkinter) controls live here; the HTML map screen tooltips
    # live in the frontend (app.js / ham_map.html), not this file.
    TOOLTIPS = {
        # Connection row
        "connected":     "Connected to JS8Call",
        "reconnect":     "If JS8Call is disconnected, click to reconnect",
        # Settings rows
        "data_rebuild":  "Refresh rate",
        "fcc_db":        "Map location of the FCC DB",
        "js8call_ini":   "Map JS8Call.ini location",
        "startup_manual": "Manual (fills this box) — cut and paste into JS8Call",
        "startup_live":  "Live (Auto-TX) — auto-send by JS8Call",
        "tx_halt":       "JS8Call Improved 3.x only feature",
        # Main buttons
        "clear_qsy":     "Frequency change — clear old data",
        "generate_map":  "Click to generate the HTML map",
        "regen_r":       "Re-generate if the HTML map was closed",
        "group_dropdown": "Select a group to tag that group on the map",
        "groups":        "Group list and callsign box color outline",
        "relay":         "Callsign to query",
        "watched_calls": "List of JS8Call callsigns to tag and monitor",
        "callsign_unknown": "Non-FCC callsigns",
        "update_fcc":    "Click to update the FCC DB",
        "update_canadian": "Click to update the Canadian DB",
    }

    # ── Config ────────────────────────────────────────────────

    def _load_config(self):
        cfg = load_config()

        self.js8_host.set(cfg.get('js8_host', '127.0.0.1'))
        self.js8_port.set(str(cfg.get('js8_port', '2442')))

        call = cfg.get('callsign', '').replace(' ', '').upper()
        if call:
            self.callsign.set(call)

        grid = cfg.get('my_grid', '').upper().strip()
        if grid:
            self.my_grid.set(grid)

        # Must stay identical to FastChat's TIME_FILTERS (constants.py) so the
        # two apps offer the SAME windows -- they read the same spot DB and a
        # range one app offers but the other cannot is a reporting mismatch.
        VALID_FILTERS = ("Last 15 minutes", "Last 30 minutes", "Last 1 hour",
                         "Last 2 hours", "Last 4 hours", "Last 12 hours",
                         "Last 24 hours")
        # "Today" was renamed to "Last 24 hours" -- it never meant since
        # midnight. Migrate BEFORE the validity test below, or an existing
        # saved value fails it and silently reverts to "Last 30 minutes".
        _RETIRED_FILTERS = {"Today": "Last 24 hours"}
        saved_filter = cfg.get('time_filter', '')
        saved_filter = _RETIRED_FILTERS.get(saved_filter, saved_filter)
        self.time_filter.set(saved_filter if saved_filter in VALID_FILTERS else "Last 30 minutes")

        # v1.5: out_path is no longer user-settable — the map always writes
        # beside JS8Map.py (the run folder). Any saved out_path is ignored, which
        # also retires the old stale-path failure mode for good.

        if cfg.get('refresh_interval', 0):
            secs = cfg['refresh_interval']
            label = {30: 'Every 30 sec', 60: 'Every 1 min', 120: 'Every 2 min',
                     300: 'Every 5 min', 600: 'Every 10 min'}.get(secs, 'Off')
            self.refresh_var.set(label)

        # FCC Database
        fcc_db = cfg.get('fcc_db_path', '').strip()
        if fcc_db and os.path.exists(fcc_db):
            self._set_fcc_db_path(fcc_db)
        else:
            found_dir  = next((c for c in FCC_DB_CANDIDATES
                               if os.path.isdir(c) and _dat_files_in(c)[0]), None)
            found_file = next((c for c in FCC_DB_CANDIDATES if os.path.isfile(c)), None)
            found = found_dir or found_file
            if found:
                parts = found.replace('\\', '/').split('/')
                self.fcc_label.config(
                    text='Auto: ' + '/'.join(parts[-2:]), fg=GREEN)

        # Watched calls from DB
        watched = set(cfg.get('watched_calls', []))
        if watched:
            self._spot_db.set_watched_calls(watched)

        # Fit exclusions — callsigns excluded from Fit All bounding box
        self._fit_exclusions = set(c.upper() for c in cfg.get('fit_exclusions', []))
        wire(fit_exclusion_callback=self._on_fit_exclusion)

        # Load station info from our own DB if already connected before
        info = self._spot_db.get_my_station()
        if info.get('callsign') and not call:
            db_call = info['callsign']
            if not is_valid_grid(db_call):          # guard: never use a grid sq as callsign
                self.callsign.set(db_call)
            else:
                # Stale bad data from earlier run — clear it
                self._spot_db.set_my_station('', info.get('grid', ''))
        if info.get('grid') and not grid:
            db_grid = info['grid']
            if is_valid_grid(db_grid):
                self.my_grid.set(db_grid)

        # My callsign groups (relay/query audience) — mirrors JS8Call's Callsign
        # Groups. First load whatever we have saved, then auto-sync from
        # JS8Call.ini so the list always mirrors JS8Call (v1.82_A).
        if hasattr(self, '_my_groups_var'):
            self._my_groups_var.set(cfg.get('my_groups', ''))
        self._js8call_ini_path = cfg.get('js8call_ini_path', '')
        # Populate the ini_label to show the saved path (mirrors fcc_label behavior)
        if self._js8call_ini_path and os.path.isfile(self._js8call_ini_path):
            _parts = self._js8call_ini_path.replace('\\', '/').split('/')
            _display = '/'.join(_parts[-2:]) if len(_parts) > 2 else self._js8call_ini_path
            if hasattr(self, 'ini_label'):
                self.ini_label.config(text=_display, fg=TEXT)
        # Defer slightly so the GUI/dropdowns exist; show a visible result.
        self.root.after(900, lambda: self._sync_groups_from_js8call(at_startup=True))

        # ── TX mode (shadow / manual / live) ──────────────────────────────
        # The saved startup default. Applied via _apply_tx_mode, which prompts
        # a confirm if the resulting mode is 'live' (even at startup, per design).
        self._tx_startup_default = _coerce_tx_mode(cfg.get('tx_mode', 'manual'))
        # HALT capability — only on JS8Call Improved API 3.0+. Default OFF so a
        # 2.x user isn't handed a dead button (their stop is JS8Call's Halt Tx).
        _TX_HALT_ENABLED[0] = bool(cfg.get('tx_halt_enabled', False))
        if hasattr(self, '_tx_halt_var'):
            try: self._tx_halt_var.set(_TX_HALT_ENABLED[0])
            except Exception: pass
        # Defer the apply slightly so the GUI (and any confirm dialog) is ready.
        self.root.after(400, lambda: self._apply_tx_mode(
            self._tx_startup_default, persist=False, at_startup=True))

        spot_count = self._spot_db.get_spot_count()
        # State B: config exists but ham.db is missing — known machine, something
        # went wrong. Warn clearly in the status bar without blocking launch.
        _db_present = bool(_find_fcc_db())
        if not _db_present:
            self._set_status(
                "\u26a0  No callsign database found \u2014 click"
                "  \u2b07 Update FCC DB  then  \u2b07 Update Canadian DB  to download.",
                '#e0a000'
            )
        else:
            self._set_status(
                f"Ready  \u00b7  {spot_count:,} spots in database  \u00b7  "
                f"Connecting to JS8Call on {self.js8_host.get()}:{self.js8_port.get()}\u2026",
                MUTED
            )

    def _save_config(self):
        secs = self._get_refresh_secs()
        watched = list(self._spot_db.load_watched_calls())
        save_config({
            'js8_host':         self.js8_host.get().strip(),
            'js8_port':         self.js8_port.get().strip(),
            'fcc_db_path':      get_fcc_db_path(),
            'callsign':         self.callsign.get().replace(' ', '').upper(),
            'my_grid':          self.my_grid.get().replace(' ', '').upper()[:6],
            'time_filter':      self.time_filter.get(),
            'out_path':         self.out_path.get().strip(),
            'refresh_interval': secs,
            'watched_calls':    watched,
            'fit_exclusions':   sorted(self._fit_exclusions),
            'my_groups':        (self._my_groups_var.get().strip()
                                 if hasattr(self, '_my_groups_var') else ''),
            'js8call_ini_path': getattr(self, '_js8call_ini_path', ''),
            'tx_mode':          getattr(self, '_tx_startup_default', 'manual'),
            'tx_halt_enabled':  bool(_TX_HALT_ENABLED[0]),
        })

    def _on_fit_exclusion(self, call: str, excluded: bool):
        """Called from HTTP thread when browser toggles a callsign's fit exclusion."""
        if excluded:
            self._fit_exclusions.add(call)
        else:
            self._fit_exclusions.discard(call)
        self.root.after(0, self._save_config)

    # ── Watched Calls dialog ──────────────────────────────────


    def _sync_groups_from_js8call(self, at_startup: bool = False):
        """Pull the Callsign Groups list from JS8Call.ini and mirror it into
        JS8Map: set the relay/query audience (_my_groups_var), merge any new
        groups into the color map (auto-coloring), and refresh the dropdowns.
        Operator chose 'always mirror on startup'."""
        try:
            groups, ini_path = read_js8call_groups(getattr(self, '_js8call_ini_path', ''))
        except Exception:
            groups, ini_path = [], ''

        if not groups:
            # Couldn't find JS8Call.ini or a groups line — keep existing list.
            if at_startup:
                self.root.after(1600, lambda: self._set_status(
                    '⚠  JS8Call groups not auto-synced (JS8Call.ini not found) — '
                    'using saved list. Use Browse… beside JS8Call.ini in Settings.', MUTED))
            else:
                self._set_status(
                    '⚠  JS8Call.ini not found — group list unchanged. '
                    'Use Browse… beside JS8Call.ini in Settings.', MUTED)
            return

        # Mirror into the relay/query audience field.
        new_str = ', '.join(groups)
        if hasattr(self, '_my_groups_var'):
            self._my_groups_var.set(new_str)

        # Reconcile the color map to EXACTLY the live JS8Call groups:
        # add missing ones (auto-colored), drop any that are no longer live.
        # The live list is authoritative (JS8Call/JS8Map parity), so stale
        # seed defaults and departed groups do not linger in the color panel.
        work = self._group_config.get_groups()
        used = set(work.values())
        live = set(groups)
        added = []
        pal_i = 0
        for g in groups:
            if g not in work:
                # pick next palette color not already in use
                color = None
                while pal_i < len(_GROUP_AUTO_PALETTE):
                    c = _GROUP_AUTO_PALETTE[pal_i]; pal_i += 1
                    if c not in used:
                        color = c; break
                if color is None:
                    color = _GROUP_AUTO_PALETTE[len(work) % len(_GROUP_AUTO_PALETTE)]
                work[g] = color
                used.add(color)
                added.append(g)
        removed = [g for g in work if g not in live]
        for g in removed:
            del work[g]
        if added or removed:
            self._group_config.set_groups(work)

        # Persist the mirrored list and refresh the dropdowns.
        self._save_config()
        if hasattr(self, '_refresh_grp_combo'):
            try: self._refresh_grp_combo()
            except Exception: pass

        # Visible confirmation.
        msg = f'✓  Groups synced from JS8Call: {new_str}'
        if added:
            msg += f'  (added {len(added)} new to map)'
        if at_startup:
            self.root.after(1600, lambda: self._set_status(msg, GREEN))
        else:
            self._set_status(msg, GREEN)


    # ── Status helper ─────────────────────────────────────────

    def _set_status(self, msg: str, color: str = MUTED):
        self.status_var.set(msg)
        self.status_lbl.config(fg=color)


# ─────────────────────────────────────────────────────────────
#  First-Run Setup Wizard
#
#  Shown only on true first run (no ham_map_config.json yet).
#  Walks the user through:
#    Panel 1 — Download FCC (US) database        [required]
#    Panel 2 — Download Canadian database         [optional / skippable]
#    Panel 3 — Enter callsign + grid square       [required]
#  Main window is created but hidden until the wizard finishes.
#  On subsequent launches the wizard is never shown again.
# ─────────────────────────────────────────────────────────────

from js8map_wizard import FirstRunWizard









# ─────────────────────────────────────────────────────────────
#  Helper alias used inside the wizard
# ─────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────

def main():
    # Windows: claim a distinct AppUserModelID BEFORE the Tk root exists so the
    # taskbar keys the window to JS8Map's own identity (its .ico, its own
    # button) instead of falling back to python.exe -- which also made JS8Map
    # and FastChat share one taskbar group. Must run before tk.Tk(); guarded so
    # a failure here can never stop launch. No-op on non-Windows platforms.
    try:
        import sys as _appid_sys
        if _appid_sys.platform.startswith('win'):
            import ctypes as _appid_ctypes
            _appid_ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                'KW3KW.JS8MapChat.JS8Map.1.75'
            )
    except Exception:
        pass
    root = tk.Tk(className="Js8map")
    # Claim the app icon as the DEFAULT for every window in this process,
    # immediately -- before the first-run wizard is built.
    #
    # HamMapApp.__init__ already calls root.iconbitmap(), but that runs only
    # AFTER the wizard has finished. On a first install the wizard is the
    # first thing the operator ever sees, and at that moment no icon has been
    # set on any window, so Windows falls back to the generic default in the
    # taskbar -- the reported symptom. Setting it here, with default=, applies
    # it to the wizard Toplevel and every window opened later, so the icon is
    # correct from the very first frame.
    try:
        import sys as _ic_sys
        if _ic_sys.platform.startswith('win'):
            _ico_early = os.path.join(_APP_DIR, 'JS8Map_icon.ico')
            if os.path.isfile(_ico_early):
                root.iconbitmap(default=_ico_early)
    except Exception:
        pass                     # an icon is never worth blocking launch over
    # Route every Tk callback exception to the crash log + a native dialog.
    # Must be set on the instance, so it cannot live in the block at the top.
    try:
        root.report_callback_exception = _crash_tk_callback
    except Exception:
        pass

    # ── First-run gate (State A) ──────────────────────────────
    # If ham_map_config.json does not exist this is a true first run.
    # Show the wizard and block until it completes or the user exits.
    if not os.path.exists(CONFIG_PATH):
        wizard = FirstRunWizard(root)
        try:
            root.wait_window(wizard.win)
        except Exception:
            pass
        if not wizard.finished:
            # User closed the wizard — exit cleanly
            try:
                root.destroy()
            except Exception:
                pass
            return
        # Wizard finished — make main window visible and continue
        try:
            root.deiconify()
            root.update()
        except Exception:
            pass

    HamMapApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
