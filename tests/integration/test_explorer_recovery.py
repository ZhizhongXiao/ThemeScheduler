from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from theme_scheduler.explorer_recovery import (
    ExplorerRecoveryError,
    WindowsExplorerRecoveryBackend,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "entrypoints" / "explorer_recovery_bridge.ps1"


class ExplorerRecoveryRiskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.bridge = Path(self.temporary.name) / "explorer_recovery_bridge.ps1"
        self.bridge.write_text("# test bridge", encoding="utf-8")
        self.backend = WindowsExplorerRecoveryBackend()

    def _run_with(self, completed: subprocess.CompletedProcess[str]):
        with (
            patch("theme_scheduler.explorer_recovery.os.name", "nt"),
            patch(
                "theme_scheduler.explorer_recovery.resource_path",
                return_value=self.bridge,
            ),
            patch(
                "theme_scheduler.explorer_recovery.subprocess.run",
                return_value=completed,
            ) as run,
        ):
            result = self.backend.restart()
        return result, run

    def test_success_is_hidden_and_requires_scoped_recovery_result(self) -> None:
        payload = {
            "ok": True,
            "action": "Restart",
            "recovered": True,
            "sessionId": 1,
            "oldProcessId": 10,
            "newProcessId": 11,
        }
        result, run = self._run_with(
            subprocess.CompletedProcess([], 0, json.dumps(payload), "")
        )

        self.assertEqual(result["newProcessId"], 11)
        command = run.call_args.args[0]
        self.assertIn("-NonInteractive", command)
        self.assertIn(str(self.bridge), command)
        self.assertEqual(
            run.call_args.kwargs["creationflags"],
            subprocess.CREATE_NO_WINDOW,
        )

    def test_timeout_launch_failure_and_wrong_session_are_distinct_failures(
        self,
    ) -> None:
        with (
            patch("theme_scheduler.explorer_recovery.os.name", "nt"),
            patch(
                "theme_scheduler.explorer_recovery.resource_path",
                return_value=self.bridge,
            ),
            patch(
                "theme_scheduler.explorer_recovery.subprocess.run",
                side_effect=subprocess.TimeoutExpired([], 30),
            ),
            self.assertRaisesRegex(ExplorerRecoveryError, "timed out"),
        ):
            self.backend.restart()

        for message in (
            "Start-Process failed to launch explorer.exe",
            "shell window did not belong to Explorer in the current session",
        ):
            with self.subTest(message=message):
                completed = subprocess.CompletedProcess([], 1, "", message)
                with self.assertRaisesRegex(ExplorerRecoveryError, message):
                    self._run_with(completed)

    def test_invalid_json_and_unrecovered_payload_are_rejected(self) -> None:
        with self.assertRaisesRegex(ExplorerRecoveryError, "invalid JSON"):
            self._run_with(subprocess.CompletedProcess([], 0, "not-json", ""))

        payload = {"ok": True, "action": "Restart", "recovered": False}
        with self.assertRaisesRegex(ExplorerRecoveryError, "unexpected result"):
            self._run_with(subprocess.CompletedProcess([], 0, json.dumps(payload), ""))

    def test_bridge_only_stops_the_shell_window_process_in_current_session(
        self,
    ) -> None:
        script = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("GetShellWindow", script)
        self.assertIn("$process.SessionId -ne $currentSession", script)
        self.assertIn("Stop-Process -Id $oldProcessId", script)
        self.assertNotIn("Stop-Process -Name explorer", script)
        self.assertIn("Start-Process -FilePath $explorerPath", script)
        self.assertIn("Explorer did not restore its shell window", script)
        self.assertIn("Explorer shell PID did not change", script)


if __name__ == "__main__":
    unittest.main()
