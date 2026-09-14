#!/usr/bin/env python3
"""Runtime paths and primary configuration for JS8Map."""

import json
import os
import sys

# Single source of truth for the displayed name + version, mirroring
# FastChat's constants.py pattern. Both apps show "JS8MapChat 1.75".
# Bump these two when the version changes; all window titles read from them.
APP_NAME = "JS8MapChat"
APP_VERSION = "1.75"
APP_TITLE = f"{APP_NAME} {APP_VERSION}"


def _resolve_app_dir() -> str:
    """Return the bundled asset directory in frozen mode or this module's directory."""
    if getattr(sys, 'frozen', False):
        return getattr(sys, '_MEIPASS', None) or os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _resolve_exe_dir(app_dir: str) -> str:
    """Return the stable executable directory used as a filesystem search anchor."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return app_dir


def _resolve_data_dir(app_dir: str) -> str:
    """Return the user-writable runtime-data directory."""
    if not getattr(sys, 'frozen', False):
        return app_dir

    if sys.platform.startswith('win'):
        base = (
            os.environ.get('LOCALAPPDATA')
            or os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
        )
    elif sys.platform == 'darwin':
        base = os.path.join(
            os.path.expanduser('~'), 'Library', 'Application Support'
        )
    else:
        base = (
            os.environ.get('XDG_DATA_HOME')
            or os.path.join(os.path.expanduser('~'), '.local', 'share')
        )

    data_dir = os.path.join(base, 'JS8Map')
    try:
        os.makedirs(data_dir, exist_ok=True)
    except Exception:
        return app_dir
    return data_dir


APP_DIR = _resolve_app_dir()
EXE_DIR = _resolve_exe_dir(APP_DIR)
DATA_DIR = _resolve_data_dir(APP_DIR)

CONFIG_PATH = os.path.join(DATA_DIR, 'ham_map_config.json')

DEFAULT_CONFIG = {
    'js8_host':      '127.0.0.1',
    'js8_port':      '2442',
    'fcc_db_path':   '',
    'callsign':      '',
    'my_grid':       '',
    'time_filter':   'Last 30 minutes',
    'out_path':      os.path.join(DATA_DIR, 'ham_map.html'),
    'refresh_interval': 60,   # default 1 min
    'watched_calls': [],
    'fit_exclusions': [],
    'my_groups':     '@HRMS, @PREPNET, @AMRRON, @MAGNET',  # relay/query audience (mirrors JS8Call)
    'js8call_ini_path': '',     # blank = auto-detect JS8Call.ini; set to override the path
    'tx_mode':       'manual',  # startup TX mode: 'shadow' | 'manual' | 'live'. Never auto-defaults to live.
    'tx_halt_enabled': False,   # True only if JS8Call supports RIG.TX_HALT (Improved API 3.0+). Default OFF (2.x safe).

    # ── Map backdrop ──────────────────────────────────────────────────
    # The map draws from bundled world borders, state/province lines and
    # place names — it needs no internet. Street-level tiles are OPTIONAL
    # extra detail, drawn UNDERNEATH the borders, and are only requested
    # when the operator has supplied their own free provider key.
    #
    # Blank key = no tile request is ever made. That is deliberate: CARTO
    # now stamp "API KEY REQUIRED" across unkeyed tiles, so requesting them
    # without a key would put that watermark on the operator's map. Drawing
    # nothing is cleaner than drawing something spoiled.
    #
    # The URLs are settings so a future provider change is a setting, not
    # a reinstall. '{key}' is substituted if present; otherwise the key is
    # appended as CARTO's documented 'key=' parameter.
    'map_tile_key':  '',
    'tile_url_light': 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',
    'tile_url_dark':  'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
    'tile_attribution': '\u00a9 OpenStreetMap \u00b7 \u00a9 CARTO',
    'show_place_labels': True,   # country / state / city names on the map
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_config(cfg: dict):
    """Write settings, MERGING over whatever is already on disk.

    This used to overwrite the file wholesale. Callers that build a fixed
    dict of the settings they own -- HamMapApp._save_config is one -- would
    then silently delete every setting they did not happen to list, which
    runs on close and on any fit-exclusion toggle. The map tile key is set
    from the wizard and from the settings window, neither of which is in
    that dict, so it vanished the next time the app saved anything.

    Merging means a caller can write the two keys it owns and leave the
    rest alone, and no future setting can be lost by an older caller.
    """
    try:
        merged = load_config()      # defaults + whatever is on disk now
        merged.update(cfg or {})
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(merged, f, indent=2)
    except Exception:
        pass
