# -*- mode: python ; coding: utf-8 -*-
# Автономная сборка IDPRO: python -m PyInstaller build.spec
# Результат: dist/IDPRO.exe (Windows) или dist/IDPRO (Linux/macOS).
# Python на целевом ПК НЕ требуется.

block_cipher = None

a = Analysis(
    ['run_app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('app/static', 'app/static'),   # веб-интерфейс (index.html, js/*)
        ('config', 'config'),           # базовые справочники (копируются в dist рядом с exe)
    ],
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.loops.auto',
        'uvicorn.loops.asyncio',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan.on',
        'python_multipart',             # FastAPI: парсинг form-data
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', 'tkinter', 'PyQt5', 'PySide2'],
    win_no_prefer_redirects=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='IDPRO',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # консоль с адресом сервера; False — полностью без окна
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='IDPRO',
)
