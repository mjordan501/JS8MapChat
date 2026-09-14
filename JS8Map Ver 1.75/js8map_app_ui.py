"""js8map_app_ui.py — HamMapApp UI mixin for JS8MapChat (Phase 6 T2).

Cohesive, low-coupling UI method groups lifted out of the HamMapApp god-class:
  • TX-mode control (apply/repaint/readout/halt-toggle)
  • App dialog windows (group colors, watched calls, unknown calls, API log)
  • FCC / JS8Call.ini file browsing + coordinate rebuild

These stay methods on _AppUIMixin; HamMapApp inherits it, so every call site and
self-reference resolves through the MRO unchanged. The three cross-cluster calls
that remain on the base class (_save_config, _set_status, _sync_groups_from_js8call)
also resolve via the instance. Imports mirror the monolith exactly, including the
sys-as-_sys and tkinter-as-tk aliases. No import back into the monolith (no cycle).
"""

import os
import sys as _sys
import json
import sqlite3
import threading
import tkinter as tk
from tkinter import filedialog

from js8map_theme import (
    BG, BLUE, BORDER, BTN_BG, CARD, FONT_MONO, GEN_BG, GREEN, MUTED, RED, TEXT,
    _FONT_SANS, _FONT_MONO_NAME,
)
from js8map_dialogs import themed_confirm, themed_message, dismiss_toplevel
from js8map_js8call_ini import find_js8call_ini, read_js8call_highlights
from js8map_fcc import (
    set_fcc_db_path, _dat_files_in, build_fcc_db_from_dats, _find_fcc_db,
    load_fcc_info, _zip5_cache, _fcc_result_cache,
)
from js8map_tx import _coerce_tx_mode, _TX_MODE, _TX_HALT_ENABLED
from js8map_web import _data_json_bytes


