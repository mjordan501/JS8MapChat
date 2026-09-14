#!/usr/bin/env python3
"""First-run setup wizard (FirstRunWizard) + its DB/OS helpers.

Extracted verbatim from JS8Map.py (behaviour unchanged; location only).
One-way deps: stdlib + tkinter, and the js8map_* leaf modules below."""
from __future__ import annotations

import os
import sys
import sqlite3
import queue as _queue_mod
import tkinter as tk
from tkinter import ttk

from js8map_util import is_valid_grid
from js8map_runtime import DATA_DIR as _DATA_DIR, load_config, save_config, APP_TITLE as _APP_TITLE_STR
from js8map_theme import BG, SURFACE, TEXT, MUTED, BLUE, RED, GREEN, FONT_MONO, _FONT_SANS
from js8map_dialogs import themed_confirm, themed_message
from js8map_fcc import FCC_DB_CANDIDATES, _find_fcc_db, download_and_update_fcc, update_canadian_db

# two aliases the moved code uses for the sys module
_sys = sys
_sys_early = sys


def _wizard_db_has_us(folder: str) -> bool:
    """Return True if ham.db in folder has at least one US record.
    Legacy databases built without a 'country' column (load_ham.py or
    older FCC Lookup) contain only US records by definition, so any
    non-empty callsigns table counts as US-present."""
    db = os.path.join(folder, 'ham.db')
    if not os.path.isfile(db):
        return False
    try:
        conn = sqlite3.connect(db)
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(callsigns)").fetchall()}
        if 'country' not in cols:
            # No country column means FCC-only data — any records = US present
            row = conn.execute(
                "SELECT COUNT(*) FROM callsigns").fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM callsigns "
                "WHERE country IS NULL OR country = 'US'").fetchone()
        conn.close()
        return (row[0] if row else 0) > 0
    except Exception:
        return False

def _wizard_db_has_ca(folder: str) -> bool:
    """Return True if ham.db in folder has at least one Canadian record.
    If the 'country' column doesn't exist, Canadian data has never been
    merged in, so the answer is definitively False."""
    db = os.path.join(folder, 'ham.db')
    if not os.path.isfile(db):
        return False
    try:
        conn = sqlite3.connect(db)
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(callsigns)").fetchall()}
        if 'country' not in cols:
            conn.close()
            return False   # column absent → no CA records merged yet
        row = conn.execute(
            "SELECT COUNT(*) FROM callsigns WHERE country = 'CA'"
        ).fetchone()
        conn.close()
        return (row[0] if row else 0) > 0
    except Exception:
        return False

