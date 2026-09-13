from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from theme_scheduler.cli.scheduler import main
from theme_scheduler.config import AppConfig, ConfigStore
from theme_scheduler.persistence import atomic_write_json
from theme_scheduler.scheduler import (
    TaskDefinitionBackup,
    TaskSpec,
)
from theme_scheduler.storage import UserDataLayout

EXECUTABLE = (
    r"C:\Users\Example\AppData\Local\Programs"
    r"\ThemeScheduler\ThemeScheduler.exe"
)


class SchedulerCliTests(unittest.TestCase):
    def test_plan_is_read_only_and_exposes_frozen_definition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = UserDataLayout(Path(directory))
            ConfigStore(layout.config).initialize(AppConfig.defaults())
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    [
                        "plan",
                        "--data-root",
                        str(layout.root),
                        "--executable",
                        EXECUTABLE,
                        "--user-id",
                        r"DESKTOP-TEST\Example",
                    ]
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertFalse(payload["taskSchedulerAccessed"])
            self.assertFalse(payload["taskSchedulerChanged"])
            self.assertFalse(payload["windowsChanged"])
            self.assertEqual(
                {item["id"] for item in payload["desired"]["triggers"]},
                {
                    "DayPrepare",
                    "DayBoundary",
                    "NightPrepare",
                    "NightBoundary",
                },
            )
            self.assertEqual(payload["desired"]["action"]["arguments"], "auto")

    def test_verify_snapshot_accepts_match_and_reports_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = UserDataLayout(Path(directory))
            ConfigStore(layout.config).initialize(AppConfig.defaults())
            base_args = [
                "--data-root",
                str(layout.root),
                "--executable",
                EXECUTABLE,
                "--user-id",
                r"DESKTOP-TEST\Example",
            ]
            plan_output = io.StringIO()
            with redirect_stdout(plan_output):
                self.assertEqual(main(["plan", *base_args]), 0)
            desired = json.loads(plan_output.getvalue())["desired"]
            snapshot = layout.root / "task.json"
            atomic_write_json(snapshot, desired)

            match_output = io.StringIO()
            with redirect_stdout(match_output):
                match_code = main(
                    ["verify-snapshot", *base_args, "--snapshot", str(snapshot)]
                )

            self.assertEqual(match_code, 0)
            self.assertTrue(json.loads(match_output.getvalue())["inspection"]["valid"])

            desired["settings"]["wakeToRun"] = True
            atomic_write_json(snapshot, desired, force=True)
            drift_output = io.StringIO()
            with redirect_stdout(drift_output):
                drift_code = main(
                    ["verify-snapshot", *base_args, "--snapshot", str(snapshot)]
                )
            self.assertEqual(drift_code, 1)
            self.assertFalse(json.loads(drift_output.getvalue())["inspection"]["valid"])

    def test_invalid_path_fails_without_scheduler_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = UserDataLayout(Path(directory))
            ConfigStore(layout.config).initialize(AppConfig.defaults())
            error = io.StringIO()

            with redirect_stderr(error):
                exit_code = main(
                    [
                        "plan",
                        "--data-root",
                        str(layout.root),
                        "--executable",
                        "ThemeScheduler.exe",
                        "--user-id",
                        "Example",
                    ]
                )

            self.assertEqual(exit_code, 2)
            self.assertIn("absolute Windows path", error.getvalue())

    @patch(
        "theme_scheduler.cli.scheduler.WindowsTaskSchedulerBackend",
        side_effect=AssertionError("backend must not be created"),
    )
    def test_live_commands_without_confirmation_never_open_backend(
        self, backend
    ) -> None:
        for command in ("apply", "enable", "disable", "delete", "run"):
            with self.subTest(command=command):
                error = io.StringIO()
                arguments = [command]
                if command == "apply":
                    arguments.extend(
                        [
                            "--data-root",
                            "unused",
                            "--executable",
                            EXECUTABLE,
                        ]
                    )
                if command == "run":
                    expected_confirmation = "--confirm-live-task-run"
                else:
                    expected_confirmation = "--confirm-live-task-write"
                with redirect_stderr(error):
                    exit_code = main(arguments)
                self.assertEqual(exit_code, 3)
                self.assertIn(expected_confirmation, error.getvalue())
        backend.assert_not_called()

    def test_probe_check_apply_and_delete_use_guarded_backend(self) -> None:
        class Backend:
            def __init__(self) -> None:
                self.task: TaskSpec | None = None

            def probe(self):
                return {
                    "ok": True,
                    "action": "Probe",
                    "currentUserId": r"S-1-5-21-1001",
                    "taskExists": self.task is not None,
                    "taskSchedulerChanged": False,
                }

            def current_user_id(self) -> str:
                return r"S-1-5-21-1001"

            def read(self, task_path: str):
                return self.task

            def register(self, task: TaskSpec) -> None:
                self.task = task

            def capture(self, task_path: str):
                if self.task is None:
                    return None
                return TaskDefinitionBackup(
                    task_path,
                    json.dumps(self.task.as_dict()),
                    self.task.enabled,
                )

            def restore(self, backup: TaskDefinitionBackup) -> None:
                self.task = TaskSpec.from_dict(json.loads(backup.definition_xml))

            def delete(self, task_path: str) -> None:
                self.task = None

            def run_now(self, task_path: str):
                return {
                    "ok": True,
                    "action": "Run",
                    "taskPath": task_path,
                    "instanceGuid": "{11111111-2222-3333-4444-555555555555}",
                    "taskStarted": True,
                    "taskSchedulerChanged": False,
                }

        with tempfile.TemporaryDirectory() as directory:
            layout = UserDataLayout(Path(directory))
            ConfigStore(layout.config).initialize(AppConfig.defaults())
            backend = Backend()
            common = [
                "--data-root",
                str(layout.root),
                "--executable",
                EXECUTABLE,
            ]
            with patch(
                "theme_scheduler.cli.scheduler.WindowsTaskSchedulerBackend",
                return_value=backend,
            ):
                probe_output = io.StringIO()
                with redirect_stdout(probe_output):
                    self.assertEqual(main(["probe"]), 0)
                self.assertFalse(
                    json.loads(probe_output.getvalue())["taskSchedulerChanged"]
                )

                check_output = io.StringIO()
                with redirect_stdout(check_output):
                    self.assertEqual(main(["check", *common]), 1)
                self.assertFalse(
                    json.loads(check_output.getvalue())["inspection"]["exists"]
                )

                apply_output = io.StringIO()
                with redirect_stdout(apply_output):
                    self.assertEqual(
                        main(
                            [
                                "apply",
                                *common,
                                "--confirm-live-task-write",
                            ]
                        ),
                        0,
                    )
                self.assertTrue(
                    json.loads(apply_output.getvalue())["taskSchedulerChanged"]
                )

                valid_output = io.StringIO()
                with redirect_stdout(valid_output):
                    self.assertEqual(main(["check", *common]), 0)
                self.assertTrue(
                    json.loads(valid_output.getvalue())["inspection"]["valid"]
                )

                run_output = io.StringIO()
                with redirect_stdout(run_output):
                    self.assertEqual(
                        main(["run", "--confirm-live-task-run"]),
                        0,
                    )
                self.assertTrue(
                    json.loads(run_output.getvalue())["result"]["taskStarted"]
                )

                delete_output = io.StringIO()
                with redirect_stdout(delete_output):
                    self.assertEqual(
                        main(
                            [
                                "delete",
                                "--confirm-live-task-write",
                            ]
                        ),
                        0,
                    )
                self.assertIsNone(backend.task)


if __name__ == "__main__":
    unittest.main()
