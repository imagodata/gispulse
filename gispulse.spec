# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for building the GISPulse backend sidecar binary.

This produces a single-file executable that can be bundled as a Tauri sidecar.

Build:
    pyinstaller gispulse.spec

Output:
    dist/gispulse-engine  (or gispulse-engine.exe on Windows)
"""

import sys
from pathlib import Path

block_cipher = None

# Collect all GISPulse Python packages
packages = [
    "gispulse",
    "core",
    "capabilities",
    "rules",
    "orchestration",
    "persistence",
    "adapters",
    "catalog",
]

# Hidden imports that PyInstaller may miss
hidden_imports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "duckdb",
    "pyogrio",
    "pyproj",
    "shapely",
    "shapely.geometry",
    "geopandas",
    "structlog",
    "typer",
    "fastapi",
    "starlette",
    "slowapi",
    "pydantic",
    "multipart",
    "multipart.multipart",
    "sqlalchemy",
    "geoalchemy2",
]

# Collect data files (built frontends)
datas = []
portal_dist = Path("portal/dist")
viewer_dist = Path("viewer/dist")

if portal_dist.exists():
    datas.append((str(portal_dist), "portal/dist"))
if viewer_dist.exists():
    datas.append((str(viewer_dist), "viewer/dist"))

a = Analysis(
    ["gispulse/cli.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "PIL",
        "notebook",
        "IPython",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "wx",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Target name matches Tauri sidecar convention: gispulse-engine-{target_triple}
target_name = "gispulse-engine"

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=target_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
