"""Transactional Stage 8.6 install/reinstall/upgrade coordinator."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigStore
from .errors import ThemeSchedulerRuntimeError
from .lifecycle import (
    InstalledAppRegistration,
    InstallLayout,
    PayloadManifest,
)
from .lifecycle.deployment import (
    DeploymentOutcome,
    FileDeploymentService,
    verify_active_payload,
)
from .scheduler import TaskSchedulerBackend
from .setup_contracts import (
    SetupOptions,
    SetupPlan,
    create_setup_plan,
)
from .setup_data import SetupDataOutcome, SetupDataService
from .system_integration import (
    CurrentUserIntegrationService,
    InstalledAppRegistryBackend,
    ShortcutBackend,
    ShortcutPlan,
)
from .system_integration_backup import (
    SystemIntegrationBackup,
    SystemIntegrationRestoreOutcome,
    capture_system_integration,
    restore_system_integration,
)


class SetupError(ThemeSchedulerRuntimeError):
    """Raised internally when a coordinated install step cannot continue."""


@dataclass(frozen=True)
class SetupOutcome:
    result: str
    operation: str
    version: str
    verified: bool
    rollback_attempted: bool
    rollback_succeeded: bool | None
    retained_data: bool | None
    immediate_sync_requested: bool
    immediate_sync_succeeded: bool | None
    message: str
    transaction_id: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "result": self.result,
            "operation": self.operation,
            "version": self.version,
            "verified": self.verified,
            "rollbackAttempted": self.rollback_attempted,
            "rollbackSucceeded": self.rollback_succeeded,
            "retainedData": self.retained_data,
            "immediateSyncRequested": self.immediate_sync_requested,
            "immediateSyncSucceeded": self.immediate_sync_succeeded,
            "message": self.message,
            "transactionId": self.transaction_id,
        }


IntegrationCapture = Callable[
    [
        InstalledAppRegistryBackend,
        ShortcutBackend,
        TaskSchedulerBackend,
        tuple[Path, ...],
    ],
    SystemIntegrationBackup,
]
IntegrationRestore = Callable[
    [
        SystemIntegrationBackup,
        InstalledAppRegistryBackend,
        ShortcutBackend,
        TaskSchedulerBackend,
    ],
    SystemIntegrationRestoreOutcome,
]
ProgressReporter = Callable[[str], None]


def report_progress(
    reporter: ProgressReporter | None,
    stage: str,
) -> None:
    if reporter is None:
        return
    try:  # noqa: SIM105 - Progress reporting must never replace an installation outcome.
        reporter(stage)
    except Exception:
        # Presentation progress must never become an installation failure.
        pass


class SetupService:
    """Commit files only after data and current-user integration verify."""

    def __init__(
        self,
        layout: InstallLayout,
        payload_root: Path,
        manifest: PayloadManifest,
        *,
        deployment: FileDeploymentService,
        data: SetupDataService,
        integration: CurrentUserIntegrationService,
        capture_integration: IntegrationCapture = capture_system_integration,
        restore_integration: IntegrationRestore = restore_system_integration,
    ) -> None:
        self.layout = layout
        self.payload_root = Path(payload_root)
        self.manifest = manifest
        self.deployment = deployment
        self.data = data
        self.integration = integration
        self.capture_integration = capture_integration
        self.restore_integration = restore_integration

    def plan(self) -> SetupPlan:
        self.manifest.verify_tree(self.payload_root)
        return create_setup_plan(self.layout, self.manifest)

    def install(
        self,
        options: SetupOptions,
        *,
        registration: InstalledAppRegistration,
        shortcut_plan: ShortcutPlan,
        user_id: str,
        windows_build: str,
        transaction_id: str | None = None,
        progress: ProgressReporter | None = None,
    ) -> SetupOutcome:
        plan = self.plan()
        data_outcome: SetupDataOutcome | None = None
        integration_backup: SystemIntegrationBackup | None = None
        deployment_outcome: DeploymentOutcome | None = None
        integration_started = False
        try:
            report_progress(progress, "preparing-data")
            data_outcome = self.data.prepare(
                options,
                target_version=self.manifest.version,
                windows_build=windows_build,
            )
            report_progress(progress, "capturing-integration")
            integration_backup = self.capture_integration(
                self.integration.registry,
                self.integration.shortcuts,
                self.integration.tasks,
                shortcut_plan.managed_paths,
            )
            report_progress(progress, "deploying-files")
            deployment_outcome = self.deployment.deploy(
                self.payload_root,
                self.manifest,
                operation=plan.operation,
                from_version=plan.installed_version,
                transaction_id=transaction_id,
                defer_completion=True,
            )
            if (
                deployment_outcome.status != "committed"
                or not deployment_outcome.active_verified
            ):
                raise SetupError(deployment_outcome.message)

            integration_started = True
            report_progress(progress, "applying-integration")
            integration_outcome = self.integration.apply(
                registration=registration,
                shortcut_plan=shortcut_plan,
                config=ConfigStore(self.data.layout.config).load(),
                user_id=user_id,
            )
            if not integration_outcome.verified:
                raise SetupError(integration_outcome.message)
            report_progress(progress, "verifying-installation")
            verify_active_payload(self.layout, self.manifest)
            report_progress(progress, "committing-files")
            completed = self.deployment.complete_committed(
                self.manifest,
                transaction_id=deployment_outcome.transaction_id,
            )
            if completed.status != "completed":
                raise SetupError(completed.message)

            message = "ThemeScheduler was installed and verified."
            report_progress(progress, "completed")
            return SetupOutcome(
                "success",
                plan.operation,
                self.manifest.version,
                True,
                False,
                None,
                data_outcome.retained,
                False,
                None,
                message,
                completed.transaction_id,
            )
        except Exception as exc:
            rollback_results: list[bool] = []
            if integration_started and integration_backup is not None:
                report_progress(progress, "rolling-back-integration")
                try:
                    restored = self.restore_integration(
                        integration_backup,
                        self.integration.registry,
                        self.integration.shortcuts,
                        self.integration.tasks,
                    )
                    rollback_results.append(bool(restored.verified))
                except Exception:
                    rollback_results.append(False)
            if (
                deployment_outcome is not None
                and deployment_outcome.status == "committed"
            ):
                report_progress(progress, "rolling-back-files")
                try:
                    rolled_back = self.deployment.rollback_committed(
                        self.manifest,
                        transaction_id=deployment_outcome.transaction_id,
                        reason=f"Setup failed after deployment: {exc}",
                    )
                    rollback_results.append(
                        rolled_back.status == "rolled-back"
                        and bool(rolled_back.rollback_succeeded)
                    )
                except Exception:
                    rollback_results.append(False)
            if data_outcome is not None and not data_outcome.retained:
                report_progress(progress, "rolling-back-data")
                try:
                    self.data.rollback(data_outcome)
                    rollback_results.append(
                        not any(path.exists() for path in data_outcome.created_files)
                    )
                except Exception:
                    rollback_results.append(False)
            rollback_attempted = bool(rollback_results)
            rollback_succeeded = all(rollback_results) if rollback_results else None
            report_progress(progress, "failed")
            return SetupOutcome(
                ("failed" if rollback_succeeded is not False else "partial"),
                plan.operation,
                self.manifest.version,
                False,
                rollback_attempted,
                rollback_succeeded,
                (data_outcome.retained if data_outcome is not None else None),
                False,
                None,
                f"Setup failed: {type(exc).__name__}: {exc}"[:500],
                (
                    deployment_outcome.transaction_id
                    if deployment_outcome is not None
                    else transaction_id
                ),
            )
