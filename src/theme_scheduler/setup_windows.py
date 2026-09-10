"""Windows composition root for the single-file Setup program."""

from __future__ import annotations

import math
import os
import subprocess
import sys
from pathlib import Path

from .core import mutex_name_for_data_root
from .execution_lock import WindowsNamedMutexLock
from .lifecycle import (
    InstalledAppRegistration,
    InstallLayout,
    PayloadManifest,
)
from .lifecycle.deployment import FileDeploymentService
from .persistence import load_json_object
from .protocol_registration_windows import (
    WindowsNotificationProtocolBackend,
)
from .resources import resource_path
from .scheduler_windows import WindowsTaskSchedulerBackend
from .setup_contracts import SetupOptions, SetupPlan
from .setup_data import SetupDataService
from .setup_service import (
    ProgressReporter,
    SetupOutcome,
    SetupService,
    report_progress,
)
from .storage import UserDataLayout
from .system_integration import (
    CurrentUserIntegrationService,
    ShortcutPlan,
)
from .system_integration_windows import (
    WindowsInstalledAppRegistryBackend,
    WindowsKnownFolderReader,
    WindowsShortcutBackend,
)
from .uninstall_service import lifecycle_mutex_name_for_program_root
from .uninstall_windows import WindowsInstalledProcessGuard

PUBLISHER = "ThemeScheduler"


class WindowsSetupRuntime:
    """Resolve trusted bundled resources and execute one guarded transaction."""

    def __init__(
        self,
        *,
        layout: InstallLayout | None = None,
        payload_root: Path | None = None,
        manifest_path: Path | None = None,
    ) -> None:
        if os.name != "nt":
            raise OSError("ThemeScheduler Setup requires Windows.")
        self.layout = layout or InstallLayout.default()
        self.payload_root = (
            Path(payload_root) if payload_root is not None else resource_path("payload")
        ).resolve(strict=False)
        self.manifest_path = (
            Path(manifest_path)
            if manifest_path is not None
            else resource_path("payload-manifest.json")
        ).resolve(strict=False)

    def manifest(self) -> PayloadManifest:
        manifest = PayloadManifest.from_dict(load_json_object(self.manifest_path))
        manifest.verify_tree(self.payload_root)
        return manifest

    def _components(
        self,
        manifest: PayloadManifest,
        *,
        desktop_shortcut: bool,
    ):
        known = WindowsKnownFolderReader()
        shortcut_plan = ShortcutPlan.create(
            self.layout,
            programs_folder=known.programs(),
            desktop_folder=known.desktop(),
            desktop_enabled=desktop_shortcut,
        )
        registry = WindowsInstalledAppRegistryBackend()
        shortcuts = WindowsShortcutBackend(shortcut_plan.managed_paths)
        tasks = WindowsTaskSchedulerBackend()
        integration = CurrentUserIntegrationService(
            self.layout,
            registry,
            shortcuts,
            tasks,
            WindowsNotificationProtocolBackend(),
        )
        service = SetupService(
            self.layout,
            self.payload_root,
            manifest,
            deployment=FileDeploymentService(self.layout),
            data=SetupDataService(UserDataLayout(self.layout.data_root)),
            integration=integration,
        )
        return service, tasks, shortcut_plan

    def preflight(self) -> SetupPlan:
        manifest = self.manifest()
        service, _tasks, _shortcut_plan = self._components(
            manifest,
            desktop_shortcut=False,
        )
        plan = service.plan()
        if plan.retained_data:
            service.data.validate_retained()
        return plan

    def default_options(self, plan: SetupPlan) -> SetupOptions:
        if plan.operation == "install":
            return SetupOptions.defaults()
        known = WindowsKnownFolderReader()
        shortcuts = ShortcutPlan.create(
            self.layout,
            programs_folder=known.programs(),
            desktop_folder=known.desktop(),
            desktop_enabled=False,
        )
        return SetupOptions(shortcuts.desktop.path.is_file())

    def launch_installed_app(self) -> None:
        executable = self.layout.executable.resolve(strict=False)
        if not executable.is_file():
            raise FileNotFoundError(
                f"Installed ThemeScheduler executable is missing: {executable}"
            )
        subprocess.Popen(
            [str(executable)],
            cwd=str(executable.parent),
            close_fds=True,
        )

    @staticmethod
    def _estimated_size_kib(manifest: PayloadManifest) -> int:
        return max(
            1,
            math.ceil(sum(item.size for item in manifest.files) / 1024),
        )

    def install(
        self,
        options: SetupOptions,
        *,
        progress: ProgressReporter | None = None,
    ) -> SetupOutcome:
        manifest = self.manifest()
        service, tasks, shortcut_plan = self._components(
            manifest,
            desktop_shortcut=options.desktop_shortcut,
        )
        registration = InstalledAppRegistration.create(
            self.layout,
            version=manifest.version,
            publisher=PUBLISHER,
            estimated_size_kib=self._estimated_size_kib(manifest),
        )
        lifecycle_lock = WindowsNamedMutexLock(
            lifecycle_mutex_name_for_program_root(self.layout.program_root)
        )
        auto_lock = WindowsNamedMutexLock(
            mutex_name_for_data_root(self.layout.data_root)
        )
        report_progress(progress, "waiting-for-applications")
        with lifecycle_lock:
            if self.layout.executable.exists():
                WindowsInstalledProcessGuard().ensure_idle(self.layout.executable)
            with auto_lock:
                return service.install(
                    options,
                    registration=registration,
                    shortcut_plan=shortcut_plan,
                    user_id=tasks.current_user_id(),
                    windows_build=str(sys.getwindowsversion().build),
                    progress=progress,
                )
