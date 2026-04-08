# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_root = Path.cwd()
hook_dir = str(project_root / "hooks")

a = Analysis(
    ['poker_gui.py'],
    pathex=[],
    binaries=[],
    datas=[('poker.ico', '.'), ('ai_processor.py', '.')],
    hiddenimports=[
        'PIL._tkinter_finder',
        'matplotlib.backends.backend_tkagg',
        'win32gui',
        'win32con',
        'win32api',
        'pywintypes',
        'google.generativeai',
        'google.ai.generativelanguage',
        'google.api_core',
        'google.auth',
        'grpc',
        'proto',
        'openai',
        'openai._client',
        'openai.resources',
        'httpx',
        'ai_processor',
    ],
    hookspath=[hook_dir],
    hooksconfig={
        'matplotlib': {
            'backends': ['TkAgg'],
        },
    },
    runtime_hooks=[],
    excludes=[
        'PIL.AvifImagePlugin',
        'PIL.WebPImagePlugin',
        'PIL.FpxImagePlugin',
        'PIL.MicImagePlugin',
        'PIL.ImageCms',
        'matplotlib.tests',
        'numpy.tests',
        'numpy.f2py',
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='LeakSnipe',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['poker.ico'],
)
