# -*- mode: python ; coding: utf-8 -*-
# JS8MapChat 1.75 - PyInstaller Spec File (exe name stays JS8Map.exe)
# Compatible with PyInstaller 5.x and newer (Python 3.12+)

a = Analysis(
    ['JS8Map.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('zip3_latlon.py',   '.'),
        ('index.html',       '.'),
        ('app.js',           '.'),
        ('app.css',          '.'),
        ('JS8Map_icon.ico',  '.'),
        ('JS8Map_icon.png',  '.'),
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
    name='JS8Map',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='JS8Map_icon.ico',
)
