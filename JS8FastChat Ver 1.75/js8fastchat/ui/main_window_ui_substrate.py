"""FastChat UI substrate extracted from MainWindow (T4).

The widget factory (themed label/button/entry/combo/text_box/soft), palette and
font helpers, the hover-tooltip subsystem (ToolTip + the central TOOLTIPS table),
and theme application (apply_theme and friends). Mixed into MainWindow via MRO, so
every method stays a method reachable as ``self.<name>`` / ``host.<name>``. This
module never imports main_window (no cycle).
"""
from __future__ import annotations

import sys
from contextlib import contextmanager

import tkinter as tk
from tkinter import ttk

from ..constants import (
    LIVE_RIG_GREEN,
    RX_HB_TILE_COLOR,
    UI_SCALE_PRESETS,
    WATCHED_TILE_COLOR,
)
from ..utils import debug_exc
from .theme import DARK, LIGHT, Palette

# Platform font compensation, applied ON TOP of the user's UI Scale preset.
# The 1.28 was a Windows-only correction hardcoded at two sites; on Linux it
# inflated every font ~28% and overflowed panels, and the UI Scale dropdown
# could not undo it (90% x 1.28 = 1.152, still oversized).
#
# RULED OUT BY MEASUREMENT on the Linux box -- do not re-investigate: DPI is 96
# and `tk scaling` is 1.333, IDENTICAL to Windows; font substitution is
# metric-compatible (Arial -> Liberation Sans, same 19px height, 13px 'M').
# Neither was the cause. The literal was.
#
# 0.9 (not 1.0) is the operator's chosen value, confirmed BY EYE on the box.
# ONE constant, used at BOTH sites -- two copies of this test would be free to
# drift apart, which is the failure this codebase has been bitten by before.
# NOT related to constants.py FONT_BOOST, which is documented DEAD.
_PLATFORM_FONT_FACTOR = 1.28 if sys.platform.startswith("win") else 0.9

# Popup/dialog fonts were authored as FINAL sizes on Windows, i.e. already
# carrying the 1.28 above. They bypass scaled_font entirely, so on Linux they
# stayed at their Windows size while every scaled_font() surface around them
# shrank to 0.9/1.28 = 70%. That mismatch -- not the absolute size -- is what
# reads as "massive" on the box.
#
# Dividing by the Windows value re-bases those literals onto the running
# platform. On Windows this is EXACTLY 1.0, so every call returns the tuple
# unchanged: a no-op by construction, not by judgement.
#
# Derived from the ONE constant above rather than restating 0.9/0.703, so the
# two can never drift apart.
_WIN_FONT_FACTOR = 1.28
_POPUP_FONT_FACTOR = _PLATFORM_FONT_FACTOR / _WIN_FONT_FACTOR

# UI Scale for popups. popup_font is a MODULE-LEVEL helper with no access to
# the running window's ui_scale_var, so the main window mirrors the preset
# here whenever it is set or changed. Default 1.0 == "Normal 100%", so an
# app that never calls the setter behaves exactly as before.
#
# Deliberately multiplies the EXISTING popup literals rather than re-basing
# them onto scaled_font's grid -- that re-basing is what forced +/-1pt shifts
# the last time this was attempted, and was reverted.
_POPUP_UI_SCALE = 1.0


def set_popup_ui_scale(scale: float) -> None:
    """Mirror the UI Scale preset for popup_font. Called by the main window."""
    global _POPUP_UI_SCALE
    try:
        value = float(scale)
    except (TypeError, ValueError):
        value = 1.0
    _POPUP_UI_SCALE = value if value > 0 else 1.0


def popup_font(family: str, size: int, weight: str = "normal"):
    """Platform-compensate a hardcoded popup font tuple, then apply UI Scale.

    On Windows at "Normal 100%" both factors are exactly 1.0 and the tuple is
    returned unchanged -- a no-op by construction, not by judgement.
    """
    factor = _POPUP_FONT_FACTOR * _POPUP_UI_SCALE
    if factor == 1.0:
        return (family, size, weight)
    return (family, max(8, int(round(size * factor))), weight)


