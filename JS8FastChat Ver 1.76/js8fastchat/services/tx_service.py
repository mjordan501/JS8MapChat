from __future__ import annotations

import threading
from typing import Callable, Optional

from .js8_api import JS8ApiClient


class TxService:
    """One TX chokepoint for main screen, FastChat, macros, and replies."""

    def __init__(self, api: JS8ApiClient, tx_armed_getter: Callable[[], bool], confirm_getter: Callable[[], bool], confirm_func: Callable[[str], bool], status_func: Callable[[str], None], ui_call: Callable[[Callable], None]):
        self.api = api
        self.tx_armed_getter = tx_armed_getter
        self.confirm_getter = confirm_getter
        self.confirm_func = confirm_func
        self.status_func = status_func
        self.ui_call = ui_call

    def transmit(self, frame: str, context: str = "TX", on_success: Optional[Callable[[], None]] = None) -> bool:
        frame = (frame or "").strip().upper()
        if not frame:
            self.status_func("No command to send.")
            return False
        if not self.tx_armed_getter():
            self.status_func(f"TX Armed is OFF — preview only: {frame}")
            return False
        if self.confirm_getter() and not self.confirm_func(frame):
            self.status_func("TX canceled by Confirm TX.")
            return False
        self.status_func(f"Sending {context}: {frame}")

        def worker():
            ok, msg = self.api.send_message(frame)

            def done():
                self.status_func(msg)
                if ok and callable(on_success):
                    on_success()

            self.ui_call(done)

        threading.Thread(target=worker, daemon=True, name="TxService.send").start()
        return True
