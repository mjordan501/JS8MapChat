"""
tx_lock.py  --  Phase 1 of "Big Task": process-global transmit lock.

WHAT THIS IS
------------
A single, process-wide lock that represents "a transmission is pending right
now." Every FastChat window (main window + every station popup) shares ONE of
these, so the instant any window commits a send, all transmit buttons in all
windows darken/disable together. This is the in-process half of Phase 1 and it
needs NO new socket, NO file, and NO JS8Map rebuild.

WHY INTENT-BASED (not PTT-based)
--------------------------------
tx_mode defaults to 'manual', so RIG.PTT fires AFTER the click (too late to stop
a double-fire) and may never fire at all. So we lock on INTENT -- the moment a
send commits -- and RELEASE on real completion (a pluggable probe) OR a TTL
safety net. The TTL guarantees the lock can never hang locked, which would be a
worse broken state than the bug we're fixing.

THREAD-SAFETY
-------------
Tkinter is single-threaded. All public methods are meant to be called from the
Tk main loop. The TTL uses the Tk event loop (after/after_cancel) so expiry also
runs on the main thread. If you release from a background poll thread, marshal
back with root.after(0, lock.release) -- do NOT call release() off-thread.

USAGE (minimal)
---------------
    import tkinter as tk
    from tx_lock import TxLock

    root = tk.Tk()
    LOCK = TxLock(after=root.after, after_cancel=root.after_cancel)

    LOCK.bind_button(send_btn)          # darkens/disables while locked
    LOCK.add_listener(indicator.on_lock)  # drive the sine wave

    # in your send handler, BEFORE sending:
    if LOCK.is_locked():
        return                          # refuse -- a TX is already pending
    LOCK.acquire(reason="TX", ttl_seconds=90)
    # ... send ...
    # release happens via your completion probe, or the TTL fires.
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk

__version__ = "1.75"

# Safety ceiling. The REAL release comes from the completion probe (Phase 1
# wiring); this is only the net that guarantees no permanent lockout. Generous
# is fine -- better to release a little late than mid-transmission. Tune to the
# slowest realistic multi-frame send (see handoff "open decisions: TTL").
DEFAULT_TTL_SECONDS = 90.0

# Fallback darken color if a widget's background can't be read/scaled.
_FALLBACK_DARK = "#3a3a3a"


def _darken(widget: tk.Widget, factor: float = 0.55) -> str:
    """Return a darkened version of the widget's current background color."""
    try:
        bg = widget.cget("background")
        r, g, b = widget.winfo_rgb(bg)  # 16-bit per channel
        r = int((r >> 8) * factor)
        g = int((g >> 8) * factor)
        b = int((b >> 8) * factor)
        return "#%02x%02x%02x" % (max(0, r), max(0, g), max(0, b))
    except Exception:
        return _FALLBACK_DARK


