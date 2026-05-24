# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['QytCroRec.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('hooks/dinput_hook/dinput_hook_x64.dll', 'hooks/dinput_hook'),
        ('hooks/dinput_hook/dinput_hook_x86.dll', 'hooks/dinput_hook'),
        ('hooks/dinput_hook/injector_x64.exe',    'hooks/dinput_hook'),
        ('hooks/dinput_hook/injector_x86.exe',    'hooks/dinput_hook'),
    ],
    hiddenimports=[],
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
