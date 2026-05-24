# -*- mode: python ; coding: utf-8 -*-
#
# QytCroRec build spec
# Run via:  build_setup.bat   (installs deps + Tesseract, then calls pyinstaller)
# Or:       pyinstaller QytCroRec.spec   (if deps already installed)

import os, sys, shutil, glob
from pathlib import Path

# ── Locate Tesseract-OCR installation ────────────────────────────────────────
_TESS_SEARCH = [
    r'C:\Program Files\Tesseract-OCR',
    r'C:\Program Files (x86)\Tesseract-OCR',
    r'C:\Tesseract-OCR',
    str(Path(SPECPATH) / 'vendor' / 'tesseract'),   # local vendor folder
]
# Also check PATH
_tess_on_path = shutil.which('tesseract')
if _tess_on_path:
    _TESS_SEARCH.insert(0, str(Path(_tess_on_path).parent))

_tess_dir = None
for _p in _TESS_SEARCH:
    if Path(_p, 'tesseract.exe').exists():
        _tess_dir = _p
        break

_tess_binaries = []
_tess_datas    = []

if _tess_dir:
    print(f"[spec] Bundling Tesseract from: {_tess_dir}")
    _tess_binaries.append((str(Path(_tess_dir, 'tesseract.exe')), '.'))
    # Bundle all DLLs tesseract.exe depends on
    for _dll in Path(_tess_dir).glob('*.dll'):
        _tess_binaries.append((str(_dll), '.'))
    # Bundle tessdata — only eng for smaller exe; add more if needed
    _tessdata = Path(_tess_dir, 'tessdata')
    if _tessdata.exists():
        # Include every *.traineddata present (user may have installed extras)
        for _td in _tessdata.glob('*.traineddata'):
            _tess_datas.append((str(_td), 'tessdata'))
        # Also include any config files tesseract needs
        for _cfg in _tessdata.glob('configs/*'):
            _tess_datas.append((str(_cfg), 'tessdata/configs'))
else:
    print("[spec] WARNING: Tesseract-OCR not found — pixel guard OCR will not work in the exe.")
    print("[spec]          Run  build_setup.bat  to install all dependencies first.")

# ── Hook DLL artifacts (optional — skip missing files gracefully) ─────────────
_hook_files = [
    ('hooks/dinput_hook/dinput_hook_x64.dll', 'hooks/dinput_hook'),
    ('hooks/dinput_hook/dinput_hook_x86.dll', 'hooks/dinput_hook'),
    ('hooks/dinput_hook/injector_x64.exe',    'hooks/dinput_hook'),
    ('hooks/dinput_hook/injector_x86.exe',    'hooks/dinput_hook'),
]
_hook_datas = [(src, dst) for src, dst in _hook_files if Path(src).exists()]

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    ['QytCroRec.py'],
    pathex=[],
    binaries=_tess_binaries,
    datas=_hook_datas + _tess_datas,
    hiddenimports=[
        # Pillow — PIL sub-modules PyInstaller may miss
        'PIL', 'PIL.Image', 'PIL.ImageGrab', 'PIL.ImageFilter',
        'PIL.ImageOps', 'PIL.ImageEnhance', 'PIL.BmpImagePlugin',
        'PIL.PngImagePlugin', 'PIL.JpegImagePlugin', 'PIL.TiffImagePlugin',
        # pytesseract
        'pytesseract', 'pytesseract.pytesseract',
        # pynput internals that may be missed on Windows
        'pynput.keyboard._win32', 'pynput.mouse._win32',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='QytCroRec',
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
)
