from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import tkinter as tk
from tkinter import messagebox

from ..constants import JS8_SPEED_CODES, JS8_SPEED_DURATIONS, TX_HISTORY_PATH
from ..services.intent_poller import IntentPoller
from ..services.live_rig_monitor import JS8LiveRigMonitor
from ..utils import base_call, debug, fmt_freq, read_json, write_json


class _RigIntentSpeedMixin:
    """Rig / intent / speed cluster (T5). Live JS8Call rig monitor, Set
    Freq/Offset/Speed control, the JS8Map->FastChat handoff intent, observed-TX
    logging, and TX-armed/HALT state. Self-bound; resolves against MainWindow
    through the MRO (no call-site rewiring)."""

    # API actions
    def start_live_rig_monitor(self) -> None:
        try:
            if self.live_rig_monitor:
                self.live_rig_monitor.stop()
        except Exception:
            pass
        self.live_rig_monitor = JS8LiveRigMonitor(lambda: self.host_var.get(), lambda: self.port_var.get(), self.on_live_rig_status_thread, self.on_live_rig_error_thread)
        self.live_rig_monitor.start()

    def start_intent_poller(self) -> None:
        """Phase 4: start polling for a JS8Map -> FastChat handoff intent file.

        The poller calls on_intent(callsign) on the Tk main thread whenever
        a fresh, previously-unseen intent is found. FastChat is read-only
        with respect to the intent file -- it never writes or deletes it.
        """
        try:
            if self._intent_poller:
                self._intent_poller.stop()
        except Exception:
            pass
        self._intent_poller = IntentPoller(
            self, on_intent=self.on_intent, on_raise=self._raise_main_window
        )
        self._intent_poller.start()

    def _raise_main_window(self) -> None:
        """Bring FastChat's MAIN console window to the front.

        Fired by the IntentPoller when JS8Map's app-switch button writes the
        raise signal. Uses the same reliable-on-Windows bring-to-front sequence
        as _raise_popup_to_front (deiconify + lift + brief topmost + focus),
        applied to the main window (self) rather than a popup Toplevel.
        """
        try:
            self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
            self.focus_force()
            self.after(400, lambda: self._safe_unset_topmost(self))
        except Exception:
            pass

    def on_intent(self, callsign: str) -> None:
        """Handle a fresh JS8Map handoff intent: open the FastChat popup
        pre-targeted to the given callsign.

        If the console is currently hidden (running in standalone popup mode
        via launch_standalone_popup), bring the popup to the front instead of
        opening a duplicate. Otherwise behave exactly like the operator
        selecting the callsign and opening FastChat from the main console.
        """
        if self._closing:
            return
        callsign = str(callsign or "").strip().upper()
        if not callsign:
            return
        self.set_status(f"JS8Map handoff received: opening FastChat for {callsign}")
        # If a FastChat popup for this callsign is already open, raise it.
        for child in self.winfo_children():
            if isinstance(child, tk.Toplevel) and child.title().startswith(f"FastChat - {callsign}"):
                try:
                    child.lift()
                    child.focus_force()
                except Exception:
                    pass
                return
        self.selected_call = callsign
        self.open_qso_popup()
        # Bring the newly-opened popup to the front. Without this, a handoff
        # from JS8Map can leave the FastChat popup behind other windows / only
        # flashing in the taskbar, so the operator has to hunt for it.
        for child in self.winfo_children():
            if isinstance(child, tk.Toplevel) and child.title().startswith(f"FastChat - {callsign}"):
                self._raise_popup_to_front(child)
                break

    def _raise_popup_to_front(self, top) -> None:
        """Force a Toplevel to the front and give it focus, reliably on Windows.

        lift() alone is often not enough when the window is opened from a
        background/taskbar state, so this also briefly sets topmost then
        releases it (a standard Tk trick) and pulls focus."""
        try:
            top.deiconify()
            top.lift()
            top.attributes("-topmost", True)
            top.focus_force()
            # Release topmost shortly after so it doesn't stay pinned above
            # everything else for the rest of the session.
            top.after(400, lambda: self._safe_unset_topmost(top))
        except Exception:
            pass

    def _safe_unset_topmost(self, top) -> None:
        try:
            if top.winfo_exists():
                top.attributes("-topmost", False)
        except Exception:
            pass

    def on_live_rig_status_thread(self, data: dict) -> None:
        self.ui_call(lambda d=data: self.apply_live_rig_status(d))

    def on_live_rig_error_thread(self, err: str) -> None:
        self.ui_call(lambda: self.apply_live_rig_connection_error(err))

    def apply_live_rig_connection_error(self, err: str) -> None:
        self.live_status_var.set("JS8: not connected")
        if "probe failed" not in self.status_var.get().lower():
            self.set_status(f"Live JS8 rig monitor not connected: {err}")

    def normalize_speed_value(self, speed) -> str:
        if speed is None:
            return ""
        raw = str(speed).strip().upper().replace("_", " ").replace("-", " ")
        try:
            if raw and raw.replace(".", "", 1).lstrip("-").isdigit():
                raw = str(int(float(raw)))
        except Exception:
            pass
        mapping = {"0":"Normal", "1":"Fast", "2":"JS8 40", "4":"Slow", "8":"JS8 60", "NORMAL":"Normal", "FAST":"Fast", "TURBO":"JS8 40", "JS8 40":"JS8 40", "JS840":"JS8 40", "JS8 60":"JS8 60", "JS860":"JS8 60", "ULTRA":"JS8 60", "SLOW":"Slow"}
        return mapping.get(raw, "")

    def _hold_rig_fields(self) -> bool:
        """True when the live rig monitor must NOT overwrite Set Freq / Set
        Offset: while the operator is editing one of those fields, or for a few
        seconds after they commit a change (so a poll can't stomp the typed
        value before it reaches JS8Call). Mirrors the speed field's guard.
        """
        if time.time() - float(getattr(self, "_last_rig_edit_ts", 0.0) or 0.0) < 4.0:
            return True
        try:
            w = self.focus_get()
        except Exception:
            w = None
        if w is None:
            return False
        if w in getattr(self, "_freq_combo_widgets", []):
            return True
        return bool(getattr(w, "_mj_rig_field", False))

    def apply_live_rig_status(self, data: dict) -> None:
        if data.get("tx_frame_raw") is not None:
            self.handle_observed_tx_frame(data["tx_frame_raw"])
            return
        if data.get("connected"):
            self.live_status_var.set("JS8: connected")
        freq = data.get("freq")
        offset = data.get("offset")
        speed = data.get("speed")
        hold = self._hold_rig_fields()
        if freq not in (None, "") and not hold:
            f = fmt_freq(freq)
            if f: self.freq_var.set(f)
        if offset not in (None, "") and not hold:
            try: self.offset_var.set(str(int(float(offset))))
            except Exception: self.offset_var.set(str(offset))
        self.apply_live_speed(speed)

    def apply_live_speed(self, speed) -> None:
        norm_speed = self.normalize_speed_value(speed)
        if norm_speed:
            if time.time() - self.last_manual_speed_change_ts > 4.0:
                self.speed_var.set(norm_speed)
        elif speed not in (None, ""):
            # Value arrived from JS8Call but did not match a known speed. Surface
            # it once (not on every poll) so it can be mapped exactly.
            if getattr(self, "_last_unmapped_speed", None) != str(speed):
                self._last_unmapped_speed = str(speed)
                self.set_status(f"JS8 speed value not recognized: {speed!r} — please report this exact value.")

    @staticmethod
    def _deep_find(obj, *names):
        """Defensive field lookup across an arbitrarily-nested API message.
        Used only to BEST-EFFORT log observed TX.FRAME content — never to
        drive TX/control logic. Unknown shapes degrade to a raw capture."""
        wanted = {str(n).upper().replace(" ", "_").replace("-", "_") for n in names}

        def norm(k):
            return str(k).upper().replace(" ", "_").replace("-", "_")

        def walk(x):
            if isinstance(x, dict):
                for k, v in x.items():
                    if norm(k) in wanted:
                        return v
                for v in x.values():
                    found = walk(v)
                    if found not in (None, ""):
                        return found
            elif isinstance(x, (list, tuple)):
                for v in x:
                    found = walk(v)
                    if found not in (None, ""):
                        return found
            return None

        return walk(obj)

    def handle_observed_tx_frame(self, msg: dict) -> None:
        """Optional: log a TX.FRAME broadcast seen from ANY API client (JS8Map,
        JS8Call itself, another FastChat instance) into our local outgoing TX
        history, so History shows traffic regardless of who actually sent it.

        Off by default (Settings > Log Observed Outgoing TX). Always captures
        the raw message to the debug log on first-seen shapes so the parser
        can be corrected from real data rather than guessed JS8Call internals.
        Never logs our own sends twice — record_outgoing_tx already covers
        FastChat-originated TX at the moment we send it.
        """
        with self.soft("handle_observed_tx_frame"):
            debug(f"TX.FRAME observed: {msg}")
            if not bool(getattr(self.cfg, "log_observed_tx", False)):
                return
            text = self._deep_find(msg, "TEXT", "VALUE") or str(msg.get("value", "") or "")
            text = str(text or "").strip().upper()
            if not text:
                return
            my_call = base_call(self.locator.callsign() or "")
            parts = text.split()
            from_call = base_call(self._deep_find(msg, "FROM", "CALL", "CALLSIGN") or (parts[0] if parts else ""))
            # If this looks like our own frame (no FROM field to distinguish
            # other than the leading token matching our callsign), skip it —
            # record_outgoing_tx already logged it at send time. This avoids
            # double entries for FastChat's own TX without assuming the wire
            # format includes an explicit FROM/CALL field on every build.
            if my_call and from_call == my_call:
                return
            to_call = base_call(parts[0]) if len(parts) > 1 else (base_call(self._deep_find(msg, "TO") or "") or "")
            row = {
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                "from": from_call or "?",
                "to": to_call,
                "context": "Observed TX",
                "text": text,
            }
            data = read_json(TX_HISTORY_PATH)
            rows = data.get("rows", []) if isinstance(data, dict) else []
            if not isinstance(rows, list):
                rows = []
            # De-dupe defensively: JS8Call may rebroadcast the same frame to a
            # client more than once during a transmission window.
            if rows and rows[-1].get("text") == row["text"] and rows[-1].get("context") == "Observed TX":
                return
            rows.append(row)
            rows = rows[-1500:]
            write_json(TX_HISTORY_PATH, {"rows": rows})

    def probe_js8_async(self) -> None:
        self.live_status_var.set("JS8: checking")
        def worker():
            ok, info, err = self.api.probe()
            self.ui_call(lambda: self.apply_probe_result(ok, info, err))
        threading.Thread(target=worker, daemon=True, name="JS8Api.probe").start()

    def start_speed_sync(self) -> None:
        """Keep FastChat's speed in step with JS8Call using the reliable
        request/response path. The passive monitor poll does not deliver speed
        dependably on all JS8Call builds, so this drives it directly. Re-arms
        itself on a light cadence and never overlaps its own worker."""
        if getattr(self, "_closing", False):
            return
        if not getattr(self, "_speed_poll_busy", False):
            self._speed_poll_busy = True
            def worker():
                spd = None
                try:
                    spd = self.api.current_speed()
                except Exception:
                    spd = None
                finally:
                    self._speed_poll_busy = False
                if spd not in (None, ""):
                    self.ui_call(lambda s=spd: self.apply_live_speed(s))
            threading.Thread(target=worker, daemon=True, name="JS8Api.speed_poll").start()
        if getattr(self, "_closing", False):
            return
        try:
            self._speed_sync_after_id = self.after(3000, self.start_speed_sync)
        except Exception:
            pass

    def apply_probe_result(self, ok: bool, info: dict, err: str) -> None:
        if ok:
            freq = fmt_freq(info.get("freq", "")); offset = str(info.get("offset", "") or "").strip()
            if freq: self.freq_var.set(freq)
            if offset: self.offset_var.set(offset)
            self.apply_live_speed(info.get("speed"))
            self.live_status_var.set("JS8: connected")
            detail = "JS8Call API probe succeeded."
            if info.get("callsign"): detail += f" Station: {info.get('callsign')}."
            if info.get("grid"): detail += f" Grid: {info.get('grid')}."
            self.set_status(detail)
        else:
            self.live_status_var.set("JS8: not connected"); self.set_status(f"JS8Call API probe failed: {err}")

    def on_rig_field_commit(self, _event=None) -> None:
        freq = self.freq_var.get().strip(); offset = self.offset_var.get().strip()
        self.clear_frequency_field_selection()
        if not freq:
            self.set_status("Set Freq is blank; nothing sent to JS8Call."); return
        key = f"{freq}|{offset}"
        now = time.time()
        if key == getattr(self, "_last_rig_commit_key", "") and now - float(getattr(self, "_last_rig_commit_ts", 0.0) or 0.0) < 0.6:
            return
        self._last_rig_commit_key = key
        self._last_rig_commit_ts = now
        self._last_rig_edit_ts = now
        self.set_status(f"Sending Set Freq {freq}{(' / offset ' + offset) if offset else ''}...")
        def worker():
            ok, msg = self.api.set_frequency(freq, offset)
            self.ui_call(lambda: self.after_rig_field_commit(ok, msg))
        threading.Thread(target=worker, daemon=True, name="JS8Api.set_frequency").start()

    def after_rig_field_commit(self, ok: bool, msg: str) -> None:
        self.clear_frequency_field_selection()
        if ok:
            self.set_status(msg + ". Refreshing live rig state..."); self.probe_js8_async()
        else:
            self.set_status("RIG.SET_FREQ failed: " + msg)

    def _deselect_combo(self, box) -> None:
        """Clear a readonly combobox's lingering selection highlight by dropping
        keyboard focus off it to the window."""
        try:
            box.selection_clear()
            box.winfo_toplevel().focus_set()
        except Exception:
            pass

    def on_speed_selected(self, box) -> None:
        self.apply_speed(box.get())
        self._deselect_combo(box)

    def apply_speed(self, selected_value=None) -> None:
        selected = str(selected_value if selected_value is not None else self.speed_var.get()).strip()
        normalized = self.normalize_speed_value(selected) or selected
        if normalized not in JS8_SPEED_CODES:
            self.set_status(f"Unknown speed: {selected}"); return
        self.last_manual_speed_change_ts = time.time(); self.speed_var.set(normalized)
        code = JS8_SPEED_CODES[normalized]; duration = JS8_SPEED_DURATIONS.get(normalized, "")
        def worker():
            ok, msg, _ = self.api.set_speed(code)
            self.ui_call(lambda: self.set_status((f"Sent JS8 speed {normalized} ({code}). Verify waterfall: {duration}." if ok else f"Speed change failed: {msg}")))
        threading.Thread(target=worker, daemon=True, name="JS8Api.set_speed").start()

    def halt_tx(self) -> None:
        if self.api_mode_var.get() != "Improved / 3.x":
            messagebox.showinfo("HALT TX", "API mode is Legacy 2.x Compatible. JS8FastChat will not send speculative halt commands.\n\nUse JS8Call's native Halt Tx button, or switch API Mode to Improved / 3.x if your JS8Call supports RIG.TX_HALT.", parent=self)
            return
        ok, msg = self.api.halt_tx()
        if ok:
            self.set_status(msg + " TX Armed state preserved.")
        else: self.set_status("RIG.TX_HALT failed: " + msg); messagebox.showwarning("HALT TX failed", msg, parent=self)

    def update_tx_armed_button(self) -> None:
        if not hasattr(self, "tx_armed_btn"): return
        if self.tx_armed_var.get(): self.tx_armed_btn.configure(text="TX Armed", bg=self.pal().green, fg="#000000", activebackground="#22c55e")
        else: self.tx_armed_btn.configure(text="TX Disarmed", bg="#facc15", fg="#0f172a", activebackground="#eab308")

    def toggle_tx_armed(self) -> None:
        self.tx_armed_var.set(not self.tx_armed_var.get()); self.update_tx_armed_button(); self.save_current_config()
        self.set_status("TX Armed ON — sends can transmit through JS8Call." if self.tx_armed_var.get() else "TX Armed OFF — preview only.")
