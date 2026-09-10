"""Guarded stage-5 Task Scheduler management entry point."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..config import ConfigStore
from ..persistence import load_json_object
from ..scheduler import (
    DEFAULT_TASK_PATH,
    SchedulerMutationError,
    TaskSpec,
    build_task_spec,
    delete_task,
    inspect_task,
    reconcile_task,
    set_task_enabled,
)
from ..scheduler_windows import WindowsTaskSchedulerBackend
from ..storage import UserDataLayout

LIVE_TASK_CONFIRMATION = "--confirm-live-task-write"
LIVE_TASK_RUN_CONFIRMATION = "--confirm-live-task-run"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m theme_scheduler.cli.scheduler",
        description=(
            "Build, inspect, and manage the frozen stage-5 Task Scheduler task."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "verify-snapshot"):
        command = commands.add_parser(name)
        command.add_argument("--data-root", type=Path, required=True)
        command.add_argument("--executable", required=True)
        command.add_argument("--user-id", required=True)
        if name == "verify-snapshot":
            command.add_argument("--snapshot", type=Path, required=True)
    commands.add_parser(
        "probe",
        help="Read-only Task Scheduler connectivity and current-user probe.",
    )
    for name in ("check", "apply"):
        command = commands.add_parser(name)
        command.add_argument("--data-root", type=Path, required=True)
        command.add_argument("--executable", required=True)
        if name == "apply":
            command.add_argument(LIVE_TASK_CONFIRMATION, action="store_true")
    for name in ("enable", "disable", "delete"):
        command = commands.add_parser(name)
        command.add_argument(LIVE_TASK_CONFIRMATION, action="store_true")
    run = commands.add_parser("run")
    run.add_argument(LIVE_TASK_RUN_CONFIRMATION, action="store_true")
    return parser


def _print(payload: Mapping[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command in {"apply", "enable", "disable", "delete"} and not getattr(
            args, "confirm_live_task_write", False
        ):
            print(
                f"error: live Task Scheduler modification is blocked without "
                f"{LIVE_TASK_CONFIRMATION}; no task was changed.",
                file=sys.stderr,
            )
            return 3
        if args.command == "run" and not args.confirm_live_task_run:
            print(
                f"error: live scheduled action is blocked without "
                f"{LIVE_TASK_RUN_CONFIRMATION}; the task was not started.",
                file=sys.stderr,
            )
            return 3
        if args.command == "probe":
            backend = WindowsTaskSchedulerBackend()
            probe = backend.probe()
            _print(
                {
                    "probe": probe,
                    "taskSchedulerAccessed": True,
                    "taskSchedulerChanged": False,
                    "windowsChanged": False,
                }
            )
            return 0
        if args.command in {"enable", "disable", "delete"}:
            backend = WindowsTaskSchedulerBackend()
            if args.command == "delete":
                outcome = delete_task(backend, DEFAULT_TASK_PATH)
            else:
                outcome = set_task_enabled(
                    backend,
                    DEFAULT_TASK_PATH,
                    args.command == "enable",
                )
            _print(
                {
                    "result": outcome.as_dict(),
                    "taskSchedulerAccessed": True,
                    "taskSchedulerChanged": outcome.changed,
                    "windowsChanged": False,
                }
            )
            return 0
        if args.command == "run":
            backend = WindowsTaskSchedulerBackend()
            result = backend.run_now(DEFAULT_TASK_PATH)
            _print(
                {
                    "result": result,
                    "taskSchedulerAccessed": True,
                    "taskSchedulerChanged": False,
                    "windowsChanged": False,
                }
            )
            return 0
        layout = UserDataLayout(args.data_root)
        config = ConfigStore(layout.config).load()
        if args.command in {"check", "apply"}:
            backend = WindowsTaskSchedulerBackend()
            user_id = backend.current_user_id()
            desired = build_task_spec(
                config,
                executable=args.executable,
                user_id=user_id,
            )
            if args.command == "check":
                inspection = inspect_task(desired, backend.read(desired.task_path))
                _print(
                    {
                        "desired": desired.as_dict(),
                        "inspection": inspection.as_dict(),
                        "taskSchedulerAccessed": True,
                        "taskSchedulerChanged": False,
                        "windowsChanged": False,
                    }
                )
                return 0 if inspection.valid else 1
            outcome = reconcile_task(backend, desired)
            _print(
                {
                    "result": outcome.as_dict(),
                    "taskSchedulerAccessed": True,
                    "taskSchedulerChanged": outcome.changed,
                    "windowsChanged": False,
                }
            )
            return 0
        desired = build_task_spec(
            config,
            executable=args.executable,
            user_id=args.user_id,
        )
        payload: dict[str, Any] = {
            "desired": desired.as_dict(),
            "taskSchedulerAccessed": False,
            "taskSchedulerChanged": False,
            "windowsChanged": False,
        }
        if args.command == "verify-snapshot":
            actual = TaskSpec.from_dict(load_json_object(args.snapshot))
            inspection = inspect_task(desired, actual)
            payload["inspection"] = inspection.as_dict()
            _print(payload)
            return 0 if inspection.valid else 1
        _print(payload)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        suffix = ""
        if isinstance(exc, SchedulerMutationError):
            suffix = (
                f"; rollbackAttempted={str(exc.rollback_attempted).lower()}"
                f"; rollbackSucceeded={str(exc.rollback_succeeded).lower()}"
            )
        print(f"error: {exc}{suffix}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
