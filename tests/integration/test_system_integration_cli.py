from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from theme_scheduler.cli.system_integration import main
from theme_scheduler.lifecycle import InstallLayout
from theme_scheduler.system_integration import ShortcutPlan


class SystemIntegrationCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.program_root = root / "Programs" / "ThemeScheduler"
        self.data_root = root / "UserData" / "ThemeScheduler"
        self.snapshot = root / "backup.json"
        self.config = root / "config.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def layout_arguments(self) -> list[str]:
        return [
            "--program-root",
            str(self.program_root),
            "--data-root",
            str(self.data_root),
        ]

    @patch("theme_scheduler.cli.system_integration._components")
    def test_live_apply_without_confirmation_never_opens_adapters(
        self, components
    ) -> None:
        stderr = StringIO()

        with redirect_stderr(stderr):
            code = main(
                [
                    "apply",
                    *self.layout_arguments(),
                    "--snapshot",
                    str(self.snapshot),
                    "--config",
                    str(self.config),
                    "--publisher",
                    "ThemeScheduler",
                    "--no-desktop-shortcut",
                ]
            )

        self.assertEqual(code, 3)
        self.assertIn("Windows was not changed", stderr.getvalue())
        components.assert_not_called()

    @patch("theme_scheduler.cli.system_integration._components")
    def test_live_restore_without_confirmation_never_opens_adapters(
        self, components
    ) -> None:
        stderr = StringIO()

        with redirect_stderr(stderr):
            code = main(
                [
                    "restore",
                    *self.layout_arguments(),
                    "--snapshot",
                    str(self.snapshot),
                ]
            )

        self.assertEqual(code, 3)
        components.assert_not_called()

    @patch("theme_scheduler.cli.system_integration.capture_system_integration")
    @patch("theme_scheduler.cli.system_integration._components")
    def test_capture_writes_only_explicit_snapshot(self, components, capture) -> None:
        layout = InstallLayout(self.program_root, self.data_root)
        plan = ShortcutPlan.create(
            layout,
            programs_folder=Path(self.temporary.name) / "Start Menu" / "Programs",
            desktop_folder=Path(self.temporary.name) / "Desktop",
            desktop_enabled=False,
        )
        components.return_value = (
            plan,
            Mock(),
            Mock(),
            Mock(),
        )
        backup = Mock()
        backup.registration = None
        backup.shortcuts = (Mock(), Mock())
        backup.task_before = None
        capture.return_value = backup
        stdout = StringIO()

        with redirect_stdout(stdout):
            code = main(
                [
                    "capture",
                    *self.layout_arguments(),
                    "--snapshot",
                    str(self.snapshot),
                ]
            )

        self.assertEqual(code, 0)
        backup.save.assert_called_once_with(self.snapshot)
        self.assertIn('"windowsChanged": false', stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
