from __future__ import annotations

import threading
from typing import Callable

from ..constants import GROUP_ALL
from ..data.activity_reader import ActivityReader
from ..data.db_locator import MapDbLocator
from ..models import UserAppConfig
from ..utils import debug_exc


class ActivityService:
    """Owns background DB discovery and activity reads."""

    def __init__(self, cfg: UserAppConfig, on_update: Callable[[dict], None], ui_call: Callable[[Callable], None], my_groups_getter: Callable[[], list] | None = None):
        self.cfg = cfg
        self.on_update = on_update
        self.ui_call = ui_call
        self.my_groups_getter = my_groups_getter
        self.locator = MapDbLocator(cfg)
        self.reader = ActivityReader(self.locator, cfg.msg_watch_words)
        self._refresh_lock = threading.Lock()
        self._last_groups: list = []

    def refresh_async(self, time_filter: str, group_filter: str, probe_groups: bool = True, limit: int = 350) -> None:
        def worker():
            if not self._refresh_lock.acquire(blocking=False):
                return
            try:
                self.locator.refresh()
                self.reader = ActivityReader(self.locator, self.cfg.msg_watch_words)
                # Group list source, in order of authority:
                #   1. JS8Call live via the API (MY_GROUPS) -- authoritative and
                #      path-independent; fetched only on a probe (manual) refresh
                #      and cached, so an idle auto-refresh never pays the socket
                #      round-trip and the list stays stable between probes.
                #   2. Fallback: the operator's CONFIGURED groups from the .ini /
                #      map config (NOT groups merely heard on air -- those made the
                #      list volatile and snapped the filter back to All Groups).
                if probe_groups:
                    api_groups = []
                    if self.my_groups_getter is not None:
                        try:
                            api_groups = list(self.my_groups_getter() or [])
                        except Exception:
                            api_groups = []
                    if api_groups:
                        merged = set(api_groups)
                        merged.add("@ALLCALL")
                        groups = [GROUP_ALL] + sorted(g for g in merged if g != GROUP_ALL)
                    else:
                        groups = self.locator.groups(include_db_groups=False)
                    self._last_groups = groups
                else:
                    groups = self._last_groups or self.locator.groups(include_db_groups=False)
                rows = self.reader.display_rows(time_filter, group_filter=group_filter, limit=limit)
                newest = self.reader.newest_db_time()
                payload = {"locator": self.locator, "reader": self.reader, "rows": rows, "groups": groups, "newest": newest, "error": ""}
            except Exception as e:
                debug_exc("ActivityService refresh failed")
                payload = {"locator": self.locator, "reader": self.reader, "rows": [], "groups": [], "newest": None, "error": f"{type(e).__name__}: {e}"}
            finally:
                self._refresh_lock.release()
            self.ui_call(lambda: self.on_update(payload))

        threading.Thread(target=worker, daemon=True, name="ActivityService.refresh").start()
