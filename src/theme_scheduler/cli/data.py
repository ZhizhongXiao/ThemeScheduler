"""Guarded offline entry point for stage-3 persistence acceptance."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

from ..accent_profile import AccentProfileStore
from ..backup import InstallBackupStore
from ..config import ConfigStore
from ..log_policy import (
    LOG_BACKUP_COUNT,
    LOG_FILE_NAME,
    LOG_MAX_BYTES,
    EventLogWriter,
    LogEvent,
)
from ..persistence import captured_at
from ..runtime_retention import plan_runtime_cleanup
from ..state import StateStore
from ..storage import UserDataLayout

DATA_WRITE_CONFIRMATION = "--confirm-data-write"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m theme_scheduler.cli.data",
        description="Initialize or validate a stage-3 data root without changing Windows.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser(
        "initialize", help="Create default config and explicit initial state."
    )
    initialize.add_argument("--data-root", type=Path, required=True)
    initialize.add_argument(DATA_WRITE_CONFIRMATION, action="store_true")

    validate = commands.add_parser(
        "validate", help="Strictly validate an initialized data root."
    )
    validate.add_argument("--data-root", type=Path, required=True)

    log_smoke = commands.add_parser(
        "log-smoke", help="Append one bounded stage-3 acceptance event."
    )
    log_smoke.add_argument("--data-root", type=Path, required=True)
    log_smoke.add_argument(DATA_WRITE_CONFIRMATION, action="store_true")
    return parser


def _print(payload: Mapping[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def _initialize(layout: UserDataLayout) -> dict[str, Any]:
    config_store = ConfigStore(layout.config)
    state_store = StateStore(layout.state)
    if layout.config.exists() or layout.state.exists():
        raise FileExistsError(
            "Refusing initialization because config.json or state.json already exists."
        )
    layout.ensure_directories()
    config_created = False
    try:
        config = config_store.initialize()
        config_created = True
        state = state_store.initialize()
    except Exception:
        if config_created:
            for created_path in (layout.state, layout.config):
                with suppress(OSError):
                    created_path.unlink(missing_ok=True)
        raise
    return {
        "dataRoot": str(layout.root.resolve()),
        "config": config.as_dict(),
        "state": state.as_dict(),
        "windowsChanged": False,
    }


def _optional_profile(layout: UserDataLayout, name: str) -> dict[str, Any]:
    path = layout.profile_path(name)
    if not path.exists():
        return {"status": "absent", "path": str(path.resolve())}
    profile = AccentProfileStore(path, name).load()
    return {
        "status": "valid",
        "path": str(path.resolve()),
        "profile": profile.as_dict(),
    }


def _optional_backup(layout: UserDataLayout) -> dict[str, Any]:
    manifest_exists = layout.install_backup_manifest.exists()
    theme_exists = layout.install_backup_theme.exists()
    if not manifest_exists and not theme_exists:
        return {"status": "absent"}
    if manifest_exists != theme_exists:
        raise ValueError("Install backup is incomplete.")
    manifest = InstallBackupStore(
        layout.install_backup_manifest, layout.install_backup_theme
    ).load_verified()
    return {"status": "valid", "manifest": manifest.as_dict()}


def _validate(layout: UserDataLayout) -> dict[str, Any]:
    config = ConfigStore(layout.config).load()
    state = StateStore(layout.state).load()
    cleanup = plan_runtime_cleanup(layout.runtime)
    return {
        "dataRoot": str(layout.root.resolve()),
        "config": {"status": "valid", "value": config.as_dict()},
        "state": {"status": "valid", "value": state.as_dict()},
        "profiles": {
            "day": _optional_profile(layout, "day"),
            "night": _optional_profile(layout, "night"),
        },
        "installBackup": _optional_backup(layout),
        "eventLog": {
            "path": str(layout.event_log.resolve()),
            "exists": layout.event_log.exists(),
            "format": LOG_FILE_NAME,
            "maxBytes": LOG_MAX_BYTES,
            "backupCount": LOG_BACKUP_COUNT,
        },
        "runtimeCleanup": {
            "deleteCount": len(cleanup.delete),
            "keepCount": len(cleanup.keep),
        },
        "valid": True,
        "windowsChanged": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    layout = UserDataLayout(args.data_root)
    try:
        if args.command in {"initialize", "log-smoke"} and not args.confirm_data_write:
            print(
                f"error: data write is blocked without {DATA_WRITE_CONFIRMATION}; no files were changed.",
                file=sys.stderr,
            )
            return 3
        if args.command == "initialize":
            _print(_initialize(layout))
            return 0
        if args.command == "log-smoke":
            ConfigStore(layout.config).load()
            StateStore(layout.state).load()
            event = LogEvent(
                occurred_at=captured_at(),
                level="INFO",
                event="persistence.acceptance",
                result="success",
                trigger="manual",
                message="Stage-3 persistence log smoke test.",
            )
            EventLogWriter(layout.event_log).append(event)
            _print(
                {
                    "eventLog": str(layout.event_log.resolve()),
                    "event": event.as_dict(),
                    "windowsChanged": False,
                }
            )
            return 0
        _print(_validate(layout))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
