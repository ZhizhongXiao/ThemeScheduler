"""Guarded Stage 8.4 maintenance acceptance entry point."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..backup import InstallBackupStore, capture_install_backup
from ..core import mutex_name_for_data_root
from ..execution_lock import WindowsNamedMutexLock
from ..maintenance_service import MaintenanceService
from ..state import StateStore
from ..storage import UserDataLayout

DATA_CONFIRMATION = "--confirm-backup-data-write"
LIVE_CONFIRMATION = "--confirm-live-appearance-restore"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m theme_scheduler.cli.maintenance",
        description=(
            "Inspect, capture, and restore the Stage 8.4 first-install "
            "appearance backup."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--data-root", type=Path, required=True)
    capture = commands.add_parser("capture-backup")
    capture.add_argument("--data-root", type=Path, required=True)
    capture.add_argument("--created-by-version", required=True)
    capture.add_argument("--windows-build", required=True)
    capture.add_argument(DATA_CONFIRMATION, action="store_true")
    restore = commands.add_parser("restore")
    restore.add_argument("--data-root", type=Path, required=True)
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


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "capture-backup" and not args.confirm_backup_data_write:
            print(
                f"error: backup creation is blocked without "
                f"{DATA_CONFIRMATION}; no data or Windows state was changed.",
                file=sys.stderr,
            )
            return 3
        if args.command == "restore" and not args.confirm_live_appearance_restore:
            print(
                f"error: live appearance restore is blocked without "
                f"{LIVE_CONFIRMATION}; no data or Windows state was changed.",
                file=sys.stderr,
            )
            return 3
        layout = UserDataLayout(Path(args.data_root).resolve(strict=False))
        store = InstallBackupStore(
            layout.install_backup_manifest,
            layout.install_backup_theme,
        )
        if args.command == "inspect":
            state = StateStore(layout.state).load()
            backup = store.load_verified()
            _print(
                {
                    "state": state.as_dict(),
                    "backup": backup.as_dict(),
                    "valid": True,
                    "dataChanged": False,
                    "windowsChanged": False,
                }
            )
            return 0
        if args.command == "capture-backup":
            backup = capture_install_backup(
                store,
                created_by_version=args.created_by_version,
                windows_build=args.windows_build,
            )
            _print(
                {
                    "backup": backup.as_dict(),
                    "manifest": str(layout.install_backup_manifest.resolve()),
                    "theme": str(layout.install_backup_theme.resolve()),
                    "dataChanged": True,
                    "windowsChanged": False,
                }
            )
            return 0
        lock = WindowsNamedMutexLock(mutex_name_for_data_root(layout.root))
        outcome = MaintenanceService(
            layout,
            lock,
            backup_store=store,
        ).restore_install_appearance()
        _print(outcome.as_dict())
        return 0 if outcome.result.value == "restored" else 2
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
