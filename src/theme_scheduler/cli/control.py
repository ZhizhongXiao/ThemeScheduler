"""Guarded pause, resume, and status development entry point."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..control_service import ControlService
from ..core import mutex_name_for_data_root
from ..execution_lock import WindowsNamedMutexLock
from ..state import StateStore
from ..storage import UserDataLayout

STATE_WRITE_CONFIRMATION = "--confirm-state-write"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m theme_scheduler.cli.control",
        description=(
            "Inspect or control the persisted pause state. "
            "This never modifies Windows appearance or Task Scheduler."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "pause", "resume"):
        command = commands.add_parser(name)
        command.add_argument("--data-root", type=Path, required=True)
        if name in {"pause", "resume"}:
            command.add_argument(STATE_WRITE_CONFIRMATION, action="store_true")

    return parser


def _print(payload: Mapping[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def _status(layout: UserDataLayout) -> dict[str, Any]:
    state = StateStore(layout.state).load()
    return {
        "action": "status",
        "result": "success",
        "state": state.as_dict(),
        "dataChanged": False,
        "windowsChanged": False,
        "taskSchedulerAccessed": False,
        "taskSchedulerChanged": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    layout = UserDataLayout(args.data_root)
    try:
        if args.command == "status":
            _print(_status(layout))
            return 0

        if args.command in {"pause", "resume"}:
            if not args.confirm_state_write:
                print(
                    f"error: pause-state write is blocked without "
                    f"{STATE_WRITE_CONFIRMATION}; no files or Windows settings "
                    "were changed.",
                    file=sys.stderr,
                )
                return 3
            mutex_name = mutex_name_for_data_root(layout.root)
            service = ControlService(
                layout,
                WindowsNamedMutexLock(mutex_name),
            )
            outcome = service.set_paused(args.command == "pause")
            payload = outcome.as_dict()
            payload["mutexName"] = mutex_name
            _print(payload)
            return int(outcome.exit_code)

        raise ValueError("Unsupported control command.")
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