def popup_scale() -> float:
    """The exact factor popup_font applies to a size: platform x UI Scale.

    Exposed so a popup laid out in FIXED pixel coordinates can scale its own
    geometry by the same amount its text just grew by. Without this the text
    scales and the window does not, and the content runs off the edge.
    """
    return _POPUP_FONT_FACTOR * _POPUP_UI_SCALE


def popup_wrap(px: int) -> int:
    """Scale a hardcoded popup wraplength the way popup_font scales its text.

    A wraplength is a PIXEL width. Beside a popup_font label it was authored
    against Windows-final text, exactly like the font literals above, so it
    needs the same re-basing. Left hardcoded, the text grows with UI Scale
    while its permitted width does not, and the label wraps to more and more
    lines instead of getting wider -- which reads as 'the font did not scale'.

    Uses popup_scale(), NOT the main window's ui_scale_factor(). Popup literals
    carry the Windows platform factor; scaled_font literals do not. Matching the
    wrong one would over-scale on Windows and shrink on Linux.

    On Windows at 100% this returns px unchanged -- a no-op by construction, as
    with popup_font itself. On Linux it narrows, because popup text there is
    already rendered at about 70% and the width has been out of step with it
    since the port.
    """
    return max(1, int(round(px * popup_scale())))


class ToolTip:
    """Lightweight hover tooltip for any Tk widget.

    Shows a small borderless label ~0.5 s after the pointer enters the widget
    and hides it on leave, click, or when the widget is destroyed. One instance
    is created per widget via MainWindow._attach_tooltip. Deliberately dependency
    -free (pure Tk) and defensive: any Tk error while showing/hiding is swallowed
    so a tooltip can never take down the UI.
    """

    __slots__ = ("widget", "text", "delay", "_after_id", "_tip", "_bg", "_fg")

    def __init__(self, widget, text: str, delay_ms: int = 500,
                 bg: str = "#1b2430", fg: str = "#e8eaed"):
        self.widget = widget
        self.text = text
        self.delay = delay_ms
        self._after_id = None
        self._tip = None
        self._bg = bg
        self._fg = fg
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _schedule(self, _event=None):
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
        try:
            # Position just below-right of the pointer.
            x = self.widget.winfo_pointerx() + 12
            y = self.widget.winfo_pointery() + 18
            self._tip = tk.Toplevel(self.widget)
            self._tip.wm_overrideredirect(True)   # no title bar / border
            self._tip.wm_geometry(f"+{x}+{y}")
            try:
                self._tip.attributes("-topmost", True)
            except Exception:
                pass
            lbl = tk.Label(
                self._tip, text=self.text, justify="left",
                bg=self._bg, fg=self._fg, relief="solid", borderwidth=1,
                font=popup_font("Arial", 12), padx=7, pady=4, wraplength=popup_wrap(360),
            )
            lbl.pack()
        except Exception:
            self._tip = None

    def _hide(self, _event=None):
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None

    def update_text(self, text: str):
        """Change the tooltip text (used when a widget's meaning changes)."""
        self.text = text


