# -*- mode: python ; coding: utf-8 -*-

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
version_info = project_root / "packaging" / "version_info.txt"
uninstall_version_info = (
    project_root / "packaging" / "version_info_uninstall.txt"
)
icon_path = project_root / "assets" / "ThemeScheduler.ico"

shared_datas = [
    (str(ui_root), "ui"),
    (
        str(entrypoint_root / "theme_manager_bridge.ps1"),
        "entrypoints",
    ),
    (
        str(entrypoint_root / "task_scheduler_bridge.ps1"),
        "entrypoints",
    ),
    (
        str(entrypoint_root / "explorer_recovery_bridge.ps1"),
        "entrypoints",
    ),
    (
        str(entrypoint_root / "shortcut_bridge.ps1"),
        "entrypoints",
    ),
    (
        str(entrypoint_root / "notification_bridge.ps1"),
        "entrypoints",
    ),
]

uninstall_datas = [
    (str(ui_root), "ui"),
    (
        str(entrypoint_root / "theme_manager_bridge.ps1"),
        "entrypoints",
    ),
    (
        str(entrypoint_root / "task_scheduler_bridge.ps1"),
        "entrypoints",
    ),
    (
        str(entrypoint_root / "shortcut_bridge.ps1"),
        "entrypoints",
    ),
    (
        str(entrypoint_root / "uninstall_cleanup.ps1"),
        "entrypoints",
    ),
]

main_analysis = Analysis(
    [str(entrypoint_root / "themescheduler_main.py")],
    pathex=[str(source_root), str(project_root)],
    binaries=[],
    datas=shared_datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
main_pyz = PYZ(main_analysis.pure)
main_exe = EXE(
    main_pyz,
    main_analysis.scripts,
    [],
    exclude_binaries=True,
    name="ThemeScheduler",
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
main_collect = COLLECT(
    main_exe,
    main_analysis.binaries,
    main_analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ThemeScheduler",
)

uninstall_analysis = Analysis(
    [str(entrypoint_root / "uninstall_main.py")],
    pathex=[str(source_root), str(project_root)],
    binaries=[],
    datas=uninstall_datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
uninstall_pyz = PYZ(uninstall_analysis.pure)
uninstall_exe = EXE(
    uninstall_pyz,
    uninstall_analysis.scripts,
    uninstall_analysis.binaries,
    uninstall_analysis.datas,
    [],
    name="Uninstall",
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
    version=str(uninstall_version_info),
    icon=str(icon_path),
)
