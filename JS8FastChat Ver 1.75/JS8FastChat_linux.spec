# -*- mode: python ; coding: utf-8 -*-
# JS8MapChat 1.75 - PyInstaller Spec File, LINUX
# JS8FastChat
#
# Mirrors JS8FastChat.spec (Windows) with four deliberate differences:
#   1. No icon= argument. PyInstaller ignores icons on Linux; passing one warns.
#      The window icon and the menu icon are handled by install.sh instead.
#   2. upx=False. UPX is usually not installed on Linux and is not needed.
#   3. Windows-only modules excluded so the build cannot pick them up.
#   4. The text-drawing libraries are filtered out - see the block below.
#
# ---------------------------------------------------------------------------
# THE TEXT-DRAWING LIBRARIES ARE DELIBERATELY NOT BUNDLED.
#
# Identical to the note in JS8Map_linux.spec. PyInstaller packs whatever shared
# libraries it finds on the BUILD machine. This program is built inside an
# Ubuntu 20.04 container so that it will start on older distributions, and
# 20.04's libXft crashes outright when asked to render a COLOUR EMOJI font.
# FastChat draws emoji on its buttons, so it would die before showing a window:
#
#     X Error of failed request:  BadLength
#     Major opcode of failed request:  139 (RENDER)
#     Minor opcode of failed request:  20 (RenderAddGlyphs)
#
# The symptom is badly misleading: the SAME file works on a machine with no
# emoji font installed and fails on one that has it.
#
# Leaving them out means the operator's own copies are used, which are as new
# as their desktop is. Every Linux desktop has them. DO NOT add them back.
# ---------------------------------------------------------------------------

import os
from PyInstaller.utils.hooks import collect_submodules

# ------------------------------------------------------------------------------
# datas -- BUNDLED READ-ONLY ASSETS ONLY. Never user data.
#
# js8map_icon.png is read at run time by ui/main_window.py through
#     Path(__file__).resolve().parents[2] / "js8map_icon.png"
# which in a frozen build resolves to the unpack root, so target '.' is correct.
# If it is missing the app falls back to a text button and does NOT crash.
#
# The .ico is shipped on Windows for the window icon. Tk on Linux cannot read
# .ico, so the .png is shipped in its place and the .ico is not included.
# ------------------------------------------------------------------------------
_candidate_datas = [
    ('js8map_icon.png',       '.'),
    ('js8fastchat_icon.png',  '.'),
]
datas = [(src, dst) for (src, dst) in _candidate_datas if os.path.isfile(src)]

# ------------------------------------------------------------------------------
# hiddenimports
#
# The entry point is a 6-line shim:  from js8fastchat.app import main
# PyInstaller follows static imports fine, but this package has around 23
# modules and any dynamically-imported one would be silently dropped, producing
# a program that dies at run time with no error. collect_submodules() sweeps the
# whole package so that cannot happen. Same reasoning as the Windows spec.
# ------------------------------------------------------------------------------
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
    excludes=[
        'win32api', 'win32con', 'win32gui', 'win32com',
        'pythoncom', 'pywintypes', 'wmi',
    ],
    noarchive=False,
)

# --- drop the text-drawing stack from the bundle ---------------------------
# See the note at the top of this file. Matched on the start of the file name,
# so version suffixes (.so.2, .so.6.17.4 and so on) are all covered.
_HOST_FONT_LIBS = (
    'libXft',            # the actual crash: colour emoji handling
    'libXrender',        # its partner; keep the pair consistent
    'libfontconfig',     # font discovery
    'libfreetype',       # glyph rasterising
    'libharfbuzz',       # text shaping
    'libgraphite2',      # shaping, pulled in by harfbuzz
    'libbrotlidec',      # font decompression, pulled in by freetype
    'libbrotlicommon',
    'libpng16',          # pulled in by freetype
)

_kept = []
_dropped = []
for _entry in a.binaries:
    _name = os.path.basename(_entry[0])
    if _name.startswith(_HOST_FONT_LIBS):
        _dropped.append(_name)
    else:
        _kept.append(_entry)
a.binaries = _kept

print('')
print('=' * 60)
print(' TEXT-DRAWING LIBRARIES LEFT OUT (the operator\'s own are used)')
print('=' * 60)
if _dropped:
    for _n in sorted(_dropped):
        print('   dropped: %s' % _n)
else:
    print('   NOTHING WAS DROPPED.')
    print('   If this build is made on an older system than it runs on,')
    print('   check this before trusting it -- see the note at the top.')
print('=' * 60)
print('')

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
    strip=True,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,         # onefile, same as the Windows build
    console=False,               # NOTE: PyInstaller ignores this on Linux.
                                 # Errors still reach the terminal either way,
                                 # so there is no first-build console trick to
                                 # perform here as there is on Windows.
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