class _UISubstrateMixin:
    """Widget factory, palette/font, tooltip subsystem, and theme application."""

    def pal(self) -> Palette:
        return DARK if self.theme_var.get() == "Dark" else LIGHT

    def rig_green(self) -> str:
        # Dark mode: unify ALL green text to the one bright palette green
        # (DARK.green = #33ff77). Light mode: keep the darker LIVE_RIG_GREEN so
        # it stays readable on the white entry background. Single source of
        # truth for the rig freq/offset green text across every window.
        return self.pal().green if self.theme_var.get() == "Dark" else LIVE_RIG_GREEN

    def scaled_font(self, size: int, weight: str = "normal", family: str = "Arial"):
        scale = UI_SCALE_PRESETS.get(self.ui_scale_var.get(), 1.0) * _PLATFORM_FONT_FACTOR
        return (family, max(8, int(round(size * scale))), weight)

    def ui_scale_factor(self) -> float:
        """The UI Scale preset ALONE -- no platform compensation.

        For scaling PIXEL layout (panel widths, pane minimums) authored at
        100%. Deliberately NOT scaled_font's combined factor: the platform
        correction is already baked into those literals on the platform they
        were measured on, so multiplying by it again would move a layout that
        is currently correct at 100%.
        """
        return UI_SCALE_PRESETS.get(self.ui_scale_var.get(), 1.0)

    def wrap(self, px: int) -> int:
        """Scale a hardcoded wraplength authored beside a scaled_font label.

        The text grows with UI Scale but a fixed pixel width does not, so the
        label wraps or clips instead of appearing larger. Seen at Large 115%
        on the callsign information line under the selected target.
        Logged as item 1 of LINUX_OPEN_ITEMS_0829.md.

        Uses ui_scale_factor() -- the UI Scale preset ALONE -- for the reason
        that method documents: these literals were authored at 100% on a
        platform whose font factor is already baked into them, so applying
        the platform factor again would move a layout that is correct today.
        """
        return max(1, int(round(px * self.ui_scale_factor())))

    def ui_call(self, func) -> None:
        if getattr(self, "_closing", False):
            return
        with self.soft("ui_call after", log=False):
            self.after(0, func)

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    @contextmanager
    def soft(self, label: str, *, log: bool = True):
        """Soft-fail UI helper with lifecycle-aware diagnostic logging.

        Use log=True for rare/actionable failures and log=False for expected
        shutdown/rebuild/Configure noise.  The central policy prevents clean
        exits and UI rebuilds from filling the debug log with false errors.
        """
        try:
            yield
        except Exception:
            if log and not getattr(self, "_closing", False) and not getattr(self, "_rebuilding", False):
                debug_exc(label)

    # widget helpers
    def label(self, parent, text: str = "", textvariable=None, size: int = 10, weight: str = "bold", bg=None, fg=None, **kwargs):
        p = self.pal()
        return tk.Label(parent, text=text, textvariable=textvariable, bg=p.panel if bg is None else bg, fg=p.text if fg is None else fg, font=self.scaled_font(size, weight), anchor="w", **kwargs)

    def button(self, parent, text: str, command=None, width: int = 10, danger: bool = False, good: bool = False, **kwargs):
        p = self.pal()
        bg = p.danger if danger else (p.green if good else p.button)
        fg = "#ffffff" if danger else ("#000000" if good else p.text)
        return tk.Button(parent, text=text, command=command, width=width, bg=bg, fg=fg, activebackground=p.button_active, activeforeground=fg, relief="raised", bd=1, font=self.scaled_font(12, "bold"), **kwargs)

    def entry(self, parent, textvariable=None, width: int = 10, **kwargs):
        p = self.pal()
        border = "#ffffff" if self.theme_var.get() == "Dark" else p.border
        return tk.Entry(
            parent,
            textvariable=textvariable,
            width=width,
            bg=p.entry_bg,
            fg=p.entry_fg,
            insertbackground=p.entry_fg,
            relief="solid",
            bd=1,
            highlightbackground=border,
            highlightcolor=border,
            highlightthickness=1,
            font=self.scaled_font(11, "bold", family="Consolas"),
            **kwargs,
        )

    def text_box(self, parent, **kwargs) -> tk.Text:
        """Create a Tk Text box with a visible theme border.

        Tk's native dark-mode relief can disappear into the #111827 panel.
        Use highlightborder as the reliable visible border, especially for
        Inbox detail/reply and FastChat typing boxes.
        """
        p = self.pal()
        border = "#ffffff" if self.theme_var.get() == "Dark" else p.border
        defaults = dict(
            bg=p.entry_bg,
            fg=p.entry_fg,
            insertbackground=p.entry_fg,
            relief="solid",
            bd=1,
            highlightbackground=border,
            highlightcolor=border,
            highlightthickness=1,
            wrap="word",
        )
        defaults.update(kwargs)
        return tk.Text(parent, **defaults)

    def combo_font(self):
        return self.scaled_font(13, "bold")

    def _apply_combo_font_options(self) -> None:
        combo_font = self.combo_font()
        try:
            self.option_add("*TCombobox*Listbox.font", combo_font)
            self.option_add("*ComboboxPopdown*Listbox.font", combo_font)
        except Exception:
            pass
        try:
            style = ttk.Style(self)
            style.configure("MJ.TCombobox", font=combo_font, padding=(5, 3, 5, 3), arrowsize=max(18, combo_font[1] + 4))
            style.configure("Rig.TCombobox", font=combo_font, padding=(5, 3, 5, 3), arrowsize=max(18, combo_font[1] + 4))
        except Exception:
            pass

    def combo(self, parent, textvariable, values, width: int = 14, state: str = "readonly"):
        self._apply_combo_font_options()
        cb = ttk.Combobox(parent, textvariable=textvariable, values=list(values), width=width, state=state, style="MJ.TCombobox")
        cb.configure(font=self.combo_font())
        return cb

    def _attach_tooltip(self, widget, text: str):
        """Attach a hover tooltip to a widget. Safe no-op on failure.

        Returns the ToolTip instance (or None) so callers can later change the
        text via .update_text(...). This method's existence also activates the
        guarded tooltip hook already present in the header (the JS8Map button),
        which checks for self._attach_tooltip before wiring its tip.
        """
        if not text:
            return None
        try:
            tip = ToolTip(widget, text)
            if not hasattr(self, "_tooltips"):
                self._tooltips = []
            self._tooltips.append(tip)   # keep a ref so it isn't GC'd
            return tip
        except Exception:
            return None

    def _tt(self, key: str) -> str:
        """Look up tooltip text by key from the central TOOLTIPS table."""
        return self.TOOLTIPS.get(key, "")

    # ── Central tooltip text table ──────────────────────────────────────────
    # All hover-tooltip strings for the main window live here, keyed by a short
    # identifier. Sourced from the operator's Tool Tips document. Keeping them
    # in one place makes wording edits a single-location change and lets the
    # FastChat popup reuse the command-button entries verbatim.
    TOOLTIPS = {
        # Top-right header buttons
        "hdr_js8map":     "Switch to the JS8Map screen (brings its window to the front)",
        "hdr_refresh":    "Forced refresh to update the screen",
        "hdr_halt_tx":    "Stop transmission (JS8Call Improved 3.x versions only)",
        "hdr_tx_armed":   "Direct transmission within JS8Call",
        "hdr_confirm_tx": "Displays the command with an option to send in JS8Call",
        "hdr_freqline":   "Frequency line — shows when transmitting",
        # Top control row
        "row_set_freq":   "Frequency list from JS8Call",
        "row_set_offset": "Type in an offset / shows the current offset",
        "row_api_mode":   "Old JS8Call version, or JS8Call Improved version",
        # JS8 command buttons (shared with the FastChat popup)
        "cmd_SNR?":      "What is my SNR? / Right-click to send yours",
        "cmd_GRID?":     "What is your grid locator? / Right-click to send yours",
        "cmd_INFO?":     "What is your station information? / Right-click to send yours",
        "cmd_STATUS?":   "What is your station status message? / Right-click to send yours",
        "cmd_HEARING?":  "What stations are you HEARING?",
        "cmd_MSG TO":    "Relay: ask this station to hold a message for a THIRD callsign, to hand over when that station shows up (CALL MSG TO:OTHERCALL ...). Transmits.",
        "cmd_INB-MSG":   "Send a message to THEIR station to hold in their inbox (transmits over the air). To hold a message at YOUR station instead, use STORE MSG.",
        "cmd_STORE MSG": "Hold a message at YOUR station for this callsign until they retrieve it with QUERY MSGS. Stored locally -- nothing is transmitted. Turns green with a count while messages wait; right-click to see and cancel them. (Their station holding it for you is INB-MSG.)",
        "cmd_QRY MSGS":  "Ask this station to deliver any messages it is holding for you. Transmits.",
        "cmd_QRY CALL":  "Can you communicate directly with CALLSIGN?",
        "cmd_Send MSG ID": "Please deliver the complete message identified by ID",
        "cmd_HB":        "Send a JS8Call heartbeat",
        "cmd_CQ":        "Transmit your JS8Call CQ directly",
        # Incoming Activity filter tiles
        "tile_incoming": "Total JS8Call callsigns tracked",
        "tile_heard":    "Stations I can hear",
        "tile_hearing_me": "Stations that hear me but I don't hear them",
        "tile_both":     "Stations where we hear each other",
        "tile_hb":       "Stations whose heartbeat (HB) I received",
        "tile_watched":  "Stations in JS8Call I've selected to be notified about when connected",
        "tile_watchmsg": "Keyword-list tagged word appeared in a message",
        "tile_total":    "Number of stations connected in JS8Call",
        "grp_all_filter": "Filter the list by group",
        # Right-panel target buttons
        "btn_fastchat":  "Launch the FastChat popup screen",
        "btn_info":      "FCC or Canadian database info",
        # Left panel
        "left_send_group": "Select a group to target",
        "left_target_call": "Selected callsign (highlights its row in Incoming Activity)",
        "left_type_call": "Type to search",
        "left_x":        "Close and remove the row highlight",
        "left_go":       "Select the callsign",
        # DB / activity row
        "db_group_activity": "Filter Incoming Activity to groups only",
        "db_word_search": "Filter all Incoming Activity by a word",
        "db_word_clear": "Clear the word search",
        "db_add_call":   "Add a callsign or group not showing in the Incoming Activity list",
        "db_clear_activity": "Clear the Incoming Activity display",
        "db_catch_up":   "Catch the database time window up to now",
        # FastChat popup — specific entries (command buttons reuse cmd_* above)
        "fc_refresh":    "Forced refresh to update this conversation",
        "fc_history":    "Past history of contacts with this callsign",
        "fc_inbox":      "Messages in your inbox from this callsign. The outline turns red with a count when new mail for you is waiting; opening it clears the flag.",
        "fc_info":       "FCC or Canadian database info",
        "fc_clear_sent": "Clears your transmissions to this callsign",
        "fc_other_traffic": "Traffic not directed to you but picked up",
        "fc_tx_armed":   "Direct transmission within JS8Call",
        "fc_confirm_tx": "Displays the command with an option to send in JS8Call",
        "fc_directed":   "Send a directed message to this callsign",
        "fc_store_msg":  "Store a message locally for this callsign, held at your station until they retrieve it with QUERY MSGS. Turns green with a count while messages are still waiting. Right-click to see and cancel the ones waiting.",
        "fc_query_msg":  "Please deliver any messages you have stored for me",
        "fc_query_call": "Can you communicate directly with CALLSIGN?",
        "fc_send":       "Send the typed message",
        "fc_halt":       "Stop transmission (JS8Call Improved 3.x versions only)",
        "fc_clear_msg":  "Clear the message text box",
        "fc_close":      "Close this FastChat popup",
        # -- Message window (Inbox / Outgoing) --
        "mw_view":       "Switch between mail sent TO you and everything from this callsign. (Messages you have stored FOR them are on the Store Msg button's right-click.)",
        "mw_view_outgoing": "This window is showing messages you have stored for this callsign that are still waiting to be picked up. Open the Inbox button to read incoming mail instead.",
        "mw_delete_sel": "Delete the highlighted message(s) from JS8Call's inbox. In the Outgoing view this cancels a stored message so they can no longer retrieve it. A backup of the database is saved first",
        "mw_clear_all":  "Delete every message currently listed. A backup of the database is saved first",
        "mw_send_reply": "Send the typed reply to this callsign through JS8Call",
        "mw_close":      "Close this message window",
        "mw_store_hint": "Right-click the Store Msg button to open this window on the Outgoing view",
    }

    # theme/render
    def apply_theme(self) -> None:
        p = self.pal()
        self.configure(bg=p.bg)
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        rowheight = max(18, int(16 * UI_SCALE_PRESETS.get(self.ui_scale_var.get(), 1.0) * _PLATFORM_FONT_FACTOR))
        style.configure("Treeview", background=p.tree_bg, foreground=p.tree_fg, fieldbackground=p.tree_bg, bordercolor=p.border, rowheight=rowheight, font=self.scaled_font(11, "normal"))
        style.configure("Treeview.Heading", background=p.tree_heading, foreground=p.text, relief="raised", font=self.scaled_font(11, "bold"))
        # Pin the heading's active/pressed states or clam falls back to a white
        # background when a column header is clicked to sort (the "box turns
        # white" regression).
        style.map("Treeview.Heading", background=[("active", p.tree_heading), ("pressed", p.tree_heading)], foreground=[("active", p.text), ("pressed", p.text)])
        selected_fg = "#000000"
        style.map("Treeview", background=[("selected", p.selected)], foreground=[("selected", selected_fg)])
        self._apply_combo_font_options()
        style.configure("MJ.TCombobox", fieldbackground=p.entry_bg, background=p.button, foreground=p.entry_fg, arrowcolor=p.text, font=self.combo_font())
        style.map("MJ.TCombobox", fieldbackground=[("readonly", p.entry_bg)], foreground=[("readonly", p.entry_fg)], background=[("readonly", p.button)])
        style.configure("Rig.TCombobox", fieldbackground=p.entry_bg, background=p.button, foreground=self.rig_green(), arrowcolor=p.text, font=self.combo_font())
        style.map("Rig.TCombobox", fieldbackground=[("readonly", p.entry_bg), ("!disabled", p.entry_bg)], foreground=[("readonly", self.rig_green()), ("!disabled", self.rig_green())], background=[("readonly", p.button), ("!disabled", p.button)])
        self.configure_tree_tags()
        self.force_plain_widget_palette()
        self.update_tile_button_styles()

    def force_plain_widget_palette(self) -> None:
        p = self.pal()
        protected_bgs = {p.header, p.danger, p.green, RX_HB_TILE_COLOR, WATCHED_TILE_COLOR, "#29b6f6", "#26a869", "#f5a623", "#ff7a00"}
        def walk(widget):
            try:
                cur_bg = str(widget.cget("bg"))
            except Exception:
                cur_bg = ""
            if isinstance(widget, (tk.Entry, tk.Text, tk.Listbox)):
                try:
                    border = "#ffffff" if self.theme_var.get() == "Dark" else p.border
                    fg = self.rig_green() if widget in (getattr(self, "freq_entry", None), getattr(self, "offset_entry", None)) else p.entry_fg
                    widget.configure(
                        bg=p.entry_bg,
                        fg=fg,
                        insertbackground=fg,
                        highlightbackground=border,
                        highlightcolor=border,
                        highlightthickness=1,
                    )
                except Exception:
                    pass
            elif isinstance(widget, tk.Button):
                if cur_bg not in protected_bgs:
                    try:
                        widget.configure(bg=p.button, fg=p.text, activebackground=p.button_active, activeforeground=p.text)
                    except Exception:
                        pass
            elif isinstance(widget, tk.Checkbutton):
                try:
                    widget.configure(bg=p.bg, fg=p.text, activebackground=p.bg, activeforeground=p.text, selectcolor=p.panel)
                except Exception:
                    pass
            elif isinstance(widget, (tk.Frame, tk.Label, tk.PanedWindow)):
                if cur_bg not in protected_bgs:
                    try:
                        widget.configure(bg=p.panel if widget is not self else p.bg)
                    except Exception:
                        pass
                if isinstance(widget, tk.Label) and cur_bg != p.header and cur_bg not in protected_bgs:
                    try:
                        widget.configure(fg=p.text)
                    except Exception:
                        pass
            for child in widget.winfo_children():
                walk(child)
        walk(self)
        self.update_tx_armed_button()

    def configure_tree_tags(self) -> None:
        if not hasattr(self, "activity_tree"):
            return
        p = self.pal()
        # Watched callsigns are intentionally gold/yellow-gold + bold.
        # Watch-word message hits are intentionally amber/orange + bold.
        # Unknown lookup calls stay red so they are not confused with watch hits.
        watched_fg = "#ffd700" if self.theme_var.get() == "Dark" else "#b8860b"
        unknown_fg = "#ff4040" if self.theme_var.get() == "Dark" else "#c00000"
        watchmsg_fg = "#ffb347" if self.theme_var.get() == "Dark" else "#c25f00"
        # Message-waiting red: intentionally the SAME alarm-red family as
        # unknown_fg (bright on dark theme, deep on light theme) so a stored
        # message is unmistakable at a glance, not just the tiny flag glyph in
        # the Flag cell. Bold, same as every other "you should look at this"
        # tag in this tree.
        message_fg = "#ff4040" if self.theme_var.get() == "Dark" else "#c00000"
        bold = self.scaled_font(11, "bold")
        normal = self.scaled_font(11, "normal")
        for tree in (self.activity_tree, self.calls_tree):
            tree.tag_configure("message", foreground=message_fg, font=bold)
            tree.tag_configure("mutual", foreground=p.tree_fg, font=normal)
            tree.tag_configure("hearing_me", foreground=p.tree_fg, font=normal)
            tree.tag_configure("watchmsg", foreground=watchmsg_fg, font=bold)
            tree.tag_configure("unknown_call", foreground=unknown_fg, font=bold)
            tree.tag_configure("watched", foreground=watched_fg, font=bold)
            tree.tag_configure("watched_activity", foreground=watched_fg, font=bold)
            tree.tag_configure("watched_active", foreground=watched_fg, font=bold)
            tree.tag_configure("selected_call", background=p.selected, foreground="#000000")
            tree.tag_configure("selected_watched", background=p.selected, foreground=watched_fg, font=bold)
            tree.tag_configure("selected_watched_activity", background=p.selected, foreground=watched_fg, font=bold)
            tree.tag_configure("selected_watched_active", background=p.selected, foreground=watched_fg, font=bold)
            try:
                tree.tag_raise("watched")
                tree.tag_raise("watched_activity")
                tree.tag_raise("watched_active")
                tree.tag_raise("selected_watched")
                tree.tag_raise("selected_watched_activity")
                tree.tag_raise("selected_watched_active")
                # Raised LAST (highest priority) so a pending message wins
                # visually over watched-gold on the same row. Recomputed
                # fresh every render_activity() call -- once the message is
                # picked up, this tag stops being applied and the row simply
                # falls back to watched-gold (or plain) on the next refresh.
                tree.tag_raise("message")
            except Exception:
                pass

    def update_tile_button_styles(self) -> None:
        if not hasattr(self, "tile_buttons"):
            return
        p = self.pal()
        active = self.active_tile_filter
        total_tile_color = "#d6d9df" if self.theme_var.get() == "Dark" else p.panel2
        colors = {"heard": "#29b6f6", "hearing_me": "#26a869", "both": "#f5a623", "hb": RX_HB_TILE_COLOR, "watched": WATCHED_TILE_COLOR, "watchmsg": "#ff7a00", "total": total_tile_color}
        try:
            if hasattr(self, "incoming_count_label"):
                self.incoming_count_label.configure(bg=p.green, fg="#000000")
        except Exception:
            pass
        for key, btn in self.tile_buttons.items():
            color = colors[key]
            try:
                tile_fg = "#000000" if key in ("hb", "watchmsg", "total") else "#ffffff"
                tile_font = self.scaled_font(8, "bold")
                tile_width = 8
                btn.configure(bg=color, fg=tile_fg, activebackground=color, activeforeground=tile_fg, font=tile_font, width=tile_width, relief="sunken" if key == active else "raised", bd=3 if key == active else 1, highlightthickness=2 if key == active else 0, highlightbackground="#ffffff" if self.theme_var.get() == "Dark" else "#000000")
            except Exception:
                pass
