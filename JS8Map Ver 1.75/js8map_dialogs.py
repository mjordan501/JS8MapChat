#!/usr/bin/env python3
"""
js8map_dialogs.py  —  JS8Map shared themed dialogs.

Dark, theme-matched modal dialogs that replace the native tk.messagebox.*
popups so they match the rest of the app (and FastChat). Pure UI: depends
only on tkinter and the shared theme palette in js8map_theme.py. Nothing
here imports JS8Map, so any subsystem (FirstRunWizard, HamMapApp, …) can
pull these without a circular dependency.

Public API:
    themed_confirm(parent, title, message, ...) -> bool
    themed_message(parent, title, message, ...) -> None
    dismiss_toplevel(win, parent=None) -> None
Internal worker:
    _themed_dialog(...)  -> str (chosen button label)

Extracted verbatim from JS8Map.py 2026-07-29 (source L3409-L3525).
Bodies are byte-for-byte the originals; only imports were added.
"""

import tkinter as tk

from js8map_theme import (
    BLUE,
    CARD,
    SURFACE,
    TEXT,
    _FONT_SANS,
)


def dismiss_toplevel(win, parent=None, immediate=False):
    """Close a Tk Toplevel without a one-frame blank "ghost" on Windows.

    Destroying a mapped Toplevel tears down its child widgets before Windows'
    compositor removes the native window. That can expose an empty client area
    for one paint cycle. Unmap first, release any modal grab, THEN destroy.

    immediate=False (default): destroy on the next idle turn. Correct for a
        NON-modal window (e.g. the Group Monitor) whose caller does not block
        waiting for it -- the event loop keeps turning, so the deferred destroy
        fires promptly and the HWND disappears while already invisible.

    immediate=True: destroy right now, in this call. REQUIRED for a MODAL
        dialog that the caller is blocking on via win.wait_window(). With a
        modal dialog the event loop is parked inside wait_window(), so an
        after_idle destroy can be starved -- the window stays withdrawn-but-
        alive and Windows keeps compositing its last frame as a blank box (the
        "white ghost"). The window is already withdrawn here, so destroying
        immediately produces no teardown flash; it just removes the HWND at
        once and lets wait_window() return cleanly.
    """
    try:
        if not win.winfo_exists():
            return
    except Exception:
        return

    try:
        win.grab_release()
    except Exception:
        pass
    try:
        win.withdraw()
    except Exception:
        pass

    def _refocus():
        if parent is not None:
            try:
                parent.after_idle(parent.focus_set)
            except Exception:
                pass

    if immediate:
        # Modal path: destroy now so wait_window() returns and the HWND is gone
        # this instant (no deferral to starve). Already withdrawn -> no flash.
        try:
            win.destroy()
        except Exception:
            pass
        _refocus()
        return

    def _finish():
        try:
            if win.winfo_exists():
                win.destroy()
        except Exception:
            pass
        _refocus()

    try:
        (parent if parent is not None else win).after_idle(_finish)
    except Exception:
        _finish()


