from __future__ import annotations

import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..app_config import load_config, save_config
from ..constants import (
    API_MODES,
    APP_BUILD,
    APP_TITLE,
    APP_VERSION,
    CALL_ALL,
    DEBUG_LOG,
    DEFAULT_GROUPS,
    DEFAULT_MSG_WATCH_WORDS,
    GROUP_ALL,
    LAYOUT_PATH,
    OBSERVED_TX_PATH,
    RX_HB_TILE_COLOR,
    SPEEDS,
    TIME_FILTERS,
    UI_SCALE_PRESETS,
    WATCHED_TILE_COLOR,
)
from ..data.js8call_ini import find_js8call_ini, read_js8call_frequencies, _looks_like_js8call_ini
from ..core import FastChatCore
from ..models import ActivityRow
from ..call_info import CallInfo
from .main_window_group_activity import _GroupActivityMixin
from .main_window_message_popups import _MessagePopupsMixin, INBOX_VIEW_OUTGOING
from .main_window_ui_substrate import _UISubstrateMixin, popup_font, popup_scale, popup_wrap, set_popup_ui_scale
from .main_window_rig_intent_speed import _RigIntentSpeedMixin
from .main_window_builder_send_tx import _BuilderSendTxMixin
from ..services.live_rig_monitor import JS8LiveRigMonitor
from .fastchat_popup import FastChatPopup
from .tx_lock import TxLock
from .tx_indicator import TxIndicator
from ..services.intent_poller import IntentPoller, read_server_port
from ..utils import (
    base_call,
    clean_call_display,
    debug,
    debug_exc,
    display_person_name,
    fmt_age,
    fmt_freq,
    norm_call,
    parse_ts,
    read_json,
    safe_int,
    write_json,
)


# Tk reports a window on a monitor LEFT of primary with a negative offset,
# which Windows renders as "+-1529" (a plus followed by a minus), not
# "-1529". The original pattern rejected that form, so every popup resized
# on such a monitor was validated as malformed and its size silently
# discarded -- the popup reopened at its default forever. The main window
# escaped this only because its own geometry happened to be stored in the
# "-1931" form. The optional inner minus accepts both; the anchors and the
# all-or-nothing offset group still reject partial or junk strings.
_GEOMETRY_RE = re.compile(r"^\d+x\d+(?:[+-]-?\d+[+-]-?\d+)?$")


def _is_geometry(value: object) -> bool:
    """Return True for Tk geometry strings like 1680x940 or 1680x940+10+20."""
    return bool(_GEOMETRY_RE.match(str(value or "").strip()))


# ── App-switch: bring JS8Map's DEDICATED browser map window to the front ──
# JS8Map opens its map in a Chromium --app window (no tabs). These helpers let
# FastChat's button focus that existing window (no duplicate windows) or, if it
# isn't open, launch it. Mirrors the same helpers in JS8Map.py.
def _find_app_mode_browser():
    """Locate a Chromium-based browser for --app mode. Brave, then Chrome, then
    Edge. Returns the exe path or None."""
    pf   = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    la   = os.environ.get("LOCALAPPDATA", "")
    rel = [
        r"BraveSoftware\Brave-Browser\Application\brave.exe",
        r"Google\Chrome\Application\chrome.exe",
        r"Microsoft\Edge\Application\msedge.exe",
    ]
    if not sys.platform.startswith("win"):
        # Linux/other: the Windows roots above are all empty, so look on PATH
        # instead. Names and preference order mirror JS8Map's own
        # js8map_platform/linux.py, which is proven on the Linux box.
        import shutil
        for name in ("brave-browser", "brave",
                     "google-chrome-stable", "google-chrome",
                     "microsoft-edge-stable", "microsoft-edge",
                     "chromium", "chromium-browser"):
            path = shutil.which(name)
            if path:
                return path
        return None
    roots = [pf, pf86] + ([la] if la else [])
    for r in rel:
        for root in roots:
            path = os.path.join(root, r)
            if os.path.isfile(path):
                return path
    return None


# The Chromium --app window carries a WM_CLASS built from the URL's host and
# PATH but NOT its port -- e.g. "127.0.0.1__ham_map.html.Brave-browser" -- so
# this fragment is stable across sessions, across JS8Map's random HTTP port,
# and across every Chromium-family browser. Matching the CLASS rather than the
# title is deliberate on Linux: the title carries the operator's callsign.
_MAP_WINDOW_CLASS_FRAGMENT = "ham_map.html"

_MAP_HELPER_TIMEOUT = 3  # seconds; a hung helper must never stall the button


def _focus_map_window_linux() -> bool:
    """Raise an existing map window with wmctrl, else xdotool. False if neither
    is installed, no window matched, or the helper failed."""
    import shutil
    import subprocess

    exe = shutil.which("wmctrl")
    if exe:
        try:
            listing = subprocess.run(
                [exe, "-lx"], capture_output=True, text=True,
                timeout=_MAP_HELPER_TIMEOUT,
            )
            if listing.returncode == 0:
                for line in listing.stdout.splitlines():
                    # "0x04200004  0 127.0.0.1__ham_map.html.Brave-browser  host  Title"
                    parts = line.split(None, 3)
                    if len(parts) >= 3 and _MAP_WINDOW_CLASS_FRAGMENT in parts[2].lower():
                        raised = subprocess.run(
                            [exe, "-i", "-a", parts[0]], capture_output=True,
                            timeout=_MAP_HELPER_TIMEOUT,
                        )
                        return raised.returncode == 0
        except Exception:
            pass  # fall through to xdotool

    exe = shutil.which("xdotool")
    if exe:
        try:
            found = subprocess.run(
                [exe, "search", "--class", _MAP_WINDOW_CLASS_FRAGMENT],
                capture_output=True, text=True, timeout=_MAP_HELPER_TIMEOUT,
            )
            ids = [w for w in found.stdout.split() if w.strip()]
            if found.returncode == 0 and ids:
                raised = subprocess.run(
                    [exe, "windowactivate", ids[-1]], capture_output=True,
                    timeout=_MAP_HELPER_TIMEOUT,
                )
                return raised.returncode == 0
        except Exception:
            pass

    return False


def _focus_existing_map_window() -> bool:
    """Bring an already-open dedicated JS8Map browser window to the front.

    WINDOWS: matches a Chromium window (class 'Chrome_WidgetWin_1') whose title
    ends in 'JS8Map' (the map page titles itself '<call> JS8Map'). JS8Map's Tk
    control window also contains 'JS8Map' but is a different window class, so it
    is never matched.

    LINUX: matches on WM_CLASS instead, because the title carries the operator's
    callsign while the class is derived from the URL's host and PATH but not its
    port. Needs wmctrl or xdotool; when NEITHER is installed this returns False
    and the caller opens another window -- the previous behaviour, unchanged.
    Raising is also commonly blocked under Wayland, so a missing helper is a
    supported state and not an error.

    SIBLING IMPLEMENTATION -- JS8Map carries the same Linux logic in
    js8map_platform/linux.py. The two apps ship as separate packages and neither
    may import the other (JS8Map is standalone; FastChat sits alongside it), so
    this is deliberately a SECOND COPY. If you change one, grep both trees for
    _MAP_WINDOW_CLASS_FRAGMENT and change the other to match.

    Returns True only if a window was actually focused."""
    if sys.platform.startswith("linux"):
        return _focus_map_window_linux()
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        found = []
        EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, _lp):
            if not user32.IsWindowVisible(hwnd):
                return True
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if cls.value != "Chrome_WidgetWin_1":
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            # Dedicated --app windows show ONLY the page title (which ends in
            # 'JS8Map'); a regular tabbed browser window's title ends in the
            # browser/profile name, and JS8Map's Tk window ends in its version.
            # 'ends with JS8Map' uniquely targets OUR map window.
            if buf.value.strip().endswith("JS8Map"):
                found.append(hwnd)
            return True

        user32.EnumWindows(EnumProc(_cb), 0)
        if not found:
            return False
        hwnd = found[0]
        if user32.IsIconic(hwnd):       # only un-minimize if minimized; never
            user32.ShowWindow(hwnd, 9)  # un-maximize (that shrank it to 50%)
        SWP = 0x0001 | 0x0002       # SWP_NOSIZE | SWP_NOMOVE
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, SWP)  # HWND_TOPMOST  (pulse on)
        user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, SWP)  # HWND_NOTOPMOST (pulse off)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def _launch_map_app_window(url: str) -> bool:
    """Open the map in a dedicated Chromium --app window, matching JS8Map's own
    launch: isolated user-data-dir (so --app opens reliably even when the main
    browser is already running) + hardened std handles. Falls back to the
    default browser. Returns True if something was launched."""
    browser = _find_app_mode_browser()
    if browser:
        try:
            import subprocess
            if sys.platform.startswith("win"):
                la = os.environ.get("LOCALAPPDATA", "")
                profile = os.path.join(la, "JS8Map", "map_window_profile")
            else:
                # LOCALAPPDATA is unset off Windows, which would leave the
                # profile as a bare relative path. Use the same location
                # JS8Map's own DATA_DIR resolves to on Linux.
                base = os.environ.get("XDG_DATA_HOME", "") or os.path.join(
                    os.path.expanduser("~"), ".local", "share")
                profile = os.path.join(base, "JS8Map", "map_window_profile")
            args = [
                browser,
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--no-service-autorun",
                "--start-maximized",
                f"--app={url}",
            ]
            CREATE_NO_WINDOW = 0x08000000
            if sys.platform.startswith("win"):
                popen_kw = {"creationflags": CREATE_NO_WINDOW}
            else:
                # creationflags is Windows-only and raises on Linux.
                # start_new_session detaches the browser into its own process
                # group, so closing the terminal that started FastChat does not
                # take the map window down with it (same as JS8Map does).
                popen_kw = {"start_new_session": True}
            subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **popen_kw,
            )
            return True
        except Exception:
            pass
    try:
        os.startfile(url)  # type: ignore[attr-defined]
        return True
    except Exception:
        try:
            import webbrowser
            webbrowser.open(url)
            return True
        except Exception:
            return False


