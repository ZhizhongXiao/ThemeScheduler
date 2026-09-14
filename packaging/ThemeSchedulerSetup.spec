# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path


packaging_root = Path(SPECPATH).resolve()
if str(packaging_root) not in sys.path:
    sys.path.insert(0, str(packaging_root))

from reproducible_pyinstaller import enable_reproducible_base_library


enable_reproducible_base_library()


project_root = packaging_root.parent
source_root = project_root / "src"
entrypoint_root = project_root / "entrypoints"
ui_root = project_root / "ui"
version_info = project_root / "packaging" / "version_info_setup.txt"
icon_path = project_root / "assets" / "ThemeScheduler.ico"

bundle_value = os.environ.get("THEMESCHEDULER_SETUP_BUNDLE_ROOT")
if not bundle_value:
    raise ValueError(
        "THEMESCHEDULER_SETUP_BUNDLE_ROOT must identify the assembled "
        "release directory."
    )
bundle_root = Path(bundle_value).resolve(strict=True)
payload_root = bundle_root / "payload"
manifest_path = bundle_root / "payload-manifest.json"
if not payload_root.is_dir() or not manifest_path.is_file():
    raise ValueError("Setup bundle root has no payload and manifest.")

setup_datas = [
    (str(ui_root), "ui"),
    (str(payload_root), "payload"),
    (str(manifest_path), "."),
    (
        str(entrypoint_root / "task_scheduler_bridge.ps1"),
        "entrypoints",
    ),
    (
        str(entrypoint_root / "shortcut_bridge.ps1"),
        "entrypoints",
    ),
    (
        str(entrypoint_root / "theme_manager_bridge.ps1"),
        "entrypoints",
    ),
    (
        str(entrypoint_root / "current_appearance_bridge.ps1"),
        "entrypoints",
    ),
]

analysis = Analysis(
    [str(entrypoint_root / "setup_main.py")],
    pathex=[str(source_root), str(project_root)],
    binaries=[],
    datas=setup_datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="ThemeScheduler-Setup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(version_info),
    icon=str(icon_path),
)