class FirstRunWizard:
    """
    Modal-style Toplevel that walks a first-time user through database
    download and basic station setup before the main window is shown.

    Usage:
        wizard = FirstRunWizard(root)
        root.wait_window(wizard.win)
        # wizard.finished is True if user completed all steps;
        # False means they closed the window — caller should exit.
    """

    def __init__(self, root: tk.Tk):
        self.root    = root
        self.folder  = _wizard_folder()
        self.finished = False

        # Keep main window hidden until we're done
        root.withdraw()

        win = tk.Toplevel(root)
        self.win = win
        win.title(f"{_APP_TITLE_STR}  \u2014  First-Time Setup")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", self._on_close)

        # Centre on screen
        win.update_idletasks()
        w, h = 560, 480
        sx = (win.winfo_screenwidth()  - w) // 2
        sy = (win.winfo_screenheight() - h) // 2
        win.geometry(f"{w}x{h}+{sx}+{sy}")

        # Force this window to the FRONT and give it focus.
        #
        # Without this the first-time setup opens behind whatever the operator
        # was already doing, or sits blinking in the taskbar, and a new user
        # concludes the program did not start. root.withdraw() above is what
        # makes it likely: with the master hidden, Windows has no visible
        # parent to raise this Toplevel above, so it is left wherever the
        # window manager first put it.
        #
        # -topmost is set and then released ~400ms later. Setting it alone is
        # what actually beats Windows' foreground-lock (a background process
        # is normally refused focus); releasing it matters just as much, or
        # the wizard would float over every other window for its whole life
        # and the operator could not put it behind anything.
        try:
            win.deiconify()
            win.lift()
            win.attributes('-topmost', True)
            win.focus_force()
            win.after(400, lambda: win.attributes('-topmost', False))
        except Exception:
            pass          # focus is never worth failing setup over

        # ── Title bar ────────────────────────────────────────────────
        hdr = tk.Frame(win, bg=SURFACE, pady=14)
        hdr.pack(fill='x')
        tk.Label(hdr, text=_APP_TITLE_STR, font=(_FONT_SANS, 18, 'bold'),
                 bg=SURFACE, fg=TEXT).pack()
        tk.Label(hdr, text="First-Time Setup", font=(_FONT_SANS, 11),
                 bg=SURFACE, fg=MUTED).pack()

        # ── Step indicator (3 circles) ────────────────────────────────
        step_row = tk.Frame(win, bg=BG, pady=8)
        step_row.pack(fill='x')
        self._step_labels = []
        steps = ["1  FCC Database", "2  Canadian DB", "3  Your Station"]
        for i, label in enumerate(steps):
            lbl = tk.Label(step_row, text=label,
                           font=(_FONT_SANS, 9, 'bold'),
                           bg=BG, fg=MUTED, padx=12)
            lbl.pack(side='left', expand=True)
            self._step_labels.append(lbl)

        # ── Content frame (panels swap in/out here) ───────────────────
        self._content = tk.Frame(win, bg=BG)
        self._content.pack(fill='both', expand=True, padx=30)

        # ── Progress bar (shared across panels, hidden by default) ────
        self._prog = ttk.Progressbar(win, mode='indeterminate')
        self._prog.pack(fill='x', side='bottom')
        self._prog.pack_forget()

        # ── Status label (shared, bottom of content) ──────────────────
        self._status_var = tk.StringVar(value='')
        self._status_lbl = tk.Label(win, textvariable=self._status_var,
                                    font=(_FONT_SANS, 9), bg=BG, fg=MUTED,
                                    wraplength=500, justify='center')
        self._status_lbl.pack(side='bottom', pady=(0, 6))

        self._busy = False
        # Thread-safe message queue: worker puts (msg, color) tuples,
        # main thread polls via _poll_queue() every 100 ms.
        self._q = _queue_mod.Queue()
        self._poll_queue()
        # Auto-advance: use _find_fcc_db() which searches all candidate paths.
        # Log results to a debug file in _DATA_DIR so we can troubleshoot.
        try:
            _detected = _find_fcc_db()
            _diag = []
            _diag.append(f"_find_fcc_db() returned: {_detected!r}")
            if _detected:
                self.folder = os.path.dirname(_detected)
            _diag.append(f"self.folder: {self.folder!r}")
            _diag.append(f"FCC_DB_CANDIDATES (existing): "
                         f"{[c for c in FCC_DB_CANDIDATES if os.path.exists(c)]!r}")
            _has_us = _wizard_db_has_us(self.folder)
            _has_ca = _wizard_db_has_ca(self.folder)
            _diag.append(f"_wizard_db_has_us: {_has_us}")
            _diag.append(f"_wizard_db_has_ca: {_has_ca}")
            try:
                with open(os.path.join(_DATA_DIR, 'wizard_debug.log'),
                          'w', encoding='utf-8') as _f:
                    _f.write('\n'.join(_diag))
            except Exception:
                pass
            if _has_us and _has_ca:
                self._show_panel_3()
                return
            if _has_us:
                self._show_panel_2()
                return
            self._show_panel_1()
        except Exception:
            self._show_panel_1()

    # ── Helpers ───────────────────────────────────────────────────────

    def _fcc_folder(self) -> str:
        return self.folder

    def _clear_content(self):
        for w in self._content.winfo_children():
            w.destroy()

    def _set_status(self, msg: str, color: str = MUTED):
        self._status_var.set(msg)
        self._status_lbl.config(fg=color)

    def _poll_queue(self):
        """Drain the status queue on the main thread (100 ms poll)."""
        try:
            while True:
                msg, color = self._q.get_nowait()
                if msg == "__DONE_FCC__":
                    self._fcc_done(color)   # color carries ok bool here
                elif msg == "__DONE_CA__":
                    self._ca_done(color)
                else:
                    self._set_status(msg, color)
        except Exception:
            pass
        try:
            self.win.after(100, self._poll_queue)
        except Exception:
            pass

    def _highlight_step(self, active: int):
        """Highlight the active step label (0-based)."""
        for i, lbl in enumerate(self._step_labels):
            if i == active:
                lbl.config(fg=BLUE, font=(_FONT_SANS, 9, 'bold'))
            elif i < active:
                lbl.config(fg=GREEN, font=(_FONT_SANS, 9, 'bold'))
            else:
                lbl.config(fg=MUTED, font=(_FONT_SANS, 9))

    def _start_busy(self):
        self._busy = True
        self._prog.pack(fill='x', side='bottom')
        self._prog.start(10)
        self.win.config(cursor='watch')

    def _stop_busy(self):
        self._busy = False
        self._prog.stop()
        self._prog.pack_forget()
        self.win.config(cursor='')

    def _on_close(self):
        """User closed the wizard window — confirm if download in progress."""
        if self._busy:
            if not themed_confirm(
                self.win,
                "Cancel Setup?",
                "A download is in progress.\n\n"
                "Cancel setup and exit JS8Map?",
                kind="warning", yes="Cancel Setup", no="Keep Waiting", safe_no=True
            ):
                return   # user chose to keep waiting
        self.finished = False
        try:
            self.win.destroy()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    # ── Panel 1: FCC (US) Database ────────────────────────────────────

    def _show_panel_1(self):
        self._clear_content()
        self._highlight_step(0)
        f = self._content

        tk.Label(f, text="Step 1 of 3 \u2014 US Callsign Database",
                 font=(_FONT_SANS, 13, 'bold'), bg=BG, fg=TEXT
                 ).pack(pady=(18, 6))
        tk.Label(f,
                 text="JS8Map needs the FCC amateur license database to place\n"
                      "callsigns on the map. This downloads ~100 MB directly\n"
                      "from fcc.gov and takes 2\u20135 minutes.",
                 font=(_FONT_SANS, 10), bg=BG, fg=MUTED,
                 justify='center').pack(pady=(0, 16))

        self._fcc_btn = tk.Button(
            f, text="\u2b07  Download FCC Database",
            font=(_FONT_SANS, 11, 'bold'),
            bg=BLUE, fg='#ffffff',
            activebackground='#1a5276', activeforeground='#ffffff',
            relief='flat', padx=20, pady=8, cursor='hand2',
            command=self._do_fcc_download)
        self._fcc_btn.pack(pady=(0, 12))

        self._fcc_next = tk.Button(
            f, text="Next \u2192",
            font=(_FONT_SANS, 10, 'bold'),
            bg=SURFACE, fg=MUTED,
            relief='flat', padx=16, pady=6,
            state='disabled', cursor='hand2',
            command=self._show_panel_2)
        self._fcc_next.pack()

        tk.Button(f, text="I already have the database \u2192",
                  font=(_FONT_SANS, 9), bg=BG, fg=MUTED,
                  relief='flat', cursor='hand2',
                  command=self._check_existing_db).pack(pady=(8,0))

        # If FCC DB already present (e.g. user re-runs wizard), unlock Next
        if _wizard_db_has_us(self._fcc_folder()):
            self._set_status('\u2705  US database already present \u2014 you can proceed.', GREEN)
            self._fcc_next.config(state='normal', fg=TEXT)

    def _check_existing_db(self):
        """User claims DB already exists — search all candidate paths."""
        detected = _find_fcc_db()
        if detected:
            self.folder = os.path.dirname(detected)
            self._set_status(f'\u2705  Database found at {self.folder}', GREEN)
            self._fcc_next.config(state='normal', fg=TEXT)
        else:
            self._set_status(
                '\u26a0  No database found in standard locations. '
                'Please download it.', '#e0a000')

    def _check_existing_ca_db(self):
        """User claims Canadian DB already exists — verify records present."""
        detected = _find_fcc_db()
        if detected:
            self.folder = os.path.dirname(detected)
        if _wizard_db_has_ca(self.folder):
            self._set_status(f'\u2705  Canadian records found at {self.folder}', GREEN)
            self._ca_next.config(state='normal', fg=TEXT)
        else:
            self._set_status(
                '\u26a0  No Canadian records found. Click Download or Skip.',
                '#e0a000')

    def _do_fcc_download(self):
        if self._busy:
            return
        self._fcc_btn.config(state='disabled')
        self._start_busy()
        self._set_status('\u23f3  Starting FCC download\u2026', MUTED)

        def _run():
            ok = download_and_update_fcc(
                self._fcc_folder(),
                status_cb=lambda m: self._q.put((m, MUTED))
            )
            self._q.put(("__DONE_FCC__", ok))

        import threading
        threading.Thread(target=_run, daemon=True).start()

    def _fcc_done(self, ok: bool):
        self._stop_busy()
        if ok:
            self._set_status('\u2705  US database downloaded successfully!', GREEN)
            self._fcc_next.config(state='normal', fg=TEXT, bg=SURFACE)
        else:
            self._set_status(
                '\u274c  Download failed \u2014 check your internet connection and try again.',
                RED)
            self._fcc_btn.config(state='normal')

    # ── Panel 2: Canadian Database (optional) ─────────────────────────

    def _show_panel_2(self):
        self._clear_content()
        self._highlight_step(1)
        f = self._content

        tk.Label(f, text="Step 2 of 3 \u2014 Canadian Callsign Database",
                 font=(_FONT_SANS, 13, 'bold'), bg=BG, fg=TEXT
                 ).pack(pady=(18, 6))
        tk.Label(f,
                 text="Adds VE / VA / VY callsign coverage from ISED Canada.\n"
                      "Smaller download (\u22481\u20132 minutes). You can skip this\n"
                      "and add it later via the Update Canadian DB button.",
                 font=(_FONT_SANS, 10), bg=BG, fg=MUTED,
                 justify='center').pack(pady=(0, 16))

        btn_row = tk.Frame(f, bg=BG)
        btn_row.pack(pady=(0, 8))

        self._ca_btn = tk.Button(
            btn_row, text="\u2b07  Download Canadian Database",
            font=(_FONT_SANS, 11, 'bold'),
            bg=BLUE, fg='#ffffff',
            activebackground='#1a5276', activeforeground='#ffffff',
            relief='flat', padx=20, pady=8, cursor='hand2',
            command=self._do_ca_download)
        self._ca_btn.pack(side='left', padx=(0, 10))

        self._ca_skip = tk.Button(
            btn_row, text="Skip for now",
            font=(_FONT_SANS, 10), bg=SURFACE, fg=MUTED,
            relief='flat', padx=14, pady=8, cursor='hand2',
            command=self._show_panel_3)
        self._ca_skip.pack(side='left')

        self._ca_next = tk.Button(
            f, text="Next \u2192",
            font=(_FONT_SANS, 10, 'bold'),
            bg=SURFACE, fg=MUTED,
            relief='flat', padx=16, pady=6,
            state='disabled', cursor='hand2',
            command=self._show_panel_3)
        self._ca_next.pack()

        tk.Button(f, text="I already have the Canadian DB \u2192",
                  font=(_FONT_SANS, 9), bg=BG, fg=MUTED,
                  relief='flat', cursor='hand2',
                  command=self._check_existing_ca_db).pack(pady=(8, 0))

        if _wizard_db_has_ca(self._fcc_folder()):
            self._set_status('\u2705  Canadian database already present \u2014 you can proceed.', GREEN)
            self._ca_next.config(state='normal', fg=TEXT)

    def _do_ca_download(self):
        if self._busy:
            return
        self._ca_btn.config(state='disabled')
        self._ca_skip.config(state='disabled')
        self._start_busy()
        self._set_status('\u23f3  Starting Canadian download\u2026', MUTED)

        # Remember the last thing update_canadian_db() said. Without this the
        # specific reason is lost: _ca_done() used to overwrite every failure
        # with the words "Download failed", so a MISSING build_canadian_db.py
        # -- which is detected before any download is even attempted -- was
        # reported to the operator as a network problem. That cost a morning.
        self._ca_last_msg = ''

        def _run():
            def _cb(m):
                self._ca_last_msg = m
                self._q.put((m, MUTED))
            ok = update_canadian_db(
                self._fcc_folder(),
                status_cb=_cb
            )
            self._q.put(("__DONE_CA__", ok))

        import threading
        threading.Thread(target=_run, daemon=True).start()

    def _ca_done(self, ok: bool):
        self._stop_busy()
        # Also accept as success if records are already in the DB
        has_ca = _wizard_db_has_ca(self._fcc_folder())
        if ok or has_ca:
            self._set_status('\u2705  Canadian database ready!', GREEN)
            self._ca_next.config(state='normal', fg=TEXT, bg=SURFACE)
        else:
            # Prefer the real reason over a guess about the network. Anything
            # update_canadian_db() flagged with a cross is a diagnosis; a
            # progress line is not, so only the former is shown.
            detail = (getattr(self, '_ca_last_msg', '') or '').strip()
            if detail[:1] in ('\u2717', '\u2718', '\u274c'):
                msg = detail + '  \u2014 you can skip and add Canadian data later.'
            else:
                msg = ('\u274c  Download failed \u2014 you can skip and add '
                       'Canadian data later.')
            self._set_status(msg, RED)
            self._ca_btn.config(state='normal')
            self._ca_skip.config(state='normal')

    # ── Panel 3: Your Station ─────────────────────────────────────────

    def _show_panel_3(self):
        self._clear_content()
        self._highlight_step(2)
        self._set_status('', MUTED)
        f = self._content

        tk.Label(f, text="Step 3 of 3 \u2014 Your Station",
                 font=(_FONT_SANS, 13, 'bold'), bg=BG, fg=TEXT
                 ).pack(pady=(18, 6))
        tk.Label(f,
                 text="Enter your callsign and Maidenhead grid square.\n"
                      "You can change these later in Settings.",
                 font=(_FONT_SANS, 10), bg=BG, fg=MUTED,
                 justify='center').pack(pady=(0, 16))

        grid_frm = tk.Frame(f, bg=BG)
        grid_frm.pack()

        def _row(label_text, row):
            tk.Label(grid_frm, text=label_text,
                     font=(_FONT_SANS, 11, 'bold'),
                     bg=BG, fg=TEXT, anchor='e', width=14
                     ).grid(row=row, column=0, padx=(0, 8), pady=6, sticky='e')

        _row("Callsign:", 0)
        self._call_var = tk.StringVar()
        call_entry = tk.Entry(grid_frm, textvariable=self._call_var,
                              font=(FONT_MONO[0], 13, 'bold'),
                              bg=SURFACE, fg=BLUE,
                              insertbackground=BLUE,
                              relief='flat', width=12, bd=2)
        call_entry.grid(row=0, column=1, pady=6, ipady=4, sticky='w')
        call_entry.focus_set()

        # Force uppercase as the user types
        def _upper_call(*_):
            v = self._call_var.get()
            u = v.upper()
            if v != u:
                self._call_var.set(u)
        self._call_var.trace_add('write', _upper_call)

        _row("Grid Square:", 1)
        self._grid_var = tk.StringVar()
        grid_entry = tk.Entry(grid_frm, textvariable=self._grid_var,
                              font=(FONT_MONO[0], 13, 'bold'),
                              bg=SURFACE, fg=BLUE,
                              insertbackground=BLUE,
                              relief='flat', width=12, bd=2)
        grid_entry.grid(row=1, column=1, pady=6, ipady=4, sticky='w')

        def _upper_grid(*_):
            v = self._grid_var.get()
            u = v.upper()
            if v != u:
                self._grid_var.set(u)
        self._grid_var.trace_add('write', _upper_grid)

        tk.Button(f, text="Finish Setup  \u2192",
                  font=(_FONT_SANS, 11, 'bold'),
                  bg=GREEN, fg='#ffffff',
                  activebackground='#1e8449',
                  relief='flat', padx=20, pady=8, cursor='hand2',
                  command=self._station_finish).pack(pady=(20, 0))

        # Bind Enter on either field to continue
        call_entry.bind('<Return>', lambda e: self._station_finish())
        grid_entry.bind('<Return>', lambda e: self._station_finish())

    def _station_finish(self):
        """Validate callsign/grid, save them, and finish setup.

        There was briefly a fourth step here asking for an optional map tile
        key. Dropped 2026-08-26: the map is complete offline from the borders
        and place names shipped in the installer, so the key buys nothing most
        operators want, and an extra screen only invited the question "do I
        need this?". The setting still exists in Settings under Map Detail for
        anyone who wants street-level tiles.
        """
        if not self._validate_and_save_station():
            return
        self._station_saved = True
        self._finish()

    def _validate_and_save_station(self) -> bool:
        call = self._call_var.get().strip().upper()
        grid = self._grid_var.get().strip().upper()
        if not call:
            themed_message(self.win, "Missing Callsign",
                "Please enter your callsign.", kind="warning")
            return False
        if not grid:
            themed_message(self.win, "Missing Grid",
                "Please enter your grid square (e.g. FM05SX).", kind="warning")
            return False
        if not is_valid_grid(grid):
            themed_message(self.win, "Invalid Grid",
                f"\"{grid}\" is not a valid Maidenhead grid.\n"
                "Use 4 or 6 characters, e.g. FM05 or FM05SX.", kind="warning")
            return False

        cfg = load_config()
        cfg['callsign'] = call
        cfg['my_grid']  = grid
        if self.folder and os.path.isfile(os.path.join(self.folder, 'ham.db')):
            cfg['fcc_db_path'] = self.folder
        save_config(cfg)
        return True

    def _finish(self):
        # Callsign and grid were validated and saved by _station_finish before
        # this panel was reachable. Re-validate anyway if the station panel
        # was somehow bypassed, so Finish can never write a blank callsign.
        if not getattr(self, '_station_saved', False):
            if not self._validate_and_save_station():
                return
            self._station_saved = True

        # On Linux AppImage first run, create desktop shortcut and menu entry
        _install_linux_desktop()

        self.finished = True
        try:
            self.win.destroy()
        except Exception:
            pass

