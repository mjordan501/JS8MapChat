"""
tx_indicator.py  --  Phase 1 of "Big Task": scrolling sine-wave TX indicator.

A small Tkinter widget that shows a horizontally scrolling sine wave while a
transmission is active and sits as a flat, dimmed line when idle. Designed to
live in the top-right, under the toolbar, to the right of the callsign data.

It is driven entirely by TxLock state: register it with
    lock.add_listener(indicator.on_lock)
and it animates whenever the lock is engaged. (Phase 1 drives it off the lock's
'active' state; the bounded RIG.PTT / TX.GET_QUEUE_DEPTH poll later refines this
to true RF state without changing this widget.)

No external dependencies -- pure stdlib Tkinter.

USAGE
-----
    from tx_indicator import TxIndicator
    indicator = TxIndicator(parent_frame, width=130, height=22)
    indicator.grid(row=0, column=<right of callsign>, sticky="e")
    lock.add_listener(indicator.on_lock)
"""

from __future__ import annotations

import math
import tkinter as tk

__version__ = "1.75"

# Visual defaults -- tweak to taste / match the toolbar palette.
_ACTIVE_COLOR = "#ff2d2d"     # transmit red
_IDLE_COLOR = "#4a4a4a"       # dimmed flat line when not transmitting
_BG = None                    # None => inherit parent background
_FRAME_MS = 33                # ~30 fps
_AMPLITUDE_FRAC = 0.42        # wave height as fraction of canvas height (taller)
_CYCLES = 4.5                 # visible sine cycles across the width (more peaks)
_SCROLL_SPEED = 0.5           # phase advance per frame (higher = faster scroll)
_LINE_WIDTH = 3               # bolder stroke
_SAMPLE_STEP = 2              # px between sampled points (smaller = smoother)


class TxIndicator(tk.Frame):
    def __init__(self, parent, width: int = 130, height: int = 22,
                 active_color: str = _ACTIVE_COLOR, idle_color: str = _IDLE_COLOR,
                 bg=_BG, hide_when_idle: bool = False, **kw):
        bg = bg if bg is not None else parent.cget("background")
        super().__init__(parent, **kw)
        self._width = width
        self._height = height
        self._active_color = active_color
        self._idle_color = idle_color
        self._hide_when_idle = hide_when_idle

        self.canvas = tk.Canvas(self, width=width, height=height,
                                highlightthickness=0, bd=0, bg=bg)
        self.canvas.pack(fill="both", expand=True)

        self._mid = height / 2.0
        self._amp = height * _AMPLITUDE_FRAC
        self._phase = 0.0
        self._active = False
        self._anim_token = None

        # single reusable line item
        self._line = self.canvas.create_line(0, self._mid, width, self._mid,
                                              fill=idle_color, width=_LINE_WIDTH,
                                              smooth=True)
        self._draw_idle()

    # ---- public: lock listener hook -------------------------------------
    def on_lock(self, locked: bool, reason: str = "") -> None:
        """Wire this to TxLock.add_listener."""
        self.set_active(locked)

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        if active:
            self._start_anim()
        else:
            self._stop_anim()

    # ---- animation -------------------------------------------------------
    def _start_anim(self) -> None:
        if self._hide_when_idle:
            self.canvas.itemconfigure(self._line, state="normal")
        self.canvas.itemconfigure(self._line, fill=self._active_color)
        if self._anim_token is None:
            self._tick()

    def _stop_anim(self) -> None:
        if self._anim_token is not None:
            try:
                self.after_cancel(self._anim_token)
            except Exception:
                pass
            self._anim_token = None
        self._draw_idle()

    def _tick(self) -> None:
        self._phase += _SCROLL_SPEED
        self._redraw_wave()
        self._anim_token = self.after(_FRAME_MS, self._tick)

    def _redraw_wave(self) -> None:
        pts = []
        k = (_CYCLES * 2.0 * math.pi) / max(1, self._width)
        x = 0
        while x <= self._width:
            y = self._mid - self._amp * math.sin(k * x + self._phase)
            pts.append(x)
            pts.append(y)
            x += _SAMPLE_STEP
        try:
            self.canvas.coords(self._line, *pts)
        except Exception:
            pass

    def _draw_idle(self) -> None:
        if self._hide_when_idle:
            self.canvas.itemconfigure(self._line, state="hidden")
            return
        try:
            self.canvas.coords(self._line, 0, self._mid, self._width, self._mid)
            self.canvas.itemconfigure(self._line, fill=self._idle_color,
                                      state="normal")
        except Exception:
            pass


if __name__ == "__main__":
    # standalone preview: toggles the wave on/off every 3s
    root = tk.Tk()
    root.title("tx_indicator preview")
    root.configure(bg="#1e1e1e")
    bar = tk.Frame(root, bg="#1e1e1e")
    bar.pack(fill="x", padx=10, pady=10)
    tk.Label(bar, text="KW3KW  EM85", fg="#ddd", bg="#1e1e1e",
             font=("Segoe UI", 10)).pack(side="left")
    ind = TxIndicator(bar, width=140, height=24, bg="#1e1e1e")
    ind.pack(side="right")

    state = {"on": False}

    def toggle():
        state["on"] = not state["on"]
        ind.set_active(state["on"])
        root.after(3000, toggle)

    root.after(800, toggle)
    root.mainloop()
