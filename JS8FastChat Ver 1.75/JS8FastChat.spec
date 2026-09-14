# -*- mode: python ; coding: utf-8 -*-
# JS8FastChat — PyInstaller Spec File   (exe name: JS8FastChat.exe)
# JS8MapChat 1.75 — created 2026-07-14
# Modeled on JS8Map.spec, which is PROVEN working on this machine.
# Compatible with PyInstaller 5.x and newer (Python 3.12+)
#
# BUILD FROM THE APP ROOT:
#     cd /d "C:\Users\KW3KW\Documents\Claude AI\JS8MapChat\JS8FastChat Ver 1.75"
#     pyinstaller JS8FastChat.spec
#   → produces  dist\JS8FastChat.exe   (onefile, same as JS8Map.exe)
#
# ==============================================================================
# ⚠ console=True IS DELIBERATE ON THIS FIRST BUILD. READ BEFORE "FIXING" IT.
# ==============================================================================
# Production FastChat runs HIDDEN (Launch_JS8FastChat_*.vbs uses
# shell.Run cmd, 0, False). A hidden process that dies during startup gives you
# NOTHING: no window, no dialog, no traceback -- and no shared_resolve.log
# either, because it never got far enough to write one. That is how this project
# loses evenings.
#
# So: build VISIBLE first. If the exe crashes on launch you will SEE the Python
# traceback in the console window and know exactly what is missing.
#
# ONCE THE EXE IS PROVEN TO START AND THE CLICK->POPUP CHAIN WORKS:
#     change   console=True   to   console=False   and rebuild.
# That is the only change needed for the production build.
# ==============================================================================

import os
from PyInstaller.utils.hooks import collect_submodules

# ------------------------------------------------------------------------------
# datas -- BUNDLED READ-ONLY ASSETS ONLY. Never user data. (See ticket #26.)
# ------------------------------------------------------------------------------
# js8map_icon.png is read at runtime by ui/main_window.py:802 via
#     Path(__file__).resolve().parents[2] / "js8map_icon.png"
# Frozen, __file__ is <_MEIPASS>\js8fastchat\ui\main_window.py, so parents[2] is
# the _MEIPASS ROOT. Shipping the icon with target '.' therefore puts it exactly
# where that line already looks -- NO CODE CHANGE NEEDED. This is the one job a
# __file__-derived path is correct for: reading a bundled asset.
#
# If the icon is missing, main_window falls back to a gold TEXT button (lines
# 801-807). The app will NOT crash -- you will just see text instead of an icon.
#
# Only existing files are added, so a missing optional asset can never fail the
# build.
_candidate_datas = [
    ('js8map_icon.png', '.'),
    # Ship the app's own .ico as bundled data too (not just the exe icon set
    # via _icon below). main_window._init_window reads it at runtime through
    # parents[2] to set the WINDOW icon, so the running app's taskbar button
    # shows FC-8 instead of a default. Frozen, parents[2] is the _MEIPASS root,
    # so target '.' puts it exactly where that call looks. isfile-guarded below.
    ('JS8FastChat_icon.ico', '.'),
]
datas = [(src, dst) for (src, dst) in _candidate_datas if os.path.isfile(src)]

# ------------------------------------------------------------------------------
# exe icon -- NO CROSS-TREE FALLBACK. This is deliberate.
# ------------------------------------------------------------------------------
# An earlier version of this spec fell back to ..\JS8Map Ver 1.75\JS8Map_icon.ico
# if no FastChat icon was found. That was a MISTAKE and it shipped: the build
# succeeded and JS8FastChat.exe came out wearing JS8Map's icon.
#
# The #1 bug class in this project is "which .exe am I actually running?" Two
# exes with the SAME icon on the same desktop makes that question harder, not
# easier. A generic PyInstaller icon is obviously-different and therefore SAFER
# than a borrowed one that is actively misleading.
#
# TO GIVE FASTCHAT ITS OWN ICON: drop a real .ico named JS8FastChat_icon.ico into
# this folder (the app root, beside this spec) and rebuild. Nothing else to edit.
# Note js8map_icon.png in this folder is NOT it -- that is the map artwork used
# for the in-app "go to JS8Map" button.
_icon = 'JS8FastChat_icon.ico' if os.path.isfile('JS8FastChat_icon.ico') else None

# ------------------------------------------------------------------------------
# hiddenimports
# ------------------------------------------------------------------------------
# The entry point is a 136-byte shim:
#     from js8fastchat.app import main
# PyInstaller follows static imports fine, but this package has ~23 modules and
# any dynamically-imported one would be silently dropped -- producing an exe that
# dies at runtime, HIDDEN, with no error. collect_submodules() sweeps the whole
# package so that cannot happen.
hiddenimports = collect_submodules('js8fastchat') + [
    'sqlite3',
    'csv',
    'json',
    'configparser',      # js8call_ini.py reads JS8Call.ini
    'queue',
    'threading',
    'atexit',            # fastchat_heartbeat registers its stop() with atexit
    'datetime',
    'urllib.request',    # POST to JS8Map's /raise_window (port discovery)
    'socket',
    'zipfile',
    'tempfile',
    'webbrowser',
    'tkinter',
    'tkinter.ttk',
    'tkinter.font',
    'tkinter.messagebox',
    'tkinter.filedialog',
    'tkinter.scrolledtext',
]

a = Analysis(
    ['JS8FastChat.py'],
    pathex=[SPECPATH],           # so the js8fastchat package is importable
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='JS8FastChat',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,         # onefile, same as JS8Map.exe
    console=False,                # ← SEE THE BIG COMMENT AT THE TOP. Flip to False
                                 #   only AFTER the exe is proven to start.
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon,
)
