from __future__ import annotations

from dataclasses import fields
from typing import Any

from .constants import (API_MODES, CONFIG_PATH, LEGACY_CONFIG_PATH,
                        LEGACY_TIME_FILTER_ALIASES, TIME_FILTERS,
                        UI_SCALE_PRESETS)
from .models import UserAppConfig
from .utils import read_json, write_json


def _coerce_config_value(name: str, value: Any) -> Any:
    if name == "port":
        return int(value)
    if name == "activity_self_refresh_minutes":
        try:
            return max(0, int(value))
        except Exception:
            return 0
    if name in ("confirm_tx", "tx_armed", "log_observed_tx"):
        return bool(value)
    if name in ("macros", "msg_watch_words"):
        if isinstance(value, list):
            return [str(x).upper().strip() for x in value if str(x).strip()]
        return []
    return str(value)


def load_config() -> UserAppConfig:
    cfg = UserAppConfig()
    data = read_json(CONFIG_PATH)
    if not data:
        # Allow clean 3.2 to reuse the known working 3.1 config when the user
        # drops the new folder beside the old app files.
        data = read_json(LEGACY_CONFIG_PATH)
    names = {f.name for f in fields(cfg)}
    for key, value in data.items():
        if key not in names:
            continue
        try:
            setattr(cfg, key, _coerce_config_value(key, value))
        except Exception:
            pass
    if cfg.ui_scale not in UI_SCALE_PRESETS:
        cfg.ui_scale = "Normal 100%"
    if cfg.theme not in ("Light", "Dark"):
        cfg.theme = "Light"
    if cfg.api_mode not in API_MODES:
        cfg.api_mode = "Legacy 2.x Compatible"
    # Migrate retired labels BEFORE validating, or an old saved value is
    # treated as invalid and the operator loses their chosen window.
    cfg.db_time = LEGACY_TIME_FILTER_ALIASES.get(cfg.db_time, cfg.db_time)
    if cfg.db_time not in TIME_FILTERS:
        cfg.db_time = "Last 30 minutes"
    return cfg


def save_config(cfg: UserAppConfig) -> None:
    write_json(CONFIG_PATH, cfg.__dict__)
