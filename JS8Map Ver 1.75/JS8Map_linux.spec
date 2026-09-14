# -*- mode: python ; coding: utf-8 -*-
# JS8MapChat 1.75 - PyInstaller Spec File, LINUX
# Mirrors JS8Map.spec (Windows) with three deliberate differences:
#   1. No icon= argument. PyInstaller ignores icons on Linux; passing one warns.
#   2. upx=False. UPX is usually not installed on Linux and is not needed.
#   3. Windows-only modules excluded so the build cannot pick them up.
#
# The six offline-map files are NOT listed here on purpose. They are installed
# BESIDE the executable, not packed inside it. build_js8map_linux.sh copies them
# into dist/ after the build, the same way Build_JS8Map.bat does on Windows.
#
# ---------------------------------------------------------------------------
# 2026-08-29: THE TEXT-DRAWING LIBRARIES ARE DELIBERATELY NOT BUNDLED.
#
# PyInstaller packs whatever shared libraries it finds on the BUILD machine.
# When the build machine is older than the machine that runs the program, the
# bundled text-drawing stack is older than the fonts it is asked to draw --
# and the version in Ubuntu 20.04 crashes outright on a colour emoji font.
# JS8Map draws emoji on its buttons, so it never reaches its own window:
#
#     X Error of failed request:  BadLength
#     Major opcode of failed request:  139 (RENDER)
#     Minor opcode of failed request:  20 (RenderAddGlyphs)
#
# It fails on any desktop that has a colour emoji font installed, and works on
# one that does not -- which is why it passed in a bare container and failed on
# the developer's own machine, from the SAME package file.
#
# Leaving these out means the program uses the copies already on the operator's
# machine, which are as new as their desktop is. Every Linux desktop has them;
# nothing with a window can run without them. This is the standard way to build
# a portable Linux binary on an older system and it is NOT a workaround.
#
# DO NOT "fix" this by adding them back. That reintroduces the crash for every
# operator whose desktop has emoji fonts, which is most of them.
# ---------------------------------------------------------------------------

import os

a = Analysis(
    ['JS8Map.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('zip3_latlon.py',    '.'),
        ('index.html',        '.'),
        ('app.js',            '.'),
        ('app.css',           '.'),
        ('JS8Map_icon.png',   '.'),
        ('fastchat_icon.png', '.'),
    ],
    hiddenimports=[
        'sqlite3',
        'csv',
        'json',
        'queue',
        'threading',
        'urllib.request',
        'zipfile',
        'tempfile',
        'tkinter',
        'tkinter.ttk',
        'tkinter.messagebox',
        'tkinter.filedialog',
        'http.server',
        'webbrowser',
        'socket',
        'socketserver',
    ],
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

# Printed into the build log so the exclusion is visible on every build
# rather than being an invisible property of the spec file.
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
    name='JS8Map',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