def _themed_dialog(parent, title, message, kind="info", buttons=("OK",),
                   default_index=0, safe_index=0):
    """A dark, theme-matched modal dialog for JS8Map (replaces the native
    tk.messagebox.* dialogs so they match the rest of the app + FastChat).

    kind: "info" | "warning" | "error" | "question" -- picks the accent + glyph.
    buttons: tuple of button labels, drawn right-to-left (last = rightmost).
    default_index: which button is highlighted / triggered by Enter.
    safe_index: which button Escape / window-close selects (the SAFE choice --
                e.g. "Cancel"/"No" on destructive or go-live actions).
    Returns the label (str) of the button chosen.
    """
    accent = {"info": BLUE, "question": BLUE, "warning": "#e0a500", "error": "#e0433a"}.get(kind, BLUE)
    glyph  = {"info": "i", "question": "?", "warning": "\u26a0", "error": "\u2715"}.get(kind, "i")

    win = tk.Toplevel(parent)
    win.withdraw()                      # stay hidden until positioned (no flash)
    win.title(title)
    win.configure(bg=SURFACE)
    win.resizable(False, False)
    try:
        win.transient(parent)
    except Exception:
        pass

    result = {"choice": buttons[safe_index] if 0 <= safe_index < len(buttons) else buttons[0]}

    # Header: glyph badge + title
    head = tk.Frame(win, bg=SURFACE)
    head.pack(fill="x", padx=16, pady=(14, 6))
    tk.Label(head, text=glyph, bg=accent, fg="#ffffff", font=(_FONT_SANS, 14, "bold"),
             width=3, height=1).pack(side="left", padx=(0, 10))
    tk.Label(head, text=title, bg=SURFACE, fg=TEXT, font=(_FONT_SANS, 13, "bold"),
             anchor="w", justify="left").pack(side="left", fill="x", expand=True)

    # Body message
    tk.Label(win, text=message, bg=SURFACE, fg=TEXT, font=(_FONT_SANS, 11),
             justify="left", anchor="w", wraplength=380).pack(fill="x", padx=16, pady=(2, 12))

    # Buttons row (right-aligned; last label = rightmost)
    btnrow = tk.Frame(win, bg=SURFACE)
    btnrow.pack(fill="x", padx=16, pady=(0, 14))

    def choose(lbl):
        result["choice"] = lbl
        # Modal dialog: caller is blocking in win.wait_window() below, so the
        # event loop is parked -- a deferred destroy would be starved and leave
        # a withdrawn-but-alive window that Windows composites as a blank ghost.
        # Destroy immediately (window is already withdrawn -> no flash).
        dismiss_toplevel(win, parent, immediate=True)

    for i, lbl in enumerate(buttons):
        is_default = (i == default_index)
        b = tk.Button(btnrow, text=lbl, command=lambda l=lbl: choose(l),
                      font=(_FONT_SANS, 10, "bold"),
                      bg=(accent if is_default else CARD),
                      fg=("#ffffff" if is_default else TEXT),
                      activebackground=(accent if is_default else "#243444"),
                      activeforeground="#ffffff",
                      relief="flat", bd=0, padx=16, pady=6, cursor="hand2")
        b.pack(side="right", padx=(6, 0))

    # Keyboard: Enter = default, Escape/close = safe choice
    def on_enter(_e=None):
        if 0 <= default_index < len(buttons):
            choose(buttons[default_index])
    def on_escape(_e=None):
        if 0 <= safe_index < len(buttons):
            choose(buttons[safe_index])
    win.bind("<Return>", on_enter)
    win.bind("<Escape>", on_escape)
    win.protocol("WM_DELETE_WINDOW", on_escape)

    # Center over parent BEFORE showing, so the window only ever paints once,
    # already in the right place (creating it withdrawn above prevents the
    # brief flash at Tk's default top-left position).
    try:
        win.update_idletasks()
        if parent is not None:
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
            w, h = win.winfo_width(), win.winfo_height()
            win.geometry(f"+{px + max(0, (pw - w)//2)}+{py + max(0, (ph - h)//3)}")
    except Exception:
        pass
    try:
        win.deiconify()                 # now show it, already positioned
    except Exception:
        pass
    try:
        win.grab_set()
        win.focus_set()
        win.wait_window()
    except Exception:
        pass
    return result["choice"]


def themed_confirm(parent, title, message, kind="question", yes="Yes", no="No", safe_no=True):
    """Themed yes/no confirm. Returns True if the user chose `yes`.
    safe_no=True makes `no` the default AND the Escape/close choice (fat-finger
    protection for destructive or go-live actions)."""
    buttons = (no, yes)  # no on left, yes on right
    default_index = 0 if safe_no else 1   # 0 == no
    return _themed_dialog(parent, title, message, kind=kind, buttons=buttons,
                          default_index=default_index, safe_index=0) == yes


def themed_message(parent, title, message, kind="info"):
    """Themed OK-only info/warning/error dialog."""
    _themed_dialog(parent, title, message, kind=kind, buttons=("OK",),
                   default_index=0, safe_index=0)
