from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from theme_scheduler.cli.control import main as control_cli_main
from theme_scheduler.state import AppState, StateStore


class FakeLock:
    def acquire(self) -> bool:
        return True

    def release(self) -> None:
        return None


class ControlCliTests(unittest.TestCase):
    def test_write_commands_require_confirmation_before_data_access(self) -> None:
        for command in ("pause", "resume"):
            with (
                self.subTest(command=command),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory) / "absent"
                with redirect_stderr(StringIO()):
                    exit_code = control_cli_main([command, "--data-root", str(root)])
                self.assertEqual(exit_code, 3)
                self.assertFalse(root.exists())

    def test_status_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "data"
            StateStore(root / "state.json").initialize(AppState.initial())
            before = (root / "state.json").read_bytes()
            output = StringIO()

            with redirect_stdout(output):
                exit_code = control_cli_main(["status", "--data-root", str(root)])

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertFalse(payload["state"]["paused"])
            self.assertFalse(payload["dataChanged"])
            self.assertFalse(payload["windowsChanged"])
            self.assertFalse(payload["taskSchedulerAccessed"])
            self.assertEqual((root / "state.json").read_bytes(), before)

    def test_pause_uses_state_only_service_and_never_scheduler(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "data"
            StateStore(root / "state.json").initialize(AppState.initial())
            output = StringIO()

            with (
                patch(
                    "theme_scheduler.cli.control.WindowsNamedMutexLock",
                    return_value=FakeLock(),
                ),
                redirect_stdout(output),
            ):
                exit_code = control_cli_main(
                    [
                        "pause",
                        "--data-root",
                        str(root),
                        "--confirm-state-write",
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertTrue(StateStore(root / "state.json").load().paused)
            self.assertFalse(payload["windowsChanged"])
            self.assertFalse(payload["taskSchedulerAccessed"])
            self.assertFalse(payload["taskSchedulerChanged"])

    def test_sync_command_is_not_exposed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "absent"
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                control_cli_main(
                    [
                        "sync",
                        "--data-root",
                        str(root),
                    ]
                )
            self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
