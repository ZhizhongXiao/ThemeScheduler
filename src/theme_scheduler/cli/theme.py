"""Guarded command-line interface for the phase-1 theme prototype."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..diagnostics import (
    SNAPSHOT_KIND,
    capture_registry_snapshot,
    collect_environment,
)
from ..persistence import atomic_write_json, load_json
from ..theme import (
    ThemeApplyError,
    ThemeError,
    ThemeMode,
    ThemeProfile,
    apply_theme_profile,
    read_theme_snapshot,
    restore_theme_snapshot,
    theme_snapshot_from_dict,
)

LIVE_WRITE_CONFIRMATION = "--confirm-live-write"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m theme_scheduler.cli.theme",
        description="Phase-1 Windows theme read/write prototype.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "read",
        help="Read the candidate system and app registry values for diagnostics.",
    )

    apply = commands.add_parser("apply", help="Apply and verify the app theme mode.")
    apply.add_argument(
        "--apps", choices=[mode.value for mode in ThemeMode], required=True
    )
    apply.add_argument("--backup", type=Path, required=True)
    apply.add_argument(
        LIVE_WRITE_CONFIRMATION,
        action="store_true",
        help="Acknowledge that this command will modify current-user theme registry values.",
    )

    restore = commands.add_parser(
        "restore", help="Restore the app theme value from a phase-1 backup."
    )
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--safety-backup", type=Path, required=True)
    restore.add_argument(
        LIVE_WRITE_CONFIRMATION,
        action="store_true",
        help="Acknowledge that this command will modify current-user theme registry values.",
    )
    return parser


def _print(payload: Mapping[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def _validate_live_environment(environment: Mapping[str, Any]) -> None:
    platform_data = environment.get("platform", {})
    release_data = environment.get("windowsRelease", {})
    if platform_data.get("system") != "Windows" or platform_data.get("release") != "11":
        raise ThemeError("Live prototype requires Windows 11.")
    if platform_data.get("machine", "").casefold() not in {"amd64", "x86_64"}:
        raise ThemeError("Live prototype requires Windows 11 x64.")
    try:
        build = int(release_data.get("CurrentBuild", 0))
    except (TypeError, ValueError) as exc:
        raise ThemeError("Cannot identify the current Windows build.") from exc
    if build < 26100:
        raise ThemeError(
            "Live prototype requires Windows 11 24H2 (build 26100) or newer."
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "read":
            _print(read_theme_snapshot().as_dict())
            return 0

        if not args.confirm_live_write:
            print(
                f"error: live write is blocked without {LIVE_WRITE_CONFIRMATION}; no backup or registry write occurred.",
                file=sys.stderr,
            )
            return 3

        environment = collect_environment()
        _validate_live_environment(environment)

        if args.command == "restore":
            backup = load_json(args.backup)
            if (
                backup.get("kind") != SNAPSHOT_KIND
                or backup.get("phase") != "phase-1-theme-prototype"
            ):
                raise ThemeError("Backup is not a phase-1 theme prototype backup.")
            target = theme_snapshot_from_dict(backup.get("themeState"))
            current = read_theme_snapshot()
            safety_backup = capture_registry_snapshot()
            safety_backup["phase"] = "phase-1-theme-prototype"
            safety_backup["themeState"] = current.as_dict()
            atomic_write_json(args.safety_backup, safety_backup)
            result = restore_theme_snapshot(target)
            _print(
                {
                    "restoredFrom": str(args.backup.resolve()),
                    "safetyBackup": str(args.safety_backup.resolve()),
                    "result": result.as_dict(),
                }
            )
            return 0

        before = read_theme_snapshot()
        backup = capture_registry_snapshot()
        backup["phase"] = "phase-1-theme-prototype"
        backup["themeState"] = before.as_dict()
        atomic_write_json(args.backup, backup)

        profile = ThemeProfile(apps=ThemeMode(args.apps))
        result = apply_theme_profile(profile)
        _print(
            {
                "backup": str(args.backup.resolve()),
                "result": result.as_dict(),
            }
        )
        return 0
    except ThemeApplyError as exc:
        _print(
            {
                "error": str(exc),
                "rollbackSucceeded": exc.rollback_succeeded,
                "rollbackErrors": list(exc.rollback_errors),
            }
        )
        return 4
    except (OSError, ValueError, ThemeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
