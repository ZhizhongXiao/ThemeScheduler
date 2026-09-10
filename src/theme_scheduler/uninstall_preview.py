"""Write-free runtime for the classic uninstaller safety preview."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .lifecycle import InstallLayout
from .uninstall_contracts import (
    AppearanceChoice,
    UninstallOptions,
    build_uninstall_plan,
)


class PreviewUninstallRuntime:
    preview_mode = True
    mode = "selection"

    def __init__(
        self,
        *,
        result: str = "completed",
        layout: InstallLayout | None = None,
    ) -> None:
        if result not in {"completed", "partial"}:
            raise ValueError("Unsupported uninstall preview result.")
        self.result = result
        self.layout = layout or InstallLayout.default()

    @staticmethod
    def default_options() -> UninstallOptions:
        return UninstallOptions(
            AppearanceChoice.RESTORE,
            False,
            False,
        )

    def get_status(self) -> Mapping[str, Any]:
        options = self.default_options()
        return {
            "mode": self.mode,
            "programRoot": str(self.layout.program_root),
            "dataRoot": str(self.layout.data_root),
            "options": options.as_dict(),
            "plan": build_uninstall_plan(
                self.layout,
                options,
            ).as_dict(),
            "transactionId": "uninstall-preview",
            "autoStart": False,
        }

    @staticmethod
    def _stage(
        progress: Callable[[str], None],
        stage: str,
    ) -> None:
        progress(stage)
        time.sleep(0.25)

    def uninstall(
        self,
        options: UninstallOptions,
        *,
        progress: Callable[[str], None],
    ) -> Mapping[str, Any]:
        stages = (
            "waiting-for-launcher",
            "task-removed",
            "appearance-handled",
            "shortcuts-removed",
            "registration-removed",
            "program-root-removed",
            "user-data-cleaned",
        )
        completed: list[str] = []
        for stage in stages:
            self._stage(progress, stage)
            if stage.endswith("-removed") or stage in {
                "appearance-handled",
                "user-data-cleaned",
            }:
                completed.append(stage)
            if self.result == "partial" and stage == "registration-removed":
                self._stage(progress, "failed")
                return {
                    "result": "partial",
                    "completedSteps": completed,
                    "appearanceRestored": (
                        options.appearance is AppearanceChoice.RESTORE
                    ),
                    "taskRemoved": True,
                    "shortcutsRemoved": True,
                    "registrationRemoved": True,
                    "programRootRemoved": False,
                    "dataCleaned": False,
                    "selfCleanupScheduled": False,
                    "journalPath": str(
                        Path(self.layout.data_root) / "preview-uninstall-journal.json"
                    ),
                    "residualPaths": [
                        str(self.layout.program_root),
                    ],
                    "verified": False,
                    "windowsChanged": False,
                    "filesChanged": False,
                    "message": (
                        "Safe partial-result preview; no files or Windows "
                        "state were changed."
                    ),
                    "options": options.as_dict(),
                }
        self._stage(progress, "self-cleanup-scheduled")
        completed.append("self-cleanup-scheduled")
        self._stage(progress, "completed")
        return {
            "result": "completed",
            "completedSteps": completed,
            "appearanceRestored": (options.appearance is AppearanceChoice.RESTORE),
            "taskRemoved": True,
            "shortcutsRemoved": True,
            "registrationRemoved": True,
            "programRootRemoved": True,
            "dataCleaned": True,
            "selfCleanupScheduled": True,
            "journalPath": str(
                Path(self.layout.data_root) / "preview-uninstall-journal.json"
            ),
            "residualPaths": [],
            "verified": True,
            "windowsChanged": False,
            "filesChanged": False,
            "message": (
                "Safe completed-result preview; no files or Windows state were changed."
            ),
            "options": options.as_dict(),
        }
