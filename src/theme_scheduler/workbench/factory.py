"""Windows production composition root for the workbench API."""

from __future__ import annotations

from pathlib import Path

from ..accent_theme import (
    ThemeVisualState,
    WindowsThemeApplyBackend,
    read_visual_state,
)
from ..automation import WindowsAutoBackend
from ..core import mutex_name_for_data_root
from ..execution_lock import WindowsNamedMutexLock
from ..health_service import HealthService
from ..lifecycle import InstallLayout
from ..maintenance_service import MaintenanceService
from ..manual_appearance_service import ManualAppearanceService
from ..notification_identity_service import (
    NotificationIdentityRepairService,
)
from ..notifications_windows import WindowsNotificationBackend
from ..protocol_registration_windows import (
    WindowsNotificationProtocolBackend,
)
from ..scheduler_windows import WindowsTaskSchedulerBackend
from ..storage import UserDataLayout
from ..system_integration import ShortcutPlan
from ..system_integration_windows import (
    WindowsKnownFolderReader,
    WindowsShortcutBackend,
)
from ..windows_identity import read_current_process_identity
from .api import GuiApi
from .shell import ShellActions


def create_live_gui_api(
    layout: UserDataLayout,
    executable: Path,
    *,
    allow_live_writes: bool,
    allow_system_reads: bool = False,
) -> GuiApi:
    scheduler = WindowsTaskSchedulerBackend()
    mutex_name = mutex_name_for_data_root(layout.root)

    def lock_factory() -> WindowsNamedMutexLock:
        return WindowsNamedMutexLock(mutex_name)

    def maintenance_service_factory() -> MaintenanceService:
        return MaintenanceService(
            layout,
            lock_factory(),
        )

    def current_appearance_reader() -> ThemeVisualState:
        backend = WindowsThemeApplyBackend()
        active_theme = backend.current_theme_path()
        return read_visual_state(active_theme.read_bytes())

    if not allow_live_writes:
        return GuiApi(
            layout,
            executable=executable,
            scheduler_backend=scheduler,
            lock_factory=lock_factory,
            maintenance_service_factory=maintenance_service_factory,
            current_appearance_reader=(
                current_appearance_reader if allow_system_reads else None
            ),
            shell_actions=ShellActions(layout),
            allow_live_writes=False,
        )

    install_layout = InstallLayout(
        executable.parent.parent,
        layout.root,
    )
    known_folders = WindowsKnownFolderReader()
    shortcut_plan = ShortcutPlan.create(
        install_layout,
        programs_folder=known_folders.programs(),
        desktop_folder=known_folders.desktop(),
        desktop_enabled=False,
    )

    def health_service_factory() -> HealthService:
        shortcuts = WindowsShortcutBackend(shortcut_plan.managed_paths)
        return HealthService(
            layout,
            install_layout,
            scheduler,
            shortcuts,
            shortcut_plan,
            WindowsNotificationProtocolBackend(),
            user_id=scheduler.current_user_id(),
            windows_probe=WindowsAutoBackend(layout).probe,
            notification_probe=WindowsNotificationBackend().probe,
            process_identity_reader=read_current_process_identity,
        )

    def identity_repair_service_factory():
        return NotificationIdentityRepairService(
            layout,
            lock_factory(),
            WindowsShortcutBackend(shortcut_plan.managed_paths),
            shortcut_plan,
            WindowsNotificationProtocolBackend(),
        )

    def manual_appearance_service_factory() -> ManualAppearanceService:
        return ManualAppearanceService(
            layout,
            lock_factory(),
            WindowsAutoBackend(layout),
        )

    return GuiApi(
        layout,
        executable=executable,
        scheduler_backend=scheduler,
        lock_factory=lock_factory,
        maintenance_service_factory=maintenance_service_factory,
        health_service_factory=health_service_factory,
        identity_repair_service_factory=(identity_repair_service_factory),
        manual_appearance_service_factory=(manual_appearance_service_factory),
        current_appearance_reader=current_appearance_reader,
        allow_live_writes=allow_live_writes,
    )
