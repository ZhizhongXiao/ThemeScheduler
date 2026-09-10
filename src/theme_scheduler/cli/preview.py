"""Unified, write-free launcher for the three source GUI previews."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _ensure_workbench_preview_data(root: Path) -> None:
    """Seed only missing files in the isolated preview data root."""

    from ..accent_profile import AccentProfile, AccentProfileStore
    from ..config import AppConfig, ConfigStore
    from ..state import AppState, StateStore
    from ..storage import UserDataLayout

    layout = UserDataLayout(root.resolve())
    layout.ensure_directories()
    if not layout.config.exists():
        ConfigStore(layout.config).initialize(AppConfig.defaults())
    if not layout.state.exists():
        StateStore(layout.state).initialize(AppState.initial())
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    colors = {"day": 0xC4744DA9, "night": 0xC4FFB900}
    for name, color in colors.items():
        path = layout.profile_path(name)
        if not path.exists():
            AccentProfileStore(path, name).create(
                AccentProfile(name, captured_at, False, color, "preview")
            )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="themescheduler-preview",
        description="Launch a write-free ThemeScheduler GUI preview.",
    )
    commands = parser.add_subparsers(dest="surface", required=True)

    workbench = commands.add_parser("workbench", help="Preview the main GUI.")
    workbench.add_argument("--data-root", type=Path, required=True)
    workbench.add_argument(
        "--allow-system-reads",
        action="store_true",
        help="Allow read-only import of the active Windows appearance.",
    )

    setup = commands.add_parser("setup", help="Preview the Setup wizard.")
    setup.add_argument(
        "--operation",
        choices=("install", "reinstall", "upgrade"),
        default="install",
    )
    setup.add_argument(
        "--result",
        choices=("success", "success-with-warning", "failed", "partial"),
        default="success",
    )
    setup.add_argument(
        "--log-root",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "preview" / "setup" / "logs",
    )

    uninstall = commands.add_parser(
        "uninstall",
        help="Preview the Uninstall wizard.",
    )
    uninstall.add_argument(
        "--result",
        choices=("completed", "partial"),
        default="completed",
    )
    return parser


def _launch_workbench(args: argparse.Namespace) -> int:
    from ..gui import launch_gui

    _ensure_workbench_preview_data(args.data_root)
    return launch_gui(
        data_root=args.data_root,
        installed=False,
        executable=None,
        allow_live_writes=False,
        allow_system_reads=args.allow_system_reads,
    )


def _launch_setup(args: argparse.Namespace) -> int:
    from ..setup_gui import launch_setup_preview
    from ..setup_gui_api import SetupGuiApi
    from ..setup_preview import PreviewSetupRuntime

    api = SetupGuiApi(
        PreviewSetupRuntime(
            operation=args.operation,
            result=args.result,
        ),
        log_root=args.log_root,
    )
    return launch_setup_preview(api)


def _launch_uninstall(args: argparse.Namespace) -> int:
    from ..uninstall_gui import launch_uninstall_window
    from ..uninstall_gui_api import UninstallGuiApi
    from ..uninstall_preview import PreviewUninstallRuntime

    api = UninstallGuiApi(PreviewUninstallRuntime(result=args.result))
    return launch_uninstall_window(api, preview=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    launchers = {
        "workbench": _launch_workbench,
        "setup": _launch_setup,
        "uninstall": _launch_uninstall,
    }
    try:
        return launchers[args.surface](args)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
