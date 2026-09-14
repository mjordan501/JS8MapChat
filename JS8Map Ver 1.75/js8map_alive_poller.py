"""
js8map_alive_poller.py  --  JS8Map side of the FastChat presence check
=======================================================================

Drop this file into JS8Map's source tree (same level as its constants /
config file).  Then wire it into the callsign popup as shown below.

This module is READ-ONLY with respect to the sentinel file.
JS8Map NEVER writes or deletes js8fastchat_alive.json.

------------------------------------------------------------------------------
HOW TO WIRE IT INTO THE CALLSIGN POPUP
------------------------------------------------------------------------------

In the JS8Map source file that builds the callsign popup:

STEP 1 -- import at the top
    from js8map_alive_poller import FastChatAlivePoller

STEP 2 -- create the poller once at app startup (e.g. in MainWindow.__init__)
    self._fc_poller = FastChatAlivePoller(
        on_change=self._on_fastchat_presence_change
    )
    self._fc_poller.start(self)   # pass the Tk root (or any widget) for .after()

STEP 3 -- add the callback that drives the button state
    def _on_fastchat_presence_change(self, is_alive: bool):
        # Called on the Tk main thread whenever FastChat comes up or goes away.
        # Store the state so the popup can read it when it is built.
        self._fastchat_alive = is_alive

STEP 4 -- in the callsign popup builder, after you create the FastChat button:
    # Gray out if FastChat is not running
    fc_alive = getattr(self, "_fastchat_alive", False)
    btn_fastchat.config(
        state="normal" if fc_alive else "disabled",
    )
    # Optionally update tooltip / label text too:
    if not fc_alive:
        btn_fastchat.config(text="FastChat (not running)")

STEP 5 -- if the popup stays open while state can change, re-poll on open:
    # At the TOP of the popup's __init__ / build method:
    fc_alive = getattr(master_app, "_fastchat_alive", False)
    # ... then use fc_alive when creating the button (same as Step 4)

------------------------------------------------------------------------------
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# Tuning -- must match fastchat_heartbeat.py values
# ---------------------------------------------------------------------------
ALIVE_STALE_SECONDS = 15    # file older than this -> FastChat is dead
ALIVE_POLL_MS       = 3000  # how often JS8Map re-checks (milliseconds)
ALIVE_FILENAME      = "js8fastchat_alive.json"


class FastChatAlivePoller:
    """
    Polls js8fastchat_alive.json in the shared folder every ALIVE_POLL_MS ms
    using Tk's .after() scheduler (no extra threads needed on the JS8Map side).

    Parameters
    ----------
    on_change : callable(bool)
        Called on the Tk main thread whenever the FastChat alive-state
        flips.  Receives True (FastChat just came up) or False (just went away).
    shared_dir : Path | None
        Path to the shared folder.  If None, resolved from JS8Map's own
        SHARED_DIR constant (mirrors FastChat's pattern).
    """

    def __init__(
        self,
        on_change: Callable[[bool], None],
        shared_dir: Path | None = None,
    ):
        if shared_dir is None:
            try:
                # JS8Map's equivalent of FastChat's constants.py
                from constants import SHARED_DIR  # type: ignore[import]
                self._path = Path(SHARED_DIR) / ALIVE_FILENAME
            except ImportError:
                self._path = Path(__file__).resolve().parent / ALIVE_FILENAME
        else:
            self._path = Path(shared_dir) / ALIVE_FILENAME

        self._on_change    = on_change
        self._last_state: bool | None = None   # None = not yet polled
        self._tk_widget    = None              # set by start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, tk_widget) -> None:
        """
        Begin polling.  tk_widget can be the Tk root or any live widget --
        we just need something to call .after() on.
        """
        self._tk_widget = tk_widget
        self._poll()   # immediate first check

    def is_alive(self) -> bool:
        """Synchronous check -- reads the file directly (no cache)."""
        return self._read_alive()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _poll(self) -> None:
        state = self._read_alive()
        if state != self._last_state:
            self._last_state = state
            try:
                self._on_change(state)
            except Exception as exc:  # noqa: BLE001
                print(f"[FastChatAlivePoller] on_change raised: {exc}")
        # Schedule next poll
        if self._tk_widget is not None:
            try:
                self._tk_widget.after(ALIVE_POLL_MS, self._poll)
            except Exception:
                pass  # widget destroyed -- stop polling silently

    def _read_alive(self) -> bool:
        """Return True if the sentinel file exists and its timestamp is fresh."""
        try:
            text = self._path.read_text(encoding="utf-8")
            data = json.loads(text)
            age  = time.time() - float(data["ts"])
            return 0 <= age <= ALIVE_STALE_SECONDS
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Standalone smoke-test  (python js8map_alive_poller.py)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import tkinter as tk

    root = tk.Tk()
    root.title("FastChat Alive Poller -- smoke test")

    label = tk.Label(root, text="Checking...", font=("Courier", 14), width=30)
    label.pack(padx=20, pady=20)

    shared = Path(__file__).resolve().parent

    def on_change(alive: bool):
        label.config(
            text="FastChat: RUNNING ✓" if alive else "FastChat: not running",
            fg="green" if alive else "gray",
        )
        print(f"[smoke-test] FastChat alive -> {alive}")

    poller = FastChatAlivePoller(on_change=on_change, shared_dir=shared)
    poller.start(root)

    root.mainloop()
