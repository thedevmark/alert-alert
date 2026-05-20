# -*- mode: python ; coding: utf-8 -*-
# Native (Qt Widgets) build of Alert! Alert! — no QtWebEngine/Chromium.
# Bundles only the Qt modules the native app needs (incl. Multimedia for the
# QMediaPlayer/QVideoWidget preview). The big WebEngine/QML/Quick stack is
# explicitly excluded, which is where the ~300 MB of Chromium lived.

a = Analysis(
    ['native_app.py'],
    pathex=[],
    binaries=[],
    datas=[('static/favicon.ico', 'static')],  # window/exe icon only
    hiddenimports=[
        'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets',
        'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets', 'PySide6.QtNetwork',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebEngineQuick', 'PySide6.QtWebChannel',
        'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuick3D',
        'PySide6.QtPdf', 'PySide6.QtPdfWidgets', 'PySide6.QtDesigner',
        'PySide6.Qt3DCore', 'PySide6.QtCharts', 'PySide6.QtDataVisualization',
    ],
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
    name='alert-alert',
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
    icon=['static\\favicon.ico'],
)
