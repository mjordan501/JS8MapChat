from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .constants import CALL_ALL, DEFAULT_MACROS, DEFAULT_MSG_WATCH_WORDS, GROUP_ALL
from .utils import base_call


@dataclass
class UserAppConfig:
    host: str = "127.0.0.1"
    port: int = 2442
    ui_scale: str = "Normal 100%"
    theme: str = "Dark"
    api_mode: str = "Legacy 2.x Compatible"
    confirm_tx: bool = False
    tx_armed: bool = True
    log_observed_tx: bool = False
    activity_self_refresh_minutes: int = 0
    # 30 minutes to match JS8Map's startup default (js8map_runtime.py
    # DEFAULT_CONFIG time_filter): both apps read the same spot DB, so opening
    # on different windows made them disagree on first sight.
    db_time: str = "Last 30 minutes"
    current_group: str = GROUP_ALL
    group_target: str = GROUP_ALL
    target_call_filter: str = CALL_ALL
    map_config_path: str = ""
    js8call_ini_path: str = ""
    spot_db_path: str = ""
    relay_db_path: str = ""
    fcc_db_path: str = ""
    canadian_db_path: str = ""
    macros: List[str] = field(default_factory=lambda: list(DEFAULT_MACROS))
    msg_watch_words: List[str] = field(default_factory=lambda: list(DEFAULT_MSG_WATCH_WORDS))
    # Session-only in practice: the UI no longer loads this at startup and
    # writes it back empty, so added ("Add Station") calls do NOT persist across
    # runs (JS8Call-style temporary). Field kept so existing configs still parse
    # and so any corrupted stored value gets cleared on the next save.
    manual_calls: List[str] = field(default_factory=list)


@dataclass
class ActivityRow:
    call: str
    to_call: str = ""
    group: str = ""
    snr: object = ""
    freq: object = ""
    offset: object = ""
    text: str = ""
    grid: str = ""
    timestamp: str = ""
    source: str = ""
    category: str = "heard"
    watched: bool = False
    lookup_unknown: bool = False
    watch_hit: str = ""
    inbox_count: int = 0
    relay_count: int = 0
    # Phase 8: number of on-air activity events this station generated inside the
    # active time window (each SNR/HB/Query/Msg/Form/Status?/Hearing?/Info?/relay
    # frame adds one; a second frame of the same kind still adds one). Populated
    # in ActivityReader.display_rows(); stays 0 for session-only "Add Station"
    # rows that never pass through the reader. Shown in the Incoming Activity
    # "Activity" column.
    activity_count: int = 0

    @property
    def base(self) -> str:
        return base_call(self.call)
