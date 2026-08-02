# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['pdf_split_scale.py'],
    pathex=[],
    binaries=[],
    datas=[('c:\\Users\\admin\\Downloads\\EBSPLIT_VER2\\ebsplit.ico', '.')],
    hiddenimports=['ebsplit_gui', 'ebsplit_config'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'scipy', 'pandas', 'matplotlib', 'PIL', 'IPython', 'jupyter', 'notebook', 'sympy', 'sqlalchemy', 'tornado', 'zmq', 'pytest', 'numba', 'cv2'],
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
    name='EBSPLIT',
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
    icon=['c:\\Users\\admin\\Downloads\\EBSPLIT_VER2\\ebsplit.ico'],
)