def _install_linux_desktop():
    """Create .desktop menu entry and desktop shortcut on Linux
    when running as an AppImage. Called once during first-run wizard.
    Silently does nothing on Windows or when not running as AppImage."""
    if not _sys_early.platform.startswith('linux'):
        return
    appimage_path = os.environ.get('APPIMAGE', '')
    if not appimage_path:
        return   # not running as AppImage — skip

    appdir_path = os.environ.get('APPDIR', '')
    home = os.path.expanduser('~')

    # Find the icon inside the mounted AppDir
    icon_src = os.path.join(appdir_path, 'JS8Map.png') if appdir_path else ''
    icon_dst = os.path.join(home, '.local', 'share', 'icons', 'JS8Map.png')

    # Copy icon to a persistent location
    try:
        os.makedirs(os.path.dirname(icon_dst), exist_ok=True)
        if icon_src and os.path.isfile(icon_src):
            import shutil
            shutil.copy2(icon_src, icon_dst)
    except Exception:
        icon_dst = ''   # icon copy failed — .desktop will work without it

    desktop_content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={_APP_TITLE_STR}\n"
        "Comment=Live map display for JS8Call\n"
        f"Exec={appimage_path}\n"
        f"Icon={icon_dst}\n"
        "Categories=HamRadio;Network;\n"
        "Terminal=false\n"
    )

    # Create app menu entry
    try:
        menu_dir = os.path.join(home, '.local', 'share', 'applications')
        os.makedirs(menu_dir, exist_ok=True)
        menu_path = os.path.join(menu_dir, 'JS8Map.desktop')
        with open(menu_path, 'w', encoding='utf-8') as f:
            f.write(desktop_content)
        os.chmod(menu_path, 0o755)
    except Exception:
        pass

    # Create desktop shortcut
    try:
        desktop_dir = os.path.join(home, 'Desktop')
        if os.path.isdir(desktop_dir):
            desktop_path = os.path.join(desktop_dir, 'JS8Map.desktop')
            with open(desktop_path, 'w', encoding='utf-8') as f:
                f.write(desktop_content)
            os.chmod(desktop_path, 0o755)
    except Exception:
        pass

def _wizard_folder() -> str:
    """Platform-correct default FCC DB folder for the wizard."""
    if _sys.platform.startswith('linux') or _sys.platform == 'darwin':
        return os.path.join(os.path.expanduser('~'), 'FCC_DB')
    return r'C:\FCC_DB'

