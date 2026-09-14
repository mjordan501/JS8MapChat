from __future__ import annotations

"""FastChatCore — UI-free service container for JS8FastChat.

Phase 1 of the JS8Map integration refactor.

This object owns the JS8Call API client, the activity/DB service (and its
reader + locator), and the TX service. Every point of UI interaction is
injected as a getter or callback, so the core can be constructed and driven
WITHOUT a Tk root. That is what lets the very same services back three front
ends with no duplicated logic:

    * the full console (MainWindow),
    * the standalone FastChat popup (Phase 2),
    * the thin map-launch host (Phase 3).

Behavior note: this is a behavior-preserving extraction. The services are
constructed here with exactly the same arguments MainWindow used before; the
window simply holds a FastChatCore and aliases the services back onto itself
(self.api, self.tx_service, self.reader, self.locator, self.activity_service),
so the rest of the existing UI code is unchanged.
"""

from typing import Callable, Optional

from .services.js8_api import JS8ApiClient
from .services.activity_service import ActivityService
from .services.tx_service import TxService


class FastChatCore:
    def __init__(
        self,
        cfg,
        *,
        host_getter: Callable[[], str],
        port_getter: Callable[[], object],
        tx_armed_getter: Callable[[], bool],
        confirm_getter: Callable[[], bool],
        confirm_func: Callable[[str], bool],
        status_func: Callable[[str], None],
        ui_call: Callable[[Callable], None],
        on_activity_update: Callable[[dict], None],
    ) -> None:
        self.cfg = cfg

        # JS8Call socket client (host/port resolved lazily via the getters).
        self.api = JS8ApiClient(host_getter, port_getter)

        # Activity/DB service owns the reader + locator internally.
        self.activity_service = ActivityService(cfg, on_activity_update, ui_call, my_groups_getter=self.api.get_my_groups)
        self.reader = self.activity_service.reader
        self.locator = self.activity_service.locator

        # Shared TX path — enforces TX Armed / Confirm TX. All UI hooks injected.
        self.tx_service = TxService(
            self.api,
            tx_armed_getter,
            confirm_getter,
            confirm_func,
            status_func,
            ui_call,
        )

    # -- thin, UI-free command surface (used by the popup/host in later phases) --

    def transmit(
        self,
        frame: str,
        context: str = "TX",
        on_success: Optional[Callable[[], None]] = None,
    ) -> bool:
        """Send a frame through the shared TX path.

        Honors TX Armed / Confirm TX exactly as the console does, because it
        delegates to the same TxService instance. Returns True if the frame was
        actually transmitted (vs. staged/declined).
        """
        return self.tx_service.transmit(frame, context=context, on_success=on_success)

    def halt(self):
        """Request RIG.TX_HALT through the API. Returns (ok, message).

        The decision of whether HALT is permitted in the current API mode, and
        any operator messaging, stays in the UI layer (MainWindow.halt_tx),
        which calls this only when appropriate.
        """
        return self.api.halt_tx()
