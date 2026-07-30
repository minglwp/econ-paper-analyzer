# -*- mode: python ; coding: utf-8 -*-

import os
import platform
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata


project_root = Path(SPECPATH)
version = os.environ.get("EPA_APP_VERSION", "0.1.0")
target_arch = os.environ.get("EPA_TARGET_ARCH", platform.machine())
icon_path = Path(os.environ.get("EPA_ICON_PATH", project_root / "assets" / "app-icon.png"))

semopy_data, semopy_binaries, semopy_hidden = collect_all(
    "semopy",
    filter_submodules=lambda name: ".tests" not in name and ".examples" not in name,
    exclude_datas=["**/tests/**", "**/examples/**"],
)
datas = [
    (str(project_root / "app" / "static"), "app/static"),
    (str(project_root / "app" / "templates"), "app/templates"),
    (str(project_root / "examples" / "demo_survey.csv"), "examples"),
] + semopy_data

for package in (
    "numpy",
    "pandas",
    "scipy",
    "statsmodels",
    "scikit-learn",
    "semopy",
    "matplotlib",
    "fastapi",
):
    try:
        datas += copy_metadata(package)
    except Exception:
        pass

hiddenimports = semopy_hidden + [
    "numdifftools",
    "openpyxl",
    "openpyxl.cell._writer",
    "patsy",
    "xlsxwriter",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.lifespan.on",
    "matplotlib.backends.backend_agg",
    "matplotlib.backends.backend_svg",
]

a = Analysis(
    [str(project_root / "desktop.py")],
    pathex=[str(project_root)],
    binaries=semopy_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "graphviz",
        "tkinter",
        "uvloop",
        "httptools",
        "websockets",
        "watchfiles",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="econ-paper-analyzer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=target_arch,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="econ-paper-analyzer",
)
app = BUNDLE(
    coll,
    name="Econ Paper Analyzer.app",
    icon=str(icon_path),
    bundle_identifier="com.econpaperanalyzer.desktop",
    info_plist={
        "CFBundleDisplayName": "经管论文数据自动处理器",
        "CFBundleName": "Econ Paper Analyzer",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "LSMinimumSystemVersion": "14.0",
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSHighResolutionCapable": True,
    },
)
