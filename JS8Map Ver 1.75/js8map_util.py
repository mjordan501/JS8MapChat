"""Shared pure utilities — extracted verbatim from JS8Map.py.

Moved by tools/extract_symbols.py. No behavior change; location only.
This module imports nothing from the monolith (one-way dependency).
"""
from __future__ import annotations

from datetime import timezone as _dt_timezone
from datetime import datetime
import re


_DB_TS_FMT = '%Y-%m-%d %H:%M:%S UTC'

def _utc_now():
    """Timezone-aware UTC 'now' (for timedelta arithmetic)."""
    return datetime.now(_dt_timezone.utc)

def _utc_stamp():
    """UTC wall-clock as a stored DB timestamp string (with ' UTC' suffix)."""
    return _utc_now().strftime(_DB_TS_FMT)

def _utc_cutoff(delta):
    """A stored-format UTC timestamp `delta` in the past (window/purge cutoffs)."""
    return (_utc_now() - delta).strftime(_DB_TS_FMT)

def _utc_from_epoch_ms(ms):
    """JS8Call UTC ms-epoch -> stored DB timestamp string (true decode time, UTC)."""
    return datetime.fromtimestamp(ms / 1000, _dt_timezone.utc).strftime(_DB_TS_FMT)

def _age_seconds(stored_ts):
    """Seconds between now and a stored UTC timestamp string. Suffix-tolerant:
    reads the leading 19 chars so ' UTC' (or its absence) never breaks parsing."""
    dt = datetime.strptime(stored_ts[:19], '%Y-%m-%d %H:%M:%S').replace(
        tzinfo=_dt_timezone.utc)
    return (_utc_now() - dt).total_seconds()

GRID_RE      = re.compile(r'^[A-Ra-r]{2}[0-9]{2}([A-Xa-x]{2})?$')

def _base(call: str) -> str:
    """Strip /P /M /B suffix — W3BFO/P → W3BFO.
    Suffix callsigns are the same operator in portable/mobile operation.
    Used to share grids, FCC data, SNR proofs and watched status."""
    return call.split('/')[0] if '/' in call else call

def norm_call(v) -> str:
    """Normalise a raw callsign value to uppercase stripped string."""
    return str(v or '').upper().strip()

def norm_grid(v) -> str:
    """Normalise a raw grid value: uppercase, stripped, max 6 chars."""
    return str(v or '').upper().strip()[:6]

def status(cb, msg: str):
    """Fire status_cb(msg) if cb is set — avoids repetitive guard blocks."""
    if cb:
        cb(msg)

def is_valid_grid(g: str) -> bool:
    return bool(g and GRID_RE.match(g.strip()))

def is_precise_grid(g: str) -> bool:
    """True only for 6-digit grids (e.g. EM94WX). 4-digit grids (EM94) return
    False so we prefer FCC ZIP3 lookup (~50mi) over 4-digit JS8 grid (~150mi).
    Used to filter JS8Call CALL_ACTIVITY and spot grids."""
    g = (g or '').strip().upper()
    return bool(g and len(g) == 6 and GRID_RE.match(g))

def grid_to_latlon(grid: str):
    """Return (lat, lon) center for a 4- or 6-character Maidenhead locator."""
    grid = grid.strip().upper()
    if len(grid) < 4:
        return None
    try:
        if not (grid[0].isalpha() and grid[1].isalpha() and
                grid[2].isdigit() and grid[3].isdigit()):
            return None
        lon = (ord(grid[0]) - ord('A')) * 20.0 - 180.0
        lat = (ord(grid[1]) - ord('A')) * 10.0 - 90.0
        lon += int(grid[2]) * 2.0
        lat += int(grid[3]) * 1.0
        if len(grid) >= 6 and grid[4].isalpha() and grid[5].isalpha():
            lon += (ord(grid[4]) - ord('A')) * (2.0 / 24.0) + (1.0 / 24.0)
            lat += (ord(grid[5]) - ord('A')) * (1.0 / 24.0) + (1.0 / 48.0)
        else:
            lon += 1.0
            lat += 0.5
        return round(lat, 5), round(lon, 5)
    except Exception:
        return None
