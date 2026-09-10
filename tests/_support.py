"""Shared construction helpers for unittest modules."""

from __future__ import annotations

from pathlib import Path

from theme_scheduler.lifecycle import InstallLayout, PayloadManifest


def install_layout(root: Path) -> InstallLayout:
    return InstallLayout(
        (root / "Programs" / "ThemeScheduler").resolve(),
        (root / "ThemeScheduler").resolve(),
    )


def write_payload_tree(
    root: Path,
    *,
    main: bytes = b"main",
    uninstall: bytes = b"uninstall",
    runtime: bytes | None = None,
) -> None:
    app = root / "app"
    maintenance = root / "maintenance"
    app.mkdir(parents=True)
    maintenance.mkdir(parents=True)
    (app / "ThemeScheduler.exe").write_bytes(main)
    (maintenance / "Uninstall.exe").write_bytes(uninstall)
    if runtime is not None:
        internal = app / "_internal"
        internal.mkdir()
        (internal / "runtime.bin").write_bytes(runtime)


def capture_payload(
    root: Path,
    version: str,
    *,
    main: bytes = b"main",
    uninstall: bytes = b"uninstall",
    runtime: bytes | None = None,
) -> PayloadManifest:
    write_payload_tree(
        root,
        main=main,
        uninstall=uninstall,
        runtime=runtime,
    )
    return PayloadManifest.capture(root, version=version)
