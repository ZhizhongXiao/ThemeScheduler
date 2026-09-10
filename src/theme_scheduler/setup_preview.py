"""Write-free Setup runtime for source GUI acceptance."""

from __future__ import annotations

import time
from collections.abc import Callable

from .lifecycle import InstallLayout
from .setup_contracts import SetupOptions, SetupPlan
from .setup_service import SetupOutcome


class PreviewSetupRuntime:
    preview_mode = True

    def __init__(
        self,
        *,
        operation: str = "install",
        result: str = "success",
        layout: InstallLayout | None = None,
    ) -> None:
        if operation not in {"install", "reinstall", "upgrade"}:
            raise ValueError("Unsupported Setup preview operation.")
        if result not in {
            "success",
            "success-with-warning",
            "failed",
            "partial",
        }:
            raise ValueError("Unsupported Setup preview result.")
        self.operation = operation
        self.result = result
        self.layout = layout or InstallLayout.default()
        self.launch_count = 0

    def preflight(self) -> SetupPlan:
        retained = self.operation != "install"
        return SetupPlan(
            operation=self.operation,
            target_version="0.1.2-preview",
            installed_version=(None if self.operation == "install" else "0.1.0"),
            install_root=str(self.layout.program_root),
            data_root=str(self.layout.data_root),
            retained_data=retained,
        )

    def default_options(self, plan: SetupPlan) -> SetupOptions:
        del plan
        return SetupOptions(self.operation != "install")

    def launch_installed_app(self) -> None:
        self.launch_count += 1

    @staticmethod
    def _stage(
        progress: Callable[[str], None],
        stage: str,
    ) -> None:
        progress(stage)
        time.sleep(0.3)

    def install(
        self,
        options: SetupOptions,
        *,
        progress: Callable[[str], None],
    ) -> SetupOutcome:
        for stage in (
            "waiting-for-applications",
            "preparing-data",
            "capturing-integration",
            "deploying-files",
            "applying-integration",
        ):
            self._stage(progress, stage)

        failed = self.result in {"failed", "partial"}
        if failed:
            for stage in (
                "rolling-back-integration",
                "rolling-back-files",
                "rolling-back-data",
                "failed",
            ):
                self._stage(progress, stage)
        else:
            for stage in (
                "verifying-installation",
                "committing-files",
            ):
                self._stage(progress, stage)
            self._stage(progress, "completed")

        verified = self.result in {"success", "success-with-warning"}
        rollback_succeeded = None if not failed else self.result == "failed"
        return SetupOutcome(
            result=self.result,
            operation=self.operation,
            version="0.1.2-preview",
            verified=verified,
            rollback_attempted=failed,
            rollback_succeeded=rollback_succeeded,
            retained_data=self.operation != "install",
            immediate_sync_requested=False,
            immediate_sync_succeeded=None,
            message="Safe Setup preview; no Windows state was changed.",
            transaction_id="preview-session",
        )
