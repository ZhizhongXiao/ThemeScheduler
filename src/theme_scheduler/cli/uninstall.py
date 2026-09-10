"""Independent Stage 8.5 uninstaller entry point.

The installed copy only gathers explicit user choices and stages itself.  The
temporary, hash-bound copy performs the actual transaction.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..core import mutex_name_for_data_root
from ..execution_lock import WindowsNamedMutexLock
from ..lifecycle import (
    InstallContractError,
    InstallLayout,
    file_sha256,
)
from ..maintenance_service import MaintenanceService
from ..persistence import load_json_object
from ..protocol_registration_windows import (
    WindowsNotificationProtocolBackend,
)
from ..scheduler_windows import WindowsTaskSchedulerBackend
from ..storage import UserDataLayout
from ..system_integration import (
    SHORTCUT_FILE_NAME,
    START_MENU_FOLDER_NAME,
)
from ..system_integration_windows import (
    WindowsInstalledAppRegistryBackend,
    WindowsKnownFolderReader,
    WindowsShortcutBackend,
)
from ..uninstall_contracts import (
    AppearanceChoice,
    UninstallContractError,
    UninstallOptions,
    UninstallRequest,
    build_uninstall_plan,
)
from ..uninstall_service import (
    IndependentUninstallService,
    lifecycle_mutex_name_for_program_root,
)
from ..uninstall_windows import (
    IndependentUninstallerStager,
    WindowsInstalledProcessGuard,
    WindowsProcessApi,
    WindowsSelfCleanupScheduler,
    WindowsUninstallError,
)

Notifier = Callable[[str, str], None]
ChoiceProvider = Callable[[], UninstallOptions | None]
StagerFactory = Callable[
    [InstallLayout, Path],
    IndependentUninstallerStager,
]
LauncherWaiter = Callable[[int, int], bool]

IDOK = 1
IDCANCEL = 2
IDYES = 6
IDNO = 7
MB_OKCANCEL = 0x00000001
MB_YESNOCANCEL = 0x00000003
MB_ICONQUESTION = 0x00000020
MB_ICONWARNING = 0x00000030
MB_ICONINFORMATION = 0x00000040
_STAGED_TRANSACTION_PATTERN = re.compile(r"uninstall-[0-9a-f]{32}\Z")


def build_read_only_plan(layout: InstallLayout) -> dict[str, object]:
    """Describe owned lifecycle targets without touching them."""

    base = build_uninstall_plan(
        layout,
        UninstallOptions(AppearanceChoice.KEEP, True, True),
    ).as_dict()
    return {
        **base,
        "kind": "themescheduler.uninstall-read-only-plan",
        "mainExecutable": str(layout.executable),
        "independentUninstaller": str(layout.uninstaller),
        "targets": {
            "scheduledTask": r"\ThemeScheduler",
            "installedAppRegistration": (
                r"HKCU\Software\Microsoft\Windows\CurrentVersion"
                r"\Uninstall\ThemeScheduler"
            ),
            "programFiles": str(layout.program_root),
            "userData": str(layout.data_root),
        },
        "restoreChoices": [
            AppearanceChoice.KEEP.value,
            AppearanceChoice.RESTORE.value,
        ],
        "dataChoices": [
            "keep-config-and-profiles",
            "keep-logs",
        ],
        "liveUninstallAvailable": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ThemeScheduler-Uninstall")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser(
        "inspect",
        help="Print the read-only lifecycle target plan.",
    )
    inspect.add_argument("--program-root", type=Path, required=True)
    inspect.add_argument("--data-root", type=Path, required=True)
    stage = commands.add_parser(
        "stage",
        help="Stage an authorized temporary uninstaller.",
    )
    stage.add_argument(
        "--appearance",
        choices=tuple(choice.value for choice in AppearanceChoice),
        required=True,
    )
    stage.add_argument("--keep-config-and-profiles", action="store_true")
    stage.add_argument("--keep-logs", action="store_true")
    stage.add_argument("--confirm-live-uninstall", action="store_true")
    execute = commands.add_parser(
        "execute",
        help=argparse.SUPPRESS,
    )
    execute.add_argument("--request", type=Path, required=True)
    execute.add_argument("--authorization-token", required=True)
    return parser


def _native_notice(title: str, message: str) -> None:
    ctypes.windll.user32.MessageBoxW(
        None,
        message,
        title,
        MB_ICONINFORMATION,
    )


def _native_choice_provider() -> UninstallOptions | None:
    user32 = ctypes.windll.user32
    appearance = user32.MessageBoxW(
        None,
        (
            "是否恢复 ThemeScheduler 接管前的应用模式和强调色？\n\n"
            "“是”：恢复安装前外观；“否”：保持当前外观。"
        ),
        "ThemeScheduler 卸载程序 · 外观",
        MB_YESNOCANCEL | MB_ICONQUESTION,
    )
    if appearance == IDCANCEL:
        return None
    if appearance not in {IDYES, IDNO}:
        raise WindowsUninstallError("Appearance dialog returned no choice.")
    keep_config = user32.MessageBoxW(
        None,
        ("是否保留配置、暂停状态、昼夜应用模式和颜色记录，供以后重新安装？"),
        "ThemeScheduler 卸载程序 · 配置",
        MB_YESNOCANCEL | MB_ICONQUESTION,
    )
    if keep_config == IDCANCEL:
        return None
    if keep_config not in {IDYES, IDNO}:
        raise WindowsUninstallError("Configuration dialog returned no choice.")
    keep_logs = user32.MessageBoxW(
        None,
        "是否保留 ThemeScheduler 日志？",
        "ThemeScheduler 卸载程序 · 日志",
        MB_YESNOCANCEL | MB_ICONQUESTION,
    )
    if keep_logs == IDCANCEL:
        return None
    if keep_logs not in {IDYES, IDNO}:
        raise WindowsUninstallError("Log dialog returned no choice.")
    options = UninstallOptions(
        (AppearanceChoice.RESTORE if appearance == IDYES else AppearanceChoice.KEEP),
        keep_config == IDYES,
        keep_logs == IDYES,
    )
    summary = (
        "即将卸载 ThemeScheduler。\n\n"
        f"外观：{'恢复安装前外观' if options.appearance is AppearanceChoice.RESTORE else '保持当前外观'}\n"
        f"配置与颜色：{'保留' if options.keep_config_and_profiles else '删除'}\n"
        f"日志：{'保留' if options.keep_logs else '删除'}\n\n"
        "任务、快捷方式、安装登记、程序文件、运行缓存和 WebView2 "
        "缓存将始终删除。是否继续？"
    )
    confirmed = user32.MessageBoxW(
        None,
        summary,
        "ThemeScheduler 卸载程序 · 最终确认",
        MB_OKCANCEL | MB_ICONWARNING,
    )
    return options if confirmed == IDOK else None


def _print(payload: Mapping[str, Any]) -> None:
    if sys.stdout is None:
        return
    json.dump(
        payload,
        sys.stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    sys.stdout.write("\n")


def _error(message: str) -> None:
    if sys.stderr is not None:
        print(message, file=sys.stderr)


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve(strict=False))) == os.path.normcase(
        str(right.resolve(strict=False))
    )


def _staged_resume_arguments(
    current_executable: Path,
) -> list[str] | None:
    """Allow a recent hash-bound temporary copy to resume by double-click."""

    executable = Path(current_executable).resolve(strict=False)
    workspace = executable.parent
    if (
        executable.name != "Uninstall.exe"
        or _STAGED_TRANSACTION_PATTERN.fullmatch(workspace.name) is None
    ):
        return None
    request_path = workspace / "request.json"
    if not request_path.is_file():
        return None
    request = UninstallRequest.from_dict(load_json_object(request_path))
    if request.transaction_id != workspace.name:
        raise UninstallContractError(
            "Staged retry request does not match its workspace."
        )
    return [
        "execute",
        "--request",
        str(request_path),
        "--authorization-token",
        request.authorization_token,
    ]


def _validate_execute_request(
    request_path: Path,
    authorization_token: str,
    current_executable: Path,
    *,
    now: datetime | None = None,
    temp_root: Path | None = None,
) -> tuple[UninstallRequest, Path]:
    request_path = Path(request_path).resolve(strict=True)
    current_executable = Path(current_executable).resolve(strict=True)
    request = UninstallRequest.from_dict(load_json_object(request_path))
    if authorization_token != request.authorization_token:
        raise UninstallContractError(
            "Authorization token does not match the staged request."
        )
    workspace = request_path.parent
    if (
        request_path.name != "request.json"
        or workspace.name != request.transaction_id
        or current_executable.name != "Uninstall.exe"
        or not _same_path(current_executable.parent, workspace)
    ):
        raise UninstallContractError("Temporary executable/request layout is invalid.")
    raw_temp = (
        temp_root
        if temp_root is not None
        else Path(os.environ.get("TEMP") or os.environ.get("TMP") or "")
    )
    if not raw_temp or not Path(raw_temp).is_absolute():
        raise UninstallContractError("TEMP root is unavailable.")
    expected_parent = Path(raw_temp).resolve(strict=False) / "ThemeScheduler"
    if not _same_path(workspace.parent, expected_parent):
        raise UninstallContractError(
            "Temporary request is outside the fixed product temp root."
        )
    default_layout = InstallLayout.default()
    if not (
        _same_path(
            request.layout.program_root,
            default_layout.program_root,
        )
        and _same_path(
            request.layout.data_root,
            default_layout.data_root,
        )
    ):
        raise UninstallContractError(
            "Live uninstall request does not target the default current-user "
            "installation."
        )
    if file_sha256(current_executable) != request.source_uninstaller_sha256:
        raise UninstallContractError(
            "Temporary uninstaller hash does not match its request."
        )
    cleanup = workspace / "cleanup.ps1"
    if not cleanup.is_file():
        raise UninstallContractError("Temporary cleanup script is missing.")
    if file_sha256(cleanup) != request.cleanup_script_sha256:
        raise UninstallContractError(
            "Temporary cleanup script hash does not match its request."
        )
    created = datetime.fromisoformat(request.created_at)
    current = now or datetime.now().astimezone()
    if current.tzinfo is None or current.utcoffset() is None:
        raise UninstallContractError("Current time must include a UTC offset.")
    if created > current + timedelta(minutes=2):
        raise UninstallContractError("Uninstall request timestamp is in the future.")
    if current - created > timedelta(minutes=15):
        raise UninstallContractError("Uninstall request authorization has expired.")
    return request, workspace


def _create_live_service(
    request: UninstallRequest,
    workspace: Path,
) -> IndependentUninstallService:
    layout = request.layout
    known = WindowsKnownFolderReader()
    shortcuts = (
        known.programs() / START_MENU_FOLDER_NAME / SHORTCUT_FILE_NAME,
        known.desktop() / SHORTCUT_FILE_NAME,
    )
    lifecycle_lock = WindowsNamedMutexLock(
        lifecycle_mutex_name_for_program_root(layout.program_root)
    )
    auto_lock = WindowsNamedMutexLock(mutex_name_for_data_root(layout.data_root))
    user_layout = UserDataLayout(layout.data_root)
    maintenance = MaintenanceService(
        user_layout,
        auto_lock,
    )
    return IndependentUninstallService(
        request,
        workspace,
        lifecycle_lock,
        auto_lock,
        WindowsInstalledAppRegistryBackend(),
        WindowsShortcutBackend(shortcuts),
        WindowsTaskSchedulerBackend(),
        shortcuts,
        protocol=WindowsNotificationProtocolBackend(),
        appearance_restorer=(
            maintenance.restore_install_appearance_locked
            if request.options.appearance is AppearanceChoice.RESTORE
            else None
        ),
        process_guard=WindowsInstalledProcessGuard(),
        self_cleanup=WindowsSelfCleanupScheduler(),
    )


def _wait_for_launcher_or_raise(
    request: UninstallRequest,
    waiter: LauncherWaiter,
) -> None:
    """Ensure the installed launcher is gone before UI or mutation starts."""
    if request.launcher_process_id == os.getpid():
        return
    if waiter(request.launcher_process_id, 15_000):
        return
    raise WindowsUninstallError(
        "Installed uninstaller did not exit within 15 seconds; "
        "no uninstall mutation was started."
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    notifier: Notifier | None = None,
    choice_provider: ChoiceProvider | None = None,
    stager_factory: StagerFactory | None = None,
    current_executable: Path | None = None,
    service_factory: Callable[
        [UninstallRequest, Path],
        IndependentUninstallService,
    ]
    | None = None,
    launcher_waiter: LauncherWaiter | None = None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    notify = notifier or _native_notice
    executable = Path(current_executable or sys.executable).resolve(strict=False)
    production_gui = all(
        item is None
        for item in (
            notifier,
            choice_provider,
            stager_factory,
            current_executable,
            service_factory,
            launcher_waiter,
        )
    )
    try:
        if not arguments:
            resumed = _staged_resume_arguments(executable)
            if resumed is not None:
                arguments = resumed
        if not arguments and production_gui:
            from ..uninstall_gui import launch_uninstall_window
            from ..uninstall_gui_api import (
                SelectionUninstallRuntime,
                UninstallGuiApi,
            )

            layout = InstallLayout.default()
            stager = IndependentUninstallerStager(
                layout,
                executable,
            )
            return launch_uninstall_window(
                UninstallGuiApi(SelectionUninstallRuntime(layout, stager))
            )
        if not arguments:
            options = (choice_provider or _native_choice_provider)()
            if options is None:
                return 0
            layout = InstallLayout.default()
            stager = (
                stager_factory(layout, executable)
                if stager_factory is not None
                else IndependentUninstallerStager(
                    layout,
                    executable,
                )
            )
            result = stager.stage(options)
            _print(result)
            return 0
        args = _parser().parse_args(arguments)
        if args.command == "execute" and production_gui:
            from ..uninstall_gui import launch_uninstall_window
            from ..uninstall_gui_api import (
                ExecuteUninstallRuntime,
                UninstallGuiApi,
            )

            request, workspace = _validate_execute_request(
                args.request,
                args.authorization_token,
                executable,
            )
            _wait_for_launcher_or_raise(
                request,
                WindowsProcessApi().wait,
            )
            return launch_uninstall_window(
                UninstallGuiApi(
                    ExecuteUninstallRuntime(
                        request,
                        workspace,
                        _create_live_service(request, workspace),
                    )
                )
            )
        if args.command == "inspect":
            layout = InstallLayout(
                args.program_root,
                args.data_root,
            )
            _print(build_read_only_plan(layout))
            return 0
        if args.command == "stage":
            if not args.confirm_live_uninstall:
                _error(
                    "error: live uninstall staging is blocked without "
                    "--confirm-live-uninstall; no files or Windows state "
                    "were changed."
                )
                return 3
            layout = InstallLayout.default()
            options = UninstallOptions(
                AppearanceChoice(args.appearance),
                args.keep_config_and_profiles,
                args.keep_logs,
            )
            stager = (
                stager_factory(layout, executable)
                if stager_factory is not None
                else IndependentUninstallerStager(
                    layout,
                    executable,
                )
            )
            _print(stager.stage(options))
            return 0
        request, workspace = _validate_execute_request(
            args.request,
            args.authorization_token,
            executable,
        )
        wait_for_launcher = (
            launcher_waiter if launcher_waiter is not None else WindowsProcessApi().wait
        )
        _wait_for_launcher_or_raise(request, wait_for_launcher)
        service = (
            service_factory(request, workspace)
            if service_factory is not None
            else _create_live_service(request, workspace)
        )
        outcome = service.run()
        payload = outcome.as_dict()
        _print(payload)
        if outcome.result == "completed":
            notify(
                "ThemeScheduler 卸载程序",
                ("卸载主体已完成并验证。临时卸载工作区已安排在本窗口退出后清理。"),
            )
            return 0
        notify(
            "ThemeScheduler 卸载未完整完成",
            (
                f"{outcome.message}\n\n"
                f"事务证据：{outcome.journal_path}\n"
                "请在 15 分钟内双击同一临时目录中的 "
                "Uninstall.exe 重试，或重新运行安装器修复。"
            ),
        )
        return 2
    except (
        InstallContractError,
        UninstallContractError,
        WindowsUninstallError,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        message = f"{type(exc).__name__}: {exc}"
        _error(f"error: {message}")
        if not arguments or (arguments and arguments[0] == "execute"):
            notify(
                "ThemeScheduler 卸载程序",
                f"无法安全继续：{message}",
            )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
