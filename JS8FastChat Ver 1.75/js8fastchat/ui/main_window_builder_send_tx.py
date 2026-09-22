from __future__ import annotations

import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import tkinter as tk
from tkinter import messagebox, ttk

from ..constants import (
    CAPTURE_HISTORY_PATH,
    CAPTURE_PER_CALL_CAP,
    DEFAULT_MACROS,
    GROUP_ALL,
    OBSERVED_TX_PATH,
    TIME_FILTERS,
    TX_HISTORY_PATH,
)
from ..utils import base_call, debug_exc, norm_call, read_json, write_json
from .main_window_ui_substrate import popup_font, popup_wrap


class _BuilderSendTxMixin:
    """Builder / send / TX cluster (T6). Message builder + query/send popups,
    the singular TX gate (transmit_frame -> TxLock/api), local + observed + captured
    outgoing history stores, and the cross-app JS8Map->FastChat shared-dir readers
    (_poll_js8map_tx_lease, observed_outgoing_history_for). JS8Map is the sole writer;
    FastChat only reads. Self-bound; resolves against MainWindow through the MRO."""

    # commands/tx
    def builder_target_call(self) -> str:
        return norm_call(self.msg_to_var.get()) or self.selected_call or norm_call(self.manual_call_var.get())

    def update_builder_prefix_display(self) -> None:
        target = self.builder_target_call()
        # Keep the main-window STORE MSG button's green waiting-count in step
        # with whatever callsign the builder is now pointed at.
        self._paint_main_store_button()
        if not target:
            self.command_prefix_var.set("Command prefix: —")
            return
        suffix = " MSG" if self.builder_msg_mode_var.get() == "INB-MSG" else ""
        self.command_prefix_var.set(f"Command prefix: {target}{suffix}")

    def _paint_main_store_button(self) -> None:
        """Green outline + count on the main window's STORE MSG button, showing
        how many messages I am holding for the CURRENT builder target. Unlike the
        per-callsign popup button, this one follows the selected callsign, so it
        repaints on target change as well as on the data-refresh tick."""
        with self.soft("paint main store button", log=False):
            btn = getattr(self, "_main_store_btn", None)
            if btn is None or not btn.winfo_exists():
                return
            p = self.pal()
            target = self.builder_target_call()
            n = self.store_waiting_count_for(target) if target else 0
            if n > 0:
                btn.configure(text=f"STORE MSG ({n})", fg=self.STORE_WAITING_FG,
                              highlightthickness=2,
                              highlightbackground=p.green, highlightcolor=p.green)
            else:
                btn.configure(text="STORE MSG", fg=p.text, highlightthickness=0)

    def set_builder_message_mode(self, mode: str = "DIRECTED") -> None:
        mode = "INB-MSG" if str(mode or "").upper().replace("_", "-") in ("INB-MSG", "INB MSG") else "DIRECTED"
        self.builder_msg_mode_var.set(mode)
        self.update_builder_prefix_display()
        if mode == "INB-MSG":
            self.set_status("INB-MSG mode armed. Type the message and press Enter or Send.")
            try:
                self.msg_text.focus_set()
            except Exception:
                pass

    def reset_builder_message_mode(self) -> None:
        self.set_builder_message_mode("DIRECTED")

    def target_for_command(self, command: str) -> str:
        group_commands = {"SNR?", "INFO?", "GRID?", "STATUS?", "HEARING?", "QRY MSGS"}
        group = self.group_target_var.get()
        if command in group_commands and group and group != GROUP_ALL:
            return group
        if command == "HB":
            return ""
        if command in {"MSG TO", "INB-MSG"}:
            return self.builder_target_call()
        return self.selected_call or norm_call(self.manual_call_var.get())

    def message_body(self) -> str:
        return self.msg_text.get("1.0", "end").strip().upper()

    def query_audience_from_send_group(self) -> str:
        aud = str(self.group_target_var.get() or "").strip().upper()
        if not aud or aud in (GROUP_ALL.upper(), GROUP_ALL):
            aud = "@ALLCALL"
        if not aud.startswith("@"):
            aud = "@" + aud
        return aud

    def open_query_call_popup(self, target_call: str = "", parent=None) -> None:
        target = norm_call(target_call or self.selected_call or norm_call(self.manual_call_var.get()))
        if not target:
            self.set_status("Select or type the callsign to query first.")
            return
        owner = parent or self
        p = self.pal()
        top = tk.Toplevel(owner)
        top.title(f"Query Call - {target}")
        top.configure(bg=p.panel)
        top.resizable(False, False)
        try:
            top.transient(owner)
            top.grab_set()
        except Exception:
            pass
        groups = ["@ALLCALL"]
        for g in self.groups or []:
            g = str(g or "").strip().upper()
            if not g or g == GROUP_ALL.upper() or g == GROUP_ALL:
                continue
            if not g.startswith("@"):
                g = "@" + g
            if g not in groups and g != "@HB":
                groups.append(g)
        default = self.query_audience_from_send_group()
        if default not in groups:
            groups.insert(0, default)
        aud_var = tk.StringVar(value=default if default else "@ALLCALL")
        tk.Label(top, text=f"Query callsign: {target}", bg=p.panel, fg=p.text, font=popup_font("Arial", 14, "bold"), anchor="w").pack(fill="x", padx=14, pady=(12, 6))
        row = tk.Frame(top, bg=p.panel)
        row.pack(fill="x", padx=14, pady=(0, 8))
        tk.Label(row, text="Send query to:", bg=p.panel, fg=p.text, font=popup_font("Arial", 12, "bold")).pack(side="left", padx=(0, 8))
        cb = ttk.Combobox(row, textvariable=aud_var, values=groups, state="readonly", width=18, font=popup_font("Arial", 13, "bold"), style="MJ.TCombobox")
        cb.pack(side="left")
        preview_var = tk.StringVar()
        def update_preview(*_):
            aud = str(aud_var.get() or "@ALLCALL").strip().upper()
            if not aud.startswith("@"):
                aud = "@" + aud
            preview_var.set(f"{aud} QUERY CALL {target}?")
        aud_var.trace_add("write", update_preview)
        update_preview()
        tk.Label(top, textvariable=preview_var, bg=p.panel, fg=p.text, font=popup_font("Consolas", 13, "bold"), anchor="w").pack(fill="x", padx=14, pady=(0, 10))
        buttons = tk.Frame(top, bg=p.panel)
        buttons.pack(fill="x", padx=14, pady=(0, 14))
        def send_query(event=None):
            frame = preview_var.get().strip().upper()
            if self.transmit_frame(frame, clear_preview=True, context="QUERY CALL"):
                try:
                    top.destroy()
                except Exception:
                    pass
            return "break"
        tk.Button(buttons, text="SEND QUERY", command=send_query, bg=p.green, fg="#000000", activebackground=p.green, font=popup_font("Arial", 12, "bold"), width=14).pack(side="left", padx=(0, 8))
        tk.Button(buttons, text="Cancel", command=top.destroy, bg=p.button, fg=p.text, activebackground=p.button_active, font=popup_font("Arial", 12, "bold"), width=10).pack(side="right")
        top.bind("<Return>", send_query)
        top.bind("<Escape>", lambda _e: top.destroy())
        try:
            cb.focus_set()
        except Exception:
            pass

    def build_query_call_frame(self, target_call: str = "") -> str:
        target = norm_call(target_call or self.selected_call or norm_call(self.manual_call_var.get()))
        return f"{self.query_audience_from_send_group()} QUERY CALL {target}?" if target else ""

    def themed_input(self, title: str, prompt: str, initial: str = "", uppercase: bool = True, width: int = 420, height: int = 150) -> "str | None":
        """A small THEMED single-line input dialog (replaces simpledialog.askstring,
        which ignores the app theme and renders tiny/inconsistent). Returns the
        entered string, or None if canceled. Modal: blocks until closed.
        """
        p = self.pal()
        win = tk.Toplevel(self); win.title(title); win.configure(bg=p.panel)
        win.geometry(self._popup_geometry(width, height)); win.transient(self)
        tk.Label(win, text=prompt, bg=p.panel, fg=p.text, font=popup_font("Arial", 13, "normal"), anchor="w").pack(anchor="w", padx=14, pady=(14, 6))
        var = tk.StringVar(value=initial)
        ent = tk.Entry(win, textvariable=var, bg=p.entry_bg, fg=p.entry_fg, insertbackground=p.entry_fg, relief="solid", bd=1, font=popup_font("Consolas", 15, "bold"), highlightthickness=1, highlightbackground="#5a6b7a", highlightcolor=p.green if hasattr(p, "green") else "#33ff77")
        ent.pack(fill="x", padx=14, pady=(0, 10), ipady=3)
        if uppercase:
            try:
                self.attach_uppercase_var(var)
            except Exception:
                pass
        result = {"value": None}
        def ok():
            result["value"] = var.get()
            win.destroy()
        def cancel():
            result["value"] = None
            win.destroy()
        btns = tk.Frame(win, bg=p.panel); btns.pack(fill="x", padx=14, pady=(0, 12))
        tk.Button(btns, text="OK", command=ok, bg=p.button, fg=p.text, font=popup_font("Arial", 12, "bold"), width=8).pack(side="right", padx=(6, 0))
        tk.Button(btns, text="Cancel", command=cancel, bg=p.button, fg=p.text, font=popup_font("Arial", 12, "bold"), width=8).pack(side="right")
        ent.bind("<Return>", lambda _e: ok())
        ent.bind("<Escape>", lambda _e: cancel())
        ent.focus_set()
        try:
            win.grab_set()
            self.wait_window(win)
        except Exception:
            pass
        return result["value"]

    def build_command(self, command: str, target_call: str = "") -> None:
        # target_call: the station this command MUST go to, whatever the main
        # window happens to have selected. The FastChat popup passes its OWN
        # station here.
        #
        # Without it, a popup's command fell through to target_for_command(),
        # which reads self.selected_call LIVE -- the main window's highlighted
        # row. Open FastChat for KE2KN, then click KD9DSS on the main list, then
        # press HEARING? in the KE2KN window, and the frame went out addressed to
        # KD9DSS. The window said one callsign and the radio transmitted another.
        # Observed on the air 2026-07-12: "KW3KW -> KD9DSS HEARING?" sent from
        # the KE2KN popup, twice.
        #
        # An explicit target also beats the group override inside
        # target_for_command(): a popup is a conversation with ONE station, so a
        # group selected on the main window must never capture its commands.
        override = norm_call(target_call)
        target = override or self.target_for_command(command)
        body = self.message_body()
        frame = ""
        if command in {"SNR?", "INFO?", "GRID?", "STATUS?", "HEARING?"}:
            if not target: self.set_status(f"Select a callsign or group before building {command}."); return
            frame = f"{target} {command}"
        elif command == "QRY MSGS":
            frame = f"{target} QUERY MSGS" if target else "QUERY MSGS"
        elif command == "QRY CALL":
            self.open_query_call_popup(override or self.selected_call or norm_call(self.manual_call_var.get()), parent=self)
            return
        elif command == "Send MSG ID":
            msg_id = self.themed_input("Send Message ID", "Enter JS8 message ID:", uppercase=True)
            msg_id = str(msg_id or "").strip().upper()
            if not msg_id: self.set_status("Send MSG ID canceled or empty."); return
            frame = f"{target} QUERY MSG {msg_id}" if target else f"QUERY MSG {msg_id}"
        elif command == "INB-MSG":
            if not target: self.set_status("Select or type a callsign before arming INB-MSG."); return
            self.set_builder_message_mode("INB-MSG")
            self.preview_var.set(f"{target} MSG {body}".strip() if body else f"{target} MSG ...")
            return
        elif command == "STORE MSG":
            if not target: self.set_status("Select a callsign before building STORE MSG."); return
            # Open the SAME themed store-message dialog the FastChat popup uses,
            # seeded from the main-screen Message Builder box. Previously this
            # only built a bare "CALL STORE MSG" preview string and opened no
            # dialog, so the button appeared to do nothing.
            self.open_store_msg_popup(target, self.msg_text, parent=self)
            return
        elif command == "MSG TO":
            if not target: self.set_status("Enter MSG TO callsign first."); return
            if not body: self.set_status("Type message text before MSG TO."); self.msg_text.focus_set(); return
            frame = f"{target} {body}".strip()
        elif command == "HB":
            frame = "HB"
        else:
            frame = command
        self.preview_var.set(frame)
        quick = {"SNR?", "INFO?", "GRID?", "STATUS?", "HEARING?", "QRY MSGS", "QRY CALL", "HB"}
        if self.tx_armed_var.get() and command in quick:
            self.transmit_frame(frame, clear_preview=True, context=command)
        elif self.tx_armed_var.get():
            self.set_status(f"Preview built: {frame}. TX Armed is ON — press Send to transmit.")
        else:
            self.set_status(f"Preview built: {frame}. TX Armed is OFF — preview only.")

    def send_cq(self) -> None:
        """Phase 9: transmit our CQ call directly, exactly like the HB button
        (no popup). JS8Call's own CQ message is "CQ CQ CQ <MYGRID4>" (Settings ->
        Station Messages); we reproduce that, deriving the 4-char grid from the
        same resolver the distance/info features use. If no grid is known we
        still send a valid bare "CQ CQ CQ". Honors the TX-Armed gate and routes
        through the shared transmit chokepoint like every other one-press
        command."""
        grid4 = (self.call_info._home_grid() or "")[:4].upper()
        frame = f"CQ CQ CQ {grid4}".strip()
        self.preview_var.set(frame)
        if self.tx_armed_var.get():
            self.transmit_frame(frame, clear_preview=True, context="CQ")
        else:
            self.set_status(f"Preview built: {frame}. TX Armed is OFF — preview only.")

    def _observed_snr_for_call(self, call: str) -> str:
        """Return the SNR (as a signed string, e.g. '-07') at which OUR station
        most recently decoded `call` -- i.e. how well WE hear THEM. That is the
        number an operator sends in an SNR report. Reads the same in-memory
        activity rows the Incoming Activity list shows, newest first; returns ''
        if we have no numeric SNR on record for them yet."""
        base = base_call(call)
        full = norm_call(call)
        best = ""
        best_ts = ""
        for row in self.call_info._rows_for_call(base):
            # Rows come back for the BASE call, so W3BFO and W3BFO/P arrive
            # mixed. Keep only this exact station, or a report could carry
            # the SNR we measured on the other rig.
            if norm_call(getattr(row, "call", "")) != full:
                continue
            m = re.search(r"[+\-]?\d+", str(getattr(row, "snr", "") or ""))
            if not m:
                continue
            ts = str(getattr(row, "timestamp", "") or "")
            if ts >= best_ts:
                best_ts = ts
                try:
                    best = f"{int(m.group(0)):+03d}"
                except Exception:
                    best = m.group(0)
        return best

    def open_send_mine_popup(self, command: str, target_call: str = "", parent=None) -> None:
        """Phase 9: right-click handler for SNR? / INFO? / GRID? / STATUS?.

        Left-click on those buttons REQUESTS the other station's data
        ("{call} SNR?"). Right-click SENDS OURS to the selected station, using
        the JS8Call convention that the bare command (no "?") carries our own
        info: "{call} SNR -07", "{call} INFO", "{call} GRID", "{call} STATUS".
        A confirm popup shows the exact frame before anything is transmitted.

        target_call: when given (e.g. from a FastChat popup, which is pinned to
        one station), send to THAT call instead of the main window's current
        selection. This keeps the FastChat send aimed at the popup's station
        even if the operator clicks elsewhere on the map/list meanwhile.

        parent: the window the confirm box should attach to. FastChat passes its
        own Toplevel so the box doesn't raise the main window over FastChat."""
        send_word = command.rstrip("?")  # "SNR?" -> "SNR"
        target = norm_call(target_call or self.selected_call or norm_call(self.manual_call_var.get()))
        if not target:
            self.set_status(f"Select a callsign before sending your {send_word}.")
            return
        if send_word == "SNR":
            snr = self._observed_snr_for_call(target)
            if not snr:
                self.set_status(f"No SNR on record for {target} yet — can't send a report. Wait until you decode them.")
                return
            frame = f"{target} SNR {snr}"
            detail = f"Send your signal report of {target} (you hear them at {snr} dB)."
        else:
            frame = f"{target} {send_word}"
            noun = {"INFO": "station info", "GRID": "grid square", "STATUS": "station status"}.get(send_word, send_word)
            detail = f"Send your {noun} to {target}."
        self._confirm_and_send(
            title=f"Send {send_word}",
            heading=f"Send {send_word} to {target}",
            detail=detail,
            frame=frame,
            context=send_word,
            parent=parent,
        )

    def _confirm_and_send(self, title: str, heading: str, detail: str, frame: str, context: str, parent=None) -> None:
        """Shared confirm-then-transmit popup for the Phase 9 one-press sends
        (CQ button + the right-click send-mine commands). Shows the exact frame,
        SEND transmits it through the normal tx chokepoint (TX-lock guarded),
        Cancel/Esc does nothing. Modeled on open_query_call_popup.

        parent: the window this dialog should be transient to. Defaults to the
        main window, but the FastChat popup passes its own Toplevel so the
        confirm box stays attached to FastChat instead of raising the main
        window over it (which would shove FastChat down to the taskbar)."""
        owner = parent if parent is not None else self
        p = self.pal()
        top = tk.Toplevel(owner)
        top.title(title)
        top.geometry(self._popup_geometry(420, 210))
        top.configure(bg=p.panel)
        top.resizable(False, False)
        try:
            top.transient(owner)
            top.grab_set()
        except Exception:
            pass
        tk.Label(top, text=heading, bg=p.panel, fg=p.text, font=popup_font("Arial", 15, "bold"), anchor="w", wraplength=popup_wrap(392), justify="left").pack(fill="x", padx=14, pady=(14, 4))
        tk.Label(top, text=detail, bg=p.panel, fg=p.text, font=popup_font("Arial", 12), anchor="w", wraplength=popup_wrap(392), justify="left").pack(fill="x", padx=14, pady=(0, 8))
        tk.Label(top, text="Will transmit:", bg=p.panel, fg=p.text, font=popup_font("Arial", 11, "bold"), anchor="w").pack(fill="x", padx=14, pady=(0, 2))
        tk.Label(top, text=frame, bg=p.panel, fg=p.green, font=popup_font("Consolas", 15, "bold"), anchor="w", wraplength=popup_wrap(392), justify="left").pack(fill="x", padx=14, pady=(0, 12))
        buttons = tk.Frame(top, bg=p.panel)
        buttons.pack(fill="x", padx=14, pady=(0, 14))

        def do_send(event=None):
            if self.transmit_frame(frame, clear_preview=True, context=context):
                try:
                    top.destroy()
                except Exception:
                    pass
            return "break"

        _send_btn = tk.Button(buttons, text="SEND", command=do_send, bg=p.green, fg="#000000", activebackground=p.green, font=popup_font("Arial", 12, "bold"), width=12)
        _send_btn.pack(side="left", padx=(0, 8))
        self.tx_lock.bind_button(_send_btn)
        tk.Button(buttons, text="Cancel", command=top.destroy, bg=p.button, fg=p.text, activebackground=p.button_active, font=popup_font("Arial", 12, "bold"), width=10).pack(side="right")
        top.bind("<Return>", do_send)
        top.bind("<Escape>", lambda _e: top.destroy())
        try:
            _send_btn.focus_set()
        except Exception:
            pass

    def clear_preview(self) -> None:
        self.preview_var.set(""); self.set_status("Preview cleared.")

    def copy_preview(self) -> None:
        text = self.preview_var.get().strip()
        if text:
            self.clipboard_clear(); self.clipboard_append(text); self.set_status("Preview copied to clipboard.")

    def send_preview_only(self) -> None:
        text = self.preview_var.get().strip()
        if not text: self.set_status("No preview to send."); return
        self.transmit_frame(text, clear_preview=True, context="preview")

    def send_builder_directed_msg(self, event=None):
        body = self.message_body()
        prefix_text = str(self.command_prefix_var.get() or "").replace("Command prefix:", "").strip().upper()
        visual_inbox_mode = bool(prefix_text and prefix_text.endswith(" MSG"))
        inbox_mode = self.builder_msg_mode_var.get() == "INB-MSG" or visual_inbox_mode
        target = self.builder_target_call()
        if visual_inbox_mode:
            target = prefix_text[:-4].strip() or target
        if not target or target == "—":
            self.set_status("Select or type a callsign before sending Message Builder text.")
            return "break"
        if not body:
            self.set_status("Type message text before Send.")
            self.msg_text.focus_set()
            return "break"
        frame = (f"{target} MSG {body}" if inbox_mode else f"{target} {body}").strip()
        context = "INB-MSG" if inbox_mode else "Directed MSG"
        def after_builder_send():
            self.clear_main_msg_box()
            self.reset_builder_message_mode()
        self.transmit_frame(frame, clear_preview=True, context=context, on_success=after_builder_send)
        return "break"

    def transmit_frame(self, frame: str, clear_preview: bool = False, context: str = "TX", on_success=None) -> bool:
        frame = (frame or "").strip().upper()
        # Big Task Phase 1: refuse if a transmission is already pending. This is
        # the single chokepoint every send (main window + popups) funnels
        # through, so the guard covers all of them and blocks double-fire across
        # windows or within one window.
        if self.tx_lock.is_locked():
            self.set_status("Transmission in progress — wait for it to finish before sending again.")
            return False
        self.preview_var.set(frame)
        def success_wrapper():
            if clear_preview:
                self.preview_var.set("")
            self.record_outgoing_tx(frame, context=context)
            if callable(on_success):
                on_success()
        # Lock on intent (now), release on real completion (poll) or TTL.
        self.tx_lock.acquire(reason=context, ttl_seconds=self._tx_lock_ttl())
        sent = self.tx_service.transmit(frame, context=context, on_success=success_wrapper)
        if sent:
            self._start_tx_completion_poll()
        else:
            self.tx_lock.release()  # nothing went out — don't leave it locked
        return sent

    def _tx_lock_ttl(self) -> float:
        """Safety ceiling for the transmit lock. The completion poll releases
        earlier in Improved/3.x mode; this TTL only guarantees the lock can
        never hang. Generous on purpose. (Open item: tune to the slowest
        realistic multi-frame send for the active speed.)"""
        return 120.0

    def _start_tx_completion_poll(self) -> None:
        """Release the transmit lock once JS8Call finishes sending.

        Improved/3.x only (uses RIG.GET_PTT + TX.GET_QUEUE_DEPTH). In Legacy 2.x
        those endpoints don't exist, so the lock simply rides the TTL. Runs on a
        daemon thread and marshals the release back onto the Tk main loop. Waits
        for TX to actually start before arming the 'went idle => done' check, and
        bails out if it never starts (e.g. the send silently failed)."""
        if self.api_mode_var.get() != "Improved / 3.x":
            return  # Legacy: no PTT/queue endpoints -> TTL handles release
        def worker():
            import time
            seen_active = False
            idle_streak = 0
            start = time.monotonic()
            # JS8Call only keys up on a TX WINDOW BOUNDARY, so "TX hasn't
            # started yet" is normal for up to one full window after the send
            # commits. This deadline must therefore exceed the LONGEST window
            # any JS8 mode uses -- Slow, at 30s (Normal 15, Fast 10, Turbo 6,
            # and Improved's 40/60 wpm modes are all shorter). At 8s the probe
            # gave up mid-wait and released the lock, so a send committed early
            # in a Normal window unlocked the buttons and stopped the wave just
            # before the transmission actually began.
            no_start_deadline = start + 35.0
            hard_deadline = start + 115.0
            time.sleep(1.0)  # give JS8Call a moment to key up
            while time.monotonic() < hard_deadline:
                try:
                    active, determined = self.api.tx_active()
                except Exception:
                    active, determined = False, False
                if active:
                    seen_active = True
                    idle_streak = 0
                elif determined:
                    if seen_active:
                        idle_streak += 1
                        if idle_streak >= 2:
                            break  # transmitted, now idle -> done
                    elif time.monotonic() > no_start_deadline:
                        break  # never started -> release
                time.sleep(1.0)
            try:
                self.after(0, self.tx_lock.release)
            except Exception:
                pass
        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _poll_js8map_tx_lease(self) -> None:
        """Phase 2: read JS8Map's TX-lease file and mirror it onto the lock's
        'remote' hold. While the lease is fresh, FastChat's TX buttons darken and
        the wave runs -- so FastChat won't fire into a JS8Call TX window JS8Map is
        already using. JS8Map is the sole writer; FastChat only reads.

        Lease shape actually written by JS8Map.py's write_js8map_tx_lease():
          {"active": true/false, "acquired_utc": "YYYY-mm-dd HH:MM:SS UTC",
           "from": CALL, "to": TARGET, "tx_mode": "manual"|"live"|"shadow",
           "est_duration_sec": 180, "trigger": "..."}
        (Earlier draft of this poller assumed a "ts"/"hold_seconds" shape that
        JS8Map never actually wrote -- fixed to match the real file.)

        "active" is a flag JS8Map sets True right as it commits the send and
        clears (manual mode) right after -- but in live mode JS8Map deliberately
        leaves it True (real RF takes longer than the API call returning), so
        freshness here is bounded by est_duration_sec since acquired_utc, not by
        "active" alone. The lease is therefore still self-expiring on FastChat's
        side even if JS8Map's own clear is ever skipped (e.g. on exception).
        (Read directly with json -- the app's read_json helper is tuned for its
        config files and dropped this plain file's keys.)
        """
        with self.soft("js8map_tx_lease", log=False):
            fresh = False
            lease_path = getattr(self, "_js8map_lease_path", "")
            data = None
            if lease_path:
                try:
                    import json as _json
                    with open(lease_path, "r", encoding="utf-8") as _fh:
                        data = _json.load(_fh)
                except Exception:
                    data = None
            if isinstance(data, dict) and data.get("active") and data.get("acquired_utc"):
                try:
                    raw = str(data.get("acquired_utc", "")).replace(" UTC", "").strip()
                    ts = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    hold = float(data.get("est_duration_sec", 180) or 180)
                    age = (datetime.now(timezone.utc) - ts).total_seconds()
                    if -5.0 < age < hold:
                        fresh = True
                        ctx = str(data.get("tx_mode") or "TX").strip() or "TX"
                        # TTL = remaining lease window + small buffer (never-stuck)
                        self.tx_lock.acquire(reason=f"JS8Map {ctx}", source="remote",
                                             ttl_seconds=max(2.0, hold - age + 5.0))
                        self._remote_lease_held = True
                except Exception:
                    fresh = False
            if not fresh:
                # Only release when we actually hold the remote lease. Calling
                # release every poll (once/sec) otherwise just spams a no-op; fire
                # it once, on the held -> not-held transition.
                if getattr(self, "_remote_lease_held", False):
                    self.tx_lock.release(source="remote")
                    self._remote_lease_held = False
        # This reschedule sits OUTSIDE the soft() block above, so an unguarded
        # after() here raises TclError against a destroyed interpreter on exit.
        if getattr(self, "_closing", False):
            return
        try:
            self._js8map_lease_after_id = self.after(1000, self._poll_js8map_tx_lease)
        except Exception:
            pass

    def record_outgoing_tx(self, frame: str, context: str = "TX") -> None:
        """Keep a small local outgoing TX log so FastChat History includes what we sent."""
        with self.soft("record_outgoing_tx"):
            frame = (frame or "").strip().upper()
            if not frame:
                return
            parts = frame.split()
            target = norm_call(parts[0]) if parts else ""
            row = {
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                "from": self.locator.callsign() or "KW3KW",
                "to": target,
                "context": str(context or "TX"),
                "text": frame,
            }
            data = read_json(TX_HISTORY_PATH)
            rows = data.get("rows", []) if isinstance(data, dict) else []
            if not isinstance(rows, list):
                rows = []
            rows.append(row)
            rows = rows[-1500:]
            write_json(TX_HISTORY_PATH, {"rows": rows})

    def _parse_tx_history_timestamp(self, value: object) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text:
            return None
        is_utc = text.upper().endswith("UTC")
        text = text.replace("UTC", "").strip()[:19]
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(text, fmt)
                if is_utc:
                    return dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                pass
        return None

    def local_outgoing_history_for(self, call: str, time_label: str | None = None, search_text: str = "") -> list[dict]:
        with self.soft("local_outgoing_history_for"):
            data = read_json(TX_HISTORY_PATH)
            rows = data.get("rows", []) if isinstance(data, dict) else []
            target = norm_call(call)
            out = [r for r in rows if norm_call(str(r.get("to", "") or "")) == target]
            hours = TIME_FILTERS.get(time_label or "", None)
            if hours is not None:
                cutoff_utc = datetime.now(timezone.utc) - timedelta(hours=float(hours))
                filtered = []
                for row in out:
                    dt = self._parse_tx_history_timestamp(row.get("timestamp", ""))
                    if not dt:
                        continue
                    if dt.tzinfo is None:
                        # Treat old local/no-zone rows as local-ish and compare loosely.
                        if dt >= datetime.now() - timedelta(hours=float(hours)):
                            filtered.append(row)
                    elif dt >= cutoff_utc:
                        filtered.append(row)
                out = filtered
            needle = str(search_text or "").upper().strip()
            if needle:
                out = [r for r in out if needle in (" ".join(str(r.get(k, "")) for k in ("timestamp", "from", "to", "context", "text"))).upper()]
            return out
        return []

    def observed_outgoing_history_for(self, call: str, time_label: str | None = None, search_text: str = "") -> list[dict]:
        """Outgoing frames JS8Map reported transmitting to `call`.

        These are sends fired from the JS8Map UI: they go straight to JS8Call and
        never pass through FastChat's own TX path, so they aren't in TX_HISTORY_PATH.
        JS8Map appends them to OBSERVED_TX_PATH (it is the SOLE writer; FastChat is
        strictly read-only here, exactly like the intent/raise/server handshake
        files). Returns the same row shape as local_outgoing_history_for so the
        inline chat and History popup can merge both sources identically.
        """
        with self.soft("observed_outgoing_history_for"):
            data = read_json(OBSERVED_TX_PATH)
            rows = data.get("rows", []) if isinstance(data, dict) else []
            target = norm_call(call)
            out = [r for r in rows if norm_call(str(r.get("to", "") or "")) == target]
            hours = TIME_FILTERS.get(time_label or "", None)
            if hours is not None:
                cutoff_utc = datetime.now(timezone.utc) - timedelta(hours=float(hours))
                filtered = []
                for row in out:
                    dt = self._parse_tx_history_timestamp(row.get("timestamp", ""))
                    if not dt:
                        continue
                    if dt.tzinfo is None:
                        if dt >= datetime.now() - timedelta(hours=float(hours)):
                            filtered.append(row)
                    elif dt >= cutoff_utc:
                        filtered.append(row)
                out = filtered
            needle = str(search_text or "").upper().strip()
            if needle:
                out = [r for r in out if needle in (" ".join(str(r.get(k, "")) for k in ("timestamp", "from", "to", "context", "text"))).upper()]
            return out
        return []

    def incoming_partial_for(self, call: str) -> "dict | None":
        """The in-progress long-message preview FROM `call`, or None.

        Assembled live, in-process, from JS8Call's per-frame RX.ACTIVITY events
        by FastChat's own live listener (services.live_rig_monitor feeds
        services.rx_partial). No shared file and no JS8Map involvement -- the
        preview is a throwaway UI element that clears the instant the real
        directed row lands in the shared DB, so both apps stay in sync on the
        persistent data exactly as before.
        """
        with self.soft("incoming_partial_for"):
            from ..services.rx_partial import rx_partials
            return rx_partials.partial_for(call)
        return None

    def clear_local_outgoing_history_for(self, call: str) -> int:
        """Delete this station's rows from FastChat's LOCAL sent-history file.

        Removes every TX-history row whose 'to' base-callsign matches `call`,
        rewrites the file, and returns how many were removed. Only touches
        FastChat's own TX_HISTORY_PATH -- no JS8Map or JS8Call DB records are
        affected. Returns 0 on any failure (soft-guarded).

        (Previously this body was orphaned after incoming_partial_for's return,
        with no def of its own, so callers hit AttributeError and the Clear Sent
        History button silently did nothing. Restored to a real method here.)
        """
        with self.soft("clear_local_outgoing_history_for"):
            data = read_json(TX_HISTORY_PATH)
            rows = data.get("rows", []) if isinstance(data, dict) else []
            target = norm_call(call)
            kept = [r for r in rows if norm_call(str(r.get("to", "") or "")) != target]
            removed = len(rows) - len(kept)
            write_json(TX_HISTORY_PATH, {"rows": kept})
            return removed
        return 0

    # --- persistent per-callsign capture (what each station sent, to anyone) ---

    def _ensure_capture_loaded(self) -> None:
        if getattr(self, "_capture_store", None) is not None:
            return
        self._capture_store = {}
        self._capture_keys = {}
        self._capture_dirty = False
        self._capture_last_save = 0.0
        try:
            data = read_json(CAPTURE_HISTORY_PATH)
            calls = data.get("calls", {}) if isinstance(data, dict) else {}
            for call, rows in calls.items():
                if not isinstance(rows, list):
                    continue
                # Older files filed W3BFO and W3BFO/P together under W3BFO.
                # File each row under its own sender so the two separate again.
                for r in rows:
                    if not isinstance(r, dict):
                        continue
                    c = norm_call(str(r.get("from") or call))
                    if not c:
                        continue
                    k = (str(r.get("timestamp", "")), str(r.get("text", "")))
                    seen = self._capture_keys.setdefault(c, set())
                    if k in seen:
                        continue
                    seen.add(k)
                    self._capture_store.setdefault(c, []).append(r)
        except Exception:
            debug_exc("ensure_capture_loaded")

    def capture_activity_rows(self, rows) -> None:
        """Persist real (non-clutter) messages from heard callsigns into a local
        per-callsign capture log, so History keeps a running record of what each
        station sent (to anyone) beyond JS8Map's DB time window."""
        with self.soft("capture_activity_rows"):
            self._ensure_capture_loaded()
            added = 0
            for row in rows or []:
                try:
                    if (self.is_history_clutter(row)
                            and self.snr_report_value(row.text) is None):
                        continue
                    text = (row.text or "").strip()
                    if not text:
                        continue
                    c = norm_call(str(row.call or ""))
                    if not c:
                        continue
                    ts = str(row.timestamp or "")
                    key = (ts, text)
                    seen = self._capture_keys.setdefault(c, set())
                    if key in seen:
                        continue
                    seen.add(key)
                    self._capture_store.setdefault(c, []).append({
                        "timestamp": ts,
                        "from": row.call,
                        "to": row.to_call or "",
                        "snr": row.snr,
                        "freq": row.freq,
                        "text": text,
                        "source": row.source,
                    })
                    added += 1
                    lst = self._capture_store[c]
                    if len(lst) > CAPTURE_PER_CALL_CAP:
                        for d in lst[:len(lst) - CAPTURE_PER_CALL_CAP]:
                            seen.discard((str(d.get("timestamp", "")), str(d.get("text", ""))))
                        del lst[:len(lst) - CAPTURE_PER_CALL_CAP]
                except Exception:
                    continue
            if added:
                self._capture_dirty = True
                self._maybe_save_capture()

    def _maybe_save_capture(self, force: bool = False) -> None:
        if not getattr(self, "_capture_dirty", False):
            return
        if not force and (time.time() - float(getattr(self, "_capture_last_save", 0.0))) < 10.0:
            return
        with self.soft("save_capture"):
            write_json(CAPTURE_HISTORY_PATH, {"calls": self._capture_store})
            self._capture_dirty = False
            self._capture_last_save = time.time()

    def captured_history_for(self, call: str, time_label: str | None = None, search_text: str = "") -> list[dict]:
        self._ensure_capture_loaded()
        c = norm_call(call)
        rows = self._capture_store.get(c, [])
        needle = (search_text or "").upper().strip()
        cutoff = ""
        try:
            if time_label:
                cutoff = self.reader.cutoff_string(time_label) or ""
        except Exception:
            cutoff = ""
        out = []
        for r in rows:
            ts = str(r.get("timestamp", ""))
            if cutoff and len(ts) >= 10 and ts[:4].isdigit() and ts < cutoff:
                continue
            if needle:
                hay = (str(r.get("text", "")) + " " + str(r.get("to", "")) + " " + ts).upper()
                if needle not in hay:
                    continue
            out.append(r)
        return out

    def confirm_tx_dialog(self, frame: str) -> bool:
        # Parent the dialog to whichever window the operator is actually working
        # in (the active chat/history/inbox popup), not always the main console.
        # This keeps focus on that window instead of yanking it to the main app.
        parent = self
        try:
            focused = self.focus_get()
            if focused is not None:
                parent = focused.winfo_toplevel()
        except Exception:
            parent = self
        return messagebox.askyesno("Confirm TX", f"Send this through JS8Call now?\n\n{frame}", parent=parent)

    def apply_macro_text(self, raw: str) -> None:
        """Load a macro into the Command Preview, transmitting if TX Armed.
        Shared by the Saved Macros popup's Go button and double-click; behaves
        exactly as the old inline list did."""
        txt = str(raw or "").strip().upper()
        if not txt:
            return
        self.preview_var.set(txt)
        if self.tx_armed_var.get():
            self.transmit_frame(txt, clear_preview=True, context="macro")
        else:
            self.set_status("Macro loaded into preview. TX Armed is OFF — not sent.")

    def open_macros_popup(self) -> None:
        """Saved Macros chooser in a popup so the main window stays compact
        (frees the old large list area). Pick a macro and press Go (or double-
        click / Enter) to load it; X Clear wipes the Command Preview. Non-modal,
        so it can sit beside the main window while you fire several. Reopening
        just raises the existing popup."""
        existing = getattr(self, "_macros_popup", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    self._raise_popup_to_front(existing)
                    return
            except Exception:
                pass
        p = self.pal()
        top = tk.Toplevel(self)
        top.title("Saved Macros")
        top.configure(bg=p.panel)
        top.transient(self)
        self._macros_popup = top

        self.label(top, "Saved Macros", size=12, bg=p.panel).pack(fill="x", padx=12, pady=(12, 4))
        box = tk.Frame(top, bg=p.panel)
        box.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        vsb = tk.Scrollbar(box, orient="vertical")
        vsb.pack(side="right", fill="y")
        lst = tk.Listbox(box, bg=p.entry_bg, fg=p.entry_fg, font=self.scaled_font(12, "normal"),
                         relief="solid", bd=1, activestyle="none", width=34, height=14,
                         yscrollcommand=vsb.set)
        lst.pack(side="left", fill="both", expand=True)
        vsb.configure(command=lst.yview)
        for macro in self.cfg.macros or DEFAULT_MACROS:
            lst.insert("end", str(macro).upper())

        def do_go(_e=None):
            sel = lst.curselection()
            if not sel:
                self.set_status("Pick a macro first, then Go.")
                return
            self.apply_macro_text(lst.get(sel[0]))

        def do_clear(_e=None):
            self.preview_var.set("")
            self.set_status("Command Preview cleared.")

        def do_close(_e=None):
            try:
                top.destroy()
            finally:
                self._macros_popup = None

        lst.bind("<Double-Button-1>", do_go)
        lst.bind("<Return>", do_go)

        btns = tk.Frame(top, bg=p.panel)
        btns.pack(fill="x", padx=12, pady=(0, 12))
        self.button(btns, "Go", command=do_go, width=8, good=True).pack(side="left")
        self.button(btns, "X Clear", command=do_clear, width=8).pack(side="left", padx=(6, 0))
        self.button(btns, "Close", command=do_close, width=8).pack(side="right")

        top.protocol("WM_DELETE_WINDOW", do_close)
        top.bind("<Escape>", do_close)

        # Open over the main window (dual-monitor safe), like the Add Station
        # and FastChat popups; non-modal so the main screen stays usable.
        top.update_idletasks()
        try:
            px, py = self.winfo_rootx(), self.winfo_rooty()
            pw = self.winfo_width()
            w = top.winfo_reqwidth()
            top.geometry(f"+{px + max(0, (pw - w) // 2)}+{py + 80}")
        except Exception:
            pass
        try:
            self._raise_popup_to_front(top)
        except Exception:
            pass
        if lst.size():
            lst.selection_set(0)
        lst.focus_set()
