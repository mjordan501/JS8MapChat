from __future__ import annotations

"""FastChatPopup — the standalone QSO/transmit popup, extracted from main_window.py.

Phase 2 of the JS8FastChat <-> JS8Map integration refactor (see
JS8FastChat_Ver_3_5_Integration_Handoff_Plan.docx, Phase 2: "extract the
popups out of main_window.py into their own UI modules").

This is a behavior-preserving extraction, not a rewrite. The popup still
needs a wide slice of MainWindow's state and helpers (palette, theme, rig
vars, the TX path, other popups it opens, the live-reply registries that the
main refresh loop pushes updates through). Rather than guess at trimming
that down — which the project's own lessons-learned explicitly warn against
("do not guess... capture it") — this module takes the owning MainWindow
instance as `host` and calls back through it for everything it needs. That
keeps today's behavior identical while giving Phase 3 (a standalone popup
host/launcher) a single, named seam to target: replace `host` with a small
set of injected getters/callbacks (the same pattern core.py already uses for
the service layer) once the popup needs to run without a full MainWindow.

Moved here from MainWindow (now thin call-throughs on MainWindow, or removed
entirely where this module owns the only call site):
    - open_qso_popup            -> FastChatPopup.open
    - qso_latest_text           -> FastChatPopup.latest_text
    - qso_text_panel            -> FastChatPopup.text_panel
    - _color_latest_red         -> FastChatPopup.color_latest_red
    - update_qso_latest_widget  -> FastChatPopup.update_latest_widget

Left on MainWindow (shared beyond this popup, so they did not move):
    - _color_from_marker_red    (also used by Group Activity detail views)
    - refresh_open_qso_popups   (called from the data-refresh pipeline;
                                  delegates into this module's
                                  update_latest_widget per open popup)
    - self._qso_latest_widgets, self._freq_combo_widgets
                                 (shared registries other code walks too)
"""

import re
import tkinter as tk
from datetime import datetime, timezone
from tkinter import messagebox, simpledialog, ttk

from ..constants import APP_VERSION, LIVE_RIG_GREEN, SPEEDS, TIME_FILTERS
from ..utils import base_call, display_person_name, fmt_age
from .main_window_ui_substrate import popup_font
from .tx_indicator import TxIndicator


