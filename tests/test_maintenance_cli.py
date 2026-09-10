from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from theme_scheduler.cli.maintenance import main


class MaintenanceCliSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data_root = Path(self.temporary.name) / "ThemeScheduler"

    def test_capture_without_confirmation_creates_nothing(self) -> None:
        stderr = StringIO()

        with redirect_stderr(stderr):
            code = main(
                [
                    "capture-backup",
                    "--data-root",
                    str(self.data_root),
                    "--created-by-version",
                    "0.1.0",
                    "--windows-build",
                    "26200",
                ]
            )

        self.assertEqual(code, 3)
        self.assertFalse(self.data_root.exists())
        self.assertIn(
            "no data or Windows state was changed",
            stderr.getvalue(),
        )

    @patch("theme_scheduler.cli.maintenance.WindowsNamedMutexLock")
    def test_restore_without_confirmation_never_opens_lock(self, lock) -> None:
        stderr = StringIO()

        with redirect_stderr(stderr):
            code = main(
                [
                    "restore",
                    "--data-root",
                    str(self.data_root),
                ]
            )

        self.assertEqual(code, 3)
        self.assertFalse(self.data_root.exists())
        lock.assert_not_called()

    @patch("theme_scheduler.cli.maintenance.capture_install_backup")
    def test_confirmed_capture_reports_data_only_change(self, capture) -> None:
        backup = Mock()
        backup.as_dict.return_value = {
            "kind": "themescheduler.install-backup",
        }
        capture.return_value = backup
        stdout = StringIO()

        with redirect_stdout(stdout):
            code = main(
                [
                    "capture-backup",
                    "--data-root",
                    str(self.data_root),
                    "--created-by-version",
                    "0.1.0",
                    "--windows-build",
                    "26200",
                    "--confirm-backup-data-write",
                ]
            )

        self.assertEqual(code, 0)
        self.assertIn('"dataChanged": true', stdout.getvalue())
        self.assertIn('"windowsChanged": false', stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