class _AppUIMixin:
    # ── TX mode control ───────────────────────────────────────────────
    def _apply_tx_mode(self, mode, persist=False, at_startup=False):
        """Set the live TX mode. If the resulting mode is 'live', a confirm
        dialog is shown EVERY time live becomes active (including at startup);
        declining falls back to 'manual'. When persist=True the chosen mode also
        becomes the saved startup default. Updates the on-screen readout."""
        mode = _coerce_tx_mode(mode)

        # Reflect the mode being applied in the radio UP FRONT, before any
        # confirm dialog. Otherwise the selector still shows its initial
        # 'manual' while the Live confirm is open — which reads as "Manual is
        # selected but a Live popup appeared" and looks like the selection
        # wasn't saved. If Live is declined below, the post-dialog re-sync drops
        # the radio back to Manual. (Programmatic .set() does not fire the radio
        # command, so this can't recurse.)
        if hasattr(self, '_tx_mode_sel_var'):
            try: self._tx_mode_sel_var.set(mode)
            except Exception: pass

        if mode == 'live':
            # Confirm every time live becomes active — fat-finger insurance.
            where = 'at startup' if at_startup else 'now'
            ok = themed_confirm(
                self.root,
                'Go LIVE — real RF on the air?',
                'System going LIVE ' + where + ' — Realtime TX about to be '
                'active.\n\nContinue?',
                kind='warning', yes='Continue', no='Cancel', safe_no=True)
            if not ok:
                mode = 'manual'   # decline → safe fallback

        _TX_MODE[0] = mode
        try:
            self._spot_db.add_log_entry('\u2699', 'TX-MODE', 'mode set to ' + mode)
        except Exception:
            pass
        if persist:
            self._tx_startup_default = mode
            self._save_config()
        # Keep the settings selector (if built) in sync without re-triggering.
        if hasattr(self, '_tx_mode_sel_var'):
            try: self._tx_mode_sel_var.set(mode)
            except Exception: pass
        self._repaint_tx_radios()
        self._update_tx_readout()

    def _repaint_tx_radios(self):
        """Make the active mode's radio label green + bold, others neutral.
        Driven off _TX_MODE[0] so green always shows the REAL armed mode (e.g.
        stays on Manual if a Live selection was declined at the confirm)."""
        if not hasattr(self, '_tx_radio_btns'):
            return
        active = _TX_MODE[0]
        for _mode, _btn in self._tx_radio_btns.items():
            try:
                if _mode == active:
                    _btn.config(fg='#33d17a', activeforeground='#33d17a',
                                font=(_FONT_SANS, 11, 'bold'))
                else:
                    _btn.config(fg=TEXT, activeforeground=TEXT,
                                font=(_FONT_SANS, 11))
            except Exception:
                pass

    def _update_tx_readout(self):
        """Refresh the always-visible LIVE / MANUAL / SHADOW indicator."""
        if not hasattr(self, '_tx_readout_lbl'):
            return
        mode = _TX_MODE[0]
        cfg = {'shadow': ('\U0001f534 SHADOW  ·  log only',          '#888'),
               'manual': ('\U0001f7e1 MANUAL  ·  fills JS8Call box', '#e0a000'),
               'live':   ('\U0001f7e2 LIVE  ·  auto-transmits RF',   '#e05555')}
        txt, col = cfg.get(mode, cfg['manual'])
        try:
            self._tx_readout_lbl.config(text='TX: ' + txt, fg=col)
        except Exception:
            pass

    def _on_tx_halt_toggle(self):
        """Settings checkbox: enable/disable the RIG.TX_HALT capability. Persists.
        Also pushes the new state to the browser banner via boot/data so the
        compose-window HALT button enables/disables to match."""
        _TX_HALT_ENABLED[0] = bool(self._tx_halt_var.get())
        self._save_config()
        try:
            self._spot_db.add_log_entry('\u2699', 'TX-HALT-CAP',
                                        'enabled' if _TX_HALT_ENABLED[0] else 'disabled')
        except Exception:
            pass
        # Patch data.json so the browser learns the new capability on next poll.
        try:
            d = json.loads(_data_json_bytes[0])
            d['tx_halt_enabled'] = _TX_HALT_ENABLED[0]
            _data_json_bytes[0] = json.dumps(d, separators=(',', ':')).encode()
        except Exception:
            pass

    def _on_startup_tx_mode_change(self):
        """User picked a startup TX mode in Settings. This persists as the saved
        default AND applies to the current session (with live-confirm if 'live').
        If the user declines the live confirm, both the session and the saved
        default fall back to manual so the radio reflects reality."""
        chosen = _coerce_tx_mode(self._tx_mode_sel_var.get())
        self._apply_tx_mode(chosen, persist=True, at_startup=False)
        # _apply_tx_mode re-syncs _tx_mode_sel_var to the actual resulting mode
        # (e.g. manual if live was declined) and saves config.

    def _show_group_colors(self):
        """⊕ Groups dialog — assign colors to watched groups."""
        import tkinter.colorchooser as colorchooser
        win = tk.Toplevel(self.root)
        win.withdraw()  # build off-screen; map only after layout is complete
        try:
            win.transient(self.root)
        except Exception:
            pass
        win.title("Group Monitor — Color Assignment")
        win.geometry("480x500")
        win.configure(bg=BG)
        win.resizable(False, True)
        tk.Label(win, text="Group Activity Monitor",
                 font=(_FONT_SANS, 12, 'bold'), bg=BG, fg=GREEN).pack(pady=(12,2))
        tk.Label(win,
                 text="Match your JS8Call Settings → General → Station → Callsign Groups.",
                 font=(_FONT_SANS, 9), bg=BG, fg=MUTED).pack(pady=(0,8))

        # ── My Callsign Groups (relay/query audience) — separate from the color list ──
        mg_frame = tk.Frame(win, bg=BG)
        mg_frame.pack(fill='x', padx=14, pady=(0, 8))
        tk.Label(mg_frame, text="My Callsign Groups (relay / query audience):",
                 font=(_FONT_SANS, 9, 'bold'), bg=BG, fg=TEXT).pack(anchor='w')
        _mg_var = tk.StringVar(value=self._my_groups_var.get())
        tk.Entry(mg_frame, textvariable=_mg_var, font=FONT_MONO,
                 bg=CARD, fg=TEXT, insertbackground=TEXT,
                 relief='flat', bd=5,
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=BLUE).pack(fill='x', pady=(2, 0))
        tk.Label(mg_frame,
                 text="Auto-synced from JS8Call.ini on startup."
                      "  ·  Comma-separated, e.g.  @HRMS, @PREPNET, @AMRRON"
                      "  ·  feeds the 🔁 relay audience dropdown.",
                 font=(_FONT_SANS, 8), bg=BG, fg=MUTED).pack(anchor='w')

        canvas  = tk.Canvas(win, bg=BG, highlightthickness=0)
        sb      = tk.Scrollbar(win, orient='vertical', command=canvas.yview)
        inner   = tk.Frame(canvas, bg=BG)
        inner.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0,0), window=inner, anchor='nw')
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True, padx=14)

        work = self._group_config.get_groups()

        def _refresh():
            for w in list(inner.winfo_children()):
                w.destroy()
            for grp in sorted(work):
                color = work[grp]
                row = tk.Frame(inner, bg=BG)
                row.pack(fill='x', pady=2)
                tk.Label(row, text=grp, font=FONT_MONO, bg=BG, fg=TEXT,
                         width=14, anchor='w').pack(side='left')
                sw = tk.Label(row, text='  ', bg=color, width=4, cursor='hand2')
                sw.pack(side='left', padx=(0,6))
                def _pick(g=grp, s=sw):
                    r = colorchooser.askcolor(color=work.get(g,'#888'), parent=self.root)
                    if r and r[1]:
                        work[g] = r[1]; s.config(bg=r[1])
                sw.bind('<Button-1>', lambda e, g=grp, s=sw: _pick(g,s))
                tk.Button(row, text='✕', font=(_FONT_SANS,8),
                          bg=BG, fg=RED, relief='flat', bd=0, cursor='hand2',
                          command=lambda g=grp: (work.pop(g,None), _refresh())
                          ).pack(side='left')

        _refresh()


        def _close_group_window():
            # Immediate destroy (window is withdrawn first inside dismiss_toplevel
            # -> no flash). Even though this window is non-modal, deferring the
            # destroy to after_idle can leave a withdrawn-but-alive HWND that
            # Windows keeps compositing as a blank ghost; destroying now avoids
            # that limbo entirely. The passive sweep is the backstop.
            dismiss_toplevel(win, self.root, immediate=True)

        def _save():
            self._group_config.set_groups(work)
            self._my_groups_var.set(_mg_var.get().strip())
            self._save_config()
            _close_group_window()
            self._set_status(f"✓  Group monitor saved ({len(work)} groups)", GREEN)
            # Refresh the inline group query + relay audience dropdowns
            if hasattr(self, '_refresh_grp_combo'):
                self.root.after(100, self._refresh_grp_combo)

        fr = tk.Frame(win, bg=BG)
        fr.pack(pady=(10,12))
        tk.Button(fr, text='Save', font=(_FONT_SANS,10,'bold'),
                  bg=BTN_BG, fg='white', relief='flat', bd=0,
                  padx=20, pady=7, cursor='hand2', command=_save).pack(side='left', padx=(0,8))
        tk.Button(fr, text='Cancel', font=(_FONT_SANS,10,'bold'),
                  bg='#1a2a3a', fg=MUTED, relief='flat', bd=0,
                  padx=20, pady=7, cursor='hand2',
                  command=_close_group_window).pack(side='left')
        win.protocol("WM_DELETE_WINDOW", _close_group_window)

        # Show only after all children exist and the final geometry is known.
        # This removes both the opening flash and the blank close-frame on Windows.
        try:
            win.update_idletasks()
            px, py = self.root.winfo_rootx(), self.root.winfo_rooty()
            pw, ph = self.root.winfo_width(), self.root.winfo_height()
            ww, wh = win.winfo_width(), win.winfo_height()
            win.geometry(f"+{px + max(0, (pw - ww)//2)}+{py + max(0, (ph - wh)//3)}")
        except Exception:
            pass
        try:
            win.deiconify()
            win.lift()
        except Exception:
            pass

    def _show_watched_calls(self):
        win = tk.Toplevel(self.root)
        win.title("Watched Callsigns")
        win.geometry("440x480")
        win.configure(bg=BG)
        win.resizable(False, True)

        tk.Label(win, text="Watched Callsigns",
                 font=(_FONT_SANS, 12, 'bold'), bg=BG, fg=RED
                 ).pack(pady=(12, 2))
        ini_path = find_js8call_ini()
        ini_note = f"JS8Call.ini: {os.path.basename(os.path.dirname(ini_path))}/JS8Call.ini"\
                   if ini_path else "JS8Call.ini not found — sync unavailable"
        tk.Label(win, text="These stations glow gold on the map.",
                 font=(_FONT_SANS, 10, 'bold'), bg=BG, fg=MUTED
                 ).pack(pady=(0, 2))
        tk.Label(win, text=ini_note,
                 font=(_FONT_SANS, 8), bg=BG, fg=GREEN if ini_path else MUTED
                 ).pack(pady=(0, 2))
        tk.Label(win, text="Double-click a call for FCC / Canadian info.",
                 font=(_FONT_SANS, 8), bg=BG, fg=MUTED
                 ).pack(pady=(0, 8))

        frame = tk.Frame(win, bg=BG)
        frame.pack(fill='both', expand=True, padx=14)

        lb = tk.Listbox(frame, font=FONT_MONO, bg=CARD, fg=TEXT,
                        selectbackground=BLUE, selectforeground='#fff',
                        relief='flat', bd=0, highlightthickness=1,
                        highlightbackground=BORDER, activestyle='none')
        sb = tk.Scrollbar(frame, orient='vertical', command=lb.yview)
        lb.config(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        lb.pack(side='left', fill='both', expand=True)

        current = sorted(self._spot_db.load_watched_calls())
        for c in current:
            lb.insert('end', c)

        add_row = tk.Frame(win, bg=BG)
        add_row.pack(fill='x', padx=14, pady=(6, 0))
        entry_var = tk.StringVar()
        entry = tk.Entry(add_row, textvariable=entry_var, font=FONT_MONO,
                         bg=CARD, fg=TEXT, insertbackground=TEXT,
                         relief='flat', bd=5, width=14,
                         highlightthickness=1, highlightbackground=BORDER)
        entry.pack(side='left')

        def _add():
            c = entry_var.get().replace(' ', '').upper()
            if c and c not in lb.get(0, 'end'):
                lb.insert('end', c)
            entry_var.set('')

        def _remove():
            sel = lb.curselection()
            for i in reversed(sel):
                lb.delete(i)

        def _save():
            calls = set(lb.get(0, 'end'))
            self._spot_db.set_watched_calls(calls)
            win.destroy()
            self._set_status(f"✓  Watched callsigns saved ({len(calls)} entries)", GREEN)

        def _sync_js8():
            highlights = read_js8call_highlights()
            if not highlights:
                themed_message(
                    win,
                    'Not Found',
                    'Could not find or parse JS8Call highlight callsigns.\n\n'
                    'Make sure JS8Call is installed and has callsigns listed under:\n'
                    'Settings → UI → Band & Call Activity → Secondary Highlight Background',
                    kind="info")
                return
            existing = set(lb.get(0, 'end'))
            new_ones = highlights - existing
            for c in sorted(new_ones):
                lb.insert('end', c)
            themed_message(
                win,
                'Synced',
                f'Added {len(new_ones)} callsign(s) from JS8Call highlight list.\n'
                f'Total now: {len(existing | highlights)}\n\n'
                f'Click Save & Close to apply.',
                kind="info")

        tk.Button(add_row, text="Add", font=(_FONT_SANS, 10, 'bold'),
                  bg=BTN_BG, fg='white', relief='flat', bd=0, padx=10, pady=2,
                  cursor='hand2', command=_add).pack(side='left', padx=(5, 0))
        tk.Button(add_row, text="Remove Selected", font=(_FONT_SANS, 10, 'bold'),
                  bg='#5a1a1a', fg='white', relief='flat', bd=0, padx=10, pady=2,
                  cursor='hand2', command=_remove).pack(side='left', padx=(5, 0))

        sync_row = tk.Frame(win, bg=BG)
        sync_row.pack(fill='x', padx=14, pady=(4, 0))
        tk.Button(sync_row,
                  text='🔄 Sync from JS8Call Highlight List',
                  font=(_FONT_SANS, 10, 'bold'), bg='#1a3a1a', fg='#7fff7f',
                  relief='flat', bd=0, padx=10, pady=4,
                  cursor='hand2', command=_sync_js8
                  ).pack(fill='x')
        tk.Label(sync_row,
                 text='Reads JS8Call Settings → UI → Band & Call Activity → Secondary Highlight',
                 font=(_FONT_SANS, 7), bg=BG, fg=MUTED
                 ).pack(pady=(1, 0))

        entry.bind('<Return>', lambda e: _add())

        def _show_info(_e=None):
            """Quick FCC / Canadian info popup for the double-clicked call.
            Reuses load_fcc_info() — the same lookup the map uses — so it shows
            exactly what the map would resolve (US-FCC, or Canadian if that DB is
            loaded). No match -> a friendly note."""
            sel = lb.curselection()
            if not sel:
                return
            call = str(lb.get(sel[0]) or '').strip().upper()
            if not call:
                return
            try:
                info = (load_fcc_info({call}) or {}).get(call, {})
            except Exception:
                info = {}

            pop = tk.Toplevel(win)
            pop.title(f"Info — {call}")
            pop.configure(bg=BG)
            pop.resizable(False, False)
            try:
                pop.transient(win)
            except Exception:
                pass

            tk.Label(pop, text=call, font=(_FONT_SANS, 15, 'bold'),
                     bg=BG, fg=TEXT).pack(padx=18, pady=(14, 6), anchor='w')
            body = tk.Frame(pop, bg=BG)
            body.pack(fill='both', expand=True, padx=18, pady=(0, 8))

            if info:
                city = str(info.get('city') or '').strip()
                state = str(info.get('state') or '').strip()
                loc = ', '.join([x for x in (city, state) if x]) or '—'
                rows = [('Name', info.get('name') or '—'),
                        ('Location', loc),
                        ('Class', info.get('class') or '—')]
                z = str(info.get('zip') or '').strip()
                if z:
                    rows.append(('ZIP', z))
            else:
                rows = [('', 'No FCC / Canadian record found.')]

            for k, v in rows:
                line = tk.Frame(body, bg=BG)
                line.pack(fill='x', anchor='w', pady=1)
                if k:
                    tk.Label(line, text=f'{k}:', font=(_FONT_SANS, 10, 'bold'),
                             bg=BG, fg=MUTED, width=9, anchor='w').pack(side='left')
                tk.Label(line, text=v, font=(_FONT_SANS, 10), bg=BG, fg=TEXT,
                         anchor='w', justify='left').pack(side='left')

            tk.Button(pop, text='OK', font=(_FONT_SANS, 10, 'bold'),
                      bg=BTN_BG, fg='white', relief='flat', bd=0,
                      padx=16, pady=5, cursor='hand2',
                      command=pop.destroy).pack(pady=(2, 12))
            pop.bind('<Escape>', lambda e: pop.destroy())

            # Open over the Watched Callsigns window (same monitor).
            try:
                pop.update_idletasks()
                px = win.winfo_rootx() + max(0, (win.winfo_width() - pop.winfo_reqwidth()) // 2)
                py = win.winfo_rooty() + 60
                pop.geometry(f'+{px}+{py}')
            except Exception:
                pass

        lb.bind('<Double-Button-1>', _show_info)

        tk.Button(win, text="Save & Close", font=(_FONT_SANS, 10, 'bold'),
                  bg=GEN_BG, fg='white', relief='flat', bd=0, padx=16, pady=7,
                  cursor='hand2', command=_save
                  ).pack(pady=(8, 12))

    # ── API Log dialog ────────────────────────────────────────

    def _show_missing_stations(self):
        """📍 Callsign Unknown — live list of callsigns that could not be
        placed on the map.  Updates every 5 s as new unmapped calls arrive.
        Clears on 🗑 Clear (QSY) and resets each session."""
        win = tk.Toplevel(self.root)
        win.title('Callsign Unknown')
        win.geometry('420x480')
        win.configure(bg=BG)
        win.resizable(False, True)

        tk.Label(win, text='📍 Callsign Unknown',
                 font=(_FONT_SANS, 12, 'bold'), bg=BG, fg=RED).pack(pady=(12, 2))
        tk.Label(win,
                 text='Callsigns decoded by JS8Call that could not be\n'
                      'placed on the map — no grid, no FCC / Canadian DB match.',
                 font=(_FONT_SANS, 10), bg=BG, fg=MUTED, justify='center').pack(pady=(0, 4))

        count_var = tk.StringVar(value='0 stations')
        tk.Label(win, textvariable=count_var,
                 font=(_FONT_SANS, 10, 'bold'), bg=BG, fg=TEXT).pack(pady=(0, 6))

        frame = tk.Frame(win, bg=BG)
        frame.pack(fill='both', expand=True, padx=12, pady=(0, 6))

        lb = tk.Listbox(frame, font=FONT_MONO, bg=CARD, fg=TEXT,
                        selectbackground=BLUE, selectforeground='#fff',
                        relief='flat', bd=0, highlightthickness=1,
                        highlightbackground=BORDER, activestyle='none')
        sb = tk.Scrollbar(frame, orient='vertical', command=lb.yview)
        lb.config(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        lb.pack(side='left', fill='both', expand=True)

        _last_shown: set = set()

        def _refresh():
            nonlocal _last_shown
            if not win.winfo_exists():
                return
            current = self._unmapped_calls
            if current != _last_shown:
                lb.delete(0, 'end')
                for c in sorted(current):
                    lb.insert('end', c)
                n = len(current)
                count_var.set(f'{n} station{"s" if n != 1 else ""}')
                _last_shown = set(current)
            win.after(5000, _refresh)

        _refresh()

        tk.Label(win,
                 text='Updates automatically as new data arrives.\n'
                      'Clears when you click 🗑 Clear (QSY).',
                 font=(_FONT_SANS, 9), bg=BG, fg=MUTED, justify='center').pack(pady=(0, 4))

        tk.Button(win, text='Close', font=(_FONT_SANS, 10, 'bold'),
                  bg=BTN_BG, fg='white', relief='flat', bd=0,
                  padx=12, pady=5, cursor='hand2',
                  command=win.destroy).pack(pady=(0, 10))

    def _show_api_log(self):
        win = tk.Toplevel(self.root)
        win.title("JS8Call API Log")
        win.geometry("820x560")
        win.configure(bg=BG)
        win.resizable(True, True)

        tk.Label(win, text="Recent JS8Call API Messages",
                 font=(_FONT_SANS, 12, 'bold'), bg=BG, fg=RED
                 ).pack(pady=(12, 4))
        tk.Label(win,
                 text="↓ = received from JS8Call   → = sent to JS8Call",
                 font=(_FONT_SANS, 10, 'bold'), bg=BG, fg=MUTED
                 ).pack(pady=(0, 8))

        frame = tk.Frame(win, bg=BG)
        frame.pack(fill='both', expand=True, padx=12, pady=(0, 8))

        txt = tk.Text(frame, font=(_FONT_MONO_NAME, 9), bg=CARD, fg=TEXT,
                      insertbackground=TEXT, relief='flat', wrap='none',
                      highlightthickness=1, highlightbackground=BORDER)
        vsb = tk.Scrollbar(frame, orient='vertical',   command=txt.yview)
        hsb = tk.Scrollbar(frame, orient='horizontal', command=txt.xview)
        txt.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side='right',  fill='y')
        hsb.pack(side='bottom', fill='x')
        txt.pack(side='left', fill='both', expand=True)

        def _refresh_log():
            txt.config(state='normal')
            txt.delete('1.0', 'end')
            fresh = self._spot_db.get_api_log(300)
            spot_ct = self._spot_db.get_spot_count()
            msg_ct  = getattr(self._client, '_msg_count', 0)
            hdr = (f'=== JS8Call API Log  ·  {spot_ct:,} spots  ·  '
                   f'{msg_ct} msgs received ===\n\n')
            body = ''.join(
                f'[{t}] {d}  {m}\n   {p}\n\n'
                for d, m, p, t in fresh
            ) if fresh else 'No API messages yet. Check JS8Call TCP server is ON.'
            txt.insert('1.0', hdr + body)
            txt.config(state='disabled')
            if win.winfo_exists():
                win.after(2000, _refresh_log)

        _refresh_log()

        def _copy():
            win.clipboard_clear()
            win.clipboard_append(txt.get('1.0', 'end'))
        tk.Button(win, text="Copy to Clipboard",
                  font=(_FONT_SANS, 10, 'bold'), bg=BTN_BG, fg='white',
                  relief='flat', bd=0, padx=12, pady=5,
                  cursor='hand2', command=_copy
                  ).pack(pady=(0, 10))

    # ── FCC DB browsing ───────────────────────────────────────

    def _set_ini_path(self, path: str):
        """Save a JS8Call.ini override path, update the label, re-sync groups."""
        path = path.strip()
        self._js8call_ini_path = path
        self._save_config()
        if path:
            parts = path.replace('\\', '/').split('/')
            display = '/'.join(parts[-2:]) if len(parts) > 2 else path
            self.ini_label.config(text=display, fg=TEXT)
        else:
            self.ini_label.config(text='Auto-detect', fg=MUTED)
        # Re-run group sync immediately with the new path
        self._sync_groups_from_js8call(at_startup=False)

    def _browse_ini(self):
        """File picker for JS8Call.ini — saves path and re-syncs groups.
        Start dir is platform + TX-Halt-aware:
          Windows unchecked → AppData\\Local\\JS8Call\\  (standard, one click)
          Windows checked   → AppData\\Local\\          (navigate into Improved folder)
          Linux             → ~/.config/JS8Call/ or ~/.config/
        """
        cur = getattr(self, '_js8call_ini_path', '').strip()
        if cur and os.path.isfile(cur):
            # Already have a valid path — reopen its folder
            initial = os.path.dirname(cur)
        elif _sys.platform.startswith('linux'):
            home = os.path.expanduser('~')
            std = os.path.join(home, '.config', 'JS8Call')
            initial = std if os.path.isdir(std) else os.path.join(home, '.config')
        elif _sys.platform == 'darwin':
            initial = os.path.join(os.path.expanduser('~'), 'Library', 'Preferences')
        else:
            local = (os.environ.get('LOCALAPPDATA') or
                     os.path.join(os.path.expanduser('~'), 'AppData', 'Local'))
            tx_halt_on = getattr(self, '_tx_halt_var', None) and self._tx_halt_var.get()
            if tx_halt_on:
                initial = local
            else:
                std = os.path.join(local, 'JS8Call')
                initial = std if os.path.isdir(std) else local
        path = filedialog.askopenfilename(
            title="Select JS8Call.ini",
            filetypes=[
                ("JS8Call settings", "JS8Call.ini"),
                ("INI files",        "*.ini"),
                ("All files",        "*.*"),
            ],
            initialdir=initial,
        )
        if not path:
            return
        self._set_ini_path(path)
        self._set_status(
            f"\u2713  JS8Call.ini set  \u00b7  {os.path.basename(os.path.dirname(path))}/JS8Call.ini",
            GREEN)

    def _set_fcc_db_path(self, path: str):
        path = path.strip()
        set_fcc_db_path(path)
        if path:
            parts = path.replace('\\', '/').split('/')
            display = '/'.join(parts[-2:]) if len(parts) > 2 else path
            self.fcc_label.config(text=display, fg=TEXT)
        else:
            self.fcc_label.config(text='Auto-detect', fg=MUTED)

    def _browse_fcc_db(self):
        candidates = [r'C:\FCC DB', r'C:\hamdb', r'C:\fccdb', r'D:\FCC DB', r'D:\hamdb']
        if _sys.platform.startswith('linux') or _sys.platform == 'darwin':
            home = os.path.expanduser('~')
            candidates = [
                os.path.join(home, 'FCC_DB'), os.path.join(home, 'hamdb'),
                os.path.join(home, 'fcc_db'), os.path.join(home, '.local', 'share', 'fcc_db'),
                '/opt/fcc_db',
            ]
        for candidate in candidates:
            if os.path.isdir(candidate):
                initial = candidate
                break
        else:
            initial = os.path.expanduser('~')

        path = filedialog.askopenfilename(
            title="Select AM.dat, EN.dat — or an existing ham.db",
            filetypes=[
                ("FCC DAT or SQLite", "AM.dat EN.dat *.db *.sqlite *.sqlite3 ham"),
                ("FCC DAT files",     "AM.dat EN.dat"),
                ("SQLite Database",   "*.db *.sqlite *.sqlite3"),
                ("All files",         "*.*"),
            ],
            initialdir=initial
        )
        if not path:
            return

        fname = os.path.basename(path).upper()
        if fname in ('AM.DAT', 'EN.DAT'):
            folder = os.path.dirname(path)
            am, en = _dat_files_in(folder)
            if not am:
                themed_message(self.root, "Missing DAT File",
                    f"Both AM.dat and EN.dat must be in the same folder.\n"
                    f"Only found one in:\n{folder}", kind="error")
                return
            self._set_fcc_db_path(folder)
            self._set_status("⏳  Building FCC database from DAT files…", MUTED)
            self.root.update()
            db = build_fcc_db_from_dats(
                folder,
                status_cb=lambda m: (self._set_status(m, MUTED), self.root.update())
            )
            if db:
                self._set_status("✓  FCC database ready  ·  ham.db built", GREEN)
            else:
                self._set_status("✗  Failed to build FCC database from DAT files.", RED)
        else:
            try:
                conn = sqlite3.connect(path)
                conn.execute("SELECT callsign FROM callsigns LIMIT 1").fetchone()
                conn.close()
                self._set_fcc_db_path(path)
                self._set_status(f"✓  FCC database set  ·  {os.path.basename(path)}", GREEN)
            except Exception:
                themed_message(self.root, "Invalid Database",
                    "That file doesn't appear to be a valid FCC callsign database.\n"
                    "Please select AM.dat, EN.dat, or an existing ham.db file.", kind="error")

    def _rebuild_fcc_coords(self):
        """Download GeoNames ZIP5 data and rebuild lat/lon in ham.db."""
        db_path = _find_fcc_db()
        if not db_path:
            themed_message(self.root, "No FCC Database",
                "No FCC database found. Please browse to AM.dat/EN.dat first.", kind="warning")
            return
        folder = os.path.dirname(db_path)
        am, en = _dat_files_in(folder)
        if not am:
            themed_message(self.root, "DAT Files Not Found",
                "AM.dat / EN.dat not found alongside ham.db.\n"
                "Cannot rebuild — need the original DAT files.", kind="warning")
            return
        if not themed_confirm(self.root, "Rebuild FCC Coordinates",
            "This will:\n"
            "1. Download ZIP code coordinates from GeoNames (~2MB)\n"
            "2. Rebuild ham.db with accurate lat/lon per callsign\n"
            "3. Takes 2-5 minutes\n\n"
            "Requires internet access. Continue?", kind="question", yes="Continue", no="Cancel", safe_no=True):
            return
        _zip5_cache.clear()
        _fcc_result_cache.clear()
        self._set_status("⏳  Rebuilding FCC coordinates — downloading ZIP data…", MUTED)
        self.root.update()

        def _do_rebuild():
            # Force rebuild by deleting ham.db then rebuilding
            try:
                os.remove(db_path)
            except Exception:
                pass
            build_fcc_db_from_dats(
                folder,
                status_cb=lambda m: self.root.after(0, lambda msg=m:
                    (self._set_status(msg, MUTED), self.root.update()))
            )
            self.root.after(0, lambda: self._set_status(
                "✓  FCC coordinates rebuilt — ZIP5 accuracy (~5mi) now active", GREEN))

        threading.Thread(target=_do_rebuild, daemon=True).start()
