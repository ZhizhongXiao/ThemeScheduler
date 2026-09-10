"""Guarded Stage 8.3 current-user system-integration acceptance CLI."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..config import ConfigStore
from ..lifecycle import (
    InstallationRecordStore,
    InstalledAppRegistration,
    InstallLayout,
)
from ..scheduler_windows import WindowsTaskSchedulerBackend
from ..system_integration import (
    CurrentUserIntegrationService,
    ShortcutPlan,
)
from ..system_integration_backup import (
    SystemIntegrationBackup,
    capture_system_integration,
    restore_system_integration,
)
from ..system_integration_windows import (
    WindowsInstalledAppRegistryBackend,
    WindowsKnownFolderReader,
    WindowsShortcutBackend,
)

LIVE_CONFIRMATION = "--confirm-live-system-integration"


def _add_layout(command: argparse.ArgumentParser) -> None:
    command.add_argument("--program-root", type=Path, required=True)
    command.add_argument("--data-root", type=Path, required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m theme_scheduler.cli.system_integration",
        description=(
            "Capture, apply, inspect, and restore Stage 8.3 current-user "
            "Windows integration."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    probe = commands.add_parser("probe")
    _add_layout(probe)
    capture = commands.add_parser("capture")
    _add_layout(capture)
    capture.add_argument("--snapshot", type=Path, required=True)
    apply = commands.add_parser("apply")
    _add_layout(apply)
    apply.add_argument("--snapshot", type=Path, required=True)
    apply.add_argument("--config", type=Path, required=True)
    apply.add_argument("--publisher", required=True)
    desktop = apply.add_mutually_exclusive_group(required=True)
    desktop.add_argument(
        "--desktop-shortcut",
        action="store_true",
        dest="desktop_shortcut",
    )
    desktop.add_argument(
        "--no-desktop-shortcut",
        action="store_false",
        dest="desktop_shortcut",
    )
    apply.add_argument(LIVE_CONFIRMATION, action="store_true")
    restore = commands.add_parser("restore")
    _add_layout(restore)
    restore.add_argument("--snapshot", type=Path, required=True)
    restore.add_argument(LIVE_CONFIRMATION, action="store_true")
    return parser


def _print(payload: Mapping[str, Any]) -> None:
    json.dump(
        payload,
        sys.stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    sys.stdout.write("\n")


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve(strict=False))) == os.path.normcase(
        str(right.resolve(strict=False))
    )


def _estimated_size_kib(program_root: Path) -> int:
    total = 0
    for candidate in program_root.rglob("*"):
        if candidate.is_symlink():
            raise OSError(f"Program tree contains a symbolic link: {candidate}")
        try:
            details = candidate.stat()
        except OSError as exc:
            raise OSError(f"Cannot inspect installed payload: {candidate}") from exc
        attributes = getattr(details, "st_file_attributes", 0)
        if attributes & 0x400:
            raise OSError(f"Program tree contains a reparse point: {candidate}")
        if stat.S_ISREG(details.st_mode):
            total += details.st_size
    return max(1, (total + 1023) // 1024)


def _components(layout: InstallLayout):
    known = WindowsKnownFolderReader()
    plan = ShortcutPlan.create(
        layout,
        programs_folder=known.programs(),
        desktop_folder=known.desktop(),
        desktop_enabled=False,
    )
    registry = WindowsInstalledAppRegistryBackend()
    shortcuts = WindowsShortcutBackend(plan.managed_paths)
    tasks = WindowsTaskSchedulerBackend()
    return plan, registry, shortcuts, tasks


def _assert_backup_paths(
    backup: SystemIntegrationBackup,
    plan: ShortcutPlan,
) -> None:
    expected = {
        os.path.normcase(str(path.resolve(strict=False))) for path in plan.managed_paths
    }
    actual = {
        os.path.normcase(str(item.path.resolve(strict=False)))
        for item in backup.shortcuts
    }
    if actual != expected:
        raise ValueError(
            "Backup shortcut paths do not match the current user's known folders."
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command in {"apply", "restore"} and not getattr(
            args,
            "confirm_live_system_integration",
            False,
        ):
            print(
                "error: live HKCU, shortcut, and Task Scheduler writes are "
                f"blocked without {LIVE_CONFIRMATION}; Windows was not "
                "changed.",
                file=sys.stderr,
            )
            return 3
        layout = InstallLayout(
            Path(args.program_root).resolve(strict=False),
            Path(args.data_root).resolve(strict=False),
        )
        plan, registry, shortcuts, tasks = _components(layout)
        if args.command == "probe":
            snapshot = capture_system_integration(
                registry,
                shortcuts,
                tasks,
                plan.managed_paths,
            )
            shortcut_states = []
            for item in snapshot.shortcuts:
                parsed = shortcuts.read(item.path)
                shortcut_states.append(
                    {
                        "path": str(item.path),
                        "exists": item.content is not None,
                        "validThemeSchedulerShortcut": (parsed is not None),
                    }
                )
            _print(
                {
                    "registration": {
                        "exists": snapshot.registration is not None,
                        "validThemeSchedulerRegistration": (
                            registry.read() is not None
                        ),
                    },
                    "shortcuts": shortcut_states,
                    "task": {
                        "exists": snapshot.task_before is not None,
                        "fullDefinitionCaptured": (
                            snapshot.task_definition is not None
                        ),
                    },
                    "windowsChanged": False,
                }
            )
            return 0
        if args.command == "capture":
            snapshot = capture_system_integration(
                registry,
                shortcuts,
                tasks,
                plan.managed_paths,
            )
            snapshot.save(args.snapshot)
            _print(
                {
                    "snapshot": str(Path(args.snapshot).resolve()),
                    "registrationCaptured": (snapshot.registration is not None),
                    "shortcutCount": len(snapshot.shortcuts),
                    "taskCaptured": snapshot.task_before is not None,
                    "windowsChanged": False,
                }
            )
            return 0
        backup = SystemIntegrationBackup.load(args.snapshot)
        _assert_backup_paths(backup, plan)
        if args.command == "restore":
            outcome = restore_system_integration(
                backup,
                registry,
                shortcuts,
                tasks,
            )
            _print(
                {
                    "snapshot": str(Path(args.snapshot).resolve()),
                    "result": outcome.as_dict(),
                }
            )
            return 0 if outcome.verified else 2
        if not layout.executable.is_file():
            raise FileNotFoundError(
                f"Installed executable is missing: {layout.executable}"
            )
        if not layout.uninstaller.is_file():
            raise FileNotFoundError(
                f"Independent uninstaller is missing: {layout.uninstaller}"
            )
        record = InstallationRecordStore(layout.installation_record).load()
        if not _same_path(
            Path(record.install_root), layout.program_root
        ) or not _same_path(Path(record.data_root), layout.data_root):
            raise ValueError("Installation record does not match the requested layout.")
        current = capture_system_integration(
            registry,
            shortcuts,
            tasks,
            plan.managed_paths,
        )
        if not backup.state_equals(current):
            raise RuntimeError(
                "Windows integration changed after the acceptance snapshot; "
                "refusing to apply."
            )
        config = ConfigStore(args.config).load()
        registration = InstalledAppRegistration.create(
            layout,
            version=record.version,
            publisher=args.publisher,
            estimated_size_kib=_estimated_size_kib(layout.program_root),
        )
        desired_plan = ShortcutPlan(
            plan.start_menu,
            plan.desktop,
            args.desktop_shortcut,
        )
        user_id = tasks.current_user_id()
        outcome = CurrentUserIntegrationService(
            layout,
            registry,
            shortcuts,
            tasks,
        ).apply(
            registration=registration,
            shortcut_plan=desired_plan,
            config=config,
            user_id=user_id,
        )
        _print(
            {
                "snapshot": str(Path(args.snapshot).resolve()),
                "programRoot": str(layout.program_root),
                "dataRoot": str(layout.data_root),
                "desktopShortcutEnabled": args.desktop_shortcut,
                "result": outcome.as_dict(),
            }
        )
        return 0 if outcome.verified else 2
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
