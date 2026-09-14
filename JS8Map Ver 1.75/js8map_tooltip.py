"""_ToolTip — extracted verbatim from JS8Map.py.

Moved by tools/extract_class.py. Behavior is byte-identical to the
original class; only its location changed.
"""
from __future__ import annotations

import tkinter as tk

# _FONT_SANS lives in the shared theme module. The original class relied on it
# as a JS8Map.py module-level global; when the class was extracted here the
# name did NOT come with it, so every _show() raised NameError AFTER creating
# the tip Toplevel -- orphaning a blank 200x200 window on every hover (the
# "white ghost"). Importing it here is the actual fix.
from js8map_theme import _FONT_SANS

class _ToolTip:
    """Lightweight hover tooltip for any Tk widget (JS8Map main screen).

    Shows a small borderless label ~0.5s after the pointer enters the widget
    and hides it on leave / click / destroy. Pure Tk, no dependencies, and
    defensive: any Tk error while showing or hiding is swallowed so a tooltip
    can never take down the UI. One instance per widget via
    HamMapApp._attach_tooltip.
    """

    __slots__ = ("widget", "text", "delay", "_after_id", "_tip")

    def __init__(self, widget, text, delay_ms=500):
        self.widget = widget
        self.text = text
        self.delay = delay_ms
        self._after_id = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        try:
            self._after_id = self.widget.after(self.delay, self._show)
        except Exception:
            self._after_id = None

    def _cancel(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        if self._tip is not None or not self.text:
            return
        tip = None
        try:
            x = self.widget.winfo_pointerx() + 12
            y = self.widget.winfo_pointery() + 18
            tip = tk.Toplevel(self.widget)
            tip.withdraw()                      # stay unmapped until fully built
            tip.wm_overrideredirect(True)
            try:
                tip.attributes("-topmost", True)
            except Exception:
                pass
            tk.Label(tip, text=self.text, justify="left",
                     bg="#1b2430", fg="#e8eaed", relief="solid", borderwidth=1,
                     font=(_FONT_SANS, 12), padx=7, pady=4, wraplength=360).pack()
            tip.wm_geometry("+%d+%d" % (x, y))
            tip.deiconify()                     # only now map it (built + placed)
            self._tip = tip
        except Exception:
            # Anything failed mid-build: destroy the half-built window so it can
            # NEVER be orphaned into a leaked blank Toplevel (the old bug).
            if tip is not None:
                try:
                    tip.destroy()
                except Exception:
                    pass
            self._tip = None

    def _hide(self, _e=None):
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None
