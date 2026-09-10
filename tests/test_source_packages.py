from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"

from theme_scheduler.automation import AutoRunner, WindowsAutoBackend
from theme_scheduler.lifecycle.deployment import (
    DeploymentJournal,
    FileDeploymentService,
)
from theme_scheduler.scheduler import (
    TaskDefinitionBackup,
    TaskSpec,
    build_task_spec,
)
from theme_scheduler.workbench import GuiApi, ShellActions


class SourcePackageBoundaryTests(unittest.TestCase):
    def test_public_types_live_in_focused_modules(self) -> None:
        expected_modules = {
            AutoRunner: "theme_scheduler.automation.runner",
            WindowsAutoBackend: "theme_scheduler.automation.backend",
            DeploymentJournal: ("theme_scheduler.lifecycle.deployment.journal"),
            FileDeploymentService: ("theme_scheduler.lifecycle.deployment.service"),
            TaskDefinitionBackup: "theme_scheduler.scheduler.mutation",
            TaskSpec: "theme_scheduler.scheduler.models",
            GuiApi: "theme_scheduler.workbench.api",
            ShellActions: "theme_scheduler.workbench.shell",
        }
        for public_type, module_name in expected_modules.items():
            with self.subTest(public_type=public_type.__name__):
                self.assertEqual(public_type.__module__, module_name)

    def test_retired_flat_modules_are_absent(self) -> None:
        package_root = SOURCE_ROOT / "theme_scheduler"
        for name in (
            "auto_service.py",
            "deployment.py",
            "gui_api.py",
            "install_contracts.py",
            "scheduler.py",
        ):
            with self.subTest(name=name):
                self.assertFalse((package_root / name).exists())

    def test_scheduler_builder_lives_in_specification_module(self) -> None:
        self.assertEqual(
            build_task_spec.__module__,
            "theme_scheduler.scheduler.specification",
        )


if __name__ == "__main__":
    unittest.main()
