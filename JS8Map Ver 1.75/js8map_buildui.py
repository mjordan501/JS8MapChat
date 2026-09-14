"""js8map_buildui.py — HamMapApp UI-construction mixin for JS8MapChat (Phase 6 T2d).

The 576-line _build_ui method — the declarative Tk widget tree — lifted out of the
HamMapApp god-class along with its 18 nested button/placeholder/keypress closures
(incl. _do_fcc_update / _do_canadian_update / _relay_clear).

Stays a method on _BuildUIMixin; HamMapApp inherits it alongside the other three
mixins. _build_ui SETS ~29 self.<widget> instance attrs (read across the app) and
wires commands to callbacks on the other mixins — all resolve on the instance / via
the MRO, so nothing is rewired. Imports mirror the monolith exactly, including the
sys-as-_sys, tkinter-as-tk, and DATA_DIR-as-_DATA_DIR aliases. No import back (no cycle).
"""

import os
import sys as _sys
import threading
import time
import tkinter as tk
from tkinter import ttk

from js8map_runtime import (DATA_DIR as _DATA_DIR,
                            load_config as _load_config,
                            save_config as _save_config)
from js8map_theme import (
    BG, SURFACE, CARD, BORDER, TEXT, MUTED, BLUE, RED, CALLSIGN, GREEN, BTN_BG, GEN_BG,
    FONT_MONO, FONT_UI, _FONT_SANS,
)
from js8map_fcc import download_and_update_fcc, update_canadian_db
from js8map_tx import tx_query