class TxLock:
    def __init__(self, after=None, after_cancel=None):
        """
        after / after_cancel: pass a Tk widget's .after and .after_cancel
        (e.g. root.after, root.after_cancel) so the TTL runs on the main loop.
        If omitted, the lock still works but has NO auto-expiry -- set them via
        set_scheduler() before relying on the TTL.
        """
        self._after = after
        self._after_cancel = after_cancel
        self._holds = {}              # source -> {"reason","started_at","ttl_token"}
        self._listeners = []          # callbacks: cb(locked: bool, reason: str)
        self._buttons = {}            # widget -> saved style for restore

    # ---- scheduler -------------------------------------------------------
    def set_scheduler(self, after, after_cancel) -> None:
        self._after = after
        self._after_cancel = after_cancel

    # ---- listeners -------------------------------------------------------
    def add_listener(self, cb) -> None:
        if cb not in self._listeners:
            self._listeners.append(cb)
        # sync the new listener to current state immediately
        self._safe_call(cb)

    def remove_listener(self, cb) -> None:
        if cb in self._listeners:
            self._listeners.remove(cb)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            self._safe_call(cb)

    def _safe_call(self, cb) -> None:
        try:
            cb(self.is_locked(), self.reason())
        except Exception:
            # one bad listener must never break the lock or other listeners
            pass

    # ---- core state ------------------------------------------------------
    def is_locked(self) -> bool:
        return bool(self._holds)

    def has(self, source: str) -> bool:
        return source in self._holds

    def reason(self) -> str:
        for h in self._holds.values():
            if h.get("reason"):
                return h["reason"]
        return ""

    def elapsed(self) -> float:
        if not self._holds:
            return 0.0
        now = time.monotonic()
        return max(now - h["started_at"] for h in self._holds.values())

    def acquire(self, reason: str = "TX", ttl_seconds: float = DEFAULT_TTL_SECONDS, source: str = "own") -> bool:
        """Engage a lock 'hold' for the given source. The lock is locked while
        ANY source is held. Returns True if this source was newly added, False
        if it was already held (a re-acquire just refreshes reason + TTL).

        Phase 1 callers use the default source 'own' and behave exactly as
        before (single hold). Phase 2 uses source 'remote' for the JS8Map lease.
        """
        newly = source not in self._holds
        if not newly:
            self._cancel_ttl(source)        # refresh: drop the old TTL first
        self._holds[source] = {
            "reason": reason,
            "started_at": time.monotonic(),
            "ttl_token": None,
        }
        self._arm_ttl(source, ttl_seconds)
        self._apply_buttons()
        self._notify()
        return newly

    def release(self, reason: str = "", source: str = "own") -> None:
        """Release one source's hold. Idempotent. The lock stays locked if any
        other source is still held."""
        if source not in self._holds:
            return
        self._cancel_ttl(source)
        del self._holds[source]
        self._apply_buttons()
        self._notify()

    # ---- TTL safety net (per source) -------------------------------------
    def _arm_ttl(self, source: str, ttl_seconds: float) -> None:
        if self._after is None or ttl_seconds is None or ttl_seconds <= 0:
            return
        ms = int(ttl_seconds * 1000)
        h = self._holds.get(source)
        if h is not None:
            h["ttl_token"] = self._after(ms, lambda: self._on_ttl(source))

    def _cancel_ttl(self, source: str) -> None:
        h = self._holds.get(source)
        tok = h.get("ttl_token") if h else None
        if tok is not None and self._after_cancel is not None:
            try:
                self._after_cancel(tok)
            except Exception:
                pass
        if h is not None:
            h["ttl_token"] = None

    def _on_ttl(self, source: str) -> None:
        # safety release for this source -- real release never came in time
        h = self._holds.get(source)
        if h is not None:
            h["ttl_token"] = None
        self.release(source=source)

    # ---- button binding (darken + disable) -------------------------------
    def bind_button(self, widget: tk.Widget) -> None:
        """
        Register a transmit button. While locked it is disabled and darkened;
        on release its original look/state is restored. Works for tk.Button and
        ttk widgets.
        """
        if widget in self._buttons:
            return
        self._buttons[widget] = self._capture_style(widget)
        # apply current state right away
        self._apply_one(widget)

    def bind_buttons(self, widgets) -> None:
        for w in widgets:
            self.bind_button(w)

    def unbind_button(self, widget: tk.Widget) -> None:
        if widget in self._buttons:
            self._restore_one(widget)
            del self._buttons[widget]

    def _capture_style(self, widget: tk.Widget) -> dict:
        saved = {"ttk": isinstance(widget, ttk.Widget)}
        if not saved["ttk"]:
            try:
                saved["bg"] = widget.cget("background")
            except Exception:
                saved["bg"] = None
            saved["dark"] = _darken(widget)
        return saved

    def _apply_buttons(self) -> None:
        for w in list(self._buttons.keys()):
            self._apply_one(w)

    def _apply_one(self, widget: tk.Widget) -> None:
        saved = self._buttons.get(widget)
        if saved is None:
            return
        try:
            if not widget.winfo_exists():
                # widget destroyed (popup closed) -- forget it
                del self._buttons[widget]
                return
        except Exception:
            return
        try:
            if saved["ttk"]:
                if self.is_locked():
                    widget.state(["disabled"])
                else:
                    widget.state(["!disabled"])
            else:
                if self.is_locked():
                    widget.configure(state="disabled")
                    if saved.get("dark"):
                        widget.configure(background=saved["dark"])
                else:
                    widget.configure(state="normal")
                    if saved.get("bg") is not None:
                        widget.configure(background=saved["bg"])
        except Exception:
            pass

    def _restore_one(self, widget: tk.Widget) -> None:
        saved = self._buttons.get(widget)
        if saved is None:
            return
        try:
            if saved["ttk"]:
                widget.state(["!disabled"])
            else:
                widget.configure(state="normal")
                if saved.get("bg") is not None:
                    widget.configure(background=saved["bg"])
        except Exception:
            pass


if __name__ == "__main__":
    # tiny smoke test
    root = tk.Tk()
    root.title("tx_lock smoke test")
    lock = TxLock(after=root.after, after_cancel=root.after_cancel)
    b = tk.Button(root, text="TX (bound)", width=20)
    b.pack(padx=20, pady=10)
    lock.bind_button(b)
    status = tk.Label(root, text="unlocked")
    status.pack(pady=4)
    lock.add_listener(lambda locked, why: status.config(
        text=("LOCKED (%s)" % why) if locked else "unlocked"))
    tk.Button(root, text="acquire 3s TTL",
              command=lambda: lock.acquire(ttl_seconds=3)).pack(pady=4)
    tk.Button(root, text="release now", command=lock.release).pack(pady=4)
    root.mainloop()
