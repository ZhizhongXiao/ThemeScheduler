from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from theme_scheduler.config import AppConfig
from theme_scheduler.scheduler import (
    TaskDefinitionBackup,
    build_task_spec,
)
from theme_scheduler.scheduler_windows import (
    TaskSchedulerBridgeError,
    WindowsTaskSchedulerBackend,
)

BRIDGE = (
    Path(__file__).resolve().parents[2] / "entrypoints" / "task_scheduler_bridge.ps1"
)
EXECUTABLE = (
    r"C:\Users\Example\AppData\Local\Programs"
    r"\ThemeScheduler\ThemeScheduler.exe"
)
USER_ID = "S-1-5-21-1000-1000-1000-1001"


def completed(action: str, **fields) -> subprocess.CompletedProcess[str]:
    payload = {"ok": True, "action": action, **fields}
    return subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps(payload),
        stderr="",
    )


class WindowsSchedulerBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = WindowsTaskSchedulerBackend(BRIDGE)
        self.task = build_task_spec(
            AppConfig.defaults(),
            executable=EXECUTABLE,
            user_id=USER_ID,
        )

    @patch("theme_scheduler.scheduler_windows.subprocess.run")
    def test_probe_is_read_only_and_returns_current_sid(self, run) -> None:
        run.return_value = completed(
            "Probe",
            currentUserId=USER_ID,
            taskExists=False,
            taskSchedulerChanged=False,
        )

        payload = self.backend.probe()

        self.assertEqual(payload["currentUserId"], USER_ID)
        self.assertIsNone(run.call_args.kwargs["input"])
        self.assertIn("-NonInteractive", run.call_args.args[0])
        self.assertEqual(
            run.call_args.kwargs["creationflags"],
            subprocess.CREATE_NO_WINDOW,
        )
        startup_info = run.call_args.kwargs["startupinfo"]
        self.assertTrue(startup_info.dwFlags & subprocess.STARTF_USESHOWWINDOW)
        self.assertEqual(startup_info.wShowWindow, subprocess.SW_HIDE)

    @patch("theme_scheduler.scheduler_windows.subprocess.run")
    def test_read_parses_normalized_definition(self, run) -> None:
        run.return_value = completed(
            "Read",
            exists=True,
            task=self.task.as_dict(),
            taskSchedulerChanged=False,
        )

        actual = self.backend.read()

        self.assertEqual(actual, self.task)

    @patch("theme_scheduler.scheduler_windows.subprocess.run")
    def test_register_sends_strict_json_request(self, run) -> None:
        captured_request = {}

        def inspect_request(command, **kwargs):
            request_path = Path(command[command.index("-RequestPath") + 1])
            captured_request.update(
                json.loads(request_path.read_text(encoding="utf-8-sig"))
            )
            return completed(
                "Register",
                taskPath=self.task.task_path,
                taskSchedulerChanged=True,
            )

        run.side_effect = inspect_request

        self.backend.register(self.task)

        self.assertEqual(captured_request["task"], self.task.as_dict())
        self.assertIsNone(run.call_args.kwargs["input"])

    @patch("theme_scheduler.scheduler_windows.subprocess.run")
    def test_export_restore_and_delete_preserve_full_xml(self, run) -> None:
        backup = TaskDefinitionBackup(
            self.task.task_path,
            "<Task><Principals /></Task>",
            True,
        )
        restored_request = {}
        responses = iter(
            [
                completed(
                    "Export",
                    exists=True,
                    backup=backup.as_dict(),
                    taskSchedulerChanged=False,
                ),
                completed(
                    "Restore",
                    taskPath=self.task.task_path,
                    taskSchedulerChanged=True,
                ),
                completed(
                    "Delete",
                    deleted=True,
                    taskSchedulerChanged=True,
                ),
            ]
        )

        def respond(command, **kwargs):
            if "-RequestPath" in command:
                request_path = Path(command[command.index("-RequestPath") + 1])
                restored_request.update(
                    json.loads(request_path.read_text(encoding="utf-8-sig"))
                )
            return next(responses)

        run.side_effect = respond

        captured = self.backend.capture()
        self.backend.restore(captured)  # type: ignore[arg-type]
        self.backend.delete()

        self.assertEqual(captured, backup)
        self.assertEqual(
            restored_request["backup"]["definitionXml"],
            backup.definition_xml,
        )

    @patch("theme_scheduler.scheduler_windows.subprocess.run")
    def test_run_now_requires_started_instance_without_definition_change(
        self, run
    ) -> None:
        run.return_value = completed(
            "Run",
            taskPath=self.task.task_path,
            instanceGuid="{11111111-2222-3333-4444-555555555555}",
            taskStarted=True,
            taskSchedulerChanged=False,
        )

        result = self.backend.run_now()

        self.assertTrue(result["taskStarted"])

    @patch("theme_scheduler.scheduler_windows.subprocess.run")
    def test_bridge_exit_invalid_json_and_timeout_are_wrapped(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=5, stdout="", stderr="denied"
        )
        with self.assertRaisesRegex(TaskSchedulerBridgeError, "denied"):
            self.backend.probe()

        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not-json", stderr=""
        )
        with self.assertRaisesRegex(TaskSchedulerBridgeError, "invalid JSON"):
            self.backend.probe()

        run.side_effect = subprocess.TimeoutExpired([], 30)
        with self.assertRaisesRegex(TaskSchedulerBridgeError, "timed out"):
            self.backend.probe()

    @patch("theme_scheduler.scheduler_windows.subprocess.run")
    def test_access_denied_preserves_hresult_and_operation_context(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr=("Access is denied. HRESULT:0x80070005 UnauthorizedAccessException"),
        )

        with self.assertRaisesRegex(
            TaskSchedulerBridgeError,
            "0x80070005.*UnauthorizedAccessException",
        ):
            self.backend.register(self.task)

    def test_bridge_script_has_scoped_task_and_future_boundary_guards(self) -> None:
        script = BRIDGE.read_text(encoding="utf-8")

        self.assertIn("$ManagedTaskPath = '\\ThemeScheduler'", script)
        self.assertIn("[Console]::InputEncoding", script)
        self.assertIn("$candidate -le $now", script)
        self.assertIn("$candidate.AddDays(1)", script)
        self.assertIn("RegisterTaskDefinition", script)
        self.assertIn("RegisterTask(", script)
        self.assertIn("DirectoryNotFoundException", script)
        self.assertIn("while ($null -ne $exception)", script)
        self.assertIn("-2147024893", script)
        self.assertNotIn("schtasks.exe", script.casefold())


if __name__ == "__main__":
    unittest.main()
