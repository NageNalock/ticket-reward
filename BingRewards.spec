# -*- mode: python ; coding: utf-8 -*-

import json
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


ROOT = Path.cwd()
BUILD_INFO = json.loads((ROOT / "build/build-info.json").read_text(encoding="utf-8"))

datas = [
    (str(ROOT / "config/config.yaml"), "config"),
    (str(ROOT / "config/search_terms.json"), "config"),
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "build/build-info.json"), "."),
]
datas += collect_data_files("playwright")
datas += collect_data_files("playwright_stealth")

hiddenimports = collect_submodules("playwright_stealth")
hiddenimports += [
    "AppKit",
    "Foundation",
    "objc",
    "playwright.sync_api",
]

a = Analysis(
    ["scripts/menu_bar_app.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Bing Rewards",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    target_arch="arm64",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Bing Rewards",
)

app = BUNDLE(
    coll,
    name="Bing Rewards.app",
    icon=str(ROOT / "assets/AppIcon.icns"),
    bundle_identifier="com.local.bingrewards",
    info_plist={
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "CFBundleShortVersionString": BUILD_INFO["version"],
        "CFBundleVersion": str(BUILD_INFO["build_number"] or 1),
    },
)
