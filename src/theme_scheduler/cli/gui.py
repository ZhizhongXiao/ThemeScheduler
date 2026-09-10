"""Source and installed command-line entry for the on-demand GUI."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from ..gui import launch_gui


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m theme_scheduler.cli.gui",
        description="Launch the on-demand ThemeScheduler control panel.",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        help="Explicit development or acceptance data root.",
    )
    parser.add_argument(
        "--installed",
        action="store_true",
        help="Use the installed current-user data root.",
    )
    parser.add_argument(
        "--executable",
        type=Path,
        help="Task Scheduler executable target; defaults to this Python process.",
    )
    parser.add_argument(
        "--allow-live-writes",
        action="store_true",
        help=(
            "Enable confirmed task and Windows mutations for a source-tree "
            "acceptance run; requires --executable."
        ),
    )
    parser.add_argument(
        "--allow-system-reads",
        action="store_true",
        help=(
            "Allow only read-only import of the active Windows appearance; "
            "task and theme writes remain disabled."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return launch_gui(
            data_root=args.data_root,
            installed=args.installed,
            executable=args.executable,
            allow_live_writes=args.allow_live_writes,
            allow_system_reads=args.allow_system_reads,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
