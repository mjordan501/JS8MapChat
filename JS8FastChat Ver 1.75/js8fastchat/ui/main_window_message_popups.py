"""FastChat message-window popups extracted from MainWindow (T3).

History / Other-Traffic / Inbox popups, message deletion, and the per-callsign
Inbox "unread mail" indicator subsystem. Mixed into MainWindow via MRO, so every
method stays a method and reaches the rest of the app through ``self`` (widget
factory, theme, layout, and the TX/history readers that remain on MainWindow).
This module never imports main_window (no cycle).
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from datetime import datetime, timezone

from ..constants import TIME_FILTERS
from ..models import ActivityRow
from ..utils import base_call, fmt_freq, norm_call
from .main_window_ui_substrate import popup_font

# Views in the message window (Inbox popup). The third lists messages I have
# STORED for a callsign that are still waiting to be picked up.
INBOX_VIEW_OUTGOING = "Outgoing - Waiting Pickup"
INBOX_VIEWS = ("To Me Only", "All From This Callsign", INBOX_VIEW_OUTGOING)
# What the Inbox button opens on.
INBOX_VIEW_DEFAULT = "All From This Callsign"


class _MessagePopupsMixin:
    """History/Other-Traffic/Inbox popups, delete, and inbox-button indicators."""

    # -- FastChat Inbox "unread mail" indicator ------------------------------
    # The per-callsign Inbox button (built in FastChatPopup) turns its outline
    # red and shows an unread count when that station has inbox message(s)
    # addressed to me that I have not opened yet. "Unread" is tracked with a
    # per-call last-seen watermark persisted in the layout dict (the same
    # get_layout_value/set_layout_value mechanism as the compose-box height) --
    # the inbox_msgs table has no read flag of its own.
    # READ-ONLY: this only counts existing DB rows; nothing is injected into
    # all_activity_rows or anywhere else.
    def _inbox_msg_key(self, r: dict):
        """JSON-serializable, comparable identity for one inbox row:
        [utc-epoch-seconds, db-id]. Newer rows sort higher; robust whether the
        row has a numeric id (normal spot_db path) or not (derived fallback)."""
        ts = self._history_ts_utc(r.get("timestamp"))
        ts_epoch = ts.timestamp() if ts else 0.0
        try:
            rid = int(r.get("id") or 0)
        except Exception:
            rid = 0
        return [ts_epoch, rid]

    def _inbox_seen_map(self) -> dict:
        d = self.get_layout_value("fastchat_inbox_seen", {})
        return dict(d) if isinstance(d, dict) else {}

    def _inbox_rows_to_me(self, call: str) -> list:
        """Inbox rows from `call` addressed to me -- the same view the Inbox
        button's count and the inbox popup's default ('To Me Only') show."""
        return self.reader.inbox_rows_for(
            call, to_me_only=True, my_call=self.locator.callsign()
        )

    def _refresh_inbox_data(self) -> None:
        """Drop the cached JS8Call inbox read so the next access is current."""
        with self.soft("invalidate inbox cache", log=False):
            self.reader.invalidate_inbox_cache()

    def inbox_unread_count_for(self, call: str) -> int:
        """How many inbox messages from `call` (to me) are still unread.

        A message counts as unread only if BOTH are true:
          * JS8Call itself marks it UNREAD (its own read state -- the same flag
            you see in JS8Call's Message Inbox), and
          * I have not opened this callsign's inbox in FastChat since it arrived
            (the per-call last-seen watermark).
        So messages already read in JS8Call never flag, and reading them here
        clears the flag too. 0 when caught up.
        """
        with self.soft("inbox unread count", log=False):
            b = norm_call(call)
            if not b:
                return 0
            rows = self._inbox_rows_to_me(call)
            if not rows:
                return 0
            seen = self._inbox_seen_map().get(b)
            seen_key = tuple(seen) if isinstance(seen, (list, tuple)) else (0.0, 0)
            n = 0
            for r in rows:
                if not r.get("unread", True):
                    continue  # already read in JS8Call
                if tuple(self._inbox_msg_key(r)) > seen_key:
                    n += 1
            return n
        return 0

    def mark_inbox_seen(self, call: str) -> None:
        """Record that I have viewed this callsign's inbox up to its newest
        message, so the Inbox button clears. Called when the inbox popup opens."""
        with self.soft("mark inbox seen"):
            b = norm_call(call)
            if not b:
                return
            rows = self._inbox_rows_to_me(call)
            keys = [tuple(self._inbox_msg_key(r)) for r in rows]
            newest = max(keys) if keys else (0.0, 0)
            m = self._inbox_seen_map()
            m[b] = list(newest)
            self.set_layout_value("fastchat_inbox_seen", m)

    # Foreground red for the unread label. Deliberately lighter than p.danger,
    # which is tuned as a border/fill red and goes muddy as text on the dark
    # button face; this stays legible. Tweak to taste.
    INBOX_UNREAD_FG = "#ff6b6b"

    def _paint_inbox_button(self, call: str, btn) -> None:
        """Red outline + red count when this callsign has new inbox mail for me;
        plain 'Inbox' in the normal text color otherwise. Safe to call on any
        refresh tick."""
        with self.soft("paint inbox button", log=False):
            if btn is None or not btn.winfo_exists():
                return
            p = self.pal()
            n = self.inbox_unread_count_for(call)
            if n > 0:
                label = f"Inbox ({n})"
                btn.configure(
                    text=label, width=max(8, len(label) + 1),
                    fg=self.INBOX_UNREAD_FG,
                    highlightthickness=2,
                    highlightbackground=p.danger, highlightcolor=p.danger,
                )
            else:
                # Restore the normal foreground -- without this the text would
                # stay red after the mail is read.
                btn.configure(text="Inbox", width=8, fg=p.text,
                              highlightthickness=0)

    def _register_inbox_button(self, call: str, btn) -> None:
        """Track a popup's Inbox button so the data-refresh pipeline can recolor
        it as mail arrives / is read. Mirrors _qso_latest_widgets."""
        b = norm_call(call)
        if not b:
            return
        d = getattr(self, "_qso_inbox_buttons", None)
        if d is None:
            d = self._qso_inbox_buttons = {}
        d.setdefault(b, []).append(btn)

    def refresh_inbox_buttons(self) -> None:
        """Recolor every open Inbox and Store Msg button; prune dead ones.
        Called from the data-refresh pipeline alongside refresh_open_qso_popups()."""
        self._paint_main_store_button()
        for registry, painter in (
            (getattr(self, "_qso_inbox_buttons", {}), self._paint_inbox_button),
            (getattr(self, "_qso_store_buttons", {}), self._paint_store_button),
        ):
            for call, btns in list(registry.items()):
                live = []
                for btn in btns:
                    try:
                        if btn.winfo_exists():
                            painter(call, btn)
                            live.append(btn)
                    except Exception:
                        pass
                if live:
                    registry[call] = live
                else:
                    registry.pop(call, None)

    def _clean_msg(self, s) -> str:
        """Normalize a decoded message body for display (ASCII protocol).

        Same as FastChatPopup._clean_msg so the History popup and inline chat
        show identical clean text. JS8Map stores raw decodes that can carry
        non-ASCII byte runs (mojibake / EOM glyph) JS8Call cleans before display.
        """
        if not s:
            return ""
        out = "".join(ch if (ch == "\n" or 32 <= ord(ch) <= 126) else " " for ch in str(s))
        return "\n".join(" ".join(seg.split()) for seg in out.split("\n")).strip()

    def _history_ts_utc(self, value):
        """Normalize a history timestamp to a UTC-aware datetime.

        Same logic as FastChatPopup._ts_utc so the standalone History popup and
        the inline chat read on one identical clock: outgoing rows are written
        UTC (' UTC' suffix); incoming JS8Map DB rows are naive local time.
        """
        text = str(value or "").strip()
        if not text:
            return None
        is_utc = text.upper().endswith("UTC")
        core = text.upper().replace("UTC", "").strip()[:19]
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(core, fmt)
                return dt.replace(tzinfo=timezone.utc) if is_utc else dt.astimezone(timezone.utc)
            except Exception:
                pass
        return None

    def open_history_popup(self, call: str, parent=None) -> None:
        p = self.pal()
        call = norm_call(call)
        _existing = self._history_popups.get(call)
        if _existing is not None and _existing.winfo_exists():
            _existing.deiconify(); _existing.lift(); _existing.focus_force()
            return
        top = tk.Toplevel(parent or self)
        self._history_popups[call] = top

        def _forget_history(_e, c=call, t=top):
            if _e.widget is t and self._history_popups.get(c) is t:
                self._history_popups.pop(c, None)
        top.bind("<Destroy>", _forget_history, add="+")
        top.title(f"QSO History - {call}")
        top.configure(bg=p.panel)
        self.bind_popup_layout(top, "history_geometry", "1080x620")

        header = tk.Frame(top, bg=p.panel)
        header.pack(fill="x", padx=8, pady=(8, 4))
        tk.Label(header, text=f"{call} History", bg=p.panel, fg=p.text, font=popup_font("Arial", 18, "bold"), anchor="w").pack(side="left")

        controls = tk.Frame(top, bg=p.panel)
        controls.pack(fill="x", padx=8, pady=(0, 6))
        tk.Label(controls, text="Show:", bg=p.panel, fg=p.text, font=popup_font("Arial", 12, "bold")).pack(side="left", padx=(0, 4))
        history_time_var = tk.StringVar(value=self.db_time_var.get() if self.db_time_var.get() in TIME_FILTERS else "Last 1 hour")
        time_cb = ttk.Combobox(controls, textvariable=history_time_var, values=list(TIME_FILTERS.keys()), state="readonly", width=16, font=popup_font("Arial", 12, "bold"), style="MJ.TCombobox")
        time_cb.pack(side="left", padx=(0, 10))

        tk.Label(controls, text="Search:", bg=p.panel, fg=p.text, font=popup_font("Arial", 12, "bold")).pack(side="left", padx=(0, 4))
        search_var = tk.StringVar(value="")
        search_entry = tk.Entry(controls, textvariable=search_var, bg=p.entry_bg, fg=p.entry_fg, insertbackground=p.entry_fg, relief="solid", bd=1, font=popup_font("Arial", 12, "bold"), width=22, highlightthickness=1, highlightbackground="#5a6b7a", highlightcolor=(p.green if hasattr(p, "green") else "#33ff77"))
        search_entry.pack(side="left", padx=(0, 4))
        clear_x = tk.Label(search_entry, text="\u2715", bg=p.entry_bg, fg="#9aa0a6", font=popup_font("Arial", 11, "bold"), cursor="hand2")
        clear_x.place(relx=1.0, rely=0.5, anchor="e", x=-3)
        clear_x.bind("<Button-1>", lambda _e: clear_search())

        hist_holder = tk.Frame(top, bg=p.panel)
        hist_holder.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        hist_scroll = tk.Scrollbar(hist_holder, orient="vertical")
        hist_scroll.pack(side="right", fill="y")
        txt = self.text_box(hist_holder, font=popup_font("Consolas", 16, "bold"), wrap="word", yscrollcommand=hist_scroll.set)
        txt.pack(side="left", fill="both", expand=True)
        hist_scroll.configure(command=txt.yview)
        txt.tag_configure("rx_red", foreground=("#ff6b6b" if self.theme_var.get() == "Dark" else "#cc0000"))
        # Outgoing (what I sent) in gold, mirroring the incoming red. Theme-aware:
        # bright gold pops on the dark background; a darker goldenrod stays
        # readable on the light theme's white background (bright gold washes out).
        txt.tag_configure("tx_gold", foreground=("#FFD700" if self.theme_var.get() == "Dark" else "#B8860B"))

        def row_matches_search(row: ActivityRow, needle: str) -> bool:
            if not needle:
                return True
            hay = " ".join([str(row.timestamp), str(row.call), str(row.to_call or ""), str(row.snr or ""), str(row.text or "")]).upper()
            return needle in hay

        def populate_history(*_args):
            time_label = history_time_var.get()
            needle = search_var.get().upper().strip()
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            rows = [r for r in self.reader.read_activity(time_label, limit=1000) if norm_call(str(r.call or "")) == norm_call(call)]
            clean_rows = [r for r in rows
                          if (not self.is_history_clutter(r)
                              or self.snr_report_value(r.text) is not None)
                          and row_matches_search(r, needle)]
            outgoing_rows = self.local_outgoing_history_for(call, time_label, needle)
            # Merge the live DB window with the persistent capture log so History
            # shows what this station sent (to anyone) even after it ages out of
            # JS8Map's DB window.
            incoming = {}
            # Same SNR report, two formats -> one line. Keep the earlier
            # timestamp so repeated repaints settle on the same copy.
            _snr_seen: dict = {}

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

            for r in clean_rows:
                key = (str(r.timestamp), (r.text or "").strip())
                if _snr_dedupe(self.snr_report_value(r.text), r.call, str(r.timestamp), key):
                    continue
                incoming[key] = {"ts": str(r.timestamp), "from": r.call, "to": r.to_call or "", "snr": r.snr, "text": (r.text or "").strip()}
            for r in self.captured_history_for(call, time_label, needle):
                key = (str(r.get("timestamp", "")), str(r.get("text", "")).strip())
                if key not in incoming:
                    if _snr_dedupe(self.snr_report_value(r.get("text", "")), r.get("from", call), str(r.get("timestamp", "")), key):
                        continue
                    incoming[key] = {"ts": str(r.get("timestamp", "")), "from": r.get("from", call), "to": r.get("to", ""), "snr": r.get("snr", ""), "text": str(r.get("text", "")).strip()}
            incoming_rows = list(incoming.values())

            # Outgoing (gold) side merges TWO sources of OUR sends, deduped:
            #   1) local_outgoing_history_for -> FastChat's own TX log (frames sent
            #      from this app via record_outgoing_tx).
            #   2) observed_outgoing_history_for -> frames JS8Map reported sending
            #      (fired from the JS8Map UI; these never reach our TX log -- they
            #      were the missing half of a JS8Map-origin exchange).
            # Keyed by (UTC ts to the minute, cleaned text) so a frame present in
            # both renders once. No clutter filter on the gold side -- always show
            # what the operator transmitted (SNR?/HEARING? included).
            def _sent_key(udt, body):
                kts = udt.strftime("%Y-%m-%d %H:%M") if udt else ""
                return (kts, (body or "").strip().upper())

            sent = {}
            for r in self.observed_outgoing_history_for(call, time_label, needle):
                ts = str(r.get("timestamp", ""))
                udt = self._history_ts_utc(ts)
                disp = udt.strftime("%Y-%m-%d %H:%M:%S UTC") if udt else ts
                body = self._clean_msg(r.get("text", ""))
                sent[_sent_key(udt, body)] = (udt, "tx", f"[{disp}] {r.get('from','KW3KW')} -> {r.get('to', call)}  {r.get('context','TX')}", body)
            for r in outgoing_rows[-200:]:
                ts = str(r.get("timestamp", ""))
                udt = self._history_ts_utc(ts)
                disp = udt.strftime("%Y-%m-%d %H:%M:%S UTC") if udt else ts
                body = self._clean_msg(r.get("text", ""))
                sent[_sent_key(udt, body)] = (udt, "tx", f"[{disp}] {r.get('from','KW3KW')} -> {r.get('to', call)}  {r.get('context','TX')}", body)

            top.title(f"QSO History - {call} - {time_label}")
            if not incoming_rows and not sent:
                extra = f" matching '{needle}'" if needle else ""
                txt.insert("1.0", f"No filtered history found for {call}{extra} in window: {time_label}.")
            else:
                # One interleaved, chronological timeline in UTC -- identical
                # timing/format to the inline FastChat chat (incoming naive-local
                # stamps normalized to UTC). Sent gold, received red.
                events = []  # (utc_dt, kind, header, body)
                for d in incoming_rows:
                    udt = self._history_ts_utc(d["ts"])
                    disp = udt.strftime("%Y-%m-%d %H:%M:%S UTC") if udt else d["ts"]
                    events.append((udt, "rx", f"[{disp}] {d['from']} -> {d['to']}  SNR {d['snr']}", self._clean_msg(d["text"])))
                events.extend(sent.values())
                _epoch = datetime.min.replace(tzinfo=timezone.utc)
                events.sort(key=lambda e: e[0] or _epoch)
                for _udt, kind, header, body in events:
                    txt.insert("end", header + "\n")
                    body_start = txt.index("end-1c")
                    txt.insert("end", f"{body}\n\n")
                    txt.tag_add("tx_gold" if kind == "tx" else "rx_red", body_start, "end-1c")
            txt.configure(state="disabled")

        def clear_search():
            search_var.set("")
            populate_history()

        def clear_sent_history():
            if not messagebox.askyesno("Clear FastChat sent history", f"Clear local FastChat sent history for {call}?\n\nThis does not delete JS8Map or JS8Call database records.", parent=top):
                return
            removed = self.clear_local_outgoing_history_for(call)
            self.set_status(f"Cleared {removed} local FastChat sent history row(s) for {call}.")
            populate_history()

        btn_font = popup_font("Arial", 13, "bold")
        tk.Button(controls, text="GO", command=populate_history, bg=p.button, fg=p.text, font=btn_font, width=5).pack(side="left", padx=(0, 10))
        tk.Button(controls, text="Other Traffic", command=lambda: self.open_other_traffic_popup(call, parent=top), bg=p.button, fg=p.text, font=btn_font, width=13).pack(side="left", padx=(0, 10))
        tk.Button(controls, text="Clear Sent History", command=clear_sent_history, bg="#9b1c1c", fg="#ffffff", font=btn_font, width=17).pack(side="left", padx=(0, 4))
        tk.Button(controls, text="Close", command=top.destroy, bg=p.button, fg=p.text, font=btn_font, width=8).pack(side="right")

        search_entry.bind("<Return>", lambda _e: populate_history())
        time_cb.bind("<<ComboboxSelected>>", lambda _e: populate_history())
        populate_history()

    # Palette cycled per distinct callsign seen in an Other Traffic popup, so
    # each station's lines stay a consistent color within that popup's
    # lifetime. Picked for readability on the dark chat background; first
    # entry intentionally avoided (too close to tx_gold/rx_red already used
    # elsewhere in History/FastChat, which would blur the "this is a third
    # party, not me" distinction this view exists to make).
    _OTHER_TRAFFIC_PALETTE = ["#5fb4ff", "#9b8cff", "#52d68a", "#ffb454", "#ff7ab8", "#7fd4d4", "#d4a5ff", "#c9d65f"]

    def open_other_traffic_popup(self, focus_call: str, parent=None) -> None:
        """Show conversation(s) involving `focus_call` and OTHER stations --
        i.e. traffic neither sent nor received by this operator. Opened from
        the per-call History popup's "Other Traffic" button.

        Scope (by design, confirmed with operator):
          * Directed traffic only (a real to_call; bare CQ/heartbeat with no
            addressee is excluded -- this view is for following a QSO between
            two OTHER operators, not general band noise).
          * A row qualifies if focus_call is on EITHER side (sender or
            recipient) -- so if KA2YNT is chatting with N4ABC, opening this
            from KA2YNT's History shows that exchange even though N4ABC, not
            KA2YNT, may be the to_call on half the rows.
          * Rows where MY OWN callsign is on either side are excluded --
            that's what the regular History/FastChat chat already shows.
          * Same clutter filter (is_history_clutter) and same UTC
            normalization (_history_ts_utc) as History, so behavior matches
            what the operator already expects from that popup.
          * No metadata columns (no Tag/SNR/Freq/Type) -- same plain
            scrolling chat-style rendering as History, per operator request.
          * Colored BY SPEAKER (not by sent/received -- neither side is the
            operator), so a multi-party exchange reads like an actual
            conversation rather than an undifferentiated wall of text.
        """
        p = self.pal()
        focus_base = norm_call(focus_call)
        my_call = base_call(self.locator.callsign())
        top = tk.Toplevel(parent or self)
        top.title(f"Other Traffic - {focus_base}")
        top.configure(bg=p.panel)
        self.bind_popup_layout(top, "other_traffic_geometry", "1080x620")

        header = tk.Frame(top, bg=p.panel)
        header.pack(fill="x", padx=8, pady=(8, 4))
        tk.Label(header, text=f"Traffic involving {focus_base} (not directed to/from me)", bg=p.panel, fg=p.text, font=popup_font("Arial", 18, "bold"), anchor="w").pack(side="left")

        controls = tk.Frame(top, bg=p.panel)
        controls.pack(fill="x", padx=8, pady=(0, 6))
        tk.Label(controls, text="Show:", bg=p.panel, fg=p.text, font=popup_font("Arial", 12, "bold")).pack(side="left", padx=(0, 4))
        time_var = tk.StringVar(value=self.db_time_var.get() if self.db_time_var.get() in TIME_FILTERS else "Last 1 hour")
        time_cb = ttk.Combobox(controls, textvariable=time_var, values=list(TIME_FILTERS.keys()), state="readonly", width=16, font=popup_font("Arial", 12, "bold"), style="MJ.TCombobox")
        time_cb.pack(side="left", padx=(0, 10))

        tk.Label(controls, text="Search:", bg=p.panel, fg=p.text, font=popup_font("Arial", 12, "bold")).pack(side="left", padx=(0, 4))
        search_var = tk.StringVar(value="")
        search_entry = tk.Entry(controls, textvariable=search_var, bg=p.entry_bg, fg=p.entry_fg, insertbackground=p.entry_fg, relief="solid", bd=1, font=popup_font("Arial", 12, "bold"), width=22, highlightthickness=1, highlightbackground="#5a6b7a", highlightcolor=(p.green if hasattr(p, "green") else "#33ff77"))
        search_entry.pack(side="left", padx=(0, 4))
        clear_x = tk.Label(search_entry, text="\u2715", bg=p.entry_bg, fg="#9aa0a6", font=popup_font("Arial", 11, "bold"), cursor="hand2")
        clear_x.place(relx=1.0, rely=0.5, anchor="e", x=-3)
        clear_x.bind("<Button-1>", lambda _e: clear_search())

        btn_font = popup_font("Arial", 13, "bold")
        tk.Button(controls, text="GO", command=lambda: populate(), bg=p.button, fg=p.text, font=btn_font, width=5).pack(side="left", padx=(0, 10))
        tk.Button(controls, text="Close", command=top.destroy, bg=p.button, fg=p.text, font=btn_font, width=8).pack(side="right")

        hist_holder = tk.Frame(top, bg=p.panel)
        hist_holder.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        hist_scroll = tk.Scrollbar(hist_holder, orient="vertical")
        hist_scroll.pack(side="right", fill="y")
        txt = self.text_box(hist_holder, font=popup_font("Consolas", 16, "bold"), wrap="word", yscrollcommand=hist_scroll.set)
        txt.pack(side="left", fill="both", expand=True)
        hist_scroll.configure(command=txt.yview)
        txt.tag_configure("ot_hdr", foreground=p.muted)

        speaker_color: dict[str, str] = {}

        def color_for(call: str) -> str:
            base = norm_call(call)
            if base not in speaker_color:
                idx = len(speaker_color) % len(self._OTHER_TRAFFIC_PALETTE)
                speaker_color[base] = self._OTHER_TRAFFIC_PALETTE[idx]
                txt.tag_configure(f"spk_{base}", foreground=speaker_color[base])
            return f"spk_{base}"

        def row_matches_search(row: ActivityRow, needle: str) -> bool:
            if not needle:
                return True
            hay = " ".join([str(row.timestamp), str(row.call), str(row.to_call or ""), str(row.snr or ""), str(row.text or "")]).upper()
            return needle in hay

        def qualifies(row: ActivityRow) -> bool:
            from_base = base_call(row.call)
            to_base = base_call(row.to_call)
            if not to_base or to_base.startswith("@"):
                # No addressee, or a group/broadcast call -- not the
                # "two-operator QSO" this view is for.
                return False
            if my_call and (from_base == my_call or to_base == my_call):
                return False
            return focus_base in (norm_call(str(row.call or "")), norm_call(str(row.to_call or "")))

        def populate(*_args):
            time_label = time_var.get()
            needle = search_var.get().upper().strip()
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            rows = [
                r for r in self.reader.read_activity(time_label, limit=2000)
                if qualifies(r) and not self.is_history_clutter(r) and row_matches_search(r, needle)
            ]
            top.title(f"Other Traffic - {focus_base} - {time_label}")
            if not rows:
                extra = f" matching '{needle}'" if needle else ""
                txt.insert("1.0", f"No other-traffic conversation found involving {focus_base}{extra} in window: {time_label}.")
                txt.configure(state="disabled")
                return

            # Dedupe identical (timestamp, text) repeats the same way History does.
            seen: dict[tuple, ActivityRow] = {}
            for r in rows:
                key = (str(r.timestamp), (r.text or "").strip())
                seen[key] = r
            ordered = sorted(seen.values(), key=lambda r: self._history_ts_utc(r.timestamp) or datetime.min.replace(tzinfo=timezone.utc))

            for r in ordered:
                udt = self._history_ts_utc(r.timestamp)
                disp = udt.strftime("%Y-%m-%d %H:%M:%S UTC") if udt else str(r.timestamp)
                body = self._clean_msg(r.text)
                header_line = f"[{disp}] {r.call} -> {r.to_call}  SNR {r.snr}"
                txt.insert("end", header_line + "\n", ("ot_hdr",))
                body_start = txt.index("end-1c")
                txt.insert("end", f"{body}\n\n")
                txt.tag_add(color_for(r.call), body_start, "end-1c")
            txt.configure(state="disabled")

        def clear_search() -> None:
            search_var.set("")
            populate()

        search_entry.bind("<Return>", lambda _e: populate())
        time_cb.bind("<<ComboboxSelected>>", lambda _e: populate())
        populate()

    # Views offered by the message window. The third shows messages I have
    # stored for this callsign that are still waiting to be picked up -- the
    # same set the green Store Msg counter counts -- so Delete Selected /
    # Clear All work identically for incoming and outgoing mail.
    def _delete_enabled(self) -> bool:
        """Is message deletion switched on? (Ticket #2b -- off for this release.)

        The reader owns the flag, because the reader is what would actually
        write to JS8Call's database. We ask it rather than keeping our own copy,
        so the buttons can never say one thing while the database layer does
        another. Defaults to FALSE if the reader does not have the flag at all:
        an OLD activity_reader.py paired with this file must fail SAFE, hiding
        delete, rather than fail open and hand an operator a live write path
        into JS8Call's inbox.
        """
        return bool(getattr(self.reader, "DELETE_ENABLED", False))

    def delete_js8call_messages(self, ids, parent=None, on_done=None, clear_all: bool = False,
                                outgoing: bool = False, call: str = "") -> None:
        """Delete messages from JS8Call's inbox, with confirmation and a backup.

        JS8Call offers no delete over its API, so this writes to its database
        file. A timestamped backup is taken first; if the backup fails, nothing
        is deleted.

        `outgoing` = these are messages I stored for someone else that are still
        waiting to be picked up, so the consequence is different (the recipient
        can no longer retrieve them) and the wording says so.
        """
        parent = parent or self
        # Belt and braces. The buttons that call this are not built when delete
        # is off, so reaching here means some OTHER path found its way in. Say
        # no, plainly, before the confirmation dialog -- never ask an operator
        # "are you sure?" about something that is not going to happen anyway.
        if not self._delete_enabled():
            self.themed_info(
                "Deleting is turned off",
                ["Deleting messages is switched off in this version of FastChat.",
                 "",
                 "It is the one action that has to write into JS8Call's own "
                 "message database. Doing that while JS8Call is running can stop "
                 "JS8Call from saving messages that are coming in to you.",
                 "",
                 "You can still delete messages from inside JS8Call itself."],
                parent)
            self.set_status("Deleting messages is turned off in this release.")
            return
        ids = [i for i in (ids or []) if str(i).strip()]
        if not ids:
            return
        n = len(ids)
        who = (call or "").strip().upper()

        if outgoing:
            head = (f"Cancel ALL {n} stored message(s)?" if clear_all
                    else f"Cancel {n} stored message(s)?")
            body = [
                head,
                "",
                f"These are waiting at your station for {who or 'this callsign'} to retrieve."
                if who else "These are waiting at your station to be retrieved.",
                f"{who or 'They'} will no longer be able to pick them up.",
                "",
                "A backup copy of the inbox database is saved first.",
            ]
            ok_text = "Cancel Msg"
            done_title = "Stored messages removed"
        else:
            head = (f"Delete ALL {n} message(s) shown?" if clear_all
                    else f"Delete {n} selected message(s)?")
            body = [
                head,
                "",
                "This removes them from JS8Call's message inbox permanently.",
                "",
                "A backup copy of the inbox database is saved first.",
            ]
            ok_text = "Delete"
            done_title = "Messages deleted"

        if not self.themed_confirm("Delete messages", body, parent, ok_text=ok_text):
            return

        deleted, backup, err = self.reader.delete_inbox_messages(ids)
        if err:
            self.set_status(f"Delete failed: {err}")
            self.themed_error("Delete failed", [err], parent)
            return

        self._refresh_inbox_data()
        if callable(on_done):
            with self.soft("reload message list"):
                on_done()
        self.refresh_inbox_buttons()
        self.set_status(f"Deleted {deleted} message(s). Backup: {backup}")
        # JS8Call keeps its own view of the inbox in memory, so its window may
        # still list a deleted message until it is restarted.
        self.themed_info(
            done_title,
            [
                f"Removed {deleted} message(s) from JS8Call's inbox.",
                "",
                "Backup saved:",
                str(backup),
                "",
                "Note: JS8Call may still show them in its own Message Inbox "
                "window until JS8Call is restarted. FastChat is up to date now.",
            ],
            parent,
        )

    def open_inbox_popup(self, call: str, view: str = "", parent=None) -> None:
        _eff_view = view if view in INBOX_VIEWS else INBOX_VIEW_DEFAULT
        _inbox_key = (norm_call(call), _eff_view)
        _existing = self._inbox_popups.get(_inbox_key)
        if _existing is not None and _existing.winfo_exists():
            _existing.deiconify(); _existing.lift(); _existing.focus_force()
            return
        p = self.pal(); top = tk.Toplevel(parent or self)
        self._inbox_popups[_inbox_key] = top

        def _forget_inbox(_e, k=_inbox_key, t=top):
            if _e.widget is t and self._inbox_popups.get(k) is t:
                self._inbox_popups.pop(k, None)
        top.bind("<Destroy>", _forget_inbox, add="+")
        top.title(f"Stored Msgs - {call}" if view == INBOX_VIEW_OUTGOING else f"Inbox - {call}")
        top.configure(bg=p.panel); self.bind_popup_layout(top, "inbox_geometry", "1100x740")
        # Opened via right-click on Store Msg => this is the OUTGOING list: my
        # messages held for `call`. It is a different job from reading incoming
        # mail, so the window commits to it -- the view is locked, and the reply
        # controls (which only make sense for answering received mail) are not
        # built at all.
        outgoing_only = (view == INBOX_VIEW_OUTGOING)
        hdr = tk.Frame(top, bg=p.panel); hdr.pack(fill="x", padx=8, pady=8)
        _title = (f"Messages stored for {call}" if outgoing_only
                  else f"Inbox messages from {call}")
        tk.Label(hdr, text=_title, bg=p.panel, fg=p.text, font=popup_font("Arial", 18, "bold")).pack(side="left")
        # Inbox button opens on "All From This Callsign" -- the full picture of
        # mail involving this station. "To Me Only" narrows it to mail addressed
        # to me. (The outgoing list is a separate window: right-click Store Msg.)
        view_var = tk.StringVar(value=view if view in INBOX_VIEWS else INBOX_VIEW_DEFAULT)
        tk.Label(hdr, text="View:", bg=p.panel, fg=p.text, font=popup_font("Arial", 17, "bold")).pack(side="left", padx=(30, 4))
        # Each entry point offers only the views that belong to its job:
        #   Inbox button        -> incoming mail only (To Me Only / All From)
        #   right-click Store Msg -> the outgoing waiting list only
        # so the outgoing view is not reachable from the Inbox dropdown.
        _view_values = ([INBOX_VIEW_OUTGOING] if outgoing_only
                        else [v for v in INBOX_VIEWS if v != INBOX_VIEW_OUTGOING])
        view_cb = ttk.Combobox(hdr, textvariable=view_var, values=_view_values, state="readonly", width=26, font=popup_font("Arial", 17, "bold"), style="MJ.TCombobox"); view_cb.pack(side="left")
        self._attach_tooltip(view_cb, self._tt("mw_view_outgoing" if outgoing_only else "mw_view"))
        cols = ("id", "date", "frequency", "from", "to", "message")
        # extended = shift/ctrl-click to select several rows for deletion.
        tree = ttk.Treeview(top, columns=cols, show="headings", selectmode="extended")
        for col, label, width in (("id", "ID", 60), ("date", "Date", 170), ("frequency", "Frequency", 120), ("from", "From", 90), ("to", "To", 90), ("message", "Message", 470)):
            tree.heading(col, text=label); tree.column(col, width=width, stretch=(col == "message"))
        tree.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        tk.Label(top, text="Selected message", bg=p.panel, fg=p.text, font=popup_font("Arial", 14, "bold"), anchor="w").pack(fill="x", padx=8, pady=(0, 2))
        detail = self.text_box(top, font=popup_font("Consolas", 16, "bold"), height=5, wrap="word")
        detail.pack(fill="x", padx=8, pady=(0, 6))
        # Reply controls are for answering mail someone sent ME. They are
        # meaningless for outgoing messages I am holding for someone else, so
        # they are omitted entirely in that mode rather than shown-but-dead.
        reply = None
        if not outgoing_only:
            tk.Label(top, text="Message to send", bg=p.panel, fg=p.text, font=popup_font("Arial", 14, "bold"), anchor="w").pack(fill="x", padx=8, pady=(0, 2))
            reply = self.text_box(top, font=popup_font("Consolas", 16, "bold"), height=3, wrap="word")
            reply.pack(fill="x", padx=8, pady=(0, 6)); self.attach_uppercase_text(reply)
        selected_msg = {"from": call}
        def load_rows():
            tree.delete(*tree.get_children()); detail.configure(state="normal"); detail.delete("1.0", "end")
            v = view_var.get()
            if v == INBOX_VIEW_OUTGOING:
                rows = self.reader.outbox_rows_for(call, my_call=self.locator.callsign())
            else:
                rows = self.reader.inbox_rows_for(call, to_me_only=(v == INBOX_VIEWS[0]), my_call=self.locator.callsign())
            for i, r in enumerate(rows):
                tree.insert("", "end", iid=f"inbox_{i}", values=(r.get("id", ""), r.get("timestamp", ""), fmt_freq(r.get("freq", "")), r.get("from_call", ""), r.get("to_call", ""), r.get("text", "")))
            if not rows:
                # An empty list has TWO very different causes, and conflating
                # them is what made the phase-2 inbox bug invisible: either this
                # station genuinely has no mail, or we cannot READ JS8Call's
                # inbox at all. Say which. A silent "no messages" on an
                # unreadable database is the worst outcome for an operator --
                # nothing looks broken, they just never see their mail.
                locate = getattr(self.reader, "js8call_inbox_db", None)
                inbox_db = ""
                if callable(locate):
                    with self.soft("locate JS8Call inbox", log=False):
                        inbox_db = locate() or ""
                    if not inbox_db:
                        detail.insert("1.0",
                            "JS8Call's message inbox could not be found, so FastChat cannot read any "
                            "mail.\n\nTHIS IS NOT THE SAME AS HAVING NO MESSAGES -- you may well have "
                            "mail waiting that FastChat cannot see.\n\nFastChat looks for JS8Call's "
                            "inbox.db3 (usually under %LOCALAPPDATA%\\JS8Call). Check that JS8Call is "
                            "installed and has been run at least once, then restart FastChat.")
                        detail.configure(state="disabled")
                        self.set_status("JS8Call inbox not found (inbox.db3) -- messages cannot be read.")
                        return
                if v == INBOX_VIEW_OUTGOING:
                    detail.insert("1.0", f"No stored messages waiting for {call} to pick up.")
                else:
                    detail.insert("1.0", f"No inbox messages found from {call} addressed to {self.locator.callsign() or 'my station'}.")
            elif v == INBOX_VIEW_OUTGOING:
                _how = ("Select one to view; Delete Selected removes it."
                        if self._delete_enabled()
                        else "Select one to view.")
                detail.insert("1.0", f"{len(rows)} message(s) stored at my station, waiting for {call} to retrieve with QUERY MSGS. {_how}")
            else:
                detail.insert("1.0", f"{len(rows)} inbox row(s) shown. Select one to view details, then type reply below.")
            detail.configure(state="disabled")
        def on_select(_event=None):
            sel = tree.selection();
            if not sel: return
            vals = tree.item(sel[0], "values"); selected_msg["from"] = str(vals[3] if len(vals) > 3 else call)
            detail.configure(state="normal"); detail.delete("1.0", "end"); detail.insert("1.0", str(vals[5] if len(vals) > 5 else "")); detail.configure(state="disabled")
        def send_reply(event=None):
            if reply is None:
                return
            body = reply.get("1.0", "end-1c").strip().upper()
            target = norm_call(selected_msg.get("from") or call)
            if not body: self.set_status("Type a reply before sending."); return "break"
            def after_reply_sent():
                reply.delete("1.0", "end")
                load_rows()
                self.refresh_data(False)
            self.transmit_frame(f"{target} MSG {body}", clear_preview=True, context="Inbox Reply", on_success=after_reply_sent); return "break"
        tree.bind("<<TreeviewSelect>>", on_select); view_cb.bind("<<ComboboxSelected>>", lambda _e: load_rows())
        if reply is not None:
            reply.bind("<Return>", send_reply)
        btns = tk.Frame(top, bg=p.panel); btns.pack(fill="x", padx=8, pady=(0, 8))
        def _ids_from(iids) -> list:
            out = []
            for s_iid in iids:
                vals = tree.item(s_iid, "values")
                if vals and str(vals[0]).strip():
                    out.append(vals[0])
            return out
        def delete_selected():
            ids = _ids_from(tree.selection())
            if not ids:
                self.themed_info("Delete", ["Select one or more messages first."], top); return
            self.delete_js8call_messages(
                ids, parent=top, on_done=load_rows,
                outgoing=(view_var.get() == INBOX_VIEW_OUTGOING), call=call)
        def clear_all():
            ids = _ids_from(tree.get_children())
            if not ids:
                self.themed_info("Clear All", ["There is nothing to clear."], top); return
            self.delete_js8call_messages(
                ids, parent=top, on_done=load_rows, clear_all=True,
                outgoing=(view_var.get() == INBOX_VIEW_OUTGOING), call=call)
        _b_reply = None
        if not outgoing_only:
            _b_reply = tk.Button(btns, text="Send Reply", bg=p.button, fg=p.text, font=popup_font("Arial", 14, "bold"), width=12, command=send_reply); _b_reply.pack(side="right", padx=4)
        _b_close = tk.Button(btns, text="Close", bg=p.button, fg=p.text, font=popup_font("Arial", 14, "bold"), width=10, command=top.destroy); _b_close.pack(side="right", padx=4)
        # Delete is OFF for this release (ticket #2b): it is the only thing left
        # that writes to JS8Call's live database. The controls are not BUILT --
        # not merely disabled -- because a greyed-out button invites an operator
        # to hunt for the setting that re-enables it. There are three ways in
        # (Delete Selected, Clear All, and the Delete key on the list); all
        # three are gated here, and the reader refuses as well.
        _b_del = _b_clr = None
        if self._delete_enabled():
            _b_del = tk.Button(btns, text="Delete Selected", bg="#9b1c1c", fg="#ffffff", font=popup_font("Arial", 14, "bold"), width=15, command=delete_selected); _b_del.pack(side="left", padx=4)
            _b_clr = tk.Button(btns, text="Clear All", bg="#9b1c1c", fg="#ffffff", font=popup_font("Arial", 14, "bold"), width=11, command=clear_all); _b_clr.pack(side="left", padx=4)
            tree.bind("<Delete>", lambda _e: delete_selected())
        if _b_reply is not None:
            self._attach_tooltip(_b_reply, self._tt("mw_send_reply"))
        self._attach_tooltip(_b_close, self._tt("mw_close"))
        if _b_del is not None:
            self._attach_tooltip(_b_del, self._tt("mw_delete_sel"))
        if _b_clr is not None:
            self._attach_tooltip(_b_clr, self._tt("mw_clear_all"))
        self._refresh_inbox_data()
        load_rows()
        # Opening the inbox marks this callsign's mail as read up to its newest
        # message and clears the (red) unread flag on the QSO popup's Inbox button.
        self.mark_inbox_seen(call)
        self.refresh_inbox_buttons()