class MainWindow(_GroupActivityMixin, _MessagePopupsMixin, _UISubstrateMixin, _RigIntentSpeedMixin, _BuilderSendTxMixin, tk.Tk):
    def __init__(self):
        super().__init__(className="Js8fastchat")
        self.cfg = load_config()
        self.selected_call = ""
        self.all_activity_rows: List[ActivityRow] = []
        self.activity_rows: List[ActivityRow] = []
        # Added (manual) stations are session-only now — like JS8Call, a fresh
        # launch starts with none. Deliberately NOT loaded from config so a
        # typed-in placeholder never persists (and never corrupts) across runs.
        self.manual_calls: List[str] = []
        self.groups = list(DEFAULT_GROUPS)
        self._syncing_selection = False
        self._calls_iid_to_call: dict[str, str] = {}
        self._activity_iid_to_call: dict[str, str] = {}
        self._qso_latest_widgets: dict[str, tk.Text] = {}
        self._history_popups: dict[str, tk.Toplevel] = {}
        self._inbox_popups: dict[tuple, tk.Toplevel] = {}
        self._info_popup = None
        self._qso_inbox_buttons: dict[str, list] = {}
        self.last_manual_speed_change_ts = 0.0
        self._layout_restore_active = False
        self._last_db_signature = None
        self._auto_refresh_after_id = None
        self._age_tick_after_id = None
        self._self_refresh_after_id = None
        # Every repeating after() id belongs here AND in on_close. Three of
        # these were reschedule-only with no stored id (or no cancel), so they
        # outlived destroy() and fired against a dead interpreter on exit.
        self._js8map_lease_after_id = None
        self._speed_sync_after_id = None
        self._utc_after_id = None
        self._last_self_refresh_started = 0.0
        self._activity_iid_to_ts: dict[str, str] = {}
        self._calls_iid_to_ts: dict[str, str] = {}
        self._last_auto_refresh_started = 0.0
        self._closing = False
        self._layout_data = read_json(LAYOUT_PATH)
        self._layout_save_enabled = False
        self._rebuilding = False

        self.core = FastChatCore(
            self.cfg,
            host_getter=lambda: self.host_var.get() if hasattr(self, "host_var") else self.cfg.host,
            port_getter=lambda: self.port_var.get() if hasattr(self, "port_var") else self.cfg.port,
            tx_armed_getter=lambda: bool(self.tx_armed_var.get()),
            confirm_getter=lambda: bool(self.confirm_tx_var.get()),
            confirm_func=self.confirm_tx_dialog,
            status_func=self.set_status,
            ui_call=self.ui_call,
            on_activity_update=self.on_activity_update,
        )
        # Backward-compatible aliases — the rest of MainWindow still uses these
        # names directly, so the extraction changes ownership without changing
        # behavior. These five attributes are the same objects the core owns.
        self.api = self.core.api
        self.activity_service = self.core.activity_service
        self.tx_service = self.core.tx_service
        self.reader = self.core.reader
        self.locator = self.core.locator
        # Big Task Phase 1: ONE shared transmit lock for this root window and
        # every popup. Locks on send intent, releases on TX completion (3.x) or
        # a TTL safety net. Drives the header sine-wave indicator and disables
        # all TX buttons across windows while a transmission is pending.
        self.tx_lock = TxLock(after=self.after, after_cancel=self.after_cancel)
        self.live_rig_monitor: Optional[JS8LiveRigMonitor] = None
        self._intent_poller: Optional[IntentPoller] = None
        self._js8map_btn_icon = None  # PhotoImage ref for the "open JS8Map" button (prevent GC)
        self.saved_frequencies = read_js8call_frequencies(self.cfg.js8call_ini_path)
        self._freq_combo_widgets = []
        self._last_rig_commit_key = ""
        self._last_rig_commit_ts = 0.0

        self._init_vars()
        # Per-callsign derived facts (grid/distance/SNR/display) live in CallInfo
        # (domain layer). Live getters: locator is rebound when a fresh payload
        # arrives, so it is read through a getter rather than snapshotted.
        self.call_info = CallInfo(
            cfg_getter=lambda: self.cfg,
            locator_getter=lambda: self.locator,
            theme_var=self.theme_var,
            activity_rows_getter=lambda: getattr(self, "activity_rows", []),
            all_activity_rows_getter=lambda: getattr(self, "all_activity_rows", []),
        )
        self._init_window()
        self._build_menu()
        self._build_ui()
        self.apply_theme()
        self.restore_layout()
        self.update_utc_time()
        self.start_live_rig_monitor()
        self.start_intent_poller()
        # Big Task Phase 2: poll the JS8Map TX-lease so FastChat also locks while
        # JS8Map has the JS8Call TX window busy (cross-app lockout).
        self._js8map_lease_path = os.path.join(os.path.dirname(str(OBSERVED_TX_PATH)), "js8map_tx_lease.json")
        self._js8map_lease_after_id = self.after(1500, self._poll_js8map_tx_lease)
        self.after(50, lambda: self.set_status(f"{APP_VERSION} UI opened. {len(self.saved_frequency_mhz_values())} JS8Call saved frequencies loaded. DB/API are loading in background."))
        self.after(150, lambda: self.refresh_data(probe_api=True))
        self.after(900, self.start_speed_sync)
        self._auto_refresh_after_id = self.after(2200, self.auto_refresh_tick)
        self._age_tick_after_id = self.after(15000, self.age_tick)
        self._self_refresh_after_id = self.after(30000, self.activity_self_refresh_tick)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # setup
    def _init_vars(self) -> None:
        self.host_var = tk.StringVar(value=self.cfg.host or "127.0.0.1")
        self.port_var = tk.StringVar(value=str(self.cfg.port or 2442))
        self.ui_scale_var = tk.StringVar(value=self.cfg.ui_scale)
        set_popup_ui_scale(UI_SCALE_PRESETS.get(self.ui_scale_var.get(), 1.0))
        self.theme_var = tk.StringVar(value=self.cfg.theme)
        self.api_mode_var = tk.StringVar(value=self.cfg.api_mode)
        self.confirm_tx_var = tk.BooleanVar(value=self.cfg.confirm_tx)
        self.tx_armed_var = tk.BooleanVar(value=self.cfg.tx_armed)
        self.log_observed_tx_var = tk.BooleanVar(value=self.cfg.log_observed_tx)
        self.activity_self_refresh_var = tk.IntVar(value=self.cfg.activity_self_refresh_minutes)
        self.db_time_var = tk.StringVar(value=self.cfg.db_time)
        self.current_group_var = tk.StringVar(value=self.cfg.current_group if self.cfg.current_group in self.groups else GROUP_ALL)
        self.group_target_var = tk.StringVar(value=self.cfg.group_target if self.cfg.group_target in self.groups else GROUP_ALL)
        self.group_filter_var = tk.StringVar(value=GROUP_ALL)
        self.call_filter_var = tk.StringVar(value=CALL_ALL)
        self.manual_call_var = tk.StringVar(value="")
        self.freq_var = tk.StringVar(value="")
        self.offset_var = tk.StringVar(value="")
        self.speed_var = tk.StringVar(value="Normal")
        self.live_status_var = tk.StringVar(value="JS8: starting")
        self.utc_time_var = tk.StringVar(value="--:--:--")
        self.db_status_var = tk.StringVar(value="DB: loading in background")
        self.status_var = tk.StringVar(value="JS8FastChat Ver 3.3_9 ready.")
        self.target_title_var = tk.StringVar(value="No callsign selected")
        self.target_meta_var = tk.StringVar(value="")
        self.command_prefix_var = tk.StringVar(value="Command prefix: —")
        self.builder_msg_mode_var = tk.StringVar(value="DIRECTED")
        self.preview_var = tk.StringVar(value="")
        self.incoming_count_var = tk.StringVar(value="Incoming: 0")
        self.active_tile_filter = "total"
        self.tile_i_hear_var = tk.StringVar(value="0\nI HEAR")
        self.tile_hear_me_var = tk.StringVar(value="0\nHEAR ME")
        self.tile_mutual_var = tk.StringVar(value="0\nMUTUAL")
        self.tile_hb_var = tk.StringVar(value="0\nRX HB")
        self.tile_watched_var = tk.StringVar(value="0\nWATCHED")
        self.tile_watchmsg_var = tk.StringVar(value="0\nWORD")
        self.tile_total_var = tk.StringVar(value="0\nTOTAL")
        self.column_visible_vars: Dict[str, tk.BooleanVar] = {
            "flag": tk.BooleanVar(value=True), "from": tk.BooleanVar(value=True), "to": tk.BooleanVar(value=True),
            "group": tk.BooleanVar(value=True), "grid": tk.BooleanVar(value=False), "freq": tk.BooleanVar(value=False),
            "snr": tk.BooleanVar(value=True), "age": tk.BooleanVar(value=True),
            "activity": tk.BooleanVar(value=True), "text": tk.BooleanVar(value=True),
        }
        self._load_saved_column_visibility()
        self.msg_to_var = tk.StringVar(value="")
        for var in (self.manual_call_var, self.preview_var, self.msg_to_var):
            self.attach_uppercase_var(var)
        self.msg_to_var.trace_add("write", lambda *_: self.update_builder_prefix_display())

    def _init_window(self) -> None:
        self.title(f"{APP_TITLE} | build {APP_BUILD}")
        self.geometry("1680x940")
        self.minsize(1200, 720)
        # Windows: set the window icon so the RUNNING app shows the FastChat
        # (FC-8) icon on the taskbar. The distinct AppUserModelID set in app.py
        # gives FastChat its own taskbar button, but Windows still needs the
        # window's own icon to paint it -- otherwise the button shows a default,
        # not JS8FastChat_icon.ico. parents[2] is the _MEIPASS root when frozen
        # (the spec ships the .ico there via datas) and the app root from source,
        # so this one path is correct in both. Guarded; a missing icon never
        # raises. No-op off Windows (Tk .ico support is Windows-only).
        try:
            if sys.platform.startswith("win"):
                _ico = Path(__file__).resolve().parents[2] / "JS8FastChat_icon.ico"
                if _ico.is_file():
                    self.iconbitmap(str(_ico))
            else:
                # Linux/other: Tk cannot read .ico, so the desktop paints a
                # generic icon on the taskbar. Feed it the PNG instead via
                # iconphoto, which Tk does understand. Same parents[2] root as
                # the .ico above. The PhotoImage ref is kept on self so tkinter
                # does not garbage-collect it out from under the window.
                _png = Path(__file__).resolve().parents[2] / "js8fastchat_icon.png"
                if _png.is_file():
                    self._app_icon_img = tk.PhotoImage(file=str(_png))
                    self.iconphoto(True, self._app_icon_img)
        except Exception:
            pass
        try:
            self.tk.call("tk", "scaling", UI_SCALE_PRESETS.get(self.ui_scale_var.get(), 1.0))
        except Exception:
            pass

    def attach_uppercase_var(self, var: tk.StringVar) -> None:
        def sync(*_args):
            if getattr(self, "_uppercase_var_guard", False):
                return
            value = var.get()
            upper = value.upper()
            if value != upper:
                self._uppercase_var_guard = True
                try:
                    var.set(upper)
                finally:
                    self._uppercase_var_guard = False
        var.trace_add("write", sync)

    def attach_uppercase_text(self, widget: tk.Text) -> None:
        def key(event):
            try:
                if event.state & 0x0004 or event.state & 0x0008 or event.state & 0x0001:
                    return None
                ch = event.char
                if not ch or ch in ("\r", "\n", "\t", "\x08"):
                    return None
                if len(ch) == 1 and ch.isprintable():
                    try:
                        if widget.tag_ranges("sel"):
                            widget.delete("sel.first", "sel.last")
                    except Exception:
                        pass
                    widget.insert("insert", ch.upper())
                    return "break"
            except Exception:
                return None
            return None
        def sync(_event=None):
            if getattr(self, "_uppercase_text_guard", False):
                return
            try:
                text = widget.get("1.0", "end-1c")
                upper = text.upper()
                if text != upper:
                    insert = widget.index("insert")
                    self._uppercase_text_guard = True
                    try:
                        widget.delete("1.0", "end")
                        widget.insert("1.0", upper)
                        widget.mark_set("insert", insert)
                    finally:
                        self._uppercase_text_guard = False
            except Exception:
                pass
        widget.bind("<KeyPress>", key, add="+")
        widget.bind("<KeyRelease>", sync, add="+")
        widget.bind("<FocusOut>", sync, add="+")
        widget.bind("<<Paste>>", lambda _e: widget.after(1, sync), add="+")

    # menu/layout
    def _build_menu(self) -> None:
        menu_font = self.scaled_font(11, "normal")
        self.option_add("*Menu.font", menu_font)
        menubar = tk.Menu(self, font=menu_font)
        file_menu = tk.Menu(menubar, tearoff=0, font=menu_font)
        file_menu.add_command(label="Connect DB", command=self.refresh_data)
        file_menu.add_command(label="Refresh", command=lambda: self.refresh_data(probe_api=True))
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        view_menu = tk.Menu(menubar, tearoff=0, font=menu_font)
        for col, label in (("to", "Show To column"), ("snr", "Show SNR column"), ("grid", "Show Grid column"), ("freq", "Show Freq column"), ("age", "Show Age column"), ("activity", "Show Activity column"), ("text", "Show Text column")):
            view_menu.add_checkbutton(label=label, variable=self.column_visible_vars[col], command=self.update_activity_columns)
        view_menu.add_separator()
        view_menu.add_command(label="Open FastChat Window", command=self.open_qso_popup)
        view_menu.add_command(label="DB Locator", command=self.show_db_locator)
        view_menu.add_command(label="Database Status...", command=self.show_db_status)
        view_menu.add_command(label="Open Debug Log", command=self.open_debug_log)
        view_menu.add_command(label="Open App Folder", command=self.open_app_folder)
        menubar.add_cascade(label="View", menu=view_menu)

        settings_menu = tk.Menu(menubar, tearoff=0, font=menu_font)
        settings_menu.add_radiobutton(label="Legacy 2.x Compatible API", variable=self.api_mode_var, value="Legacy 2.x Compatible", command=self.save_current_config)
        settings_menu.add_radiobutton(label="Improved / 3.x API", variable=self.api_mode_var, value="Improved / 3.x", command=self.save_current_config)
        settings_menu.add_separator()
        settings_menu.add_command(label="Refresh JS8Call Freq List", command=self.refresh_saved_frequencies)
        settings_menu.add_command(label="Set JS8Call.ini Location...", command=self.browse_js8call_ini)
        settings_menu.add_command(label="Reset JS8Call.ini Location (auto)", command=self.clear_js8call_ini_override)
        settings_menu.add_separator()
        self_refresh_menu = tk.Menu(settings_menu, tearoff=0, font=menu_font)
        for label, minutes in (("Off (default)", 0), ("Every 2 minutes", 2), ("Every 5 minutes", 5), ("Every 10 minutes", 10)):
            self_refresh_menu.add_radiobutton(label=label, variable=self.activity_self_refresh_var, value=minutes, command=self.save_current_config)
        settings_menu.add_cascade(label="Activity Self-Refresh (quiet-band aging)", menu=self_refresh_menu)
        settings_menu.add_separator()
        settings_menu.add_command(label="Browse JS8Map Config...", command=self.browse_map_config)
        settings_menu.add_command(label="Browse Spot DB...", command=self.browse_spot_db)
        settings_menu.add_command(label="Browse Relay DB...", command=self.browse_relay_db)
        settings_menu.add_command(label="Browse FCC DB...", command=self.browse_fcc_db)
        settings_menu.add_command(label="Browse Canadian DB...", command=self.browse_canadian_db)
        settings_menu.add_separator()
        settings_menu.add_command(label="Message Watch Words...", command=self.open_watch_words_popup)
        menubar.add_cascade(label="Settings", menu=settings_menu)

        help_menu = tk.Menu(menubar, tearoff=0, font=menu_font)
        help_menu.add_command(label="About", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.config(menu=menubar)

    def _build_ui(self) -> None:
        self.configure(bg=self.pal().bg)
        self._build_header()
        self._build_controls()
        self._build_body()
        self._build_status_bar()

    def _build_header(self) -> None:
        p = self.pal()
        header = tk.Frame(self, bg=p.header, height=72)
        header.pack(fill="x", padx=8, pady=(8, 4))
        header.pack_propagate(False)
        tk.Label(header, text="JS8FastChat", bg=p.header, fg=p.header_text, font=self.scaled_font(22, "bold")).pack(side="left", padx=(16, 14))
        tk.Label(header, text=f"v{APP_VERSION}", bg=p.header, fg=p.header_text, font=self.scaled_font(15, "bold", family="Courier New")).pack(side="left", padx=(0, 20))
        for label, var, width in (("Host:", self.host_var, 12), ("Port:", self.port_var, 6)):
            tk.Label(header, text=label, bg=p.header, fg=p.header_text, font=self.scaled_font(15, "bold", family="Courier New")).pack(side="left")
            tk.Label(header, textvariable=var, bg=p.header, fg=p.green, font=self.scaled_font(15, "bold", family="Courier New"), width=width, anchor="w").pack(side="left", padx=(5, 12))
        tk.Label(header, textvariable=self.live_status_var, bg=p.header, fg=p.green, font=self.scaled_font(15, "bold", family="Courier New"), width=18, anchor="w").pack(side="left", padx=(0, 10))
        tk.Label(header, text="UTC:", bg=p.header, fg=p.header_text, font=self.scaled_font(15, "bold", family="Courier New")).pack(side="left", padx=(0, 4))
        tk.Label(header, textvariable=self.utc_time_var, bg=p.header, fg=p.green, font=self.scaled_font(15, "bold", family="Courier New"), width=12, anchor="w").pack(side="left", padx=(0, 10))
        self.tx_armed_btn = self.button(header, "TX Armed", command=self.toggle_tx_armed, width=12, good=True)
        self.tx_armed_btn.pack(side="right", padx=(5, 12), ipady=8)
        self._attach_tooltip(self.tx_armed_btn, self._tt("hdr_tx_armed"))
        self.halt_btn = self.button(header, "HALT TX", command=self.halt_tx, width=12, danger=True)
        self.halt_btn.pack(side="right", padx=5, ipady=8)
        self._attach_tooltip(self.halt_btn, self._tt("hdr_halt_tx"))
        self.refresh_btn = self.button(header, "Refresh", command=lambda: self.refresh_data(probe_api=True), width=12)
        self.refresh_btn.pack(side="right", padx=5, ipady=8)
        self._attach_tooltip(self.refresh_btn, self._tt("hdr_refresh"))
        self._build_open_js8map_button(header)
        # Big Task Phase 1: scrolling sine-wave TX indicator, top-right of the
        # header (just left of the action buttons). Animates while a send is
        # pending. To move it to the extreme corner, pack it before tx_armed_btn.
        self.tx_indicator = TxIndicator(header, width=120, height=22, bg=p.header)
        self.tx_indicator.pack(side="right", padx=(8, 12))
        self.tx_lock.add_listener(self.tx_indicator.on_lock)
        self._attach_tooltip(self.tx_indicator, self._tt("hdr_freqline"))
        self.update_tx_armed_button()

    def _build_open_js8map_button(self, header) -> None:
        """App-switch button: shows the JS8Map icon + a 'JS8Map' label and
        brings the JS8Map window to the front when clicked. Mirrors the JS8Map
        side, which shows the FastChat icon. Placed in the header alongside
        HALT / Refresh / TX Armed.

        Styled to stand out from its gray/green/red neighbors: a normal raised
        3D button face wrapped in a thin gold accent frame, with the label in
        gold. If the icon file can't be loaded for any reason, it falls back to
        a gold-accented text button so the feature is never silently missing --
        but if you see plain text instead of the icon, js8map_icon.png is not
        sitting beside the launcher (parents[2]).
        """
        p = self.pal()
        icon = None
        try:
            icon_path = Path(__file__).resolve().parents[2] / "js8map_icon.png"
            if icon_path.is_file():
                icon = tk.PhotoImage(file=str(icon_path))
                self._js8map_btn_icon = icon  # keep ref alive (tkinter GC gotcha)
                self._js8map_btn_icon_src = icon  # unscaled original, for refitting
        except Exception:
            icon = None

        # Built exactly like Refresh / HALT TX / TX Armed: packed straight into
        # the header, sized by its own content, no wrapper frame and no
        # measuring of anything else.
        #
        # HISTORY -- do not reintroduce. This button used to sit inside a gold
        # tk.Frame whose size was computed at runtime from a neighbor button
        # (after_idle, pack_propagate(False)). That measurement was right on a
        # 100%-scaled display and wrong on a 125% one: the button was pinned to
        # a too-small width and BOTH the label and the icon were chopped -- the
        # label read "JS8N" -- while empty header space sat unused beside it. A
        # theme change or a UI Scale round-trip did not clear it, so it was not
        # a timing problem; the measurement itself was simply wrong on that
        # machine. Anything that measures a sibling and pins this button will
        # fail again on the next display that scales differently.
        #
        # The gold accent ring is now the BUTTON'S OWN highlight border, which
        # is drawn outside its content and therefore cannot eat the label at
        # any scale. Ring colour is theme-dependent: LIGHT's gold reads as a
        # crisp raised edge, while DARK's gold sinks into the navy header, so
        # dark mode uses a brighter gold. A theme switch rebuilds the whole UI
        # (rebuild_visible_ui), so this is re-evaluated on every change.
        ring = "#ffc233" if self.theme_var.get() == "Dark" else p.gold
        _btn_kw = dict(
            command=self.open_js8map,
            bg=p.button, fg=p.gold, activebackground=p.button_active,
            activeforeground=p.gold, relief="raised", bd=2, cursor="hand2",
            highlightthickness=4, highlightbackground=ring, highlightcolor=ring,
            font=self.scaled_font(10, "bold"),
        )
        if icon is not None:
            btn = tk.Button(header, image=icon, text=" JS8Map",
                            compound="left", **_btn_kw)
        else:
            btn = tk.Button(header, text="\U0001F5FA JS8Map", **_btn_kw)
        self.open_js8map_btn = btn
        # Keep the icon roughly the height of the label text so the button is
        # not taller than its neighbors. Derived from the FONT SIZE, which
        # already carries the UI Scale preset -- not from any other widget.
        try:
            _pts = abs(int(self.scaled_font(10, "bold")[1]))
        except Exception:
            _pts = 10
        self._fit_js8map_icon(btn, max(12, int(round(_pts * 2.0))))
        # ipady=6 brings the height in line with the Refresh / HALT / TX-Armed
        # buttons (which pack with ipady=8 but carry no icon adding height).
        btn.pack(side="right", padx=5, ipadx=4, ipady=6)
        # Tooltip: only if a tooltip helper exists (none in this build yet).
        # Left guarded so wiring a real tooltip later is a drop-in.
        _tip = getattr(self, "_attach_tooltip", None)
        if callable(_tip):
            try:
                _tip(btn, self._tt("hdr_js8map"))
            except Exception:
                pass

    def _fit_js8map_icon(self, btn, max_h: int) -> None:
        """Shrink the app-switch button's icon to at most max_h pixels tall.

        Height only. Width is deliberately not considered, here or anywhere --
        the button sizes itself from its content and must never be constrained
        (see the note where it is built). tk.PhotoImage rescales only by
        whole-number zoom/subsample, so walk a short ladder of ratios
        largest-first and stop at the first that fits. Always rescales from the
        unscaled original, so repeated calls -- a UI Scale or theme change
        rebuilds the whole window -- never compound. Does nothing when the icon
        failed to load (the text-fallback button)."""
        src = getattr(self, "_js8map_btn_icon_src", None)
        if src is None:
            return
        max_h = max(1, max_h)
        for zoom, sub in ((1, 1), (3, 4), (2, 3), (1, 2), (2, 5), (1, 3), (1, 4)):
            try:
                img = src if (zoom, sub) == (1, 1) else src.zoom(zoom).subsample(sub)
                if img.height() > max_h:
                    continue
                btn.configure(image=img)
                self._js8map_btn_icon = img  # keep ref alive (tkinter GC gotcha)
                return
            except Exception:
                continue
        # Nothing on the ladder fit the height; the native icon stays and the
        # button is a little taller than its neighbors. Never clipped.

    def open_js8map(self) -> None:
        """Bring JS8Map's MAP (the live browser map) to the front -- that's the
        surface the operator flips between, not JS8Map's Python control window.

        Hot path: if the dedicated map window is already open, just focus it
        (instant, no duplicate, no port lookup). Otherwise read JS8Map's port
        from js8map_server.json, verify the server is actually reachable, and
        open the map in a dedicated Chromium --app window. If JS8Map isn't
        running, say so instead of opening a dead browser tab.
        """
        # Hot path on the GUI thread: focus the existing window immediately
        # (FastChat is the foreground app right now, so SetForegroundWindow is
        # allowed). Fast EnumWindows call; no need for a thread.
        if _focus_existing_map_window():
            try:
                self.set_status("Brought the JS8Map map to the front.")
            except Exception:
                pass
            return

        def worker():
            port = read_server_port()
            reachable = False
            if port is not None:
                try:
                    import socket
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(0.5)
                    s.connect(("127.0.0.1", port))
                    s.close()
                    reachable = True
                except Exception:
                    reachable = False
            if not reachable:
                msg = "JS8Map not reachable (is it running?)."
                try:
                    self.after(0, lambda: self.set_status(msg))
                except Exception:
                    pass
                return
            url = f"http://127.0.0.1:{port}/ham_map.html"
            ok = _launch_map_app_window(url)
            msg = ("Opening the JS8Map map\u2026" if ok
                   else "Could not open the JS8Map map window.")
            try:
                self.after(0, lambda: self.set_status(msg))
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True, name="OpenJS8Map").start()

    def clear_frequency_field_selection(self) -> None:
        """Clear the blue selected-text highlight from Set Freq combo boxes."""
        for widget in list(getattr(self, "_freq_combo_widgets", []) or []):
            try:
                if widget.winfo_exists():
                    widget.selection_clear()
                    widget.icursor(tk.END)
            except Exception:
                pass

    def refresh_saved_frequencies(self) -> None:
        """Reload JS8Call Settings > Frequencies and update all Set Freq dropdowns."""
        try:
            self.saved_frequencies = read_js8call_frequencies(self.cfg.js8call_ini_path)
            values = self.saved_frequency_mhz_values()
            alive = []
            for widget in list(getattr(self, "_freq_combo_widgets", []) or []):
                try:
                    if widget.winfo_exists():
                        widget.configure(values=values)
                        alive.append(widget)
                except Exception:
                    pass
            self._freq_combo_widgets = alive
            ini_path = self.cfg.js8call_ini_path or find_js8call_ini()
            if values:
                self.set_status(f"Loaded {len(values)} saved JS8Call frequencies from JS8Call.ini.")
            elif ini_path:
                self.set_status("No JS8Call saved frequencies found. Save JS8Call Settings > Frequencies, then use Settings > Refresh JS8Call Freq List.")
            else:
                self.set_status("JS8Call.ini not found. Start/save JS8Call once, or set Settings > Set JS8Call.ini Location, then use Settings > Refresh JS8Call Freq List.")
        except Exception:
            debug_exc("refresh_saved_frequencies failed")
            self.set_status("Refresh JS8Call Freq List failed; see debug log.")

    def saved_frequency_labels(self) -> list[str]:
        return [
            str(x.get("label", ""))
            for x in getattr(self, "saved_frequencies", [])
            if str(x.get("label", "")).strip()
        ]

    def saved_frequency_lookup(self) -> dict[str, dict]:
        return {
            str(x.get("label", "")): x
            for x in getattr(self, "saved_frequencies", [])
            if str(x.get("label", "")).strip()
        }

    def saved_frequency_mhz_values(self) -> list[str]:
        """Compact values for the combined Set Freq dropdown.

        The full saved-frequency labels are too wide for the main toolbar, so
        the field shows only the MHz value while the status line still reports
        the matching saved label after selection.
        """
        values: list[str] = []
        for row in getattr(self, "saved_frequencies", []) or []:
            try:
                value = f"{float(row.get('mhz', '')):.4f}"
            except Exception:
                continue
            if value not in values:
                values.append(value)
        return values

    def apply_saved_frequency_value(self, value: str, commit: bool = True) -> bool:
        raw = str(value or "").strip()
        if not raw:
            return False
        if raw in self.saved_frequency_lookup():
            return self.apply_saved_frequency_label(raw, commit=commit)
        m = re.search(r"\d{1,3}[.,]\d{3,6}", raw)
        if not m:
            self.set_status(f"Could not use saved frequency: {raw}")
            return False
        try:
            mhz = float(m.group(0).replace(",", "."))
        except Exception:
            self.set_status(f"Could not use saved frequency: {raw}")
            return False
        label = ""
        for row in getattr(self, "saved_frequencies", []) or []:
            try:
                if abs(float(row.get("mhz", 0)) - mhz) < 0.000001:
                    label = str(row.get("label", ""))
                    break
            except Exception:
                pass
        self.freq_var.set(f"{mhz:.4f}")
        self.clear_frequency_field_selection()
        self.after_idle(self.clear_frequency_field_selection)
        self.after(80, self.clear_frequency_field_selection)
        self.set_status(f"Selected saved frequency: {label or f'{mhz:.4f} MHz'}")
        if commit:
            self.after(35, self.on_rig_field_commit)
        return True

    def apply_saved_frequency_label(self, label: str, commit: bool = True) -> bool:
        label = str(label or "").strip()
        row = self.saved_frequency_lookup().get(label, {})
        mhz = row.get("mhz", "")
        try:
            self.freq_var.set(f"{float(mhz):.4f}")
            self.clear_frequency_field_selection()
            self.after_idle(self.clear_frequency_field_selection)
            self.after(80, self.clear_frequency_field_selection)
            self.set_status(f"Selected saved frequency: {label}")
            if commit:
                self.after(35, self.on_rig_field_commit)
            return True
        except Exception:
            self.set_status(f"Could not use saved frequency: {label}")
            return False

    def _build_controls(self) -> None:
        p = self.pal()
        controls = tk.Frame(self, bg=p.bg)
        controls.pack(fill="x", padx=10, pady=(4, 2))
        def add_label(text):
            tk.Label(controls, text=text, bg=p.bg, fg=p.text, font=self.scaled_font(11, "bold")).pack(side="left", padx=(8, 4))
        add_label("Set Freq:")
        self.freq_entry = ttk.Combobox(controls, textvariable=self.freq_var, values=self.saved_frequency_mhz_values(), width=8, state="normal", style="Rig.TCombobox", font=self.scaled_font(15, "bold", family="Consolas"))
        self.freq_entry.pack(side="left")
        self._attach_tooltip(self.freq_entry, self._tt("row_set_freq"))
        self._freq_combo_widgets.append(self.freq_entry)
        self.freq_entry.bind("<Return>", self.on_rig_field_commit)
        self.freq_entry.bind("<FocusOut>", self.on_rig_field_commit)
        self.freq_entry.bind("<<ComboboxSelected>>", lambda _e, box=self.freq_entry: self.apply_saved_frequency_value(box.get(), commit=True))
        add_label("Set Offset:")
        self.offset_entry = self.entry(controls, self.offset_var, width=7)
        self.offset_entry._mj_rig_field = True
        self.offset_entry.configure(fg=self.rig_green(), insertbackground=self.rig_green(), font=self.scaled_font(15, "bold", family="Consolas"), justify="center")
        self.offset_entry.pack(side="left")
        self._attach_tooltip(self.offset_entry, self._tt("row_set_offset"))
        self.offset_entry.bind("<Return>", self.on_rig_field_commit)
        self.offset_entry.bind("<FocusOut>", self.on_rig_field_commit)
        add_label("Speed:")
        speed_cb = self.combo(controls, self.speed_var, SPEEDS, width=10)
        speed_cb.pack(side="left")
        speed_cb.bind("<<ComboboxSelected>>", lambda _e, box=speed_cb: self.on_speed_selected(box))
        add_label("UI Scale:")
        scale_cb = self.combo(controls, self.ui_scale_var, list(UI_SCALE_PRESETS.keys()), width=13)
        scale_cb.pack(side="left")
        scale_cb.bind("<<ComboboxSelected>>", lambda _e: self.on_ui_scale_changed())
        add_label("Theme:")
        theme_cb = self.combo(controls, self.theme_var, ["Light", "Dark"], width=8)
        theme_cb.pack(side="left")
        theme_cb.bind("<<ComboboxSelected>>", lambda _e: self.on_theme_changed())
        add_label("API Mode:")
        api_cb = self.combo(controls, self.api_mode_var, API_MODES, width=20)
        api_cb.pack(side="left")
        api_cb.bind("<<ComboboxSelected>>", lambda _e: self.save_current_config())
        self._attach_tooltip(api_cb, self._tt("row_api_mode"))
        confirm = tk.Checkbutton(controls, text="Confirm TX", variable=self.confirm_tx_var, bg=p.bg, fg=p.text, activebackground=p.bg, activeforeground=p.text, selectcolor=p.panel, font=self.scaled_font(11, "bold"), command=self.save_current_config)
        confirm.pack(side="right", padx=(8, 14))
        self._attach_tooltip(confirm, self._tt("hdr_confirm_tx"))

        dbrow = tk.Frame(self, bg=p.bg)
        dbrow.pack(fill="x", padx=10, pady=(0, 6))
        tk.Label(dbrow, text="DB Time:", bg=p.bg, fg=p.text, font=self.scaled_font(11, "bold")).pack(side="left", padx=(2, 4))
        db_cb = self.combo(dbrow, self.db_time_var, list(TIME_FILTERS.keys()), width=16)
        db_cb.pack(side="left")
        db_cb.bind("<<ComboboxSelected>>", lambda _e: self.refresh_data(probe_api=False))
        _clear_act_btn = self.button(dbrow, "Clear Activity", command=self.clear_display, width=12)
        _clear_act_btn.pack(side="left", padx=(10, 4))
        self._attach_tooltip(_clear_act_btn, self._tt("db_clear_activity"))
        _catchup_btn = self.button(dbrow, "Catch Up DB Time", command=self.refresh_data, width=15)
        _catchup_btn.pack(side="left", padx=4)
        self._attach_tooltip(_catchup_btn, self._tt("db_catch_up"))
        # "Connect DB" lives in the File menu; the DB connection status moved to
        # View -> "Database Status..." (it used to be an always-on inline label).
        # Group Activity moved down here, next to Word Search, now that the DB
        # status no longer occupies this row.
        _grp_act_btn = self.button(dbrow, "Group Activity", command=self.open_group_activity_popup, width=14)
        _grp_act_btn.pack(side="left", padx=(12, 4))
        self._attach_tooltip(_grp_act_btn, self._tt("db_group_activity"))
        tk.Label(dbrow, text="Word Search:", bg=p.bg, fg=p.text, font=self.scaled_font(11, "bold")).pack(side="left", padx=(12, 4))
        self.activity_search_var = tk.StringVar(value="")
        # Same construction as the callsign popup's Search box (fastchat_popup.py):
        # a standard helper entry with a small clear glyph placed INSIDE it at the
        # right edge. No wrapper frame -- the entry packs directly like its
        # neighbours, and the glyph font is FIXED (not scaled) so it always stays
        # smaller than the entry's own text and cannot overrun the borders.
        _act_search = self.entry(dbrow, self.activity_search_var, width=22)
        _act_search.pack(side="left", padx=(0, 8))
        self._attach_tooltip(_act_search, self._tt("db_word_search"))
        _act_search_clear = tk.Label(_act_search, text="\u2715", bg=p.entry_bg, fg="#9aa0a6", font=popup_font("Arial", 10, "bold"), cursor="hand2")
        _act_search_clear.place(relx=1.0, rely=0.5, anchor="e", x=-3)
        _act_search_clear.bind("<Button-1>", lambda _e: self.activity_search_var.set(""))
        self._attach_tooltip(_act_search_clear, self._tt("db_word_clear"))
        self.activity_search_var.trace_add("write", lambda *_: self._on_activity_search_changed())
        _act_search.bind("<Return>", lambda _e: self.render_activity())
        # JS8Call-style "Add New Station": inject a callsign not currently in the
        # DB window so the operator can store a message for it (or try a relay)
        # before it ever appears on the air. Sits next to Word Search.
        _add_call_btn = self.button(dbrow, "\u2795 Add Call", command=self.open_add_call_popup, width=12)
        _add_call_btn.pack(side="left", padx=(10, 4))
        self._attach_tooltip(_add_call_btn, self._tt("db_add_call"))

    def _build_body(self) -> None:
        p = self.pal()
        body = tk.Frame(self, bg=p.bg)
        body.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self.body_panes = tk.PanedWindow(body, orient="horizontal", sashwidth=7, sashrelief="raised", opaqueresize=True, bd=0, bg=p.border)
        self.body_panes.pack(fill="both", expand=True)
        # These widths were authored at 100%. Their CONTENTS scale with the
        # UI Scale preset but the panels did not, and pack_propagate(False)
        # means an oversized child is CLIPPED rather than allowed to push
        # the panel wider -- which is why the right panel cut off its
        # buttons at 115%. Scaling by the preset keeps 100% byte-identical.
        _ps = lambda v: int(round(v * self.ui_scale_factor()))
        self.left_panel = tk.Frame(self.body_panes, bg=p.panel, highlightbackground=p.border, highlightthickness=1, width=_ps(285))
        self.center_panel = tk.Frame(self.body_panes, bg=p.panel, highlightbackground=p.border, highlightthickness=1)
        self.right_panel = tk.Frame(self.body_panes, bg=p.panel, highlightbackground=p.border, highlightthickness=1, width=_ps(430))
        self.left_panel.pack_propagate(False)
        self.right_panel.pack_propagate(False)
        self.body_panes.add(self.left_panel, minsize=_ps(220))
        self.body_panes.add(self.center_panel, minsize=_ps(520))
        self.body_panes.add(self.right_panel, minsize=_ps(380))
        self._build_left_panel()
        self._build_center_panel()
        self._build_right_panel()

    def _build_left_panel(self) -> None:
        p = self.pal()
        self.label(self.left_panel, "Send Group Target", size=10).pack(fill="x", padx=8, pady=(8, 2))
        self.group_target_combo = self.combo(self.left_panel, self.group_target_var, self.groups, width=25)
        self.group_target_combo.pack(fill="x", padx=10, pady=(0, 8))
        self.group_target_combo.bind("<<ComboboxSelected>>", lambda _e: self.save_current_config())
        self._attach_tooltip(self.group_target_combo, self._tt("left_send_group"))
        self.label(self.left_panel, "Target Callsign", size=10).pack(fill="x", padx=8, pady=(4, 2))
        self.call_filter_combo = self.combo(self.left_panel, self.call_filter_var, [CALL_ALL], width=25)
        self.call_filter_combo.pack(fill="x", padx=10, pady=(0, 8))
        self.call_filter_combo.bind("<<ComboboxSelected>>", self.on_call_filter_selected)
        self._attach_tooltip(self.call_filter_combo, self._tt("left_target_call"))
        self.label(self.left_panel, "Type Callsign", size=10).pack(fill="x", padx=8, pady=(4, 2))
        row = tk.Frame(self.left_panel, bg=p.panel)
        row.pack(fill="x", padx=10, pady=(0, 8))
        _type_call_entry = self.entry(row, self.manual_call_var, width=16)
        _type_call_entry.pack(side="left", fill="x", expand=True)
        self._attach_tooltip(_type_call_entry, self._tt("left_type_call"))
        _manual_x_btn = self.button(row, "X", command=self.clear_manual_call, width=3)
        _manual_x_btn.pack(side="left", padx=(4, 0))
        self._attach_tooltip(_manual_x_btn, self._tt("left_x"))
        _manual_go_btn = self.button(row, "GO", command=self.apply_manual_call, width=4, good=True)
        _manual_go_btn.pack(side="left", padx=(4, 0))
        self._attach_tooltip(_manual_go_btn, self._tt("left_go"))
        self.label(self.left_panel, "Active Callsigns", size=10).pack(fill="x", padx=8, pady=(4, 4))
        calls_wrap = tk.Frame(self.left_panel, bg=p.panel)
        calls_wrap.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.calls_tree = ttk.Treeview(calls_wrap, columns=("call", "last", "snr"), show="headings", selectmode="browse", height=22)
        for col, text, width, anchor in (("call", "Call", 100, "w"), ("last", "Last", 80, "center"), ("snr", "SNR", 58, "center")):
            self.calls_tree.heading(col, text=text, command=lambda c=col: self.sort_treeview(self.calls_tree, c))
            self.calls_tree.column(col, width=width, anchor=anchor, stretch=(col == "call"))
        _calls_vsb = ttk.Scrollbar(calls_wrap, orient="vertical", command=self.calls_tree.yview)
        self.calls_tree.configure(yscrollcommand=_calls_vsb.set)
        _calls_vsb.pack(side="right", fill="y")
        self.calls_tree.pack(side="left", fill="both", expand=True)
        self.calls_tree.bind("<<TreeviewSelect>>", self.on_calls_tree_select)
        self.calls_tree.bind("<Button-1>", self.on_calls_tree_single_click, add="+")
        self.calls_tree.bind("<Double-Button-1>", self.on_calls_tree_double_click)
        self.calls_tree.bind("<Return>", lambda _e: self.open_qso_popup())
        self.calls_tree.bind("<Button-3>", self.on_calls_tree_right_click)

    def _build_center_panel(self) -> None:
        p = self.pal()
        top = tk.Frame(self.center_panel, bg=p.panel)
        top.pack(fill="x", padx=6, pady=(6, 2))
        self.label(top, "Incoming Activity", size=11).pack(side="left")
        self.incoming_count_label = tk.Label(top, textvariable=self.incoming_count_var, bg=p.green, fg="#000000", font=self.scaled_font(10, "bold"), padx=10)
        self.incoming_count_label.pack(side="left", padx=(10, 8))
        self._attach_tooltip(self.incoming_count_label, self._tt("tile_incoming"))
        tiles = tk.Frame(top, bg=p.panel)
        tiles.pack(side="left", padx=(2, 8))
        self.tile_buttons = {}
        total_tile_color = "#d6d9df" if self.theme_var.get() == "Dark" else p.panel2
        tile_specs = [
            ("heard", self.tile_i_hear_var, "#29b6f6"),
            ("hearing_me", self.tile_hear_me_var, "#26a869"),
            ("both", self.tile_mutual_var, "#f5a623"),
            ("hb", self.tile_hb_var, RX_HB_TILE_COLOR),
            ("watched", self.tile_watched_var, WATCHED_TILE_COLOR),
            ("watchmsg", self.tile_watchmsg_var, "#ff7a00"),
            ("total", self.tile_total_var, total_tile_color),
        ]
        for key, var, color in tile_specs:
            tile_font = self.scaled_font(8, "bold")
            tile_width = 8
            b = tk.Button(
                tiles,
                textvariable=var,
                command=lambda k=key: self.set_activity_tile_filter(k),
                bg=color,
                fg="#000000" if key in ("hb", "watchmsg", "total") else "#ffffff",
                activebackground=color,
                activeforeground="#000000" if key in ("hb", "watchmsg", "total") else "#ffffff",
                font=tile_font,
                width=tile_width,
                height=2,
                relief="ridge",
                bd=1,
                justify="center",
            )
            b.pack(side="left", padx=1)
            self.tile_buttons[key] = b
            self._attach_tooltip(b, self._tt("tile_" + key))
        tk.Label(top, text="Group:", bg=p.panel, fg=p.text, font=self.scaled_font(10, "bold")).pack(side="right", padx=(8, 4))
        self.group_filter_combo = self.combo(top, self.group_filter_var, self.groups, width=14)
        self.group_filter_combo.pack(side="right", padx=(4, 0))
        self.group_filter_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_data(False))
        self._attach_tooltip(self.group_filter_combo, self._tt("grp_all_filter"))
        columns = ("flag", "from", "to", "group", "grid", "freq", "snr", "age", "activity", "text")
        tree_wrap = tk.Frame(self.center_panel, bg=p.panel)
        tree_wrap.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.activity_tree = ttk.Treeview(tree_wrap, columns=columns, show="headings", selectmode="browse")
        for col, label, width, anchor in (("flag", "Flag", 90, "center"), ("from", "From", 110, "w"), ("to", "To", 100, "w"), ("group", "Group", 110, "w"), ("grid", "Grid", 80, "center"), ("freq", "Freq", 90, "center"), ("snr", "SNR", 62, "center"), ("age", "Age", 90, "center"), ("activity", "Activity", 74, "center"), ("text", "Text", 420, "w")):
            self.activity_tree.heading(col, text=label, command=lambda c=col: self.sort_treeview(self.activity_tree, c))
            self.activity_tree.column(col, width=width, anchor=anchor, stretch=(col == "text"))
        self.update_activity_columns()
        _act_vsb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.activity_tree.yview)
        self.activity_tree.configure(yscrollcommand=_act_vsb.set)
        _act_vsb.pack(side="right", fill="y")
        self.activity_tree.pack(side="left", fill="both", expand=True)
        self.activity_tree.bind("<<TreeviewSelect>>", self.on_activity_tree_select)
        self.activity_tree.bind("<Double-Button-1>", self.on_activity_tree_double_click)
        self.activity_tree.bind("<Return>", lambda _e: self.open_qso_popup())
        preview_panel = tk.Frame(self.center_panel, bg=p.panel2, highlightbackground=p.border, highlightthickness=1)
        preview_panel.pack(fill="x", padx=6, pady=(0, 6))
        self.label(preview_panel, "Command Preview / Ready to Send", size=10, bg=p.panel2).pack(fill="x", padx=4, pady=(2, 2))
        preview_row = tk.Frame(preview_panel, bg=p.panel2)
        preview_row.pack(fill="x", padx=6, pady=(0, 6))
        # Pack the BUTTONS FIRST, to the right, then let the entry take what
        # is left. Packed the other way round the expanding entry claims the
        # row and the buttons -- last in, so last served -- collapse to a
        # sliver: measured at ONE PIXEL wide for Send and Clear once the
        # center panel got tight, which is what "the Clear button is missing"
        # actually was. The entry also asked for 80 characters, far more than
        # the row can give at 115%; it is expand=True and absorbs the leftover
        # either way, so a smaller request costs nothing at 100%.
        self.button(preview_row, "Clear", command=self.clear_preview, width=8).pack(side="right", padx=(6, 0))
        _preview_send_btn = self.button(preview_row, "Send", command=self.send_preview_only, width=12)
        _preview_send_btn.pack(side="right", padx=(6, 0))
        self.tx_lock.bind_button(_preview_send_btn)
        self.button(preview_row, "Copy", command=self.copy_preview, width=8).pack(side="right", padx=(6, 0))
        self.entry(preview_row, self.preview_var, width=20).pack(side="left", fill="x", expand=True)

    def _build_right_panel(self) -> None:
        p = self.pal()
        top = tk.Frame(self.right_panel, bg=p.panel2)
        top.pack(fill="x", padx=6, pady=(6, 0))
        left = tk.Frame(top, bg=p.panel2)
        left.pack(side="left", fill="both", expand=True)
        self.label(left, "Selected Target / Send Panel", size=11, bg=p.panel2).pack(fill="x", padx=4, pady=(4, 2))
        tk.Label(left, textvariable=self.target_title_var, bg=p.panel2, fg=p.text, font=self.scaled_font(15, "bold"), anchor="w", justify="left", wraplength=self.wrap(300)).pack(fill="x", padx=4)
        tk.Label(left, textvariable=self.target_meta_var, bg=p.panel2, fg=p.text, font=self.scaled_font(12, "bold"), anchor="w", justify="left", wraplength=self.wrap(300)).pack(fill="x", padx=4, pady=(0, 4))
        btns = tk.Frame(top, bg=p.panel2)
        btns.pack(side="right", padx=4, pady=4)
        _fastchat_target_btn = self.button(btns, "FastChat", command=self.open_qso_popup, width=10)
        _fastchat_target_btn.pack(fill="x", pady=(0, 4))
        self._attach_tooltip(_fastchat_target_btn, self._tt("btn_fastchat"))
        _info_target_btn = self.button(btns, "Info", command=self.open_info_popup, width=8)
        _info_target_btn.pack(fill="x")
        self._attach_tooltip(_info_target_btn, self._tt("btn_info"))

        js8 = tk.Frame(self.right_panel, bg=p.panel2, highlightbackground=p.border, highlightthickness=1)
        js8.pack(fill="x", padx=6, pady=(6, 4))
        self.label(js8, "JS8 Commands", size=14, bg=p.panel2).pack(fill="x", padx=4, pady=(4, 2))
        grid = tk.Frame(js8, bg=p.panel2)
        grid.pack(fill="x", padx=6, pady=(0, 6))
        # Row 4 ends with HB, which Phase 9 splits into HB + CQ. Build the first
        # three rows plus the two leading cells of row 4 exactly as before, then
        # place the HB/CQ pair in the last cell. Keeping this loop identical to
        # the original avoids disturbing the proven left-click wiring.
        commands = [["SNR?", "INFO?", "GRID?"], ["STATUS?", "HEARING?", "MSG TO"], ["INB-MSG", "STORE MSG", "QRY MSGS"], ["QRY CALL", "Send MSG ID"]]
        rclick_send = {"SNR?", "INFO?", "GRID?", "STATUS?"}
        for r, row in enumerate(commands):
            for c, label in enumerate(row):
                _cmd_btn = self.button(grid, label, command=lambda x=label: self.build_command(x), width=13)
                _cmd_btn.grid(row=r, column=c, sticky="ew", padx=3, pady=3, ipady=4)
                self.tx_lock.bind_button(_cmd_btn)
                self._attach_tooltip(_cmd_btn, self._tt("cmd_" + label))
                if label == "STORE MSG":
                    # Held so the green waiting-count can be repainted as the
                    # builder target changes and as messages get picked up.
                    self._main_store_btn = _cmd_btn
                    _cmd_btn.bind("<Button-3>", lambda _e: self._open_outgoing_for_target(), add="+")
                if label in rclick_send:
                    # Phase 9: left-click REQUESTS theirs ("{call} SNR?") via the
                    # button's own `command`; right-click (Button-3, plus Button-2
                    # for trackpads) SENDS mine. add="+" is used as good practice
                    # so these instance bindings never displace other handlers.
                    _cmd_btn.bind("<Button-3>", lambda _e, x=label: self.open_send_mine_popup(x), add="+")
                    _cmd_btn.bind("<Button-2>", lambda _e, x=label: self.open_send_mine_popup(x), add="+")
                grid.columnconfigure(c, weight=1)
        # HB + CQ share the last cell (row 3, column 2). HB keeps its red styling
        # and behavior; CQ (green) transmits our JS8Call CQ call directly.
        hbcq = tk.Frame(grid, bg=p.panel2)
        hbcq.grid(row=3, column=2, sticky="ew", padx=3, pady=3)
        hbcq.columnconfigure(0, weight=1)
        hbcq.columnconfigure(1, weight=1)
        _hb_btn = self.button(hbcq, "HB", command=lambda: self.build_command("HB"), width=5, danger=True)
        _hb_btn.grid(row=0, column=0, sticky="ew", padx=(0, 2), ipady=4)
        self.tx_lock.bind_button(_hb_btn)
        self._attach_tooltip(_hb_btn, self._tt("cmd_HB"))
        _cq_btn = self.button(hbcq, "CQ", command=self.send_cq, width=5, good=True)
        _cq_btn.grid(row=0, column=1, sticky="ew", padx=(2, 0), ipady=4)
        self.tx_lock.bind_button(_cq_btn)
        self._attach_tooltip(_cq_btn, self._tt("cmd_CQ"))

        mb = tk.Frame(self.right_panel, bg=p.panel2, highlightbackground=p.border, highlightthickness=1)
        mb.pack(fill="x", padx=6, pady=(2, 4))
        self.label(mb, "Message Builder", size=14, bg=p.panel2).pack(fill="x", padx=4, pady=(4, 0))
        tk.Label(mb, textvariable=self.command_prefix_var, bg=p.panel2, fg=p.text, font=self.scaled_font(13, "bold"), anchor="w").pack(fill="x", padx=8, pady=(2, 0))
        row = tk.Frame(mb, bg=p.panel2)
        row.pack(fill="x", padx=8)
        tk.Label(row, text="Message text:", bg=p.panel2, fg=p.text, font=self.scaled_font(12, "bold"), anchor="w").pack(side="left")
        self.button(row, "Clear Msg", command=self.clear_main_msg_box, width=9).pack(side="right")
        self.msg_text = self.text_box(mb, height=3, font=self.scaled_font(12, "bold", family="Consolas"))
        self.msg_text.pack(fill="x", padx=8, pady=(2, 4))
        self.attach_uppercase_text(self.msg_text)
        self.msg_text.bind("<Return>", self.send_builder_directed_msg)
        line = tk.Frame(mb, bg=p.panel2)
        line.pack(fill="x", padx=8, pady=(0, 6))
        tk.Label(line, text="MSG TO call:", bg=p.panel2, fg=p.text, font=self.scaled_font(11, "bold")).pack(side="left")
        self.entry(line, self.msg_to_var, width=10).pack(side="left", padx=(4, 8))
        _builder_send_btn = self.button(line, "Send", command=self.send_builder_directed_msg, width=10, good=True)
        _builder_send_btn.pack(side="right")
        self.tx_lock.bind_button(_builder_send_btn)

        macros = tk.Frame(self.right_panel, bg=p.panel2, highlightbackground=p.border, highlightthickness=1)
        macros.pack(fill="x", padx=6, pady=(2, 6))
        # Single row: label on the left, button on the right, to save vertical
        # space (more room below for JS8Call's waterfall when stacked).
        mrow = tk.Frame(macros, bg=p.panel2)
        mrow.pack(fill="x", padx=8, pady=(6, 6))
        self.label(mrow, "Saved Macros", size=14, bg=p.panel2).pack(side="left")
        self.button(mrow, "\U0001F4CB Open Macros\u2026", command=self.open_macros_popup, width=16, good=True).pack(side="right")
        tk.Label(mrow, text="pick \u2192 Go", bg=p.panel2, fg=p.text, font=self.scaled_font(10, "normal")).pack(side="right", padx=(0, 8))
        self._macros_popup = None

    def _build_status_bar(self) -> None:
        p = self.pal()
        bar = tk.Frame(self, bg=p.header, height=26)
        bar.pack(fill="x", side="bottom")
        tk.Label(bar, textvariable=self.status_var, bg=p.header, fg=p.header_text, font=self.scaled_font(9, "bold"), anchor="w").pack(fill="x", padx=10)

    # data refresh/render
    def refresh_data(self, probe_api: bool = False, auto: bool = False) -> None:
        if not auto:
            self.save_current_config()
            self.db_status_var.set("DB: loading in background")
            self.set_status("Refreshing activity in background...")
        else:
            self._last_auto_refresh_started = time.time()
            self.set_status("Auto DB change detected; refreshing activity...")
        self.activity_service.refresh_async(
            self.db_time_var.get(),
            self.group_filter_var.get(),
            probe_groups=not auto,
        )
        if probe_api:
            self.probe_js8_async()

    def on_activity_update(self, payload: dict) -> None:
        with self.soft("on_activity_update"):
            self.locator = payload.get("locator") or self.locator
            self.reader = payload.get("reader") or self.reader
            self.groups = payload.get("groups") or self.groups
            self.all_activity_rows = self._merge_manual_calls(payload.get("rows") or [])
            self.capture_activity_rows(self.all_activity_rows)
            self.apply_activity_tile_filter(render=False)
            self.refresh_group_widgets()
            self.render_activity()
            self.render_active_callsigns()
            self.render_tiles()
            self.update_db_status()
            self.refresh_open_qso_popups()
            self.refresh_inbox_buttons()
            err = payload.get("error") or ""
            if err:
                self.set_status(f"Activity refresh failed: {err}")
            else:
                self.set_status(f"Refreshed. {len(self.activity_rows)} displayed row(s). {self.locator.spot_db or 'No spot DB found.'}")

    def db_file_signature(self):
        """Return a lightweight signature for js8_spots.db and WAL/SHM sidecars."""
        try:
            db = str(getattr(self.locator, "spot_db", "") or "")
            if not db:
                return None
            paths = [Path(db), Path(db + "-wal"), Path(db + "-shm")]
            sig = []
            any_exists = False
            for path in paths:
                try:
                    st = path.stat()
                    any_exists = True
                    sig.append((str(path), int(st.st_mtime_ns), int(st.st_size)))
                except FileNotFoundError:
                    sig.append((str(path), 0, 0))
            return tuple(sig) if any_exists else None
        except Exception:
            debug_exc("db_file_signature")
            return None

    def auto_refresh_tick(self) -> None:
        """Poll DB/WAL/SHM modified times and refresh only when JS8Map writes new data."""
        if self._closing:
            return
        try:
            sig = self.db_file_signature()
            if sig is not None:
                if self._last_db_signature is None:
                    self._last_db_signature = sig
                elif sig != self._last_db_signature:
                    self._last_db_signature = sig
                    # Avoid stacking refresh workers during rapid WAL writes.
                    if time.time() - float(self._last_auto_refresh_started or 0.0) >= 1.5:
                        self.refresh_data(probe_api=False, auto=True)
        except Exception:
            debug_exc("auto_refresh_tick")
        finally:
            # `return` inside `finally` swallows any exception still in flight
            # and raises SyntaxWarning on 3.14; invert the guard instead. Same
            # behaviour, no early return.
            if not self._closing:
                with self.soft("auto_refresh_reschedule", log=False):
                    self._auto_refresh_after_id = self.after(2000, self.auto_refresh_tick)

    def _tree_set_value(self, tree: ttk.Treeview, iid: str, index: int, value: str) -> None:
        """Update one Treeview value in place without rebuilding/re-sorting rows."""
        if not tree.exists(iid):
            return
        vals = list(tree.item(iid, "values") or [])
        if index >= len(vals):
            return
        if str(vals[index]) == str(value):
            return
        vals[index] = value
        tree.item(iid, values=tuple(vals))

    def refresh_visible_age_labels(self) -> None:
        """UI-only age repaint. This does not read the DB or rebuild rows."""
        # Naive-UTC wall-clock now, matching parse_ts() output (DB timestamps are
        # now stored in UTC). The original datetime.now() was local wall-clock,
        # correct only while rows were local; post-UTC-migration a local ref makes
        # ref - dt off by the UTC offset (every recent row collapses to "0s ago"
        # until the next full refresh). Wall-clock (not newest_db_time) is correct
        # here so ages keep ticking up during quiet periods between full refreshes.
        ref = datetime.now(timezone.utc).replace(tzinfo=None)
        with self.soft("refresh_visible_age_labels", log=False):
            if hasattr(self, "activity_tree"):
                for iid, ts in list(getattr(self, "_activity_iid_to_ts", {}).items()):
                    self._tree_set_value(self.activity_tree, iid, 7, fmt_age(ts, ref))
            if hasattr(self, "calls_tree"):
                for iid, ts in list(getattr(self, "_calls_iid_to_ts", {}).items()):
                    self._tree_set_value(self.calls_tree, iid, 1, fmt_age(ts, ref))
            self.refresh_open_qso_popups()

    def age_tick(self) -> None:
        """Refresh displayed Age/HEARD text every 15 seconds without touching the DB."""
        if self._closing:
            return
        try:
            self.refresh_visible_age_labels()
        except Exception:
            debug_exc("age_tick")
        finally:
            if not self._closing:
                self._age_tick_after_id = self.after(15000, self.age_tick)

    def activity_self_refresh_tick(self) -> None:
        """Optional: periodically re-run refresh_data even with no DB/WAL
        change, so rows that have aged out of the selected time window (e.g.
        'Last 15 minutes') drop on a quiet band instead of lingering until the
        next new spot. Off by default (activity_self_refresh_minutes == 0);
        set via Settings > Activity Self-Refresh. Always reschedules itself
        on a fixed 30s check cadence regardless of the configured interval,
        so toggling the setting takes effect without restarting the app.
        """
        if self._closing:
            return
        try:
            minutes = int(getattr(self.cfg, "activity_self_refresh_minutes", 0) or 0)
            if minutes > 0:
                interval = max(30.0, minutes * 60.0)
                if time.time() - float(getattr(self, "_last_self_refresh_started", 0.0) or 0.0) >= interval:
                    if time.time() - float(self._last_auto_refresh_started or 0.0) >= 1.5:
                        self._last_self_refresh_started = time.time()
                        self.refresh_data(probe_api=False, auto=True)
        except Exception:
            debug_exc("activity_self_refresh_tick")
        finally:
            if not self._closing:
                self._self_refresh_after_id = self.after(30000, self.activity_self_refresh_tick)

    def update_db_status(self) -> None:
        if self.locator.spot_db and Path(self.locator.spot_db).exists():
            self.db_status_var.set(f"DB: connected ({Path(self.locator.spot_db).name}) — {self.locator.spot_db}")
        else:
            self.db_status_var.set("DB: not connected")

    def show_db_status(self) -> None:
        """View -> Database Status: report the spot-DB connection and full path,
        and offer to reconnect/refresh. Replaces the old always-on inline label
        in the activity controls row."""
        self.update_db_status()
        status = self.db_status_var.get()
        if messagebox.askyesno(
            "Database Status",
            f"{status}\n\nReconnect / refresh now?",
            parent=self,
        ):
            self.refresh_data()

    def refresh_group_widgets(self) -> None:
        for var in (self.current_group_var, self.group_target_var, self.group_filter_var):
            if var.get() not in self.groups:
                var.set(GROUP_ALL)
        new_values = tuple(self.groups)
        for combo_name in ("group_target_combo", "group_filter_combo"):
            try:
                combo = getattr(self, combo_name)
                # Reconfiguring a ttk.Combobox's values while its dropdown is
                # POSTED rebuilds the open listbox under native Tk and can hard-
                # freeze the app. On a stable group list every auto-refresh calls
                # this with identical values, so skip the no-op reconfigure and
                # only touch the widget when the list actually changed.
                if tuple(combo.cget("values")) == new_values:
                    continue
                combo.configure(values=self.groups)
            except Exception:
                pass

    def _is_hb_row(self, row: ActivityRow) -> bool:
        text = (row.text or "").upper()
        group = (row.group or "").upper()
        to = (row.to_call or "").upper()
        src = (row.source or "").upper()
        return "HEARTBEAT" in text or " HB" in text or group == "@HB" or to == "@HB" or "HB" in src

    def apply_activity_tile_filter(self, render: bool = True) -> None:
        key = self.active_tile_filter or "total"
        rows = list(self.all_activity_rows)
        if key == "heard":
            rows = [r for r in rows if r.category == "heard"]
        elif key == "hearing_me":
            rows = [r for r in rows if r.category == "hearing_me"]
        elif key == "both":
            rows = [r for r in rows if r.category == "both" or "[RSNR:" in (r.text or "").upper()]
        elif key == "hb":
            rows = [r for r in rows if self._is_hb_row(r)]
        elif key == "watched":
            rows = [r for r in rows if r.watched]
        elif key == "watchmsg":
            rows = [r for r in rows if r.watch_hit]
        self.activity_rows = rows
        if render:
            self.render_activity(); self.render_active_callsigns(); self.render_tiles()
            self.set_status(f"Activity filter: {key.upper() if key != 'total' else 'TOTAL / ALL'} — {len(rows)} row(s).")


    def _is_real_msg_row(self, row: ActivityRow) -> bool:
        text = f" {(row.text or '').upper()} "
        if " MSG " not in text:
            return False
        if "MSG ID" in text or "[RSNR" in text or " QUERY MSG" in text or " QUERY MSGS" in text:
            return False
        return True

    def _has_message_for_me(self, row: ActivityRow) -> bool:
        """True only for message indicators that appear to be for my station.

        The old 3.2_2 display treated any MSG-like row as a message flag. That
        caused the Flag column to look busy. For JS8Call-like call activity, the
        flag should mean the station left *me* a message.
        """
        if row.inbox_count:
            return True
        if not self._is_real_msg_row(row):
            return False
        my_call = base_call(self.locator.callsign())
        if my_call and base_call(row.to_call) == my_call:
            return True
        return False

    def _has_response_to_me(self, row: ActivityRow) -> bool:
        """JS8Call-style heard-me indicator.

        Star means the station responded to me or otherwise produced hearing-me
        evidence. It does not mean inbox message.
        """
        text = (row.text or "").upper()
        my_call = base_call(self.locator.callsign())
        if row.category in ("hearing_me", "both"):
            return True
        if my_call and base_call(row.to_call) == my_call:
            return True
        if "[RSNR:" in text:
            return True
        if my_call and f"{my_call} SNR" in text:
            return True
        return False

    def _is_recent_cq_row(self, row: ActivityRow, reference: datetime) -> bool:
        text = f" {(row.text or '').upper()} "
        if " CQ " not in text and not text.strip().startswith("CQ "):
            return False
        dt = parse_ts(row.timestamp)
        if not dt:
            return False
        return 0 <= (reference - dt).total_seconds() <= 300

    def call_activity_flags(self, row: ActivityRow, reference: datetime) -> str:
        """Return JS8Call-manual-compatible symbols for the Flag field.

        ★ = station responded / can hear me
        ☎ = CQ within about 5 minutes
        ⚑ = message left for me
        WATCH = watched-word hit in message text
        * = watched callsign marker when no stronger symbol is present
        """
        flags: list[str] = []
        if self._has_message_for_me(row):
            flags.append("⚑")
        if self._has_response_to_me(row):
            flags.append("★")
        if self._is_recent_cq_row(row, reference):
            flags.append("☎")
        if row.watch_hit:
            flags.append("WATCH")
        if row.watched and not flags:
            flags.append("*")
        return " ".join(flags)

    def active_call_display(self, row: ActivityRow, reference: datetime) -> str:
        """Return the Active Callsigns display value.

        Active Callsigns should stay visually clean. JS8Call-style activity
        symbols belong in the Incoming Activity Flag column, not inside the
        selectable callsign list. The only marker kept here is the trailing
        watched-call * suffix.
        """
        suffix = "*" if row.watched and not str(row.call).endswith("*") else ""
        return f"{row.call}{suffix}"

    def render_activity(self) -> None:
        self.activity_tree.delete(*self.activity_tree.get_children())
        self._activity_iid_to_call = {}
        self._activity_iid_to_ts = {}
        ref = datetime.now(timezone.utc).replace(tzinfo=None)
        selected_base = base_call(self.selected_call)
        _sv = getattr(self, "activity_search_var", None)
        needle = _sv.get().upper().strip() if _sv is not None else ""
        shown = 0
        for i, row in enumerate(self.activity_rows):
            disp = self.call_info.operator_display_text(row)
            if needle:
                hay = " ".join(str(x or "") for x in (row.call, row.to_call, row.group, row.grid, disp)).upper()
                if needle not in hay:
                    continue
            # A pending message takes visual priority over watched-gold (see
            # tag_raise order in configure_tree_tags: "message" is raised
            # ABOVE the watched family). This is recomputed fresh every
            # render_activity() call from live _has_message_for_me() state,
            # so there is nothing "sticky" here -- the row automatically
            # reverts to plain watched-gold the next refresh after the
            # message is picked up (inbox_count back to 0 / row no longer a
            # real pending MSG row). No separate "clear" logic needed.
            has_msg = self._has_message_for_me(row)
            if row.watched:
                tags = ["selected_watched_activity" if row.base == selected_base else "watched_activity"]
                if has_msg:
                    tags.append("message")
            else:
                tags = []
                if has_msg:
                    tags.append("message")
                if row.category == "both":
                    tags.append("mutual")
                elif row.category == "hearing_me":
                    tags.append("hearing_me")
                if row.watch_hit:
                    tags.append("watchmsg")
                if row.lookup_unknown:
                    tags.append("unknown_call")
                if row.base == selected_base:
                    tags.append("selected_call")
            flag = self.call_activity_flags(row, ref)
            iid = f"act_{i}_{row.base}"
            self._activity_iid_to_call[iid] = row.call
            self._activity_iid_to_ts[iid] = str(row.timestamp or "")
            act_count = int(getattr(row, "activity_count", 0) or 0)
            self.activity_tree.insert("", "end", iid=iid, values=(flag, row.call, row.to_call, row.group, row.grid, fmt_freq(row.freq), row.snr, fmt_age(row.timestamp, ref), act_count, disp), tags=tuple(tags))
            shown += 1
        self.apply_active_sort(self.activity_tree)
        if needle:
            self.incoming_count_var.set(f"Incoming: {shown}/{len(self.activity_rows)} '{needle}'")
        else:
            self.incoming_count_var.set(f"Incoming: {len(self.activity_rows)}")
        self.restore_selected_row_highlight()

    def _on_activity_search_changed(self) -> None:
        """Re-render Incoming Activity when the main-screen Word Search box
        changes (mirrors the FastChat popup's live history search)."""
        if hasattr(self, "activity_tree") and hasattr(self, "activity_rows"):
            self.render_activity()

    def render_active_callsigns(self) -> None:
        self.calls_tree.delete(*self.calls_tree.get_children())
        self._calls_iid_to_call = {}
        self._calls_iid_to_ts = {}
        ref = datetime.now(timezone.utc).replace(tzinfo=None)
        by_call: Dict[str, ActivityRow] = {}
        for row in self.activity_rows:
            if not row.call or row.call.startswith("@"):
                continue
            existing = by_call.get(row.base)
            if not existing or row.timestamp > existing.timestamp:
                by_call[row.base] = row
        rows = sorted(by_call.values(), key=lambda r: r.timestamp, reverse=True)
        calls = [CALL_ALL]
        for i, row in enumerate(rows):
            calls.append(row.call)
            tags = []
            # Active Callsigns carries status symbols in the text itself. A
            # pending message takes visual priority over watched-gold here
            # too (same rule, same tag_raise order, as Incoming Activity).
            has_msg = self._has_message_for_me(row)
            if row.watched:
                tags.append("watched_active")
                if has_msg:
                    tags.append("message")
            else:
                if has_msg:
                    tags.append("message")
                if row.category == "both":
                    tags.append("mutual")
                elif row.category == "hearing_me":
                    tags.append("hearing_me")
                if row.watch_hit:
                    tags.append("watchmsg")
                if row.lookup_unknown:
                    tags.append("unknown_call")
            if row.base == base_call(self.selected_call):
                tags.append("selected_watched_active" if row.watched else "selected_call")
            display_call = self.active_call_display(row, ref)
            iid = f"call_{i}_{row.base}"
            self._calls_iid_to_call[iid] = row.call
            self._calls_iid_to_ts[iid] = str(row.timestamp or "")
            self.calls_tree.insert("", "end", iid=iid, values=(display_call, fmt_age(row.timestamp, ref), row.snr), tags=tuple(tags))
        self.apply_active_sort(self.calls_tree)
        self.call_filter_combo.configure(values=calls)
        self.restore_selected_row_highlight()

    def render_tiles(self) -> None:
        counts = {"heard": 0, "hearing_me": 0, "both": 0, "hb": 0, "watched": 0, "watchmsg": 0, "total": 0}
        seen = set()
        for r in self.all_activity_rows:
            if r.watch_hit:
                counts["watchmsg"] += 1
            if r.base in seen:
                continue
            seen.add(r.base)
            counts["total"] += 1
            if r.category == "both" or "[RSNR:" in (r.text or "").upper(): counts["both"] += 1
            elif r.category == "hearing_me": counts["hearing_me"] += 1
            else: counts["heard"] += 1
            if self._is_hb_row(r): counts["hb"] += 1
            if r.watched: counts["watched"] += 1
        self.tile_i_hear_var.set(f"{counts['heard']}\nI HEAR")
        self.tile_hear_me_var.set(f"{counts['hearing_me']}\nHEAR ME")
        self.tile_mutual_var.set(f"{counts['both']}\nMUTUAL")
        self.tile_hb_var.set(f"{counts['hb']}\nRX HB")
        self.tile_watched_var.set(f"{counts['watched']}\nWATCHED")
        self.tile_watchmsg_var.set(f"{counts['watchmsg']}\nWORD")
        self.tile_total_var.set(f"{counts['total']}\nTOTAL")
        self.update_tile_button_styles()

    def set_activity_tile_filter(self, key: str) -> None:
        self.active_tile_filter = key or "total"
        self.apply_activity_tile_filter(render=True)

    def update_activity_columns(self) -> None:
        if not hasattr(self, "activity_tree"):
            return
        columns = ["flag", "from", "to", "group", "grid", "freq", "snr", "age", "activity", "text"]
        visible = [c for c in columns if self.column_visible_vars[c].get()]
        if not visible:
            visible = ["from"]
            self.column_visible_vars["from"].set(True)
        self.activity_tree.configure(displaycolumns=visible)
        self._save_column_visibility()

    # Columns the View menu lets the user toggle (flag/from/group are structural
    # and always shown, so they are neither saved nor restored here).
    _MENU_COLUMNS = ("to", "snr", "grid", "freq", "age", "activity", "text")

    # Menu columns that existed before the Phase-8 "activity" column shipped.
    # Used to safely migrate the legacy list format: a legacy save can only
    # speak to these columns, so a column absent from it (e.g. "activity") is
    # "unknown to that save" -> keep its default, NOT force-hidden.
    _LEGACY_MENU_COLUMNS = ("to", "snr", "grid", "freq", "age", "text")

    def _load_saved_column_visibility(self) -> None:
        """Apply the saved View-menu column toggles (from the layout file) over
        the hardcoded defaults, so choices like Show Freq persist across
        restarts. The layout is already loaded (read at startup) by the time
        this runs.

        Format: prefer the explicit map `activity_column_visibility`
        ({col: bool}), which disambiguates "explicitly hidden" from "column
        didn't exist when this was saved". Fall back to the legacy list
        `activity_visible_columns` (shown iff present) for older layout files --
        but only for the columns that existed in that era, so a newly added
        column (activity) keeps its default instead of being hidden just
        because a pre-Phase-8 save couldn't mention it."""
        try:
            layout = self._layout()
        except Exception:
            return
        explicit = layout.get("activity_column_visibility")
        if isinstance(explicit, dict):
            for col in self._MENU_COLUMNS:
                var = self.column_visible_vars.get(col)
                if var is not None and col in explicit:
                    var.set(bool(explicit[col]))
            return
        saved = layout.get("activity_visible_columns")
        if not isinstance(saved, list):
            return  # no saved state yet -> keep defaults (activity visible)
        saved_set = {str(c).strip().lower() for c in saved}
        for col in self._LEGACY_MENU_COLUMNS:
            var = self.column_visible_vars.get(col)
            if var is not None:
                var.set(col in saved_set)
        # Columns newer than the legacy list (e.g. "activity") are left at their
        # hardcoded default so an update never hides them on first launch.

    def _save_column_visibility(self) -> None:
        """Persist the View-menu column toggles to the layout file so they
        survive a restart. Writes the explicit map (authoritative) and also the
        legacy list (back-compat for anything still reading the old key)."""
        try:
            visibility = {c: bool(self.column_visible_vars[c].get()) for c in self._MENU_COLUMNS}
            visible = [c for c in self._MENU_COLUMNS if visibility[c]]
            d = dict(self._layout())
            d["activity_column_visibility"] = visibility
            d["activity_visible_columns"] = visible
            d["saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._layout_data = d
            write_json(LAYOUT_PATH, d)
        except Exception:
            debug_exc("save_column_visibility")

    # selection/sort
    def _sort_key_func(self, tree: ttk.Treeview, col: str):
        def key_for(item):
            raw = str(tree.set(item, col) or "").strip()
            try:
                return float(raw.replace("dB", ""))
            except Exception:
                pass
            m = re.match(r"^(\d+)\s*([smhd])", raw.lower())
            if m:
                n = int(m.group(1)); mult = {"s":1,"m":60,"h":3600,"d":86400}.get(m.group(2),1)
                return n * mult
            return raw.upper()
        return key_for

    def _do_sort(self, tree: ttk.Treeview, col: str, reverse: bool) -> None:
        key_for = self._sort_key_func(tree, col)
        items = list(tree.get_children(""))
        items.sort(key=key_for, reverse=reverse)
        for idx, item in enumerate(items):
            tree.move(item, "", idx)

    def sort_treeview(self, tree: ttk.Treeview, col: str) -> None:
        reverse_attr = f"_sort_reverse_{id(tree)}_{col}"
        reverse = bool(getattr(self, reverse_attr, False))
        self._do_sort(tree, col, reverse)
        # Remember the order now displayed so background auto-refreshes re-apply
        # it instead of snapping the table back to newest-heard-first.
        if not hasattr(self, "_active_sort"):
            self._active_sort = {}
        self._active_sort[id(tree)] = (col, reverse)
        setattr(self, reverse_attr, not reverse)

    def apply_active_sort(self, tree: ttk.Treeview) -> None:
        state = getattr(self, "_active_sort", {}).get(id(tree)) if hasattr(self, "_active_sort") else None
        if not state:
            return
        col, reverse = state
        try:
            self._do_sort(tree, col, reverse)
        except Exception:
            pass

    def clear_other_selection(self, source: str) -> None:
        if self._syncing_selection:
            return
        self._syncing_selection = True
        try:
            if source != "activity" and hasattr(self, "activity_tree"):
                self.activity_tree.selection_remove(self.activity_tree.selection()); self.activity_tree.focus("")
            if source != "calls" and hasattr(self, "calls_tree"):
                self.calls_tree.selection_remove(self.calls_tree.selection()); self.calls_tree.focus("")
        finally:
            self.after(10, lambda: setattr(self, "_syncing_selection", False))

    def call_from_activity_item(self, item: str) -> str:
        if item in self._activity_iid_to_call:
            return self._activity_iid_to_call[item]
        values = self.activity_tree.item(item, "values")
        return clean_call_display(values[1] if len(values) >= 2 else "")

    def call_from_calls_item(self, item: str) -> str:
        if item in self._calls_iid_to_call:
            return self._calls_iid_to_call[item]
        values = self.calls_tree.item(item, "values")
        return clean_call_display(values[0] if values else "")

    def restore_selected_row_highlight(self) -> None:
        selected_base = base_call(self.selected_call)
        if not selected_base:
            return
        old_guard = self._syncing_selection
        self._syncing_selection = True
        try:
            try:
                for item, call in getattr(self, "_activity_iid_to_call", {}).items():
                    if base_call(call) == selected_base and self.activity_tree.exists(item):
                        self.activity_tree.selection_set(item)
                        self.activity_tree.focus(item)
                        break
            except Exception:
                pass
            try:
                for item, call in getattr(self, "_calls_iid_to_call", {}).items():
                    if base_call(call) == selected_base and self.calls_tree.exists(item):
                        self.calls_tree.selection_set(item)
                        self.calls_tree.focus(item)
                        break
            except Exception:
                pass
        finally:
            self._syncing_selection = old_guard

    def on_activity_tree_select(self, _event=None) -> None:
        if self._syncing_selection: return
        sel = self.activity_tree.selection()
        if not sel: return
        call = self.call_from_activity_item(sel[0])
        if call:
            self.set_selected_call(call, source="activity")

    def on_calls_tree_select(self, _event=None) -> None:
        if self._syncing_selection: return
        sel = self.calls_tree.selection()
        if not sel: return
        call = self.call_from_calls_item(sel[0])
        if call:
            self.set_selected_call(call, source="calls")

    def on_activity_tree_double_click(self, event=None) -> None:
        try:
            item = self.activity_tree.identify_row(event.y) if event is not None else ""
            if item:
                self.activity_tree.selection_set(item); self.activity_tree.focus(item); self.on_activity_tree_select()
        except Exception:
            pass
        self.open_qso_popup()

    def _calls_open_recently(self, call: str, window_ms: int = 400) -> bool:
        """Return True if we already opened the QSO popup for `call` from the
        Active Callsigns tree within the last `window_ms`, then record this
        moment. Used to collapse a fast double-press (or a real double-click
        landing on an already-selected row) into a single open, without relying
        on the popup module's own duplicate-raise guard.
        """
        import time
        now = time.monotonic()
        last_call, last_t = getattr(self, "_calls_last_open", ("", 0.0))
        recent = (call == last_call) and ((now - last_t) * 1000.0 < window_ms)
        self._calls_last_open = (call, now)
        return recent

    def on_calls_tree_single_click(self, event=None) -> None:
        """Two-click-to-open (Option B): a left-click on a row that is ALREADY
        the selected callsign opens its popup; a click on any other row just
        selects it (as before). This fires before Tk updates the selection, so
        self.selected_call still reflects the PRIOR click -- exactly the state
        we compare against to detect a second click on the same row.

        Only region 'cell' rows count: clicks on the heading, separators, or
        empty space are ignored so sorting and blank-area clicks are unaffected.
        Right-click Remove (Button-3) is a separate binding and untouched.
        """
        try:
            if event is None:
                return
            if self.calls_tree.identify_region(event.x, event.y) not in ("cell", "tree"):
                return
            item = self.calls_tree.identify_row(event.y)
            if not item:
                return
            clicked = base_call(self.call_from_calls_item(item))
            if clicked and clicked == base_call(self.selected_call):
                # Second click on the already-selected row -> open.
                if self._calls_open_recently(clicked):
                    return "break"  # de-dupe a fast double-press / double-click
                self.open_qso_popup()
                return "break"
        except Exception:
            debug_exc("on_calls_tree_single_click")
        # Otherwise: let normal selection (<<TreeviewSelect>>) proceed.
        return None

    def on_calls_tree_double_click(self, event=None) -> None:
        try:
            item = self.calls_tree.identify_row(event.y) if event is not None else ""
            if item:
                self.calls_tree.selection_set(item); self.calls_tree.focus(item); self.on_calls_tree_select()
        except Exception:
            pass
        # If the single-click Option-B path already opened this call an instant
        # ago (fast double-click on an already-selected row), don't open twice.
        try:
            call = base_call(self.selected_call)
            if call and self._calls_open_recently(call):
                return
        except Exception:
            pass
        self.open_qso_popup()

    def on_call_filter_selected(self, _event=None) -> None:
        call = self.call_filter_var.get()
        if call == CALL_ALL:
            self.clear_manual_call()
        else:
            self.set_selected_call(call, source="combo")

    def set_selected_call(self, call: str, source: str = "") -> None:
        call = clean_call_display(call)
        if not call or call == CALL_ALL:
            self.selected_call = ""; self.target_title_var.set("No callsign selected"); self.target_meta_var.set(""); self.msg_to_var.set(""); self.reset_builder_message_mode(); self.clear_other_selection(source); self.render_activity(); self.render_active_callsigns(); return
        self.selected_call = call
        self.call_filter_var.set(call)
        self.manual_call_var.set(call)
        self.msg_to_var.set(call)
        self.target_title_var.set(call)
        self.target_meta_var.set(self._selected_meta(call))
        self.reset_builder_message_mode()
        self.clear_other_selection(source)
        self.render_activity(); self.render_active_callsigns()

    def _selected_meta(self, call: str) -> str:
        parts = []
        info = self.reader.fcc_info(call)
        if info:
            name = display_person_name(info.get("name", "")); city = str(info.get("city", "") or "").title(); state = str(info.get("state", "") or "").upper(); klass = str(info.get("class", "") or "")
            if name: parts.append(name)
            if city or state: parts.append(f"{city}, {state}".strip(", "))
            if klass: parts.append(f"Class: {klass}")
        row = next((r for r in self.activity_rows if r.base == base_call(call)), None)
        if row:
            if row.watch_hit: parts.append(f"Watch word: {row.watch_hit}")
            parts.append(f"SNR: {row.snr}")
            if row.grid: parts.append(f"Grid: {row.grid}")
        return "  |  ".join(parts)

    def apply_manual_call(self) -> None:
        call = norm_call(self.manual_call_var.get())
        if call:
            self.set_selected_call(call, source="manual")

    def clear_manual_call(self) -> None:
        self.manual_call_var.set(""); self.call_filter_var.set(CALL_ALL); self.set_selected_call("", source="manual")

    # ── Added (manual) stations — JS8Call-style "Add New Station" ─────────
    # An added call is one the operator types in that is NOT currently in the
    # DB window, so they can store a message for it (or try a relay) before it
    # is ever heard. Added calls live in self.manual_calls (session-only, NOT
    # persisted to config) and are re-merged into the activity rows on every
    # refresh as a placeholder row with no data behind it. The placeholder is
    # superseded automatically once real activity for that call arrives (real
    # row, same base, wins the dedup below and in render_active_callsigns).
    @staticmethod
    def _coerce_call_list(value):
        """Return a clean list of callsigns from any stored form. The config
        backend stores list fields as text, and an earlier bug could persist
        manual_calls as an exploded list of single characters
        (['[','K','E','2','K','N',']'] from list("[KE2KN]")). This repairs all
        of: a real list, a list-of-characters, a repr string "['KE2KN']", a JSON
        array, or a comma/space list -- and de-dups."""
        if not value:
            return []
        tokens = None
        if isinstance(value, (list, tuple, set)):
            raw = [str(x) for x in value]
            # A genuine call list never consists solely of single characters
            # (callsigns are 3+ chars). If every item is one char, the list was
            # an exploded string -> rejoin and re-parse it as a string below.
            if raw and all(len(x.strip()) <= 1 for x in raw):
                value = "".join(raw)
            else:
                tokens = raw
        if tokens is None:  # value is a string (original or rejoined)
            s = str(value).strip()
            tokens = []
            if s:
                try:
                    import json as _json
                    parsed = _json.loads(s)
                    tokens = parsed if isinstance(parsed, list) else [str(parsed)]
                except Exception:
                    tokens = re.split(r"[,\s]+", s.strip("[](){}"))
        out, seen = [], set()
        for t in tokens:
            c = norm_call(str(t).strip().strip("'\""))
            if not c or c in seen:
                continue
            if not c.startswith("@") and len(c) < 3:
                continue  # drop stray brackets/quotes/single chars
            seen.add(c)
            out.append(c)
        return out

    def _merge_manual_calls(self, rows):
        rows = list(rows or [])
        manual = self._coerce_call_list(getattr(self, "manual_calls", None))
        if not manual:
            return rows
        present = {r.base for r in rows}
        # Honor the operator's watched-calls list so an added station that is
        # also watched renders gold, exactly like a real watched row (the reader
        # sets r.watched = r.base in watched_calls()). Recomputed each refresh,
        # so toggling watched status updates the placeholder on the next poll.
        try:
            watched_set = set(self.locator.watched_calls() or [])
        except Exception:
            watched_set = set()
        # Re-stamp placeholders to "now" each refresh so an added-but-silent
        # station reads as a fresh placeholder rather than an aging real row.
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        for raw in manual:
            c = norm_call(raw)
            if not c or c.startswith("@"):
                continue  # groups are handled via the Send Group Target box
            if base_call(c) in present:
                continue  # real (or already-merged) activity exists — leave it
            rows.append(ActivityRow(call=c, source="manual", category="heard",
                                    timestamp=stamp, snr="", text="",
                                    watched=base_call(c) in watched_set))
            present.add(base_call(c))
        return rows

    def open_add_call_popup(self) -> None:
        """JS8Call-style 'Add New Station or Group' dialog: callsign entry plus
        OK / Cancel. The window-close (X) and Escape both cancel; only OK adds."""
        p = self.pal()
        top = tk.Toplevel(self)
        top.title("Add New Station or Group")
        top.configure(bg=p.panel)
        top.transient(self)
        top.resizable(False, False)
        tk.Label(top, text="Station or Group Callsign:", bg=p.panel, fg=p.text,
                 font=self.scaled_font(12, "bold")).pack(anchor="w", padx=14, pady=(14, 4))
        var = tk.StringVar(value="")
        ent = self.entry(top, textvariable=var, width=24)
        ent.pack(fill="x", padx=14, pady=(0, 10))
        btns = tk.Frame(top, bg=p.panel)
        btns.pack(fill="x", padx=14, pady=(0, 14))

        def do_ok(_e=None):
            call = var.get()
            top.destroy()
            self.add_manual_call(call)

        def do_cancel(_e=None):
            top.destroy()

        self.button(btns, "OK", command=do_ok, width=8, good=True).pack(side="right", padx=(6, 0))
        self.button(btns, "Cancel", command=do_cancel, width=8).pack(side="right")
        top.protocol("WM_DELETE_WINDOW", do_cancel)   # X cancels (no add)
        ent.bind("<Return>", do_ok)
        top.bind("<Escape>", do_cancel)
        # Open on the SAME monitor as the main window (centered over it) rather
        # than the OS default, which can land on the other screen of a dual-
        # monitor setup. winfo_rootx/rooty are absolute screen coords, so this
        # follows FastChat to whichever monitor it is on (incl. a left monitor
        # with negative X).
        top.update_idletasks()
        try:
            px, py = self.winfo_rootx(), self.winfo_rooty()
            pw, ph = self.winfo_width(), self.winfo_height()
            w, h = top.winfo_reqwidth(), top.winfo_reqheight()
            top.geometry(f"+{px + max(0, (pw - w) // 2)}+{py + max(0, (ph - h) // 3)}")
        except Exception:
            pass
        try:
            self._raise_popup_to_front(top)
        except Exception:
            pass
        top.grab_set()
        ent.focus_set()

    def add_manual_call(self, call: str) -> None:
        c = norm_call(call)
        if not c:
            return
        # @GROUP: route to the Send Group Target box rather than the station
        # list (groups are not selectable station rows in FastChat).
        if c.startswith("@"):
            with self.soft("add_manual_group"):
                try:
                    vals = list(self.group_target_combo.cget("values") or [])
                    if c not in vals:
                        self.group_target_combo.configure(values=vals + [c])
                except Exception:
                    pass
                self.group_target_var.set(c)
                self.save_current_config()
            self.set_status(f"Send Group Target set to {c}. Use the message builder to store/queue a group message.")
            return
        # Light sanity check: a callsign is alphanumeric (optionally with '/')
        # and contains at least one digit. norm_call has already uppercased and
        # stripped. This stays permissive for international calls.
        if not (re.fullmatch(r"[A-Z0-9/]{3,12}", c) and re.search(r"\d", c)):
            messagebox.showwarning("Add Station",
                                    f"'{call}' does not look like a valid callsign.", parent=self)
            return
        if not getattr(self, "manual_calls", None):
            self.manual_calls = []
        if base_call(c) not in {base_call(x) for x in self.manual_calls}:
            self.manual_calls.append(c)
            # Session-only: do NOT persist. Added stations vanish on relaunch,
            # matching JS8Call's temporary "Add New Station".
        # Inject immediately so the operator sees it without waiting for a poll.
        with self.soft("add_manual_call_render"):
            self.all_activity_rows = self._merge_manual_calls(getattr(self, "all_activity_rows", []) or [])
            self.apply_activity_tile_filter(render=False)
            self.render_tiles()
        # set_selected_call re-renders the activity and callsign views.
        self.set_selected_call(c, source="manual-add")
        # Tag FCC identity (name/city/state) onto the status line; the selected
        # target meta and the Info popup already pull reader.fcc_info(c) too.
        try:
            info = self.reader.fcc_info(c) or {}
        except Exception:
            info = {}
        if info:
            who = display_person_name(info.get("name", "")) or ""
            where = f"{str(info.get('city','') or '').title()}, {str(info.get('state','') or '').upper()}".strip(", ")
            self.set_status(f"Added {c}. {who}{(' — ' + where) if where else ''}".strip())
        else:
            self.set_status(f"Added {c}. No FCC record found (non-US or unlicensed).")

    def remove_manual_call(self, call: str) -> None:
        c = base_call(call)
        if not c:
            return
        self.manual_calls = [x for x in (self.manual_calls or []) if base_call(x) != c]
        # Session-only: nothing to persist on removal either.
        # Drop the placeholder row if it carried no real data.
        self.all_activity_rows = [r for r in (getattr(self, "all_activity_rows", []) or [])
                                  if not (r.base == c and str(getattr(r, "source", "")) == "manual")]
        self.apply_activity_tile_filter(render=False)
        self.render_activity(); self.render_active_callsigns(); self.render_tiles()
        self.set_status(f"Removed added station {call}.")

    def on_calls_tree_right_click(self, event=None) -> None:
        """Right-click an added (manual) station in Active Callsigns to remove it."""
        try:
            item = self.calls_tree.identify_row(event.y) if event is not None else ""
            if not item:
                return
            call = self.call_from_calls_item(item)
            base = base_call(call)
            if base not in {base_call(x) for x in (getattr(self, "manual_calls", []) or [])}:
                return  # only added stations get the remove menu
            has_real = any(r.base == base and str(getattr(r, "source", "")) != "manual"
                           for r in (getattr(self, "all_activity_rows", []) or []))
            menu = tk.Menu(self, tearoff=0)
            label = f"Remove added station {call}" + ("  (now live)" if has_real else "")
            menu.add_command(label=label, command=lambda c=call: self.remove_manual_call(c))
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
        except Exception:
            debug_exc("on_calls_tree_right_click")

    def clear_main_msg_box(self) -> None:
        self.msg_text.delete("1.0", "end"); self.set_status("Message Builder text cleared.")

    # -- FastChat "Store Msg" waiting counter -------------------------------
    # Green outline + count of messages I am holding locally for this callsign,
    # waiting for them to pull with QUERY MSGS / QUERY MSG [ID]. JS8Call flips
    # the message type to DELIVERED on pickup, so this live count decrements
    # itself (3 -> 2) with no pickup detection here. No watermark: this is
    # "held right now", not "unread since I last looked".
    STORE_WAITING_FG = "#4ade80"

    def _confirm_stored_message(self, call: str, before: int) -> None:
        """After asking JS8Call to store a message, verify it actually landed in
        JS8Call's inbox. JS8Call silently ignores API commands it doesn't
        support, so a 'sent OK' is not proof -- the inbox is."""
        with self.soft("confirm stored message"):
            self._refresh_inbox_data()
            after = self.store_waiting_count_for(call)
            self.refresh_inbox_buttons()
            if after > before:
                self.set_status(
                    f"Message stored locally for {call} -- {after} waiting to be picked up (no TX)."
                )
                return
            self.set_status(
                f"JS8Call did not store the message for {call}. It may not support INBOX.STORE_MESSAGE."
            )
            messagebox.showwarning(
                "Store Msg not confirmed",
                "JS8Call accepted the command but no stored message appeared in its inbox.\n\n"
                "This JS8Call build may not support storing messages over the API "
                "(INBOX.STORE_MESSAGE).\n\n"
                "Workaround: store the message from JS8Call directly (right-click the "
                "callsign in JS8Call and use its store-message option). FastChat's green "
                "counter will pick it up either way.",
                parent=self,
            )

    def store_waiting_count_for(self, call: str) -> int:
        with self.soft("store msg waiting count", log=False):
            b = base_call(call)
            if not b:
                return 0
            return self.reader.outbox_waiting_count(call, self.locator.callsign())
        return 0

    def _paint_store_button(self, call: str, btn) -> None:
        with self.soft("paint store button", log=False):
            if btn is None or not btn.winfo_exists():
                return
            p = self.pal()
            n = self.store_waiting_count_for(call)
            if n > 0:
                btn.configure(
                    text=f"Store Msg ({n})",
                    fg=self.STORE_WAITING_FG,
                    highlightthickness=2,
                    highlightbackground=p.green, highlightcolor=p.green,
                )
            else:
                btn.configure(text="Store Msg", fg=p.text, highlightthickness=0)

    def _register_store_button(self, call: str, btn) -> None:
        b = base_call(call)
        if not b:
            return
        d = getattr(self, "_qso_store_buttons", None)
        if d is None:
            d = self._qso_store_buttons = {}
        d.setdefault(b, []).append(btn)

    def _open_outgoing_for_target(self) -> None:
        """Right-click on the main STORE MSG button -> the Outgoing list for the
        current builder target (mirrors the popup button's right-click)."""
        target = self.builder_target_call()
        if not target:
            self.set_status("Select a callsign to see its stored outgoing messages.")
            return
        self.open_inbox_popup(target, view=INBOX_VIEW_OUTGOING)

    def refresh_open_qso_popups(self) -> None:
        """Refresh any open FastChat latest-reply panels after a DB activity update."""
        stale = []
        for call, widget in list(getattr(self, "_qso_latest_widgets", {}).items()):
            try:
                if widget.winfo_exists():
                    FastChatPopup(self).update_latest_widget(call, widget)
                else:
                    stale.append(call)
            except Exception:
                stale.append(call)
        for call in stale:
            self._qso_latest_widgets.pop(call, None)

    def open_qso_popup(self) -> None:
        """Open the FastChat (per-callsign QSO/transmit) popup.

        Phase 2 extraction: the popup itself now lives in
        js8fastchat/ui/fastchat_popup.py (FastChatPopup). This stays as a
        thin delegator so every existing menu/button/keybinding that calls
        self.open_qso_popup keeps working unchanged.
        """
        FastChatPopup(self).open()

    def open_store_msg_popup(self, call: str, qso_msg_widget: tk.Text, parent=None) -> None:
        call = base_call(call); owner = parent or self; p = self.pal()
        win = tk.Toplevel(owner); win.title("Message"); win.geometry(self._popup_geometry(560, 330)); win.transient(owner); win.configure(bg=p.panel)
        tk.Label(win, text=f"Store this message locally for {call}:", bg=p.panel, fg=p.text, font=popup_font("Arial", 14, "normal"), anchor="w").pack(anchor="w", padx=12, pady=(12, 6))
        txt = self.text_box(win, height=9, font=popup_font("Consolas", 17, "bold"))
        txt.pack(fill="both", expand=True, padx=12, pady=(0, 10)); self.attach_uppercase_text(txt)
        existing = qso_msg_widget.get("1.0", "end-1c").strip().upper()
        if existing: txt.insert("1.0", existing)
        btns = tk.Frame(win, bg=p.panel); btns.pack(fill="x", padx=12, pady=(0, 12))
        def stage_store_message():
            body = txt.get("1.0", "end-1c").strip().upper()
            if not body: self.set_status("Store Msg popup is empty."); txt.focus_set(); return
            # Store LOCALLY at our station (JS8Call's own "store message"
            # action, over the API). This does NOT transmit -- the message sits
            # in our JS8Call inbox until `call` sends us QUERY MSGS and pulls it.
            #
            # PREVIOUSLY this staged an on-air frame "<call> MSG <body>" into the
            # send preview, which asks the REMOTE station to store the message.
            # That is the opposite direction, stored nothing on this computer,
            # and is why the Store Msg waiting counter always read 0.
            before = self.store_waiting_count_for(call)
            ok, msg = self.api.store_message(call, body)
            if not ok:
                self.set_status(f"Store Msg failed: {msg}")
                self.themed_error("Store Msg failed", [msg], win)
                return
            clear_parent = getattr(qso_msg_widget, "_mj_clear_text", None)
            if callable(clear_parent):
                clear_parent()
            else:
                qso_msg_widget.delete("1.0", "end"); getattr(qso_msg_widget, "_mj_update_title", lambda: None)()
            self.set_status(f"Stored message locally for {call} (no TX). Confirming...")
            win.destroy()
            # JS8Call ignores unsupported API commands silently, so confirm the
            # message actually landed in its inbox rather than trusting the send.
            self.after(900, lambda: self._confirm_stored_message(call, before))

        tk.Button(btns, text="OK", command=stage_store_message, bg=p.button, fg=p.text, font=popup_font("Arial", 12, "bold"), width=8).pack(side="right", padx=(6, 0))
        tk.Button(btns, text="Cancel", command=win.destroy, bg=p.button, fg=p.text, font=popup_font("Arial", 12, "bold"), width=8).pack(side="right")
        txt.focus_set()

    def open_info_popup(self, parent=None) -> None:
        if not self.selected_call: messagebox.showinfo("Operator Info", "No callsign selected.", parent=self); return
        if self._info_popup is not None and self._info_popup.winfo_exists():
            self._info_popup.deiconify(); self._info_popup.lift(); self._info_popup.focus_force(); return
        call = self.selected_call; p = self.pal()
        # This popup is laid out with place() at FIXED pixel coordinates that
        # were authored against a fixed 390x300 window. Once popup_font started
        # honouring UI Scale the text grew while those coordinates did not, so
        # at 115% the content crowded the OK button and a long name or city ran
        # off the right edge. Every coordinate now goes through _s(), the SAME
        # factor popup_font uses, and the final size is measured from the built
        # labels rather than assumed -- which also fixes long names at 100%.
        _sf = popup_scale()
        def _s(v: int) -> int:
            return int(round(v * _sf))
        top = tk.Toplevel(parent or self); top.title("Operator Info"); top.configure(bg=p.panel)
        self._info_popup = top

        def _forget_info(_e, t=top):
            if _e.widget is t and self._info_popup is t:
                self._info_popup = None
        top.bind("<Destroy>", _forget_info, add="+")
        tk.Label(top, text="i", bg="#179ce8", fg="#ffffff", font=popup_font("Arial", 16, "bold"), width=2).place(x=_s(14), y=_s(34))
        _call_lbl = tk.Label(top, text=call, bg=p.panel, fg=p.text, font=popup_font("Arial", 21, "bold"), anchor="w")
        _call_lbl.place(x=_s(58), y=_s(36))
        info = self.reader.fcc_info(call); row = next((r for r in self.activity_rows if r.base == base_call(call)), None); lines = []
        if info:
            name = display_person_name(info.get("name", "")); city = str(info.get("city", "") or "").title(); state = str(info.get("state", "") or "").upper(); klass = str(info.get("class", "") or "")
            if name: lines.append(f"Name: {name}")
            if city or state: lines.append(f"Location: {city}, {state}".strip(", "))
            if klass: lines.append(f"Class: {klass}")
        if row:
            lines.extend(["", f"LAST HEARD: {fmt_age(row.timestamp, datetime.now(timezone.utc).replace(tzinfo=None))}", f"Last SNR: {row.snr}", f"Grid: {row.grid or ''}", f"Inbox flags: {row.inbox_count}", f"Relay paths: {row.relay_count}"])
        else:
            lines.extend(["", "LAST HEARD: unknown", "Last SNR:", "Grid:", "Inbox flags: 0", "Relay paths: 0"])
        _body = tk.Label(top, text="\n".join(lines), bg=p.panel, fg=p.text, font=popup_font("Arial", 14, "bold"), justify="left", anchor="nw")
        _body.place(x=_s(58), y=_s(76))
        _ok = tk.Button(top, text="OK", command=top.destroy, bg=p.button, fg=p.text, font=popup_font("Arial", 14, "bold"), width=9)
        # Measure what the labels actually need, then size the window to hold
        # them. Never smaller than the original 390x300 (scaled), so the window
        # keeps its familiar proportions for a short callsign.
        try:
            top.update_idletasks()
            _right = max(_call_lbl.winfo_reqwidth(), _body.winfo_reqwidth())
            _w = max(_s(390), _s(58) + _right + _s(24))
            _h = max(_s(300), _s(76) + _body.winfo_reqheight() + _ok.winfo_reqheight() + _s(28))
        except Exception:
            debug_exc("open_info_popup measure")
            _w, _h = _s(390), _s(300)
        top.geometry(self._popup_geometry(_w, _h))
        try:
            _ok.place(x=_w - _ok.winfo_reqwidth() - _s(20), y=_h - _ok.winfo_reqheight() - _s(14))
        except Exception:
            _ok.place(x=_s(250), y=_s(252))

    def snr_report_value(self, text) -> Optional[int]:
        """Return the dB value of an incoming SNR REPORT, else None.

        A reply reaches the DB twice, ~30s apart, in two formats: the tagged
        "KW3KW SNR +03 [RSNR:+03]" lands at once, the plain "K4BDL: KW3KW SNR
        +03" only on JS8Map's next poll. Both yield the same value here, so
        callers can dedupe on the VALUE and render whichever arrived first.
        Queries (SNR?) and heartbeats are NOT reports and return None.
        """
        up = str(text or "").upper().strip()
        if "SNR?" in up or "HEARTBEAT" in up:
            return None
        m = (re.search(r"\[RSNR:\s*([+\-]?\d+)", up)
             or re.search(r"\bSNR\s*([+\-]?\d+)", up))
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None

    def is_history_clutter(self, row: ActivityRow) -> bool:
        text = (row.text or "").upper().strip()
        return bool(text == "SPOT" or text.startswith("SPOT ") or "HEARTBEAT" in text or "SNR?" in text or re.search(r"\bSNR\s*[+\-]?\d+", text) or "[RSNR:" in text)

    # -- Themed modal dialogs ------------------------------------------------
    # Tk's stock messagebox draws a light, system-styled window with OS icons,
    # which looks nothing like the rest of FastChat's dark theme. These are
    # drop-in replacements built from the same palette and button helper the
    # app uses everywhere else.
    def _themed_dialog(self, title: str, lines, parent=None, *, kind: str = "info",
                       ok_text: str = "OK", cancel_text: str = "", danger: bool = False) -> bool:
        parent = parent or self
        p = self.pal()
        win = tk.Toplevel(parent)
        win.title(title)
        win.configure(bg=p.panel)
        win.transient(parent)
        win.resizable(False, False)

        wrap = tk.Frame(win, bg=p.panel)
        wrap.pack(fill="both", expand=True, padx=18, pady=16)

        accent = {"warn": p.danger, "error": p.danger, "info": p.text}.get(kind, p.text)
        tk.Label(wrap, text=title, bg=p.panel, fg=accent,
                 font=popup_font("Arial", 15, "bold"), anchor="w").pack(fill="x", pady=(0, 10))

        if isinstance(lines, str):
            lines = [lines]
        for i, line in enumerate(lines):
            if not str(line).strip():
                tk.Frame(wrap, bg=p.panel, height=6).pack(fill="x")
                continue
            # First line carries the weight; the rest are supporting detail.
            tk.Label(wrap, text=str(line), bg=p.panel, fg=p.text,
                     font=popup_font("Arial", 12, "bold" if i == 0 else "normal"),
                     justify="left", anchor="w", wraplength=popup_wrap(520)).pack(fill="x", pady=1)

        btns = tk.Frame(wrap, bg=p.panel)
        btns.pack(fill="x", pady=(16, 0))
        result = {"ok": False}

        def say_yes():
            result["ok"] = True
            win.destroy()

        def say_no():
            result["ok"] = False
            win.destroy()

        if cancel_text:
            self.button(btns, cancel_text, command=say_no, width=10).pack(side="right", padx=(6, 0))
        self.button(btns, ok_text, command=say_yes, width=10,
                    danger=danger).pack(side="right", padx=(6, 0))

        win.protocol("WM_DELETE_WINDOW", say_no)
        win.bind("<Escape>", lambda _e: say_no())
        win.bind("<Return>", lambda _e: say_yes())
        win.update_idletasks()
        try:
            # Center on the parent window.
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
            w, h = win.winfo_width(), win.winfo_height()
            win.geometry(f"+{px + max(0, (pw - w) // 2)}+{py + max(0, (ph - h) // 3)}")
        except Exception:
            pass
        win.grab_set()
        win.focus_set()
        parent.wait_window(win)
        return result["ok"]

    def themed_confirm(self, title: str, lines, parent=None, *, ok_text: str = "Yes",
                       danger: bool = True) -> bool:
        return self._themed_dialog(title, lines, parent, kind="warn",
                                   ok_text=ok_text, cancel_text="Cancel", danger=danger)

    def themed_info(self, title: str, lines, parent=None) -> None:
        self._themed_dialog(title, lines, parent, kind="info", ok_text="OK")

    def themed_error(self, title: str, lines, parent=None) -> None:
        self._themed_dialog(title, lines, parent, kind="error", ok_text="OK")

    # diagnostics/settings/layout
    def show_db_locator(self) -> None:
        messagebox.showinfo("DB Locator", "\n".join(self.locator.summary_lines()), parent=self)

    def show_diagnostics(self) -> None:
        p = self.pal(); top = tk.Toplevel(self); top.title("DB Diagnostics"); top.geometry(self._popup_geometry(900, 620))
        txt = tk.Text(top, bg=p.entry_bg, fg=p.entry_fg, font=popup_font("Consolas", 10), wrap="none")
        txt.pack(fill="both", expand=True); txt.insert("1.0", self.reader.diagnostics()); txt.configure(state="disabled")

    def browse_map_config(self) -> None:
        path = filedialog.askopenfilename(title="Select ham_map_config.json", filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if path: self.cfg.map_config_path = path; self.refresh_data(False)
    def browse_js8call_ini(self) -> None:
        path = filedialog.askopenfilename(title="Select JS8Call.ini", filetypes=[("INI", "*.ini"), ("All files", "*.*")])
        if not path:
            return
        if not _looks_like_js8call_ini(Path(path)):
            proceed = messagebox.askyesno(
                "Set JS8Call.ini Location",
                f"This file doesn't look like a JS8Call.ini (no [Configuration]/Frequencies section found):\n\n{path}\n\nUse it anyway?",
                parent=self,
            )
            if not proceed:
                return
        self.cfg.js8call_ini_path = path
        self.save_current_config()
        self.refresh_saved_frequencies()
        self.set_status(f"JS8Call.ini location set: {path}")
    def clear_js8call_ini_override(self) -> None:
        self.cfg.js8call_ini_path = ""
        self.save_current_config()
        self.refresh_saved_frequencies()
        self.set_status("JS8Call.ini location reset to auto-discovery.")
    def browse_spot_db(self) -> None:
        path = filedialog.askopenfilename(title="Select js8_spots.db", filetypes=[("SQLite DB", "*.db"), ("All files", "*.*")])
        if path: self.cfg.spot_db_path = path; self.refresh_data(False)
    def browse_relay_db(self) -> None:
        path = filedialog.askopenfilename(title="Select js8_relay.db", filetypes=[("SQLite DB", "*.db"), ("All files", "*.*")])
        if path: self.cfg.relay_db_path = path; self.refresh_data(False)
    def browse_fcc_db(self) -> None:
        path = filedialog.askopenfilename(title="Select FCC ham.db", filetypes=[("SQLite DB", "*.db"), ("All files", "*.*")])
        if path: self.cfg.fcc_db_path = path; self.refresh_data(False)
    def browse_canadian_db(self) -> None:
        path = filedialog.askopenfilename(title="Select Canadian callsign DB", filetypes=[("SQLite DB", "*.db"), ("All files", "*.*")])
        if path: self.cfg.canadian_db_path = path; self.refresh_data(False)

    def open_watch_words_popup(self) -> None:
        """Message Watch Words editor.

        A LIST, not a free-text pane: type a word and press Add (or Enter),
        highlight one or more and press Delete Selected.  The list on screen is
        the only state while the window is open -- nothing is written until
        Save, so Delete needs no confirmation dialog: Cancel undoes everything.
        Blank lines, stray spaces, lowercase and duplicates become impossible
        by construction, which is what the old free-text box could not promise.

        Matching (utils.match_watch_words) is WHOLE-WORD: the term is wrapped in
        spaces before the lookup, so GAS does not fire on GASKET.  A term may
        contain '+' to mean AND (NUCLEAR+ALERT fires only if both words appear).

        Reopening just raises the existing window.  Two copies of this dialog
        would each hold their OWN working list, and whichever was saved last
        would silently overwrite the other -- exactly the class of quiet data
        loss this list is meant to end.  Same guard as open_macros_popup.
        """
        existing = getattr(self, "_watch_words_popup", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    self._raise_popup_to_front(existing)
                    return
            except Exception:
                pass

        p = self.pal()
        top = tk.Toplevel(self); top.title("Message Watch Words"); top.configure(bg=p.panel)
        top.geometry(self._popup_geometry(600, 580)); top.transient(self)
        self._watch_words_popup = top

        def _forget_popup(_e=None) -> None:
            if getattr(_e, "widget", None) is top or _e is None:
                self._watch_words_popup = None
        top.bind("<Destroy>", _forget_popup)

        tk.Label(
            top,
            text="Words and phrases to watch for in incoming messages. A match gets a WATCH flag and appears under the WORD tile.",
            bg=p.panel, fg=p.text, font=self.scaled_font(11, "bold"),
            wraplength=self.wrap(560), justify="left", anchor="w",
        ).pack(fill="x", padx=12, pady=(12, 2))
        # The two rules people actually get wrong -- so this line is GOLD, the
        # same accent the app uses for a watched station, and it is set at the
        # same size and weight as the heading above it. It was previously grey
        # and a size smaller, which reads as fine print: exactly the styling an
        # operator's eye skips, and then they wonder why GAS never fired.
        tk.Label(
            top,
            text="Whole words only -- GAS will not match GASKET. Use + for AND: NUCLEAR+ALERT flags a message only if it contains both.",
            bg=p.panel, fg=p.gold, font=self.scaled_font(11, "bold"),
            wraplength=self.wrap(560), justify="left", anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 8))

        # ---- working copy: cleaned + sorted, so the list is always tidy -----
        words: list[str] = []
        for w in (self.cfg.msg_watch_words or DEFAULT_MSG_WATCH_WORDS):
            item = str(w).strip().upper()
            if item and not item.startswith("#") and item not in words:
                words.append(item)
        words.sort()

        def _terms(term: str) -> list[str]:
            # Mirror utils.normalize_watch_term's alphabet so the redundancy
            # check below agrees with what match_watch_words actually does.
            return re.sub(r"[^A-Z0-9+]+", " ", str(term or "").upper()).split()

        def _redundant_because(term: str) -> str:
            """An entry that already covers `term`, or "".

            If a shorter existing entry is a contiguous sub-phrase of this one,
            every message matching this term already matches that one, so this
            entry can never add coverage.  (GAS makes GAS STATIONS pointless.)
            """
            if "+" in term:
                return ""
            t = _terms(term)
            if not t:
                return ""
            for other in words:
                if other == term or "+" in other:
                    continue
                o = _terms(other)
                if not o or len(o) >= len(t):
                    continue
                for i in range(len(t) - len(o) + 1):
                    if t[i:i + len(o)] == o:
                        return other
            return ""

        # ---- add row -------------------------------------------------------
        addrow = tk.Frame(top, bg=p.panel); addrow.pack(fill="x", padx=12, pady=(0, 8))
        new_var = tk.StringVar()
        ent = self.entry(addrow, textvariable=new_var, width=30)
        ent.pack(side="left", fill="x", expand=True, ipady=3)
        self.attach_uppercase_var(new_var)
        add_btn = self.button(addrow, "Add", command=lambda: add_word(), width=8, good=True)
        add_btn.pack(side="left", padx=(8, 0))

        # ---- the list ------------------------------------------------------
        box = tk.Frame(top, bg=p.panel); box.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        vsb = tk.Scrollbar(box, orient="vertical"); vsb.pack(side="right", fill="y")
        lst = tk.Listbox(
            # Arial, like every other font in this dialog. It used to be Consolas,
            # which is a monospaced font -- worth it when characters must line up
            # in columns, which watch words never do. All it did here was make the
            # list look like it belonged to a different program, and monospace is
            # the harder face to read at a glance.
            box, bg=p.entry_bg, fg=p.entry_fg, font=self.scaled_font(12, "bold"),
            relief="solid", bd=1, activestyle="none", selectmode="extended",
            selectbackground=p.selected, selectforeground=p.entry_fg,
            height=14, yscrollcommand=vsb.set,
        )
        lst.pack(side="left", fill="both", expand=True)
        vsb.configure(command=lst.yview)

        # ---- delete row + live feedback ------------------------------------
        midrow = tk.Frame(top, bg=p.panel); midrow.pack(fill="x", padx=12, pady=(0, 4))
        del_btn = self.button(midrow, "Delete Selected", command=lambda: delete_selected(), width=15, danger=True)
        del_btn.pack(side="left")
        count_var = tk.StringVar(value="")
        tk.Label(midrow, textvariable=count_var, bg=p.panel, fg=p.text,
                 font=self.scaled_font(11, "bold"), anchor="e").pack(side="right")
        note_var = tk.StringVar(value="")
        tk.Label(top, textvariable=note_var, bg=p.panel, fg="#ffb347",
                 font=self.scaled_font(11, "bold"), anchor="w", wraplength=self.wrap(560),
                 justify="left").pack(fill="x", padx=12, pady=(0, 4))

        def note(text: str) -> None:
            note_var.set(text)
            with self.soft("watch-word note clear", log=False):
                top.after(6000, lambda: note_var.set("") if note_var.get() == text else None)

        def repaint(select: str = "") -> None:
            lst.delete(0, "end")
            for item in words:
                lst.insert("end", item)
            count_var.set(f"{len(words)} watch word{'' if len(words) == 1 else 's'}")
            if select and select in words:
                i = words.index(select)
                lst.selection_clear(0, "end"); lst.selection_set(i); lst.see(i)

        def add_word(_e=None) -> None:
            # Commas split, so pasting "FLOOD, EVACUATE, SHELTER" adds three.
            added: list[str] = []; dupes: list[str] = []
            for part in str(new_var.get() or "").split(","):
                item = part.strip().upper()
                if not item or item.startswith("#"):
                    continue
                if item in words:
                    dupes.append(item)
                else:
                    words.append(item); added.append(item)
            if not added and not dupes:
                note("Type a word or phrase, then press Add."); ent.focus_set(); return
            words.sort(); new_var.set("")
            repaint(added[-1] if added else dupes[-1])
            covered = ""
            if len(added) == 1:
                covered = _redundant_because(added[0])
            if covered:
                note(f"Added {added[0]}, but {covered} already catches it -- this entry will never add anything.")
            elif added and dupes:
                note(f"Added {len(added)}; {len(dupes)} already in the list.")
            elif added:
                note(f"Added {', '.join(added)}." if len(added) <= 3 else f"Added {len(added)} words.")
            else:
                note(f"{dupes[0]} is already in the list." if len(dupes) == 1 else f"{len(dupes)} already in the list.")
            ent.focus_set()

        def delete_selected(_e=None) -> None:
            sel = list(lst.curselection())
            if not sel:
                note("Highlight one or more words in the list first."); return
            gone = [words[i] for i in sel if 0 <= i < len(words)]
            for item in gone:
                if item in words:
                    words.remove(item)
            repaint()
            note(f"Removed {gone[0]}. Press Cancel to undo." if len(gone) == 1
                 else f"Removed {len(gone)} words. Press Cancel to undo.")

        def save_words() -> None:
            out = list(words)
            self.cfg.msg_watch_words = out
            self.save_current_config()
            self.activity_service.reader.msg_watch_words = out
            self.refresh_data(False)
            top.destroy()
            self.set_status(f"Saved {len(out)} message watch word(s).")

        btns = tk.Frame(top, bg=p.panel); btns.pack(fill="x", padx=12, pady=(0, 12))
        self.button(btns, "Save", command=save_words, width=10, good=True).pack(side="right", padx=(6, 0))
        self.button(btns, "Cancel", command=top.destroy, width=10).pack(side="right")

        ent.bind("<Return>", add_word)
        lst.bind("<Delete>", delete_selected)
        top.bind("<Escape>", lambda _e: top.destroy())

        with self.soft("watch-word tooltips", log=False):
            self._attach_tooltip(ent, "Type a word or phrase to watch for, then press Add (or just press Enter). Separate several with commas. Use + for AND: NUCLEAR+ALERT.")
            self._attach_tooltip(add_btn, "Add the typed word or phrase to the list. Duplicates are ignored.")
            self._attach_tooltip(del_btn, "Remove the highlighted word(s). Ctrl-click or Shift-click to pick several. Nothing is saved until you press Save, so Cancel undoes it.")
            self._attach_tooltip(lst, "The words being watched. Click one (or Ctrl-click several) then press Delete Selected to remove them.")

        repaint()
        ent.focus_set()

    def open_debug_log(self) -> None:
        try:
            DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
            if not DEBUG_LOG.exists():
                DEBUG_LOG.write_text("", encoding="utf-8")
            if sys.platform.startswith("win"):
                os.startfile(DEBUG_LOG)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                os.system(f"open {str(DEBUG_LOG)!r}")
            else:
                os.system(f"xdg-open {str(DEBUG_LOG)!r}")
        except Exception as e:
            messagebox.showwarning("Open Debug Log", f"Debug log path:\n{DEBUG_LOG}\n\n{e}", parent=self)

    def open_app_folder(self) -> None:
        try:
            if sys.platform.startswith("win"): os.startfile(Path(__file__).resolve().parents[2])  # type: ignore[attr-defined]
            elif sys.platform == "darwin": os.system(f"open {str(Path(__file__).resolve().parents[2])!r}")
            else: os.system(f"xdg-open {str(Path(__file__).resolve().parents[2])!r}")
        except Exception as e:
            messagebox.showwarning("Open App Folder", str(e), parent=self)

    def show_about(self) -> None:
        messagebox.showinfo("About", f"{APP_TITLE}\nBuild: {APP_BUILD}\n\n3.3 group-activity feature branch. UI opens first; DB/API load in background. TX Armed and Confirm TX remain the shared safety path.", parent=self)

    def update_utc_time(self) -> None:
        # Stored the id so on_close can cancel it; previously this rescheduled
        # itself forever with no handle, so it could not be stopped at all.
        if getattr(self, "_closing", False):
            return
        try:
            self.utc_time_var.set(datetime.now(timezone.utc).strftime("%H:%M:%S"))
            self._utc_after_id = self.after(1000, self.update_utc_time)
        except Exception:
            pass

    def _set_clear_watermark(self, ts) -> None:
        """Set (or clear with None) the QSY/clear watermark on every reader
        reference we hold, so whichever one the activity worker uses has it.
        Defensive: a missing attribute on any reference is ignored.
        """
        for holder in (
            getattr(self, "reader", None),
            getattr(getattr(self, "core", None), "reader", None),
            getattr(getattr(self, "activity_service", None), "reader", None),
        ):
            if holder is not None:
                try:
                    holder.set_clear_watermark(ts)
                except Exception:
                    pass

    def clear_display(self) -> None:
        # Watermark 'now' (UTC, in the DB timestamp format) so the refresh below
        # -- and every later auto-refresh -- excludes stations JS8Call keeps
        # re-reporting from BEFORE this clear (the ones left over from the old
        # frequency after a QSY). Only decodes AFTER this moment reappear, so the
        # Incoming Activity list shows just the new frequency's stations. The
        # watermark persists until the next Clear Activity; a genuinely new
        # decode always has a later timestamp, so live traffic is never blocked.
        try:
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            self._set_clear_watermark(stamp)
        except Exception:
            pass
        self.all_activity_rows = self._merge_manual_calls([]); self.activity_rows = list(self.all_activity_rows); self.render_activity(); self.render_active_callsigns(); self.render_tiles()
        # Ask the service for a fresh read so the watermark takes effect right
        # away rather than only on the next auto-tick.
        try:
            self.refresh_data(False)
        except Exception:
            pass
        self.set_status("Display cleared for QSY — showing only stations heard after this point. Added stations kept; no DB records were deleted.")

    def on_ui_scale_changed(self) -> None:
        set_popup_ui_scale(UI_SCALE_PRESETS.get(self.ui_scale_var.get(), 1.0))
        self.save_current_config(); self.rebuild_visible_ui("UI Scale")
    def on_theme_changed(self) -> None:
        self.save_current_config(); self.rebuild_visible_ui("Theme")

    def rebuild_visible_ui(self, reason: str) -> None:
        old_status = self.status_var.get()
        # A theme/scale rebuild destroys all child windows (popups are children
        # of the main window, so they close too). Remember the stateless Saved
        # Macros popup and reopen it in the new theme. Add Station is left to
        # close on purpose — it holds a half-typed callsign we won't silently
        # drop by reopening blank.
        try:
            macros_was_open = bool(getattr(self, "_macros_popup", None)) and self._macros_popup.winfo_exists()
        except Exception:
            macros_was_open = False
        self.save_layout_now(force=True)
        self._rebuilding = True
        self._layout_save_enabled = False
        try:
            for child in list(self.winfo_children()):
                child.destroy()
            self._build_menu()
            self._build_ui()
            self.apply_theme()
            self.render_activity()
            self.render_active_callsigns()
            self.render_tiles()
            self.after(250, lambda: self.apply_saved_layout_positions(enable_save=False))
            self.after(650, lambda: self.apply_saved_layout_positions(enable_save=True))
            self.set_status(f"{reason} applied live. {old_status}")
            if macros_was_open:
                try:
                    self.open_macros_popup()
                except Exception:
                    pass
        finally:
            self._rebuilding = False

    def save_current_config(self) -> None:
        self.cfg.host = self.host_var.get().strip() or "127.0.0.1"
        self.cfg.port = safe_int(self.port_var.get(), 2442)
        self.cfg.ui_scale = self.ui_scale_var.get()
        self.cfg.theme = self.theme_var.get()
        self.cfg.api_mode = self.api_mode_var.get()
        self.cfg.confirm_tx = self.confirm_tx_var.get()
        self.cfg.tx_armed = self.tx_armed_var.get()
        self.cfg.log_observed_tx = self.log_observed_tx_var.get()
        self.cfg.activity_self_refresh_minutes = safe_int(self.activity_self_refresh_var.get(), 0)
        self.cfg.db_time = self.db_time_var.get()
        self.cfg.current_group = self.current_group_var.get()
        self.cfg.group_target = self.group_target_var.get()
        self.cfg.target_call_filter = self.call_filter_var.get()
        self.cfg.spot_db_path = self.locator.spot_db or self.cfg.spot_db_path
        self.cfg.relay_db_path = self.locator.relay_db or self.cfg.relay_db_path
        self.cfg.fcc_db_path = self.locator.fcc_db or self.cfg.fcc_db_path
        self.cfg.canadian_db_path = self.locator.canadian_db or self.cfg.canadian_db_path
        self.cfg.map_config_path = self.locator.map_config_path or self.cfg.map_config_path
        # Added stations are session-only and never written back. Force the
        # stored value empty so any already-corrupted manual_calls in an
        # existing config file (e.g. the exploded ['[','K','E','2','K','N',']')
        # self-cleans on the next save. _coerce_call_list stays as the in-merge
        # guard per the standing note.
        self.cfg.manual_calls = []
        save_config(self.cfg)

    def _layout(self) -> dict:
        if not isinstance(getattr(self, "_layout_data", None), dict):
            self._layout_data = read_json(LAYOUT_PATH)
        return self._layout_data

    def get_layout_value(self, key: str, default=None):
        """Read a single value from the persisted layout dict (safe)."""
        try:
            return self._layout().get(key, default)
        except Exception:
            return default

    def set_layout_value(self, key: str, value) -> None:
        """Persist a single value into the layout dict (safe). Used for popup
        sub-layout state like the compose-box height, so it survives close/reopen
        and callsign switches."""
        try:
            d = dict(self._layout())
            d[key] = value
            d["saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._layout_data = d
            write_json(LAYOUT_PATH, d)
        except Exception:
            debug_exc(f"set_layout_value {key}")

    def restore_layout(self) -> None:
        """Restore saved main geometry/state, then restore panes after Tk lays out.

        Layout saving is disabled until the delayed restore has completed.
        Without that guard, normal startup <Configure> events can overwrite the
        saved pane positions with default positions before they are restored.
        """
        self._layout_save_enabled = False
        data = self._layout()
        geom = str(data.get("main_geometry", "") or "")
        if _is_geometry(geom):
            try:
                self.geometry(geom)
            except Exception:
                debug_exc("restore_layout geometry")
        self.bind("<Configure>", lambda e: self.schedule_layout_save(e), add="+")
        # One pass is sometimes too early on Windows/Tk.  Do an early restore,
        # then a final restore after the widgets have fully painted and enable
        # layout saves only after that second pass.
        self.after(300, lambda: self.apply_saved_layout_positions(enable_save=False))
        self.after(850, lambda: self.apply_saved_layout_positions(enable_save=True))
        state = str(data.get("main_state", "") or "")
        if state == "zoomed":
            try:
                self.after(900, lambda: self.state("zoomed"))
            except Exception:
                pass

    def apply_saved_layout_positions(self, enable_save: bool = True) -> None:
        """Restore pane divider and Treeview column widths after Tk finishes layout."""
        data = self._layout()
        self._layout_restore_active = True
        try:
            try:
                self.update_idletasks()
            except Exception:
                pass
            sashes = data.get("main_sashes") or []
            if sashes and hasattr(self, "body_panes"):
                width = max(1, int(self.body_panes.winfo_width()))
                pane_count = len(self.body_panes.panes())
                xs = [int(pair[0]) for pair in sashes[:max(0, pane_count - 1)]]
                # Sash positions are saved in PIXELS at whatever UI Scale was
                # in force. Restoring them verbatim after a scale change hands
                # the right panel its old width back and re-clips it, undoing
                # the scaled widths set in _build_body. Widen the panels that
                # need it and leave a layout that already fits untouched.
                if len(xs) == 2:
                    _sf = self.ui_scale_factor()
                    # The sash occupies real width BETWEEN panes, so a target
                    # computed without it lands the panel short by exactly
                    # sashwidth -- measured at 487 against a needed 494.
                    _sw = int(self.body_panes.cget("sashwidth") or 0)
                    _left_min = int(round(285 * _sf))
                    _center_min = int(round(520 * _sf))
                    _right_min = int(round(430 * _sf))
                    xs[1] = min(xs[1], width - _right_min - _sw)
                    xs[0] = max(_left_min, min(xs[0], xs[1] - _center_min - _sw))
                    xs[1] = max(xs[1], xs[0] + _center_min + _sw)
                # RIGHTMOST FIRST. Placing left-to-right lets an early sash
                # shove the panes right of it and the later placement then
                # measures against a layout that has already moved.
                for i, pair in reversed(list(enumerate(sashes[:max(0, pane_count - 1)]))):
                    try:
                        x = xs[i]
                        y = int(pair[1]) if len(pair) > 1 else 1
                        # Keep the sash on-screen if monitor/DPI changed, but
                        # otherwise respect the saved operator layout exactly.
                        x = max(80, min(width - 80, x))
                        self.body_panes.sash_place(i, x, y)
                    except Exception:
                        debug_exc(f"apply_saved_layout_positions sash {i}")
            self.restore_tree_column_widths("activity_columns", getattr(self, "activity_tree", None), data=data)
            self.restore_tree_column_widths("calls_columns", getattr(self, "calls_tree", None), data=data)
        except Exception:
            debug_exc("apply_saved_layout_positions")
        finally:
            self._layout_restore_active = False
        self.bind_layout_memory_events()
        if enable_save:
            self._layout_save_enabled = True

    def restore_tree_column_widths(self, key: str, tree, data: Optional[dict] = None) -> None:
        if tree is None:
            return
        data = data if isinstance(data, dict) else self._layout()
        widths = data.get(key) or {}
        if not isinstance(widths, dict):
            return
        for col, width in widths.items():
            try:
                tree.column(col, width=max(25, int(width)))
            except Exception:
                debug_exc(f"restore_tree_column_widths {key}.{col}")

    def bind_layout_memory_events(self) -> None:
        def save_soon(_event=None):
            if not getattr(self, "_closing", False):
                self.after(120, lambda: self.save_layout_now(force=True))
        try:
            if hasattr(self, "body_panes"):
                self.body_panes.bind("<ButtonRelease-1>", save_soon, add="+")
                self.body_panes.bind("<Configure>", lambda e: self.schedule_layout_save(e, force=True), add="+")
        except Exception:
            debug_exc("bind_layout_memory_events body_panes")
        for tree_name in ("activity_tree", "calls_tree"):
            try:
                tree = getattr(self, tree_name)
                tree.bind("<ButtonRelease-1>", save_soon, add="+")
                tree.bind("<Configure>", lambda e: self.schedule_layout_save(e, force=True), add="+")
            except Exception:
                debug_exc(f"bind_layout_memory_events {tree_name}")

    def schedule_layout_save(self, event=None, force: bool = False) -> None:
        if self._closing:
            return
        if not force and not getattr(self, "_layout_save_enabled", False):
            return
        if event is not None and not force and event.widget is not self:
            return
        if getattr(self, "_layout_restore_active", False) or getattr(self, "_rebuilding", False):
            return
        after_id = getattr(self, "_layout_save_after_id", None)
        if after_id:
            with self.soft("schedule_layout_save after_cancel", log=False):
                self.after_cancel(after_id)
        with self.soft("schedule_layout_save after", log=False):
            delay = 150 if force else 700
            self._layout_save_after_id = self.after(delay, self.save_layout_now)

    def capture_tree_column_widths(self, tree) -> dict:
        out = {}
        if tree is None:
            return out
        try:
            for col in tree["columns"]:
                out[str(col)] = int(tree.column(col, "width"))
        except Exception:
            debug_exc("capture_tree_column_widths")
        return out

    def capture_main_sashes(self) -> list:
        out = []
        try:
            self.update_idletasks()
            panes = list(self.body_panes.panes())
            for i in range(max(0, len(panes) - 1)):
                x, y = self.body_panes.sash_coord(i)
                out.append([int(x), int(y)])
        except Exception:
            debug_exc("capture_main_sashes")
        return out

    def save_layout_now(self, force: bool = False) -> None:
        if self._closing and not force:
            return
        try:
            self.update_idletasks()
        except Exception:
            pass
        try:
            geom = self.geometry()
            try:
                # winfo_* reports the actual current size; self.geometry() can be
                # stale on Windows after a single-edge (one-side) resize. Take
                # size from winfo_*, keep position from geometry().
                w = int(self.winfo_width()); h = int(self.winfo_height())
                if w > 1 and h > 1:
                    mpos = re.search(r"([+-]\d+[+-]\d+)$", geom)
                    geom2 = f"{w}x{h}{mpos.group(1) if mpos else ''}"
                    if _is_geometry(geom2):
                        geom = geom2
            except Exception:
                pass
            data = dict(self._layout())
            if _is_geometry(geom):
                data["main_geometry"] = geom
            try:
                state = str(self.state() or "")
                if state in ("normal", "zoomed"):
                    data["main_state"] = state
            except Exception:
                pass
            sashes = self.capture_main_sashes()
            if sashes:
                data["main_sashes"] = sashes
            data["activity_columns"] = self.capture_tree_column_widths(getattr(self, "activity_tree", None))
            data["calls_columns"] = self.capture_tree_column_widths(getattr(self, "calls_tree", None))
            data["saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._layout_data = data
            write_json(LAYOUT_PATH, data)
        except Exception:
            debug_exc("save_layout_now")

    def _popup_geometry(self, w: int, h: int) -> str:
        """Return a 'WxH+X+Y' geometry that opens a popup on the SAME monitor as
        the main window (centered over it). winfo_rootx/rooty are absolute
        virtual-desktop coords, so this follows FastChat to whichever monitor —
        including one to the LEFT of primary (negative X). Falls back to
        size-only if the main window isn't mapped yet."""
        try:
            self.update_idletasks()
            mx, my = self.winfo_rootx(), self.winfo_rooty()
            mw, mh = self.winfo_width(), self.winfo_height()
            if mw > 1 and mh > 1:
                x = mx + max(0, (mw - int(w)) // 2)
                y = my + max(0, (mh - int(h)) // 3)
                return f"{int(w)}x{int(h)}+{x}+{y}"
        except Exception:
            pass
        return f"{int(w)}x{int(h)}"

    def _geom_size(self, *geoms) -> tuple:
        """Pull the first WxH found from one or more geometry strings."""
        import re
        for g in geoms:
            m = re.match(r"^\s*(\d+)x(\d+)", str(g or ""))
            if m:
                return int(m.group(1)), int(m.group(2))
        return 800, 600

    def bind_popup_layout(self, top: tk.Toplevel, key: str, default_geometry: str) -> None:
        """Restore/save popup geometry without blocking normal close behavior.
        Keeps the remembered SIZE but always (re)positions over the main window,
        so on a dual-monitor setup popups open on the same screen as FastChat
        instead of on the primary monitor."""
        data = self._layout()
        geom = str(data.get(key, "") or "")
        w, h = self._geom_size(geom, default_geometry)
        try:
            top.geometry(self._popup_geometry(w, h))
        except Exception:
            try:
                top.geometry(default_geometry)
            except Exception:
                debug_exc(f"bind_popup_layout default {key}")

        # Guard against the open-time <Configure> storm. When the popup is
        # created and then repositioned by _popup_geometry above, Tk fires a
        # burst of <Configure> events. Without this guard those early events
        # would call save_popup_geometry and persist the just-restored (or a
        # transient) size before the user has resized anything -- which is why
        # the popup never remembered a NEW size. We ignore saves until the
        # window has settled (~1.2s), matching how the main window guards its
        # own layout save during restore.
        top._mj_layout_save_ready = False
        def _enable_save():
            try:
                top._mj_layout_save_ready = True
            except Exception:
                pass
        try:
            top.after(1200, _enable_save)
        except Exception:
            top._mj_layout_save_ready = True

        def save_popup_geometry(_event=None):
            if self._closing:
                return
            if not getattr(top, "_mj_layout_save_ready", False):
                return
            try:
                g = top.geometry()
                if _is_geometry(g):
                    d = dict(self._layout())
                    d[key] = g
                    d["saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self._layout_data = d
                    write_json(LAYOUT_PATH, d)
            except Exception:
                debug_exc(f"save_popup_geometry {key}")

        def close_popup():
            # On close, force a final save regardless of the settle guard, so a
            # resize made right before closing is still captured.
            try:
                top._mj_layout_save_ready = True
            except Exception:
                pass
            save_popup_geometry()
            top.destroy()

        try:
            top.bind("<Configure>", lambda e: top.after(700, save_popup_geometry), add="+")
            top.protocol("WM_DELETE_WINDOW", close_popup)
        except Exception:
            debug_exc(f"bind_popup_layout {key}")

    def on_close(self) -> None:
        # Save layout before marking the app as closing, so the final user
        # geometry/pane positions are captured rather than suppressed.
        self.save_current_config()
        self.save_layout_now(force=True)
        self._maybe_save_capture(force=True)
        self._closing = True
        try:
            if self.live_rig_monitor:
                self.live_rig_monitor.stop()
        except Exception:
            debug_exc("on_close live_rig_monitor.stop")
        try:
            if self._intent_poller:
                self._intent_poller.stop()
        except Exception:
            debug_exc("on_close intent_poller.stop")
        try:
            if self._auto_refresh_after_id:
                self.after_cancel(self._auto_refresh_after_id)
        except Exception:
            pass
        try:
            if self._age_tick_after_id:
                self.after_cancel(self._age_tick_after_id)
        except Exception:
            pass
        try:
            if self._self_refresh_after_id:
                self.after_cancel(self._self_refresh_after_id)
        except Exception:
            pass
        # These three were never cancelled. _closing is already True above, so
        # each tick now returns instead of rescheduling; cancelling here kills
        # the one callback already in flight.
        for _attr in ("_js8map_lease_after_id", "_speed_sync_after_id", "_utc_after_id"):
            try:
                _aid = getattr(self, _attr, None)
                if _aid:
                    self.after_cancel(_aid)
            except Exception:
                pass
        self.destroy()

    def launch_standalone_popup(self, call: str) -> None:
        """Phase 3: open the FastChat popup pre-targeted to `call`, with the
        full console window present but hidden.

        Per the Ver 3.5 integration plan, the FastChat popup is meant to stay
        the FULL, robust popup (real TX, real rig control, real DB-backed
        Info/History) — not a stripped-down "lite" view. Today the popup is
        still built through FastChatPopup(self).open(), which depends on the
        full MainWindow object graph (services, vars, theme, other popups it
        can open). Rather than guess at trimming that down, this reuses a
        real, fully-initialized MainWindow exactly as the normal console
        does, and simply withdraws it from the screen instead of showing it,
        so the only thing the operator sees is the popup itself.

        Closing the popup closes this whole hidden process the same way the
        console's own window-close does (on_close): saves config/layout/
        capture, stops the live rig monitor, cancels pending `after()` polls.
        """
        call = str(call or "").strip().upper()
        if not call:
            self.set_status("launch_standalone_popup called with no callsign.")
            return
        self.selected_call = call
        # Hide the console rather than show it. withdraw() does not stop any
        # background services/timers (refresh, speed sync, rig monitor) --
        # those keep running exactly as they would in the normal console, so
        # the popup behaves identically either way.
        try:
            self.withdraw()
        except Exception:
            debug_exc("launch_standalone_popup withdraw")
        self.open_qso_popup()
        popup = None
        for child in self.winfo_children():
            if isinstance(child, tk.Toplevel) and child.title().startswith(f"FastChat - {call}"):
                popup = child
                break
        if popup is None:
            # open_qso_popup declined (e.g. selected_call was rejected) -- do
            # not leave a hidden, un-closeable process behind.
            self.on_close()
            return

        def close_host_with_popup(_event=None):
            try:
                if _event is not None and _event.widget is not popup:
                    return
            except Exception:
                pass
            if not self._closing:
                self.on_close()

        # add="+" so this stacks alongside the existing <Destroy> binding
        # (forget_qso_latest_widget) in fastchat_popup.py rather than
        # replacing it.
        popup.bind("<Destroy>", close_host_with_popup, add="+")
        # Bring the standalone popup to the front so it isn't left behind /
        # only flashing in the taskbar when launched from the map.
        self._raise_popup_to_front(popup)