def _map_key_popup(app):
    """Small window for the optional map tile key.

    The map itself needs nothing here -- borders, state lines, place names and
    every station are drawn from files installed with the program and work
    with no internet at all. A key only adds street-level tiles behind them,
    for operators who are online and want them. Blank means no request is ever
    made to the tile provider, which is why no watermark can appear.

    This lives in its own window rather than in the settings card because the
    card is a fixed stack of rows: two more rows pushed the bottom of the app
    off the screen on a 13" laptop at 125%, where the window cannot be dragged
    up to reach them.

    Saved with load/update/save rather than through the app's own settings
    write, which builds a fixed list of the keys it owns. save_config merges,
    so writing this one key cannot disturb anything else.
    """
    try:
        win = tk.Toplevel(app.root)
        win.title("Map Detail")
        win.configure(bg=BG)
        win.transient(app.root)
        win.resizable(False, False)

        tk.Label(win, text="Map Detail \u2014 optional",
                 font=(_FONT_SANS, 13, 'bold'), bg=BG, fg=TEXT
                 ).pack(pady=(16, 4), padx=24)
        tk.Label(win,
                 text="Your map already works offline. World borders, state lines\n"
                      "and place names are built in. Nothing is required here.\n\n"
                      "If you are online and want street-level detail behind the\n"
                      "map, paste a free map key below.",
                 font=(_FONT_SANS, 9), bg=BG, fg=MUTED, justify='center'
                 ).pack(pady=(0, 10), padx=24)

        var = tk.StringVar(value=_load_config().get('map_tile_key', ''))
        ent = tk.Entry(win, textvariable=var, font=FONT_MONO,
                       bg=CARD, fg=TEXT, insertbackground=TEXT,
                       relief='flat', bd=5, width=34,
                       highlightthickness=1, highlightbackground=BORDER,
                       highlightcolor=BLUE)
        ent.pack(padx=24)

        tk.Label(win, text="Free key from:  carto.com/basemaps/apikey",
                 font=(_FONT_SANS, 9), bg=BG, fg=MUTED).pack(pady=(8, 2))
        status = tk.Label(win, text="", font=(_FONT_SANS, 9, 'bold'), bg=BG, fg=GREEN)
        status.pack()

        def _save():
            key = var.get().strip()
            cfg = _load_config()
            cfg['map_tile_key'] = key
            _save_config(cfg)
            status.config(text="Saved \u2014 click Generate Map to apply"
                               if key else "Cleared \u2014 map stays offline")

        row = tk.Frame(win, bg=BG)
        row.pack(pady=(10, 18))
        tk.Button(row, text="Close", font=(_FONT_SANS, 10, 'bold'),
                  bg=SURFACE, fg=MUTED, activebackground=SURFACE,
                  activeforeground=TEXT, relief='flat', padx=16, pady=7,
                  cursor='hand2', command=win.destroy).pack(side='left', padx=(0, 8))
        tk.Button(row, text="Save", font=(_FONT_SANS, 10, 'bold'),
                  bg=GREEN, fg='#ffffff', activebackground='#1e8449',
                  activeforeground='#ffffff', relief='flat', padx=20, pady=7,
                  cursor='hand2', command=_save).pack(side='left')

        ent.bind('<Return>', lambda e: _save())
        ent.focus_set()
        win.update_idletasks()
        # Centre on the app window rather than the screen, so it lands where
        # the operator is looking on a multi-monitor setup.
        try:
            px, py = app.root.winfo_x(), app.root.winfo_y()
            pw, ph = app.root.winfo_width(), app.root.winfo_height()
            w, h = win.winfo_reqwidth(), win.winfo_reqheight()
            win.geometry("+%d+%d" % (max(0, px + (pw - w) // 2),
                                     max(0, py + (ph - h) // 3)))
        except Exception:
            pass
        win.lift()
        win.grab_set()
    except Exception:
        pass          # an optional settings window must never break the app


class _BuildUIMixin:
    def _build_ui(self):
        # ── Title ──
        tk.Label(self.root, text="JS8Map",
                 font=(_FONT_SANS, 17, 'bold'), bg=BG, fg=RED
                 ).pack(pady=(18, 1))
        tk.Label(self.root,
                 text="Connects to JS8Call API  ·  Live spots  ·  Interactive HTML map",
                 font=(_FONT_SANS, 12, 'bold'), bg=BG, fg=MUTED
                 ).pack(pady=(0, 14))

        # ── Card frame ──
        outer = tk.Frame(self.root, bg=BORDER, bd=0)
        outer.pack(fill='x', expand=False, padx=20)
        inner = tk.Frame(outer, bg=SURFACE, bd=0)
        inner.pack(fill='x', padx=1, pady=1)
        inner.columnconfigure(1, weight=1)

        style = ttk.Style()
        try:
            style.theme_use('clam')
        except Exception:
            pass
        style.configure('TCombobox',
                         fieldbackground=CARD, background=BTN_BG,
                         foreground=TEXT, selectbackground=BTN_BG,
                         selectforeground=TEXT, arrowcolor=TEXT)
        style.map('TCombobox',
                  fieldbackground=[('readonly', CARD)],
                  foreground=[('readonly', TEXT)])

        def row_label(text, row, fg=MUTED, font=FONT_UI):
            tk.Label(inner, text=text, font=font, bg=SURFACE, fg=fg,
                     anchor='e', width=13
                     ).grid(row=row, column=0, padx=(14, 6), pady=7, sticky='e')

        def mini_btn(parent, text, cmd):
            return tk.Button(parent, text=text, font=(_FONT_SANS, 11, 'bold'),
                              bg=BTN_BG, fg='white', relief='flat', bd=0,
                              padx=12, pady=4, cursor='hand2', command=cmd)

        # ── Row 0: JS8Call connection ──
        row_label("JS8Call:", 0)
        conn_row = tk.Frame(inner, bg=SURFACE)
        conn_row.grid(row=0, column=1, columnspan=2, padx=(0, 14), pady=7, sticky='ew')

        self.js8_host = tk.StringVar(value='127.0.0.1')
        tk.Entry(conn_row, textvariable=self.js8_host, font=FONT_MONO,
                 bg=CARD, fg=TEXT, insertbackground=TEXT,
                 relief='flat', bd=5, width=14,
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=BLUE
                 ).pack(side='left')

        tk.Label(conn_row, text=" : ", font=FONT_UI, bg=SURFACE, fg=MUTED
                 ).pack(side='left')

        self.js8_port = tk.StringVar(value='2442')
        tk.Entry(conn_row, textvariable=self.js8_port, font=FONT_MONO,
                 bg=CARD, fg=TEXT, insertbackground=TEXT,
                 relief='flat', bd=5, width=6,
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=BLUE
                 ).pack(side='left')

        tk.Label(conn_row, text="  ", bg=SURFACE).pack(side='left')

        # Connection status dot + label
        self.conn_dot = tk.Label(conn_row, text="●", font=(_FONT_SANS, 13),
                                  bg=SURFACE, fg='#e84060')
        self.conn_dot.pack(side='left')
        self.conn_lbl = tk.Label(conn_row, text="Not connected",
                                  font=(_FONT_SANS, 10, 'bold'), bg=SURFACE, fg='#e84060')
        self.conn_lbl.pack(side='left', padx=(3, 0))
        self._attach_tooltip(self.conn_lbl, self._tt("connected"))

        _reconnect_btn = mini_btn(conn_row, "Reconnect", self._reconnect)
        _reconnect_btn.pack(side='left', padx=(8, 0))
        self._attach_tooltip(_reconnect_btn, self._tt("reconnect"))

        # ── Row 1: Callsign ──
        row_label("Callsign:", 1)
        self.callsign = tk.StringVar()
        _cs_entry = tk.Entry(inner, textvariable=self.callsign, font=FONT_MONO,
                 bg=CARD, fg=CALLSIGN, insertbackground=CALLSIGN,
                 relief='flat', bd=5, width=14,
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=BLUE)
        _cs_entry.grid(row=1, column=1, padx=(0, 10), pady=7, sticky='w')
        _cs_entry.bind('<KeyRelease>', lambda e: self.callsign.set(
            self.callsign.get().upper()))

        # ── Row 2: My Grid ──
        row_label("My Grid:", 2)
        grid_row = tk.Frame(inner, bg=SURFACE)
        grid_row.grid(row=2, column=1, columnspan=2, padx=(0, 14), pady=7, sticky='w')
        self.my_grid = tk.StringVar()
        _grid_entry = tk.Entry(grid_row, textvariable=self.my_grid, font=FONT_MONO,
                 bg=CARD, fg=CALLSIGN, insertbackground=CALLSIGN,
                 relief='flat', bd=5, width=8,
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=BLUE)
        _grid_entry.pack(side='left')
        _grid_entry.bind('<KeyRelease>', lambda e: self.my_grid.set(
            self.my_grid.get().upper()))
        tk.Label(grid_row, text="  (auto-filled from JS8Call)",
                 font=(_FONT_SANS, 10, 'bold'), bg=SURFACE, fg=TEXT
                 ).pack(side='left')

        # ── Row 3: Time filter ──
        row_label("Show:", 3)
        self.time_filter = tk.StringVar(value="Last 30 minutes")
        time_cb = ttk.Combobox(inner, textvariable=self.time_filter,
                     width=22, state='readonly',
                     values=["Last 15 minutes", "Last 30 minutes", "Last 1 hour",
                             "Last 2 hours", "Last 4 hours", "Last 12 hours",
                             "Last 24 hours"],
                     font=FONT_UI)
        time_cb.grid(row=3, column=1, padx=(0, 10), pady=7, sticky='w')
        time_cb.bind('<<ComboboxSelected>>', self._on_time_filter_changed)

        # ── Row 4: Quiet-band rebuild ──
        row_label("Data Rebuild:", 4)  # rebuilds data.json on a schedule when no new spots arrive
        self.refresh_var = tk.StringVar(value="Every 1 min")
        refresh_cb = ttk.Combobox(inner, textvariable=self.refresh_var,
                     width=22, state='readonly',
                     values=["Off", "Every 30 sec", "Every 1 min",
                             "Every 2 min", "Every 5 min", "Every 10 min"],
                     font=FONT_UI)
        refresh_cb.grid(row=4, column=1, padx=(0, 10), pady=7, sticky='w')
        refresh_cb.bind('<<ComboboxSelected>>', self._on_refresh_changed)
        self._attach_tooltip(refresh_cb, self._tt("data_rebuild"))

        # ── Output path (v1.5: no UI control). The map always writes beside
        #    JS8Map.py — your run folder. The picker was vestigial under the
        #    one-folder model and was the source of stale-path bugs, so it's
        #    gone; out_path is fixed to the script dir. ──
        self.out_path = tk.StringVar(
            value=os.path.join(_DATA_DIR, 'ham_map.html'))

        # ── Row 6: FCC Database ──
        row_label("FCC DB:", 6)
        fcc_row = tk.Frame(inner, bg=SURFACE)
        fcc_row.grid(row=6, column=1, columnspan=2, padx=(0, 14), pady=7, sticky='ew')
        self.fcc_label = tk.Label(fcc_row, text='Auto-detect', font=(_FONT_SANS, 12, 'bold'),
                 bg=CARD, fg=MUTED, anchor='w', padx=6,
                 relief='flat', bd=0, width=28,
                 highlightthickness=1, highlightbackground=BORDER)
        self.fcc_label.pack(side='left', fill='x', expand=True, ipady=4)
        self._attach_tooltip(self.fcc_label, self._tt("fcc_db"))
        mini_btn(fcc_row, "Browse…", self._browse_fcc_db).pack(side='left', padx=(5, 0))

        # ── Row 7: JS8Call.ini path ──
        row_label("JS8Call.ini:", 7)
        ini_row = tk.Frame(inner, bg=SURFACE)
        ini_row.grid(row=7, column=1, columnspan=2, padx=(0, 14), pady=7, sticky='ew')
        self.ini_label = tk.Label(ini_row, text='Auto-detect', font=(_FONT_SANS, 12, 'bold'),
                 bg=CARD, fg=MUTED, anchor='w', padx=6,
                 relief='flat', bd=0, width=28,
                 highlightthickness=1, highlightbackground=BORDER)
        self.ini_label.pack(side='left', fill='x', expand=True, ipady=4)
        self._attach_tooltip(self.ini_label, self._tt("js8call_ini"))
        mini_btn(ini_row, "Browse…", self._browse_ini).pack(side='left', padx=(5, 0))

        # ── Row 8: Startup TX mode ──
        row_label("Startup TX:", 8, fg='#FFD24A', font=(_FONT_SANS, 11, 'bold'))
        tx_set_row = tk.Frame(inner, bg=SURFACE)
        tx_set_row.grid(row=8, column=1, columnspan=2, padx=(0, 14), pady=7, sticky='w')
        self._tx_mode_sel_var = tk.StringVar(value='manual')
        self._tx_radio_btns = {}   # mode → Radiobutton, for green-on-select repaint
        for _val, _lbl in [('manual', 'Manual (fills box)'),
                           ('live',   'Live (auto-TX)')]:
            _rb = tk.Radiobutton(tx_set_row, text=_lbl, value=_val,
                           variable=self._tx_mode_sel_var,
                           font=(_FONT_SANS, 11), bg=SURFACE, fg=TEXT,
                           selectcolor=CARD, activebackground=SURFACE,
                           activeforeground=TEXT, relief='flat', bd=0,
                           highlightthickness=0,
                           command=self._on_startup_tx_mode_change)
            _rb.pack(side='left', padx=(0, 10))
            self._tx_radio_btns[_val] = _rb
            self._attach_tooltip(_rb, self._tt("startup_manual" if _val == "manual" else "startup_live"))
        tk.Label(inner, text="Saved default applied at launch  ·  use ↻ Change below to flip this session",
                 font=(_FONT_SANS, 8), bg=SURFACE, fg=MUTED
                 ).grid(row=9, column=1, columnspan=2, padx=(0, 14), sticky='w')

        # ── Row 10: HALT capability (JS8Call Improved API 3.0+) ──
        row_label("TX Halt:", 10, fg='#FFD24A', font=(_FONT_SANS, 11, 'bold'))
        halt_row = tk.Frame(inner, bg=SURFACE)
        halt_row.grid(row=10, column=1, columnspan=2, padx=(0, 14), pady=7, sticky='w')
        self._tx_halt_var = tk.BooleanVar(value=False)
        _tx_halt_cb = tk.Checkbutton(halt_row,
                       text="My JS8Call supports RIG.TX_HALT (Improved API 3.0+)",
                       variable=self._tx_halt_var, font=(_FONT_SANS, 11),
                       bg=SURFACE, fg=TEXT, selectcolor=CARD,
                       activebackground=SURFACE, activeforeground=TEXT,
                       relief='flat', bd=0, highlightthickness=0,
                       command=self._on_tx_halt_toggle)
        _tx_halt_cb.pack(side='left')
        self._attach_tooltip(_tx_halt_cb, self._tt("tx_halt"))
        tk.Label(inner,
                 text="Off (default) = HALT button disabled; older JS8Call 2.x has no API halt — use JS8Call's Halt Tx",
                 font=(_FONT_SANS, 8), bg=SURFACE, fg=MUTED
                 ).grid(row=11, column=1, columnspan=2, padx=(0, 14), sticky='w')

        # ── Button row 1: Map + Relay ────────────────────────────────────
        btn_row = tk.Frame(self.root, bg=BG)
        btn_row.pack(pady=(14, 4))

        self.clear_btn = tk.Button(
            btn_row, text="🗑  Clear  (QSY)",
            font=(_FONT_SANS, 11, 'bold'),
            bg='#7a2a2a', fg='white', activebackground='#9a3a3a', activeforeground='white',
            relief='flat', bd=0, padx=12, pady=8,
            cursor='hand2', command=self._confirm_clear)
        self.clear_btn.pack(side='left', padx=(0, 6))
        self._attach_tooltip(self.clear_btn, self._tt("clear_qsy"))

        self.gen_btn = tk.Button(
            btn_row, text="⬛  Generate Map",
            font=(_FONT_SANS, 11, 'bold'),
            bg=GEN_BG, fg='white', activebackground='#2e7d32', activeforeground='white',
            relief='flat', bd=0, padx=20, pady=8,
            cursor='hand2', command=self._generate)
        self.gen_btn.pack(side='left', padx=(0, 4))
        self._attach_tooltip(self.gen_btn, self._tt("generate_map"))

        _regen_btn = tk.Button(btn_row, text='R', font=(_FONT_SANS, 11, 'bold'),
            bg=GEN_BG, fg='white', activebackground='#2e7d32', activeforeground='white',
            relief='flat', bd=0, padx=8, pady=8,
            cursor='hand2', command=self._refresh_from_js8call)
        _regen_btn.pack(side='left', padx=(0, 4))
        self._attach_tooltip(_regen_btn, self._tt("regen_r"))


        tk.Frame(btn_row, bg=BORDER, width=1, height=30).pack(side='left', padx=(0, 14))

        # ── Group Query: 🎯 label + fused [dropdown | Go] pill + armed badge ──
        tk.Label(btn_row, text='🎯', font=(_FONT_SANS, 11),
                 bg=BG, fg='#E996F5').pack(side='left', padx=(0, 4))

        # Outer container — purple border makes combo+button read as one control
        _grp_pill = tk.Frame(btn_row, bg='#E996F5', bd=1, relief='flat')
        _grp_pill.pack(side='left', padx=(0, 8))

        # Inner frame — dark bg, sits inside the purple border
        _grp_inner = tk.Frame(_grp_pill, bg=CARD)
        _grp_inner.pack(fill='both', expand=True, padx=1, pady=1)

        self._grp_combo_var = tk.StringVar()
        self._grp_combo = ttk.Combobox(_grp_inner,
                                        textvariable=self._grp_combo_var,
                                        font=FONT_MONO, width=10,
                                        state='readonly',
                                        foreground='white')
        self._grp_combo.pack(side='left', padx=(4, 0), pady=2)
        self._attach_tooltip(self._grp_combo, self._tt("group_dropdown"))

        # Thin vertical divider between combo and Go
        tk.Frame(_grp_inner, bg='#E996F5', width=1).pack(
            side='left', fill='y', padx=(8, 0), pady=2)

        tk.Button(_grp_inner, text='Go',
            font=(_FONT_SANS, 10, 'bold'),
            bg=CARD, fg='#E996F5', activebackground='#2a1a2a', activeforeground='#E996F5',
            relief='flat', bd=0, padx=10, pady=3,
            cursor='hand2', command=lambda: _grp_go()
        ).pack(side='left', padx=(6, 2))

        # Armed status badge — shown after Go, hidden until then
        self._grp_armed_var = tk.StringVar(value='')
        self._grp_badge = tk.Label(btn_row, textvariable=self._grp_armed_var,
                                   font=(_FONT_SANS, 8, 'bold'),
                                   bg='#3a1a3a', fg='#E996F5',
                                   relief='flat', padx=4, pady=2)
        self._grp_badge.pack(side='left', padx=(0, 6))
        self._grp_badge.pack_forget()

        def _refresh_grp_combo():
            # Your callsign groups (mirrors JS8Call) — feeds BOTH the group SNR? query
            # dropdown and the relay QUERY CALL audience dropdown so the two stay in sync.
            # The map color list (⊕ Groups color rows) remains a separate concern.
            raw = self._my_groups_var.get() if hasattr(self, '_my_groups_var') else ''
            mine = []
            for g in raw.replace(';', ',').split(','):
                g = g.strip().upper()
                if not g:
                    continue
                if not g.startswith('@'):
                    g = '@' + g
                if g not in mine:
                    mine.append(g)
            # Group SNR? query dropdown — your groups
            self._grp_combo['values'] = mine
            if mine and not self._grp_combo_var.get():
                self._grp_combo_var.set(mine[0])
            # Relay audience dropdown — @ALLCALL first, then your groups
            if hasattr(self, '_relay_aud_combo'):
                self._relay_aud_combo['values'] = ['@ALLCALL'] + mine

        def _grp_go(event=None):
            grp = self._grp_combo_var.get().strip().upper()
            if not grp:
                return
            if not grp.startswith('@'):
                grp = '@' + grp
            arm_ts = time.time()
            self._client._pending_group_queries[grp] = arm_ts
            self._client._last_group_query = (grp, arm_ts)
            self._spot_db.add_log_entry('⊕', 'GRP-ARM', f'Armed {grp}')
            # Show armed badge
            self._grp_armed_var.set(f'✓ {grp}')
            self._grp_badge.pack(side='left', padx=(0, 6))
            # Copy query command to clipboard
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(f'{grp} SNR?')
            except Exception:
                pass
            # Mode-aware transmit gate (shadow/manual/live). Single TX chokepoint;
            # only deliberate user actions call it.
            _gtx = tx_query(self._client, self._spot_db, f'{grp} SNR?', trigger='group-go-button')
            _gnote = {
                'sent':   f'📡 {grp} SNR?  transmitted (LIVE)',
                'staged': f'📝 {grp} SNR?  filled JS8Call box — press send (MANUAL)',
                'shadow': f'🎯  {grp} armed  ·  "{grp} SNR?" copied (SHADOW: no TX)',
                'skip':   f'⚠  {grp} not sent — JS8Call not connected',
                'empty':  f'🎯  {grp} armed',
            }.get(_gtx, f'🎯  {grp} armed  ·  "{grp} SNR?" copied to clipboard')
            self._set_status(_gnote, GREEN if _gtx in ('sent','staged') else MUTED)
            # Auto-clear badge after 10 min
            def _expire():
                still = {g: t for g, t in self._client._pending_group_queries.items()
                         if time.time() - t <= 600}
                self._client._pending_group_queries = still
                if still:
                    last = max(still, key=still.get)
                    self._grp_armed_var.set(f'✓ {last}')
                else:
                    self._grp_armed_var.set('')
                    self._grp_badge.pack_forget()
            self.root.after(600000, _expire)

        # Populate combo after UI settles
        self.root.after(200, _refresh_grp_combo)
        self._refresh_grp_combo = _refresh_grp_combo  # expose for group config save

        _groups_btn = tk.Button(btn_row, text='⊕ Groups',
            font=(_FONT_SANS, 11, 'bold'),
            bg='#3a0a3a', fg='white', activebackground='#5a1a5a', activeforeground='white',
            relief='flat', bd=0, padx=10, pady=8,
            cursor='hand2', command=self._show_group_colors)
        _groups_btn.pack(side='left', padx=(0, 14))
        self._attach_tooltip(_groups_btn, self._tt("groups"))

        tk.Frame(btn_row, bg=BORDER, width=1, height=30).pack(side='left', padx=(0, 14))

        # ── Relay entry: fused pill  [e.g. KD4E  ✕ | Search] ──
        tk.Label(btn_row, text='🔁  Relay:', font=(_FONT_SANS, 10, 'bold'),
                 bg=BG, fg='#29b6f6').pack(side='left', padx=(0, 4))

        # Outer container — blue border makes entry+button one control
        _relay_pill = tk.Frame(btn_row, bg='#29b6f6', bd=1, relief='flat')
        _relay_pill.pack(side='left', padx=(0, 6))

        # Inner frame — dark bg, sits inside the blue border
        _relay_inner = tk.Frame(_relay_pill, bg=CARD)
        _relay_inner.pack(fill='both', expand=True, padx=1, pady=1)

        self._relay_entry_var = tk.StringVar()
        _RELAY_PH = 'e.g. KD4E'
        # _ph_active tracks whether the field currently shows placeholder text
        # (vs real input). The placeholder is NEVER allowed to coexist with typed
        # characters — a KeyPress clears it before the char lands, and a trace
        # sanitizes every change to callsign-legal chars only. This makes the
        # 2nd-callsign mangling ('EKGC1PHY') impossible.
        self._relay_ph_active = [True]
        _re = tk.Entry(_relay_inner, textvariable=self._relay_entry_var,
                       font=FONT_MONO, width=9,
                       bg=CARD, fg='#555555', insertbackground='#29b6f6',
                       relief='flat', bd=0, highlightthickness=0)
        _re.pack(side='left', padx=(4, 0), pady=2)
        self._attach_tooltip(_re, self._tt("relay"))

        _sanitizing = [False]   # re-entrancy guard for the trace

        def _show_ph():
            self._relay_ph_active[0] = True
            _sanitizing[0] = True
            self._relay_entry_var.set(_RELAY_PH)
            _sanitizing[0] = False
            _re.config(fg='#555555')

        def _clear_ph_for_input():
            """Called on first real keypress while placeholder shows: wipe it
            so the typed character lands in an empty field."""
            if self._relay_ph_active[0]:
                self._relay_ph_active[0] = False
                _sanitizing[0] = True
                self._relay_entry_var.set('')
                _sanitizing[0] = False
                _re.config(fg='#29b6f6')

        def _relay_ph_show():
            if not self._relay_entry_var.get():
                _show_ph()

        def _relay_ph_hide(event=None):
            # FocusIn: if placeholder is showing, clear it for input.
            if self._relay_ph_active[0]:
                _clear_ph_for_input()

        def _relay_clear(event=None):
            self._relay_ph_active[0] = False
            _sanitizing[0] = True
            self._relay_entry_var.set('')
            _sanitizing[0] = False
            _show_ph()

        def _on_keypress(event=None):
            # Fires BEFORE the character is inserted. If placeholder is showing,
            # clear it first so we never concatenate input with placeholder text.
            ks = event.keysym if event else ''
            # Ignore pure navigation/modifier keys
            if ks in ('Shift_L','Shift_R','Control_L','Control_R','Alt_L','Alt_R',
                      'Left','Right','Up','Down','Tab','Caps_Lock'):
                return
            _clear_ph_for_input()

        def _sanitize_trace(*_a):
            # Runs on EVERY change to the var. Skips while we set it ourselves.
            if _sanitizing[0] or self._relay_ph_active[0]:
                return
            val = self._relay_entry_var.get()
            cleaned = ''.join(ch for ch in val if ch.isalnum() or ch == '/').upper()
            if cleaned != val:
                _sanitizing[0] = True
                self._relay_entry_var.set(cleaned)
                _sanitizing[0] = False
            _re.config(fg='#29b6f6' if cleaned else '#555555')

        self._relay_entry_var.trace_add('write', _sanitize_trace)
        # No-op: sanitization now happens in the write-trace + KeyPress handler.
        # Kept as the <KeyRelease> binding target so the bind call stays valid.
        def _relay_key(event=None):
            pass

        # ✕ clear inside the pill
        tk.Button(_relay_inner, text='×',
            font=(_FONT_SANS, 11, 'bold'),
            bg=CARD, fg='#555555', activeforeground='#e04040',
            activebackground=CARD, relief='flat', bd=0,
            padx=3, pady=2, cursor='hand2',
            command=_relay_clear
        ).pack(side='left', padx=(1, 0))

        # Thin vertical divider
        tk.Frame(_relay_inner, bg='#29b6f6', width=1).pack(
            side='left', fill='y', padx=(2, 0), pady=2)

        # Audience dropdown — who the QUERY CALL is addressed to (@ALLCALL or a group)
        # Source list = your own callsign groups (self._my_groups_var), set via ⊕ Groups.
        self._my_groups_var = tk.StringVar()
        self._relay_aud_var = tk.StringVar(value='@ALLCALL')
        self._relay_aud_combo = ttk.Combobox(_relay_inner,
            textvariable=self._relay_aud_var,
            font=FONT_MONO, width=9, state='readonly',
            values=['@ALLCALL'])
        self._relay_aud_combo.pack(side='left', padx=(4, 0), pady=2)

        # Thin vertical divider
        tk.Frame(_relay_inner, bg='#29b6f6', width=1).pack(
            side='left', fill='y', padx=(2, 0), pady=2)

        # Search button flush inside the pill
        tk.Button(_relay_inner, text='Search',
            font=(_FONT_SANS, 10, 'bold'),
            bg=CARD, fg='#29b6f6', activebackground='#0d2a3a', activeforeground='#29b6f6',
            relief='flat', bd=0, padx=10, pady=3,
            cursor='hand2', command=self._arm_relay_inline
        ).pack(side='left', padx=(0, 2))

        _re.bind('<KeyPress>',   _on_keypress)   # clears placeholder BEFORE char lands
        _re.bind('<FocusIn>',    _relay_ph_hide)
        _re.bind('<FocusOut>',   lambda e: _relay_ph_show())
        _re.bind('<KeyRelease>',  _relay_key)
        _re.bind('<Return>',     lambda e: self._arm_relay_inline())
        _re.bind('<Escape>',     _relay_clear)
        _show_ph()

        # ── Button row 2: Tools ──────────────────────────────────────────
        btn_row2 = tk.Frame(self.root, bg=BG)
        btn_row2.pack(pady=(0, 8))

        _row2_tips = {
            '⭐ Watched Calls':    "watched_calls",
            '📍 Callsign Unknown': "callsign_unknown",
        }
        for (txt, cmd, bg_, fg_) in [
            ('⭐ Watched Calls',   self._show_watched_calls,   BTN_BG,    'white'),
            ('📍 Callsign Unknown',self._show_missing_stations,BTN_BG,    'white'),
        ]:
            _b2 = tk.Button(btn_row2, text=txt, font=(_FONT_SANS, 11, 'bold'),
                bg=bg_, fg=fg_, activebackground='#243444', activeforeground=fg_,
                relief='flat', bd=0, padx=14, pady=7,
                cursor='hand2', command=cmd)
            _b2.pack(side='left', padx=(0, 6))
            self._attach_tooltip(_b2, self._tt(_row2_tips.get(txt, "")))

        # ── Rebuild Coords / FCC update (rarely used) ────────────────────
        def _fcc_folder():
            if _sys.platform.startswith('linux') or _sys.platform == 'darwin':
                return os.path.join(os.path.expanduser('~'), 'FCC_DB')
            return r'C:\FCC_DB'
        def _do_fcc_update():
            def _run():
                download_and_update_fcc(_fcc_folder(),
                    lambda m: self.root.after(0, lambda msg=m: self._set_status(msg, BLUE)))
                self.root.after(0, lambda: self._set_status('✓  FCC DB updated', GREEN))
            import threading; threading.Thread(target=_run, daemon=True).start()
            self._set_status('⏳  Downloading FCC DB…', BLUE)
        def _do_canadian_update():
            def _run():
                update_canadian_db(_fcc_folder(),
                    lambda m: self.root.after(0, lambda msg=m: self._set_status(msg, BLUE)))
                self.root.after(0, lambda: self._set_status('✓  Canadian DB updated', GREEN))
            import threading; threading.Thread(target=_run, daemon=True).start()
            self._set_status('⏳  Downloading Canadian DB…', BLUE)

        fcc_btn_row = tk.Frame(self.root, bg=BG)
        fcc_btn_row.pack(pady=(0, 6))
        _fcc_tips = {
            '⬇ Update FCC DB':      "update_fcc",
            '⬇ Update Canadian DB': "update_canadian",
        }
        # Map Detail sits on THIS row, not in the settings card above.
        #
        # It was two grid rows in that card. On a 13" laptop at 125% those two
        # rows pushed the bottom status lines off the screen entirely, and the
        # window cannot be dragged up to reach them. The card is a fixed stack
        # with no scrolling, so every row added costs height that a smaller
        # screen may not have. Here it costs nothing -- this row already
        # exists and has space beside the two update buttons.
        #
        # It also belongs here on merit: an optional key most operators never
        # need is maintenance, like the database updates it now sits with.
        def _edit_map_key():
            _map_key_popup(self)

        for (txt, cmd) in [
            ('⬇ Update FCC DB',      _do_fcc_update),
            ('⬇ Update Canadian DB', _do_canadian_update),
            ('🗺 Map Detail…',      _edit_map_key),
        ]:
            _fb = tk.Button(fcc_btn_row, text=txt, font=(_FONT_SANS, 10, 'bold'),
                bg='#1a2a3a', fg=BLUE, activebackground='#243444', activeforeground=BLUE,
                relief='flat', bd=0, padx=12, pady=6,
                cursor='hand2', command=cmd)
            _fb.pack(side='left', padx=(0, 4))
            self._attach_tooltip(_fb, self._tt(_fcc_tips.get(txt, "")))

        # Rebuild Coords is a rarely-used maintenance action, so its button has
        # been removed from this row to reduce clutter. It's still available on
        # demand via the Ctrl+R hotkey (bound both cases, matching the Ctrl+L
        # API-log pattern). The underlying method is unchanged.
        self.root.bind('<Control-r>', lambda e: self._rebuild_fcc_coords())
        self.root.bind('<Control-R>', lambda e: self._rebuild_fcc_coords())

                # ── Status bar ──
        self.status_var = tk.StringVar(
            value="Starting…  ·  Ctrl+Shift+J from anywhere → brings this window to front")
        tk.Label(
            self.root,
            text='Tip: When changing frequency — click 🗑 Clear (QSY) to reset the map',
            font=(_FONT_SANS, 12, 'bold'), bg=BG, fg='#e05555'
        ).pack(pady=(0, 2))

        self.status_lbl = tk.Label(
            self.root, textvariable=self.status_var,
            font=(_FONT_SANS, 12, 'bold'), bg=BG, fg=MUTED,
            wraplength=530, justify='center'
        )
        self.status_lbl.pack(pady=(0, 10), fill='x', padx=20)
        # Re-wrap the status text to the WINDOW width.
        #
        # This used to be bound straight to root with no guard. In Tk a
        # widget's bindtags include its toplevel, so <Configure> on ANY
        # child -- a button, a combobox, the relay strip -- also fires this
        # handler, carrying that child's width. The status label was
        # therefore being told to wrap at the width of whatever small widget
        # last moved, breaking a one-line connection message into four
        # stacked lines and pushing the TX readout off the bottom of the
        # window. Ignoring events from anything but root itself is the whole
        # fix; the resize is also skipped when the value has not changed, so
        # re-wrapping cannot feed itself another Configure.
        def _rewrap_status(e, _lbl=self.status_lbl, _root=self.root):
            if e.widget is not _root:
                return
            want = max(200, e.width - 50)
            try:
                if int(_lbl.cget('wraplength')) != want:
                    _lbl.config(wraplength=want)
            except Exception:
                pass
        self.root.bind('<Configure>', _rewrap_status, add='+')

        # ── TX mode readout + mid-session flip ──
        tx_row = tk.Frame(self.root, bg=BG)
        tx_row.pack(pady=(0, 10))
        self._tx_readout_lbl = tk.Label(
            tx_row, text='TX: \U0001f7e1 MANUAL  ·  fills JS8Call box',
            font=(_FONT_SANS, 11, 'bold'), bg=BG, fg='#e0a000')
        self._tx_readout_lbl.pack(side='left', padx=(0, 8))
