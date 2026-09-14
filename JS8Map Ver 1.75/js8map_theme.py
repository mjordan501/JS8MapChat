#!/usr/bin/env python3
"""
js8map_theme.py  —  JS8Map shared theme: colours, fonts, and label sizing.

Single source of truth for the Tkinter GUI palette AND the values injected
into the browser map (index.html / boot.js). Pure data + platform font-family
detection; imports only the stdlib. Nothing here imports JS8Map, so this module
can be pulled by any subsystem (FirstRunWizard, HamMapApp, the boot.js builder)
without a circular dependency.

Extracted from JS8Map.py 2026-07-29. Values are byte-for-byte the originals.
"""

import sys as _sys


# ═══════════════════════════════════════════════════════════════════════
#  FEATURE COLORS — SINGLE SOURCE OF TRUTH
#  Change a color here and it updates everywhere (CSS, JS, and Python UI).
#    primary = dark-mode accent   ·   light = light-mode accent
#    bg      = chip/badge fill background
# ═══════════════════════════════════════════════════════════════════════
COLORS = {
    #              primary    light      bg (chip)  dot (map marker)
    'heard':      {'primary':'#29b6f6', 'light':'#0277bd', 'bg':'#0d3a5c', 'dot':'#40C4FF'},  # I HEAR    (blue)
    'hearing_me': {'primary':'#26a869', 'light':'#2e7d32', 'bg':'#0d3d22', 'dot':'#26a869'},  # HEAR ME   (green)
    'both':       {'primary':'#f5a623', 'light':'#e65100', 'bg':'#3d2a08', 'dot':'#f5a623'},  # MUTUAL    (orange)
    'watched':    {'primary':'#FFD700', 'light':'#f9a825', 'bg':'#3d3000', 'dot':'#FFD700'},  # WATCHED   (gold)
    'relay':      {'primary':'#29b6f6', 'light':'#0277bd', 'bg':'#0d3a52', 'dot':'#29b6f6'},  # RELAY     (cyan/blue)
    'hb':         {'primary':'#FFD700', 'light':'#f9a825', 'bg':'#3d3000', 'dot':'#FFD700'},  # HEARTBEAT (gold)
}


# ═══════════════════════════════════════════════════════════════════════
#  LABEL SIZE — zoom-responsive callsign box sizing (single source of truth)
#    Font scales linearly between min/max as you zoom. Padding + notif
#    badges scale proportionally so the whole box shrinks/grows together.
# ═══════════════════════════════════════════════════════════════════════
LABEL_SIZE = {
    'min_px':   8,    # font px when zoomed fully OUT (continental — declutter)
    'max_px':   18,   # font px when zoomed fully IN  (local — easy to read)
    'min_zoom': 4,    # zoom level where min_px applies
    'max_zoom': 12,   # zoom level where max_px applies
}


# ─────────────────────────────────────────────────────────────
#  Tkinter GUI colours & fonts
# ─────────────────────────────────────────────────────────────

BG      = '#080c10'
SURFACE = '#0e1520'
CARD    = '#131d2b'
BORDER  = '#1e2d40'
TEXT    = '#d0dce8'
MUTED   = '#b8d4ee'
BLUE    = COLORS['heard']['primary']        # I HEAR blue — from COLORS
RED     = '#e84060'
CALLSIGN= '#ff4422'  # matches JS8Call callsign banner orange-red
GREEN   = COLORS['hearing_me']['primary']   # HEAR ME green — from COLORS
BTN_BG  = '#1565c0'
GEN_BG  = '#1b5e20'

# ── Platform-detected font families ──────────────────────────
if _sys.platform.startswith('linux'):
    _FONT_SANS = 'Noto Sans'           # ships with Linux Mint / Cinnamon
    _FONT_MONO_NAME = 'Noto Sans Mono'
elif _sys.platform == 'darwin':
    _FONT_SANS = 'Helvetica Neue'
    _FONT_MONO_NAME = 'Menlo'
else:                                   # Windows
    _FONT_SANS = 'Segoe UI'
    _FONT_MONO_NAME = 'Consolas'

FONT_MONO = (_FONT_MONO_NAME, 12, 'bold')   # data/identifier fields (callsign, grid, host:port,
                                       # group names, etc.). Monospace on all platforms.
FONT_UI   = (_FONT_SANS, 11, 'bold')
