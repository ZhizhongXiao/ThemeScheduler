"""Guarded stage-4 automatic-core prototype entry point."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from ..accent_profile import AccentProfileStore
from ..automation import AutoRunner, WindowsAutoBackend
from ..config import ConfigStore
from ..core import (
    mutex_name_for_data_root,
    plan_auto_run,
)
from ..diagnostics import collect_environment
from ..execution_lock import WindowsNamedMutexLock
from ..state import StateStore
from ..storage import UserDataLayout

LIVE_AUTO_CONFIRMATION = "--confirm-live-auto-write"
TEST_TIME_CONFIRMATION = "--confirm-test-time-override"


class _FixedClock:
    def __init__(self, instant: datetime) -> None:
        self.instant = instant

    def now(self) -> datetime:
        return self.instant


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m theme_scheduler.cli.auto",
        description=(
            "Plan or execute the stage-4 automatic core. "
            "This does not create Task Scheduler tasks."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    plan = commands.add_parser(
        "plan",
        help="Calculate and strictly validate an automatic run without writes.",
    )
    plan.add_argument("--data-root", type=Path, required=True)
    plan.add_argument(
        "--at",
        help="Aware ISO 8601 local time override for offline planning only.",
    )

    run = commands.add_parser(
        "run",
        help="Execute one live automatic run using the actual local time.",
    )
    run.add_argument("--data-root", type=Path, required=True)
    run.add_argument(LIVE_AUTO_CONFIRMATION, action="store_true")
    run.add_argument(
        "--at",
        help="Aware ISO 8601 test time; requires a second explicit confirmation.",
    )
    run.add_argument(TEST_TIME_CONFIRMATION, action="store_true")
    return parser


def _print(payload: Mapping[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def _parse_aware_time(value: str | None) -> datetime:
    if value is None:
        return datetime.now().astimezone()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("--at must be an ISO 8601 datetime.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--at must include a UTC offset.")
    return parsed


def _validate_live_environment(environment: Mapping[str, Any]) -> str:
    platform_data = environment.get("platform")
    release_data = environment.get("windowsRelease")
    if not isinstance(platform_data, Mapping) or not isinstance(release_data, Mapping):
        raise OSError("Cannot identify the Windows environment.")
    if platform_data.get("system") != "Windows" or platform_data.get("release") != "11":
        raise OSError("Live automatic switching requires Windows 11.")
    if str(platform_data.get("machine", "")).casefold() not in {
        "amd64",
        "x86_64",
    }:
        raise OSError("Live automatic switching requires Windows 11 x64.")
    raw_build = release_data.get("CurrentBuild")
    if isinstance(raw_build, bool) or not isinstance(raw_build, (int, str)):
        raise OSError("Cannot identify the current Windows build.")
    try:
        build = int(raw_build)
    except (TypeError, ValueError) as exc:
        raise OSError("Cannot identify the current Windows build.") from exc
    if build < 26100:
        raise OSError(
            "Live automatic switching requires Windows 11 build 26100 or newer."
        )
    return str(build)


def create_live_auto_runner(
    layout: UserDataLayout,
    *,
    decision_time: datetime | None = None,
) -> tuple[AutoRunner, str]:
    """Build the verified live runner used by scheduled automatic entries."""

    environment = collect_environment()
    windows_build = _validate_live_environment(environment)
    mutex_name = mutex_name_for_data_root(layout.root)
    runner = AutoRunner(
        layout,
        WindowsNamedMutexLock(mutex_name),
        WindowsAutoBackend(layout, windows_build=windows_build),
        clock=(_FixedClock(decision_time) if decision_time is not None else None),
    )
    return runner, mutex_name


def _offline_plan(layout: UserDataLayout, instant: datetime) -> dict[str, Any]:
    config = ConfigStore(layout.config).load()
    state = StateStore(layout.state).load()
    plan = plan_auto_run(config, state, instant)
    target_profile = plan.target_profile
    profile_status: dict[str, Any] | None = None
    if target_profile is not None:
        profile = AccentProfileStore(
            layout.profile_path(target_profile),
            target_profile,
        ).load()
        profile_status = profile.as_dict()
    return {
        "dataRoot": str(layout.root.resolve()),
        "at": instant.isoformat(timespec="seconds"),
        "plan": {
            "kind": plan.kind.value,
            "targetProfile": target_profile,
            "targetAppsTheme": plan.target_apps_theme,
            "targetSystemTheme": plan.target_system_theme,
            "targetStartTaskbarAccent": plan.target_start_taskbar_accent,
            "targetTitleBordersAccent": plan.target_title_borders_accent,
            "learnProfile": None,
        },
        "targetAccentProfile": profile_status,
        "windowsChanged": False,
        "dataChanged": False,
        "taskSchedulerChanged": False,
    }


def run_installed_auto() -> int:
    """Run the production Task Scheduler route using the installed data root.

    Unlike the source-tree acceptance CLI, the installed task has already been
    explicitly created by the user through the product workflow.  It therefore
    does not require development confirmation flags or accept a test clock.
    """

    try:
        coordinator, _mutex_name = create_live_scheduled_coordinator(
            UserDataLayout.default()
        )
        return int(coordinator.run().exit_code)
    except (OSError, ValueError, RuntimeError) as exc:
        if sys.stderr is not None:
            print(f"error: {exc}", file=sys.stderr)
        return 2


def create_live_scheduled_coordinator(layout: UserDataLayout):
    """Compose the installed scheduler, WinRT, and transactional core."""

    from ..notifications_windows import WindowsNotificationBackend
    from ..scheduled_auto import ScheduledAutoCoordinator
    from ..scheduler_windows import WindowsTaskSchedulerBackend

    runner, mutex_name = create_live_auto_runner(layout)
    tasks = WindowsTaskSchedulerBackend()
    user_id = tasks.current_user_id()
    notifier = WindowsNotificationBackend()
    notifier.probe()
    executable = Path(sys.executable).resolve()
    if (
        not bool(getattr(sys, "frozen", False))
        or executable.name.casefold() != "themescheduler.exe"
    ):
        raise OSError("The installed scheduled route requires ThemeScheduler.exe.")
    return (
        ScheduledAutoCoordinator(
            layout,
            runner.execution_lock,
            runner,
            tasks,
            notifier,
            executable=str(executable),
            user_id=user_id,
        ),
        mutex_name,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    layout = UserDataLayout(args.data_root)
    try:
        if args.command == "plan":
            _print(_offline_plan(layout, _parse_aware_time(args.at)))
            return 0
        if not args.confirm_live_auto_write:
            print(
                f"error: live automatic write is blocked without "
                f"{LIVE_AUTO_CONFIRMATION}; no files or Windows settings were changed.",
                file=sys.stderr,
            )
            return 3
        if args.at is not None and not args.confirm_test_time_override:
            print(
                f"error: live test-time override is blocked without "
                f"{TEST_TIME_CONFIRMATION}; no files or Windows settings were changed.",
                file=sys.stderr,
            )
            return 3
        decision_time = _parse_aware_time(args.at) if args.at is not None else None
        runner, mutex_name = create_live_auto_runner(
            layout,
            decision_time=decision_time,
        )
        outcome = runner.run()
        payload = outcome.as_dict()
        payload["taskSchedulerChanged"] = False
        payload["mutexName"] = mutex_name
        payload["timeOverride"] = decision_time is not None
        payload["decisionTime"] = (
            decision_time.isoformat(timespec="seconds")
            if decision_time is not None
            else "actual-local-time"
        )
        _print(payload)
        return int(outcome.exit_code)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