class FastChatPopup:
    """Builds and refreshes the FastChat (per-callsign QSO/transmit) popup.

    One instance is created per open() call, mirroring the previous
    MainWindow.open_qso_popup behavior (each call opens a new Toplevel).
    """

    def __init__(self, host) -> None:
        self.host = host

    # -- text/content helpers (previously MainWindow.qso_latest_text) --

    def latest_text(self, call: str) -> str:
        host = self.host
        row = host.call_info._best_summary_row_for_call(call)
        if not row:
            return (
                "I HEAR THEM        --\n"
                "THEY HEAR ME       --\n"
                "DISTANCE           --\n"
                "HEARD              --\n\n"
                "Latest:\n"
                f"No latest decoded reply from {call} yet."
            )
        try:
            hear_them = f"{float(row.snr):+.0f} dB"
        except Exception:
            hear_them = f"{row.snr} dB" if str(row.snr or "").strip() else "--"
        they_hear = host.call_info._parse_reported_snr_for_me(call)
        distance = host.call_info._distance_text_for_call(call)
        latest = host.call_info._latest_display_text_for_call(call)
        age = fmt_age(row.timestamp, datetime.now())
        lines = [
            f"I HEAR THEM        {hear_them}",
            f"THEY HEAR ME       {they_hear}",
            f"DISTANCE           {distance}",
            f"HEARD              {age}",
            "",
            "Latest:",
            latest,
        ]
        return "\n".join(lines)

    # -- widget helpers (previously MainWindow.qso_text_panel / _color_latest_red) --

    def text_panel(self, parent, title: str, text: str, height: int, editable: bool = False, hide_title_on_typing: bool = False, expand: bool | None = None) -> tk.Text:
        host = self.host
        p = host.pal()
        is_dark = host.theme_var.get() == "Dark"
        frame = tk.Frame(parent, bg=p.panel, highlightbackground="#ffffff" if is_dark else p.border, highlightthickness=1)
        frame.pack(fill="both", expand=(not editable) if expand is None else expand, padx=8, pady=(4, 2))
        title_lbl = tk.Label(frame, text=title, bg=p.panel, fg=p.text, font=popup_font("Arial", 17, "bold"), anchor="w")
        title_lbl.pack(fill="x")
        txt_holder = tk.Frame(frame, bg=p.entry_bg if editable else ("#0f172a" if is_dark else "#ffffff"))
        txt_holder.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        txt = host.text_box(txt_holder, height=height, bg=p.entry_bg if editable else ("#0f172a" if is_dark else "#ffffff"), fg=p.entry_fg if editable else p.text, insertbackground=p.entry_fg, font=popup_font("Consolas", 16, "bold"))
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", text)
        if editable:
            host.attach_uppercase_text(txt)

            def clear_text_panel():
                txt.delete("1.0", "end")
                getattr(txt, "_mj_update_title", lambda: None)()
                txt.focus_set()
                host.set_status(f"{title} cleared.")

            clear_btn = tk.Button(txt_holder, text="X", command=clear_text_panel, bg=p.button, fg=p.text, activebackground=p.button_active, relief="raised", bd=1, font=popup_font("Arial", 10, "bold"), width=2)
            clear_btn.place(relx=1.0, x=-4, y=4, anchor="ne")
            txt._mj_clear_text = clear_text_panel
            if hide_title_on_typing:
                def update_title(_event=None):
                    has_text = bool(txt.get("1.0", "end-1c").strip())
                    if has_text and title_lbl.winfo_ismapped():
                        title_lbl.pack_forget()
                    elif not has_text and not title_lbl.winfo_ismapped():
                        title_lbl.pack(fill="x", before=txt_holder)
                txt._mj_update_title = update_title
                txt.bind("<KeyPress>", lambda _e: txt.after(1, update_title), add="+")
                txt.bind("<KeyRelease>", lambda _e: txt.after(1, update_title), add="+")
                txt.bind("<<Paste>>", lambda _e: txt.after(5, update_title), add="+")
        else:
            txt.configure(state="disabled")
        return txt

    def color_latest_red(self, widget) -> None:
        """Color the 'Latest:' label and the reply beneath it red in the
        Incoming / Latest Reply panel."""
        self.host.call_info._color_from_marker_red(widget, "Latest:", tag="latest_red")

    def update_latest_widget(self, call: str, widget: tk.Text) -> None:
        """Repaint an open FastChat popup after a DB/API activity update.

        The popup registers its inline chat Text in host._qso_latest_widgets and
        attaches a `_mj_repaint` callback (repaints HEAR/HEARD stats + the
        scrolling chat). The data-refresh pipeline calls this for every open
        popup, so the chat auto-updates on the same API tick as the rest of the
        app. Falls back to the legacy latest-reply repaint if no callback set.
        """
        host = self.host
        with host.soft(f"update_qso_latest_widget {call}"):
            if not widget.winfo_exists():
                return
            repaint = getattr(widget, "_mj_repaint", None)
            if callable(repaint):
                repaint()
                return
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.insert("1.0", self.latest_text(call))
            self.color_latest_red(widget)
            widget.configure(state="disabled")

    # -- HEAR/HEARD stats + inline chat (merged History) --

    def _parse_db(self, value) -> "float | None":
        """Pull a signed dB number out of a string like '+10 dB' or '-1 dB'."""
        m = re.search(r"[-+]?\d+(?:\.\d+)?", str(value or ""))
        try:
            return float(m.group()) if m else None
        except Exception:
            return None

    def _snr_color(self, snr, is_dark: bool) -> str:
        """JS8Map-style signal coloring. Thresholds (dB): >= +5 strong (green),
        0..+4 moderate (amber), < 0 weak (red). Tweak the cutoffs here to match
        JS8Map's exact bands."""
        if snr is None:
            return self.host.pal().text
        if snr >= 5:
            return "#33ff77" if is_dark else "#1a7f37"
        if snr >= 0:
            return "#ffc233" if is_dark else "#9a6a00"
        return "#ff6b4a" if is_dark else "#c0392b"

    def _hear_heard_rows(self, call: str):
        """Return [(label, value_text, snr_or_None), ...] for the stats panel.
        snr is set only for the two signal rows so they can be color-coded."""
        host = self.host
        row = host.call_info._best_summary_row_for_call(call)
        if not row:
            return [("I HEAR THEM", "--", None), ("THEY HEAR ME", "--", None),
                    ("DISTANCE", "--", None), ("HEARD", "--", None)]
        try:
            snr_them = float(row.snr)
            hear_them = f"{snr_them:+.0f} dB"
        except Exception:
            snr_them = None
            hear_them = f"{row.snr} dB" if str(row.snr or "").strip() else "--"
        they_raw = host.call_info._parse_reported_snr_for_me(call)
        snr_me = self._parse_db(they_raw)
        distance = host.call_info._distance_text_for_call(call)
        age = fmt_age(row.timestamp, datetime.now())
        return [("I HEAR THEM", hear_them, snr_them),
                ("THEY HEAR ME", str(they_raw), snr_me),
                ("DISTANCE", str(distance), None),
                ("HEARD", str(age), None)]

    @staticmethod
    def _ts_utc(value) -> "datetime | None":
        """Normalize a history timestamp to a UTC-aware datetime.

        Two sources feed the chat with DIFFERENT clocks:
          - outgoing (FastChat TX log) are written UTC, suffixed ' UTC'.
          - incoming (JS8Map spots DB) are naive *local* Windows time.
        Sorting/displaying the raw strings interleaves them wrong (all local
        '06:..' sort above all UTC '10:.. UTC'). Convert everything to UTC so
        the conversation reads in true order on one clock. Mirrors the approach
        in MainWindow._group_activity_timestamp_utc.
        """
        text = str(value or "").strip()
        if not text:
            return None
        is_utc = text.upper().endswith("UTC")
        core = text.upper().replace("UTC", "").strip()[:19]
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(core, fmt)
                # UTC-labeled -> tag as UTC; naive -> treat as local, convert.
                return dt.replace(tzinfo=timezone.utc) if is_utc else dt.astimezone(timezone.utc)
            except Exception:
                pass
        return None

    @staticmethod
    def _clean_msg(s) -> str:
        """Normalize a decoded message body for display.

        JS8 traffic is an ASCII protocol, but JS8Map stores the raw decode,
        which can carry non-ASCII byte runs (mojibake like 'ÂÂÂ¢...' and the
        trailing EOM glyph) that JS8Call cleans before showing. Keep printable
        ASCII + newlines, turn anything else into a space, and collapse the gaps
        so the chat reads the same clean text JS8Call shows.
        """
        if not s:
            return ""
        out = "".join(ch if (ch == "\n" or 32 <= ord(ch) <= 126) else " " for ch in str(s))
        return "\n".join(" ".join(seg.split()) for seg in out.split("\n")).strip()

    def _render_chat(self, txt: tk.Text, call: str, time_label: str, needle: str) -> None:
        """Populate the inline chat: my sent traffic to this call (gold) and
        traffic received from this call (green), interleaved chronologically.
        Mirrors the data merge in MainWindow.open_history_popup, minus SNR/HB
        clutter, with received shown green instead of red."""
        host = self.host
        base = base_call(call)
        needle = (needle or "").upper().strip()
        with host.soft(f"render qso chat {call}", log=False):
            if not txt.winfo_exists():
                return
            txt.configure(state="normal")
            txt.delete("1.0", "end")

            def matches(hay: str) -> bool:
                return (not needle) or needle in hay.upper()

            rows = [r for r in host.reader.read_activity(time_label, limit=1000) if r.base == base]
            incoming: dict = {}
            # An SNR reply reaches the DB TWICE, ~30s apart, in two formats:
            # the machine-tagged "KW3KW SNR +03 [RSNR:+03]" lands immediately,
            # the human form "KJ4UYO: KW3KW SNR +03" only on JS8Map's NEXT poll
            # (measured 2026-07-23: 17:52:42 tagged vs 17:52:41 plain, arriving
            # 13:52:42 and 13:53:12). Suppressing the tagged form therefore cost
            # 30s of latency. So: accept BOTH, then dedupe on the VALUE so only
            # one line renders. is_history_clutter still hides SPOT / HEARTBEAT /
            # SNR? for this view and every other one.
            _snr_seen: dict = {}   # (sender, snr value) -> (timestamp, incoming key)

            def _snr_dedupe(value, sender, ts, key) -> bool:
                """True if this row should be SKIPPED as a duplicate report."""
                if value is None:
                    return False
                dk = (base_call(sender), value)
                prev = _snr_seen.get(dk)
                if prev is not None:
                    if ts >= prev[0]:
                        return True
                    incoming.pop(prev[1], None)
                _snr_seen[dk] = (ts, key)
                return False

            for r in rows:
                _snr_val = host.snr_report_value(r.text)
                if host.is_history_clutter(r) and _snr_val is None:
                    continue
                hay = " ".join([str(r.timestamp), str(r.call), str(r.to_call or ""), str(r.snr or ""), str(r.text or "")])
                if not matches(hay):
                    continue
                key = (str(r.timestamp), (r.text or "").strip())
                if _snr_dedupe(_snr_val, r.call, str(r.timestamp), key):
                    continue
                incoming[key] = {"ts": str(r.timestamp), "from": r.call, "to": r.to_call or "", "snr": r.snr, "text": (r.text or "").strip()}
            for r in host.captured_history_for(call, time_label, needle):
                key = (str(r.get("timestamp", "")), str(r.get("text", "")).strip())
                if key not in incoming:
                    # The capture store now KEEPS SNR reports, so it can supply a
                    # second copy of one already merged above. Dedupe here too.
                    if _snr_dedupe(host.snr_report_value(r.get("text", "")), r.get("from", call), str(r.get("timestamp", "")), key):
                        continue
                    incoming[key] = {"ts": str(r.get("timestamp", "")), "from": r.get("from", call), "to": r.get("to", ""), "snr": r.get("snr", ""), "text": str(r.get("text", "")).strip()}

            events = []  # (utc_dt, kind, header, body)
            for d in incoming.values():
                udt = self._ts_utc(d["ts"])
                disp = udt.strftime("%Y-%m-%d %H:%M:%S UTC") if udt else d["ts"]
                events.append((udt, "rx", f"[{disp}] {d['from']} -> {d['to']}  SNR {d['snr']}", self._clean_msg(d["text"])))

            # Outgoing (gold) side merges TWO sources of OUR sends, deduped:
            #   1) FastChat's own TX log (TX_HISTORY_PATH via
            #      local_outgoing_history_for) -- frames WE sent from this app.
            #   2) JS8Map's published outgoing file (OBSERVED_TX_PATH via
            #      observed_outgoing_history_for) -- frames fired from the JS8Map UI
            #      (e.g. a HEARING?/SNR?), which go straight to JS8Call and never
            #      touch our TX log. Without this, half of a JS8Map-origin exchange
            #      (the part WE sent) is invisible here. Keyed by (UTC ts to the
            #      minute, cleaned text) so a frame present in both renders once.
            #      No clutter filter on the gold side -- always show what we sent.
            sent: dict = {}

            def _sent_key(udt, body):
                kts = udt.strftime("%Y-%m-%d %H:%M") if udt else ""
                return (kts, (body or "").strip().upper())

            for r in host.observed_outgoing_history_for(call, time_label, needle):
                ts = str(r.get("timestamp", ""))
                udt = self._ts_utc(ts)
                disp = udt.strftime("%Y-%m-%d %H:%M:%S UTC") if udt else ts
                body = self._clean_msg(r.get("text", ""))
                sent[_sent_key(udt, body)] = (udt, "tx", f"[{disp}] {r.get('from', 'KW3KW')} -> {r.get('to', call)}  {r.get('context', 'TX')}", body)

            for r in host.local_outgoing_history_for(call, time_label, needle):
                ts = str(r.get("timestamp", ""))
                udt = self._ts_utc(ts)
                disp = udt.strftime("%Y-%m-%d %H:%M:%S UTC") if udt else ts
                body = self._clean_msg(r.get("text", ""))
                # FastChat's own log wins on collision (carries the real context label).
                sent[_sent_key(udt, body)] = (udt, "tx", f"[{disp}] {r.get('from', 'TX')} -> {r.get('to', call)}  {r.get('context', 'TX')}", body)

            events.extend(sent.values())
            _epoch = datetime.min.replace(tzinfo=timezone.utc)
            events.sort(key=lambda e: e[0] or _epoch)

            if not events:
                extra = f" matching '{needle}'" if needle else ""
                txt.insert("1.0", f"No conversation with {call}{extra} in window: {time_label}.")
            else:
                for _udt, kind, header, body in events:
                    txt.insert("end", header + "\n", ("hdr_muted",))
                    start = txt.index("end-1c")
                    txt.insert("end", (body or "") + "\n\n")
                    txt.tag_add("tx_gold" if kind == "tx" else "rx_green", start, "end-1c")

            # Live preview of a long incoming transmission still in progress:
            # JS8Map publishes the frame-by-frame partial to the shared file
            # (see MainWindow.incoming_partial_for). Show it as a distinct
            # "receiving…" line at the very bottom -- a search filter suppresses
            # it (it isn't a stored message), and it's cleared automatically when
            # the real directed row lands on the next repaint. Never lets a
            # preview error break the chat render.
            if not needle:
                try:
                    part = host.incoming_partial_for(call)
                except Exception:
                    part = None
                body = self._clean_msg(part.get("text", "")) if part else ""
                if body:
                    is_dark = host.theme_var.get() == "Dark"
                    txt.tag_configure("rx_partial",
                                      foreground=("#7fd0ff" if is_dark else "#2563a8"))
                    to_disp = str(part.get("from", call)).upper()
                    frames = part.get("frags", "")
                    fcount = f"  ({frames} frames)" if frames else ""
                    txt.insert("end", f"[receiving…] {to_disp}{fcount}\n", ("hdr_muted",))
                    start = txt.index("end-1c")
                    txt.insert("end", body + " …\n\n")
                    txt.tag_add("rx_partial", start, "end-1c")
            txt.configure(state="disabled")

    # -- the popup itself (previously MainWindow.open_qso_popup) --

    def open(self) -> None:
        host = self.host
        if not host.selected_call:
            messagebox.showinfo("FastChat", "Select a callsign first.", parent=host)
            return
        call = host.selected_call
        # Single-instance guard: reuse an existing popup for this callsign.
        try:
            _existing = host._qso_latest_widgets.get(base_call(call))
            if _existing is not None and _existing.winfo_exists():
                _win = _existing.winfo_toplevel()
                _win.deiconify()
                _win.lift()
                _win.focus_force()
                return
        except Exception:
            pass
        p = host.pal()
        is_dark = host.theme_var.get() == "Dark"
        top = tk.Toplevel(host)
        top.title(f"FastChat - {call} — Ver {APP_VERSION}")
        top.minsize(900, 520)
        top.configure(bg=p.panel)
        host.bind_popup_layout(top, "fastchat_geometry", "1020x620")

        def qso_button(parent, text, command=None, width=10, bg=None, fg=None):
            return tk.Button(parent, text=text, bg=bg if bg is not None else p.button, fg=fg if fg is not None else p.text, activebackground=p.button_active, font=popup_font("Arial", 14, "bold"), width=width, command=command, relief="raised", bd=1)

        def live_rig_entry(parent, var, width):
            ent = tk.Entry(parent, textvariable=var, width=width, bg=p.entry_bg, fg=host.rig_green(), insertbackground=host.rig_green(), relief="solid", bd=1, font=popup_font("Consolas", 16, "bold"), justify="center")
            ent._mj_rig_field = True
            ent.bind("<Return>", host.on_rig_field_commit)
            ent.bind("<FocusOut>", host.on_rig_field_commit)
            return ent

        header = tk.Frame(top, bg=p.panel)
        header.pack(fill="x", padx=6, pady=6)
        # Phase 9: order matches the main window's JS8 Commands grid
        # (SNR?, INFO?, GRID?, STATUS?, HEARING?) and GRID? is added here so the
        # two windows are consistent. Left-click REQUESTS theirs (host.build_command);
        # right-click SENDS ours to THIS popup's station (call), same confirm
        # popup as the main window. Button-2 covers trackpad right-click.
        #
        # BOTH clicks must name `call` -- THIS popup's station, captured when the
        # window opened. The left-click used to call host.build_command(x) with no
        # target, which fell through to the MAIN window's live selected_call. Open
        # FastChat for KE2KN, then click KD9DSS on the main list, then press
        # HEARING? here, and the frame went out to KD9DSS while this window still
        # said KE2KN. Observed on the air 2026-07-12: "KW3KW -> KD9DSS HEARING?"
        # sent twice from the KE2KN popup. A popup is a conversation with ONE
        # station; it must never take its target from anywhere else.
        _qso_rclick_send = {"SNR?", "INFO?", "GRID?", "STATUS?"}
        for label in ("SNR?", "INFO?", "GRID?", "STATUS?", "HEARING?"):
            _qb = qso_button(header, label, command=lambda x=label: host.build_command(x, target_call=call), width=10)
            _qb.pack(side="left", padx=(0, 5), ipady=3)
            host.tx_lock.bind_button(_qb)
            host._attach_tooltip(_qb, host._tt("cmd_" + label))
            if label in _qso_rclick_send:
                _qb.bind("<Button-3>", lambda _e, x=label: host.open_send_mine_popup(x, target_call=call, parent=top), add="+")
                _qb.bind("<Button-2>", lambda _e, x=label: host.open_send_mine_popup(x, target_call=call, parent=top), add="+")
        qso_refresh_btn = qso_button(header, "Refresh", command=lambda: None, bg="#33ff77", fg="#000000", width=9)
        qso_refresh_btn.pack(side="left", padx=(0, 10), ipady=3)
        host._attach_tooltip(qso_refresh_btn, host._tt("fc_refresh"))
        rig_frame = tk.Frame(header, bg=p.panel)
        rig_frame.pack(side="right", padx=(6, 0))
        tk.Label(rig_frame, text="Set Freq:", bg=p.panel, fg=p.text, font=popup_font("Arial", 14, "bold")).pack(side="left", padx=(0, 5))
        qso_freq_cb = ttk.Combobox(rig_frame, textvariable=host.freq_var, values=host.saved_frequency_mhz_values(), width=8, state="normal", font=popup_font("Consolas", 17, "bold"), style="Rig.TCombobox")
        qso_freq_cb.pack(side="left", padx=(0, 8))
        host._attach_tooltip(qso_freq_cb, host._tt("row_set_freq"))
        host._freq_combo_widgets.append(qso_freq_cb)
        qso_freq_cb.bind("<Return>", host.on_rig_field_commit)
        qso_freq_cb.bind("<FocusOut>", host.on_rig_field_commit)
        qso_freq_cb.bind("<<ComboboxSelected>>", lambda _e, box=qso_freq_cb: host.apply_saved_frequency_value(box.get(), commit=True))
        tk.Label(rig_frame, text="Set Offset:", bg=p.panel, fg=p.text, font=popup_font("Arial", 14, "bold")).pack(side="left", padx=(0, 5))
        _qso_offset_ent = live_rig_entry(rig_frame, host.offset_var, 6)
        _qso_offset_ent.pack(side="left")
        host._attach_tooltip(_qso_offset_ent, host._tt("row_set_offset"))

        row2 = tk.Frame(top, bg=p.panel)
        row2.pack(fill="x", padx=6, pady=(0, 5))
        tk.Label(row2, text="SPEED:", bg=p.panel, fg=p.text, font=popup_font("Arial", 14, "bold")).pack(side="left")
        qso_speed_cb = ttk.Combobox(row2, textvariable=host.speed_var, values=SPEEDS, width=12, state="readonly", font=popup_font("Arial", 17, "bold"), style="MJ.TCombobox")
        qso_speed_cb.pack(side="left", padx=(4, 12))
        qso_speed_cb.bind("<<ComboboxSelected>>", lambda _e, box=qso_speed_cb: host.on_speed_selected(box))
        tk.Label(row2, text="DB Time:", bg=p.panel, fg=p.text, font=popup_font("Arial", 14, "bold")).pack(side="left")
        ttk.Combobox(row2, textvariable=host.db_time_var, values=list(TIME_FILTERS.keys()), width=17, state="readonly", font=popup_font("Arial", 17, "bold"), style="MJ.TCombobox").pack(side="left", padx=(4, 10))
        _qso_history_btn = qso_button(row2, "History", width=8, command=lambda: host.open_history_popup(call, parent=top))
        _qso_history_btn.pack(side="left", padx=(0, 5), ipady=2)
        host._attach_tooltip(_qso_history_btn, host._tt("fc_history"))
        _qso_inbox_btn = qso_button(row2, "Inbox", width=8, command=lambda: host.open_inbox_popup(call, parent=top))
        _qso_inbox_btn.pack(side="left", padx=(0, 5), ipady=2)
        host._attach_tooltip(_qso_inbox_btn, host._tt("fc_inbox"))
        # Unread-mail indicator: register so the data-refresh pipeline can recolor
        # this button, and paint its current state now (red outline + count if
        # this callsign has inbox mail for me that I have not opened yet).
        host._register_inbox_button(call, _qso_inbox_btn)
        host._paint_inbox_button(call, _qso_inbox_btn)
        _qso_info_btn = qso_button(row2, "Info", width=6, command=lambda: host.open_info_popup(parent=top))
        _qso_info_btn.pack(side="left", padx=(0, 7), ipady=2)
        host._attach_tooltip(_qso_info_btn, host._tt("fc_info"))
        _qso_tx_armed_cb = tk.Checkbutton(row2, text="TX Armed", variable=host.tx_armed_var, command=lambda: (host.update_tx_armed_button(), host.save_current_config()), bg=p.panel, fg=p.text, activebackground=p.panel, activeforeground=p.text, selectcolor=p.panel2, font=popup_font("Arial", 13, "bold"))
        _qso_tx_armed_cb.pack(side="left", padx=(0, 6))
        host._attach_tooltip(_qso_tx_armed_cb, host._tt("fc_tx_armed"))
        _qso_confirm_cb = tk.Checkbutton(row2, text="Confirm TX", variable=host.confirm_tx_var, command=host.save_current_config, bg=p.panel, fg=p.text, activebackground=p.panel, activeforeground=p.text, selectcolor=p.panel2, font=popup_font("Arial", 13, "bold"))
        _qso_confirm_cb.pack(side="left", padx=(0, 4))
        host._attach_tooltip(_qso_confirm_cb, host._tt("fc_confirm_tx"))

        info = host.reader.fcc_info(call)
        id_bg = p.panel2 if is_dark else "#d6d9df"

        # Identity (left) + HEAR/HEARD stats (right), directly under the buttons.
        id_row = tk.Frame(top, bg=id_bg)
        id_row.pack(fill="x", padx=6, pady=(0, 2))
        id_left = tk.Frame(id_row, bg=id_bg)
        id_left.pack(side="left", fill="y")
        tk.Label(id_left, text=call, bg=id_bg, fg=p.text, font=popup_font("Arial", 21, "bold"), anchor="w").pack(fill="x", padx=8, pady=(3, 0))
        name_line = ""
        if info:
            name = display_person_name(info.get("name", ""))
            city = str(info.get("city", "") or "").title()
            state = str(info.get("state", "") or "").upper()
            name_line = "\n".join(x for x in (name, f"{city}, {state}".strip(", ")) if x)
        tk.Label(id_left, text=name_line, bg=id_bg, fg=p.text, font=popup_font("Arial", 16, "bold"), justify="left", anchor="w").pack(fill="x", padx=8, pady=(0, 6))

        stats_txt = tk.Text(id_row, height=4, width=26, bg=id_bg, fg=p.text, relief="flat", bd=0, highlightthickness=0, font=popup_font("Consolas", 16, "bold"), wrap="none")
        stats_txt.pack(side="right", padx=(6, 10), pady=(4, 4))
        # Big Task Phase 2: sine-wave TX indicator centered in the gap between the
        # callsign (left) and the HEAR/HEARD stats (right), under the Inbox/Info
        # buttons. Shares the same lock as the main window, so it animates in sync.
        tx_center = tk.Frame(id_row, bg=id_bg)
        tx_center.pack(side="left", fill="both", expand=True)
        qso_indicator = TxIndicator(tx_center, width=160, height=26, bg=id_bg)
        qso_indicator.pack(expand=True)
        host.tx_lock.add_listener(qso_indicator.on_lock)
        qso_indicator.bind("<Destroy>", lambda _e, cb=qso_indicator.on_lock: host.tx_lock.remove_listener(cb))
        stats_txt.tag_configure("lbl", foreground=p.text)

        def render_stats():
            with host.soft(f"qso stats {call}", log=False):
                if not stats_txt.winfo_exists():
                    return
                stats_txt.configure(state="normal")
                stats_txt.delete("1.0", "end")
                for i, (lbl, val, snr) in enumerate(self._hear_heard_rows(call)):
                    stats_txt.insert("end", f"{lbl:<13}", ("lbl",))
                    tag = f"val{i}"
                    stats_txt.tag_configure(tag, foreground=self._snr_color(snr, is_dark) if snr is not None else p.text)
                    stats_txt.insert("end", f"{val}\n", (tag,))
                stats_txt.configure(state="disabled")

        # Show / Search toolbar (minimal gap above the chat).
        bar = tk.Frame(top, bg=p.panel)
        bar.pack(fill="x", padx=6, pady=(0, 2))
        tk.Label(bar, text="Show:", bg=p.panel, fg=p.text, font=popup_font("Arial", 12, "bold")).pack(side="left", padx=(2, 4))
        hist_time_var = tk.StringVar(value=host.db_time_var.get() if host.db_time_var.get() in TIME_FILTERS else "Last 1 hour")
        ttk.Combobox(bar, textvariable=hist_time_var, values=list(TIME_FILTERS.keys()), state="readonly", width=14, font=popup_font("Arial", 12, "bold"), style="MJ.TCombobox").pack(side="left", padx=(0, 10))
        tk.Label(bar, text="Search:", bg=p.panel, fg=p.text, font=popup_font("Arial", 12, "bold")).pack(side="left", padx=(0, 4))
        hist_search_var = tk.StringVar(value="")
        search_entry = tk.Entry(bar, textvariable=hist_search_var, bg=p.entry_bg, fg=p.entry_fg, insertbackground=p.entry_fg, relief="solid", bd=1, highlightbackground="#ffffff" if is_dark else p.border, highlightcolor="#ffffff" if is_dark else p.border, highlightthickness=1, font=popup_font("Arial", 12, "bold"), width=20)
        search_entry.pack(side="left", padx=(0, 4))
        clear_x = tk.Label(search_entry, text="\u2715", bg=p.entry_bg, fg="#9aa0a6", font=popup_font("Arial", 10, "bold"), cursor="hand2")
        clear_x.place(relx=1.0, rely=0.5, anchor="e", x=-3)
        clear_x.bind("<Button-1>", lambda _e: (hist_search_var.set(""), render_chat()))
        tk.Button(bar, text="GO", command=lambda: render_chat(), bg=p.button, fg=p.text, font=popup_font("Arial", 11, "bold"), width=4).pack(side="left", padx=(0, 8))
        search_entry.bind("<Return>", lambda _e: render_chat())

        def clear_sent_history():
            if not messagebox.askyesno("Clear FastChat sent history", f"Clear local FastChat sent history for {call}?\n\nThis does not delete JS8Map or JS8Call database records.", parent=top):
                return
            n = host.clear_local_outgoing_history_for(call)
            host.set_status(f"Cleared {n} local sent message(s) for {call}.")
            repaint()
        _clear_sent_btn = tk.Button(bar, text="Clear Sent History", command=clear_sent_history, bg="#9b1c1c", fg="#ffffff", font=popup_font("Arial", 11, "bold"), width=16)
        _clear_sent_btn.pack(side="right", padx=(4, 2))
        host._attach_tooltip(_clear_sent_btn, host._tt("fc_clear_sent"))

        # Resizable: scrolling chat (top) + compose box (bottom). PanedWindow
        # minsize on each pane is the "can't shrink below ~2 lines" clamp.
        #
        # Layout fix (Phase 9): the compose-mode row and the bottom button bar
        # must be packed side="bottom" BEFORE the expanding PanedWindow, so Tk
        # reserves their height first. Otherwise, dragging the window shorter
        # from the bottom edge starves the last-packed widgets and the button
        # bar (HALT/SEND/etc.) gets clipped off the bottom.
        bottom_area = tk.Frame(top, bg=p.panel)
        bottom_area.pack(side="bottom", fill="x")

        panes = tk.PanedWindow(top, orient="vertical", sashwidth=7, sashrelief="raised", bg=p.border, opaqueresize=True, bd=0)
        panes.pack(fill="both", expand=True, padx=6, pady=(0, 2))

        chat_frame = tk.Frame(panes, bg=p.panel, highlightbackground="#ffffff" if is_dark else p.border, highlightthickness=1)
        chat_holder = tk.Frame(chat_frame, bg="#0f172a" if is_dark else "#ffffff")
        chat_holder.pack(fill="both", expand=True, padx=6, pady=6)
        chat_scroll = tk.Scrollbar(chat_holder, orient="vertical")
        chat_scroll.pack(side="right", fill="y")
        chat_txt = tk.Text(chat_holder, height=10, bg="#0f172a" if is_dark else "#ffffff", fg=p.text, insertbackground=p.text, relief="flat", bd=0, highlightthickness=0, font=popup_font("Consolas", 16, "bold"), wrap="word", yscrollcommand=chat_scroll.set)
        chat_txt.pack(side="left", fill="both", expand=True)
        chat_scroll.configure(command=chat_txt.yview)
        chat_txt.tag_configure("tx_gold", foreground=("#FFD700" if is_dark else "#B8860B"))
        chat_txt.tag_configure("rx_green", foreground=(p.green if is_dark else "#1a7f37"))
        chat_txt.tag_configure("hdr_muted", foreground=p.muted)

        send_frame = tk.Frame(panes, bg=p.panel)
        panes.add(chat_frame, minsize=80, stretch="always")
        panes.add(send_frame, minsize=70, stretch="never")

        # Remembered height of the "Message to send" (compose) box. This is the
        # slice BETWEEN the sash and the bottom -- i.e. how tall the compose box
        # is. Previously the sash was always placed at a fixed (h - 170) on every
        # open, so dragging it bigger was discarded on reopen. Now we restore the
        # saved compose height and re-save it whenever the user drags the sash.
        _DEFAULT_COMPOSE_H = 170
        _MIN_COMPOSE_H = 70

        def _saved_compose_h():
            try:
                v = int(host.get_layout_value("fastchat_compose_h", _DEFAULT_COMPOSE_H))
                return max(_MIN_COMPOSE_H, v)
            except Exception:
                return _DEFAULT_COMPOSE_H

        def _init_sash():
            if not panes.winfo_exists():
                return
            h = panes.winfo_height()
            if h <= 1:
                top.after(60, _init_sash)
                return
            # Place the sash so the compose box gets its remembered height, with
            # the chat area taking the rest. Clamp so the chat pane keeps at
            # least ~80px even on a short window.
            compose_h = _saved_compose_h()
            sash_y = max(80, h - compose_h)
            with host.soft("init sash", log=False):
                panes.sash_place(0, 1, sash_y)
        top.after(80, _init_sash)

        def _save_compose_h(_event=None):
            # Called when the user finishes dragging the sash. Compute the
            # compose-box height (bottom slice) and persist it.
            if not panes.winfo_exists():
                return
            with host.soft("save compose_h", log=False):
                h = panes.winfo_height()
                coords = panes.sash_coord(0)
                sash_y = coords[1] if coords else None
                if h > 1 and sash_y is not None:
                    compose_h = max(_MIN_COMPOSE_H, h - int(sash_y))
                    host.set_layout_value("fastchat_compose_h", compose_h)
        # Save after a sash drag ends. ButtonRelease on the PanedWindow fires
        # when the user lets go of the sash; the small after() lets Tk settle the
        # final position first.
        panes.bind("<ButtonRelease-1>", lambda e: top.after(80, _save_compose_h), add="+")

        def _chat_at_bottom() -> bool:
            try:
                return chat_txt.yview()[1] >= 0.97
            except Exception:
                return True

        def render_chat():
            stick = _chat_at_bottom()
            self._render_chat(chat_txt, call, hist_time_var.get(), hist_search_var.get())
            if stick:
                with host.soft("chat autoscroll", log=False):
                    chat_txt.see("end")

        def repaint():
            render_stats()
            render_chat()

        chat_txt._mj_repaint = repaint
        host._qso_latest_widgets[base_call(call)] = chat_txt

        hist_time_var.trace_add("write", lambda *_: render_chat())
        hist_search_var.trace_add("write", lambda *_: render_chat())

        def forget_qso_latest_widget(_event=None):
            try:
                if _event is not None and _event.widget is not top:
                    return
            except Exception:
                pass
            # Registry is keyed by CALLSIGN but open() creates a new Toplevel
            # every time, so two popups on the same call both register and the
            # second overwrites the first. Popping unconditionally then meant
            # closing the FIRST deregistered the SECOND, which was still open:
            # it kept limping on its 15s heartbeat and looked merely "slow to
            # update". Only clear the entry if it is still OURS.
            _b = base_call(call)
            if host._qso_latest_widgets.get(_b) is chat_txt:
                host._qso_latest_widgets.pop(_b, None)

        top.bind("<Destroy>", forget_qso_latest_widget, add="+")

        # Heartbeat backstop in case an update ever arrives without tripping the
        # main refresh pipeline; the API-driven refresh_open_qso_popups is primary.
        def _heartbeat():
            if not top.winfo_exists():
                return
            repaint()
            top.after(15000, _heartbeat)
        top.after(15000, _heartbeat)

        # Live long-message preview poll. JS8Map publishes an in-progress
        # transmission's partial text to a shared file as each ~15s frame lands;
        # that file changes without any DB row changing, so the DB-driven refresh
        # won't catch it. Poll it every 2.5s and repaint ONLY when the partial
        # from this station actually changes (a new frame, or completion clearing
        # it) -- cheap when idle, responsive when a long message is coming in.
        _last_partial_sig = [None]

        def _partial_poll():
            if not top.winfo_exists():
                return
            with host.soft("qso partial poll", log=False):
                part = host.incoming_partial_for(call)
                sig = str(part.get("text", "")) if part else None
                if sig != _last_partial_sig[0]:
                    _last_partial_sig[0] = sig
                    render_chat()
            top.after(2500, _partial_poll)
        top.after(2500, _partial_poll)

        def refresh_qso_popup():
            host.set_status(f"Refreshing FastChat for {call}...")
            host.refresh_data(False)
            for delay in (250, 900, 1800, 3000):
                top.after(delay, repaint)

        qso_refresh_btn.configure(command=refresh_qso_popup)
        repaint()

        qso_msg = self.text_panel(send_frame, "Message to send", "", height=4, editable=True, hide_title_on_typing=True, expand=True)
        qso_mode_var = tk.StringVar(value="DIRECTED")
        qso_mode_label_var = tk.StringVar(value=f"Mode: Directed -> {call} ...")
        mode_row = tk.Frame(bottom_area, bg=p.panel)
        mode_row.pack(fill="x", padx=8, pady=(0, 2))
        tk.Label(mode_row, textvariable=qso_mode_label_var, bg=p.panel, fg=p.text, font=popup_font("Arial", 12, "bold"), anchor="w").pack(side="left")

        def update_qso_mode_label():
            if qso_mode_var.get() == "INB-MSG":
                qso_mode_label_var.set(f"Mode: INB-MSG -> {call} MSG ...")
            else:
                qso_mode_label_var.set(f"Mode: Directed -> {call} ...")

        def set_qso_mode(mode: str = "DIRECTED"):
            qso_mode_var.set("INB-MSG" if str(mode or "").upper().replace("_", "-") in ("INB-MSG", "INB MSG") else "DIRECTED")
            update_qso_mode_label()
            if qso_mode_var.get() == "INB-MSG":
                host.set_status(f"FastChat INB-MSG armed for {call}. Type message and press Enter.")
            try:
                qso_msg.focus_set()
            except Exception:
                pass

        def reset_qso_mode():
            set_qso_mode("DIRECTED")

        def qso_body():
            return qso_msg.get("1.0", "end-1c").strip().upper()

        def clear_qso_msg():
            clear_fn = getattr(qso_msg, "_mj_clear_text", None)
            if callable(clear_fn):
                clear_fn()
            else:
                qso_msg.delete("1.0", "end")
                getattr(qso_msg, "_mj_update_title", lambda: None)()
                host.set_status("FastChat message box cleared.")

        def arm_then_send_if_needed(frame: str) -> bool:
            if host.tx_armed_var.get():
                return True
            host.preview_var.set(frame)
            host.set_status(f"TX Armed is OFF — preview only: {frame}")
            if messagebox.askyesno("TX Armed is OFF", "TX Armed is OFF, so this was only staged as a preview and was not sent to JS8Call.\n\nTurn TX Armed ON and send this message now?", parent=top):
                host.tx_armed_var.set(True)
                host.update_tx_armed_button()
                host.save_current_config()
                return True
            return False

        def send_qso_current_msg(event=None):
            body = qso_body()
            if not body:
                host.set_status("Type a FastChat message before sending.")
                qso_msg.focus_set()
                return "break"
            inbox_mode = qso_mode_var.get() == "INB-MSG"
            frame = f"{call} MSG {body}" if inbox_mode else f"{call} {body}"
            context = "FastChat INB-MSG" if inbox_mode else "FastChat Directed"

            def after_qso_send():
                clear_qso_msg()
                reset_qso_mode()

            if arm_then_send_if_needed(frame):
                host.transmit_frame(frame, clear_preview=True, context=context, on_success=after_qso_send)
            return "break"

        def arm_qso_directed_msg(event=None):
            set_qso_mode("DIRECTED")
            host.set_status(f"FastChat Directed mode armed for {call}. Type message and press Enter or SEND.")
            return "break"

        def arm_qso_inbox_msg(event=None):
            set_qso_mode("INB-MSG")
            return "break"

        def store_qso_msg():
            host.open_store_msg_popup(call, qso_msg, top)

        def query_qso_msg():
            frame = f"{call} QUERY MSGS"
            if arm_then_send_if_needed(frame):
                host.transmit_frame(frame, clear_preview=True, context="FastChat QUERY MSGS")

        def query_qso_msg_id():
            msg_id = host.themed_input("Query Message ID", f"Enter JS8 message ID to query from {call}:", uppercase=True)
            msg_id = str(msg_id or "").strip().upper()
            if not msg_id:
                host.set_status("Send MSG ID canceled or empty.")
                return
            frame = f"{call} QUERY MSG {msg_id}"
            if arm_then_send_if_needed(frame):
                host.transmit_frame(frame, clear_preview=True, context="FastChat QUERY MSG ID")

        def query_call():
            host.open_query_call_popup(call, parent=top)

        qso_msg.bind("<Return>", send_qso_current_msg)
        bottom = tk.Frame(bottom_area, bg=p.panel)
        bottom.pack(fill="x", padx=6, pady=(3, 6))
        # Phase 9 layout fix: the old bar packed 10 buttons side="left" plus a
        # side="right" Close in one frame. Their combined natural width (~1150px)
        # exceeds the window's min width (860), so when the window was narrowed
        # pack could not place them all and the rightmost buttons silently
        # dropped off ("the row disappears"). Using grid with every column
        # weighted makes the buttons share the available width and compress
        # together, so the full row stays visible at any window size down to the
        # minimum. sticky="ew" lets each button fill its cell.
        bottom_buttons = [
            ("HALT", dict(bg="#9b1c1c", fg="#ffffff", command=host.halt_tx)),
            ("SEND", dict(bg=p.green, fg="#000000", command=send_qso_current_msg)),
            ("Clear Msg", dict(command=clear_qso_msg)),
            ("Directed", dict(command=arm_qso_directed_msg)),
            ("INB-MSG", dict(command=arm_qso_inbox_msg)),
            ("Store Msg", dict(command=store_qso_msg)),
            ("Query Msg", dict(command=query_qso_msg)),
            ("Send MSG ID", dict(command=query_qso_msg_id)),
            ("Query Call", dict(command=query_call)),
            ("Close", dict(bg="#374151" if is_dark else p.button, fg="#ffffff" if is_dark else p.text, command=top.destroy)),
        ]
        _tx_lock_labels = {"SEND", "Query Msg", "Send MSG ID"}
        # Map bottom-bar labels to central tooltip keys (host._tt).
        _bottom_tips = {
            "HALT": "fc_halt",
            "SEND": "fc_send",
            "Clear Msg": "fc_clear_msg",
            "Directed": "fc_directed",
            "INB-MSG": "cmd_INB-MSG",
            "Store Msg": "fc_store_msg",
            "Query Msg": "fc_query_msg",
            "Send MSG ID": "cmd_Send MSG ID",
            "Query Call": "fc_query_call",
            "Close": "fc_close",
        }
        _last_col = len(bottom_buttons) - 1
        for _col, (_label, _kw) in enumerate(bottom_buttons):
            _b = qso_button(bottom, _label, width=1, bg=_kw.get("bg"), fg=_kw.get("fg"), command=_kw.get("command"))
            # No trailing pad on the final (Close) cell, so it sits flush to the
            # right edge and can't spill past the frame when the window narrows.
            _pad = (0, 0) if _col == _last_col else (0, 4)
            _b.grid(row=0, column=_col, sticky="ew", padx=_pad, ipady=2)
            bottom.columnconfigure(_col, weight=1, uniform="qso_bottom")
            host._attach_tooltip(_b, host._tt(_bottom_tips.get(_label, "")))
            if _label == "Store Msg":
                # Green waiting-count indicator: messages I am holding locally
                # for this callsign until they pull them.
                host._register_store_button(call, _b)
                host._paint_store_button(call, _b)
                # Right-click -> see (and delete) the messages still waiting.
                # View name matches main_window.INBOX_VIEW_OUTGOING (passed as a
                # literal to avoid a circular import back into main_window).
                _b.bind("<Button-3>", lambda _e, c=call: host.open_inbox_popup(c, view="Outgoing - Waiting Pickup"))
            if _label in _tx_lock_labels:
                host.tx_lock.bind_button(_b)
