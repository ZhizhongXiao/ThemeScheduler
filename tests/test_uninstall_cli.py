from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from theme_scheduler.cli.uninstall import (
    _validate_execute_request,
    build_read_only_plan,
)
from theme_scheduler.cli.uninstall import (
    main as uninstall_main,
)
from theme_scheduler.lifecycle import (
    InstallLayout,
    file_sha256,
)
from theme_scheduler.uninstall_contracts import (
    AppearanceChoice,
    UninstallOptions,
    UninstallRequest,
)


class IndependentUninstallerShellTests(unittest.TestCase):
    def _layout(self, root: Path) -> InstallLayout:
        return InstallLayout(
            (root / "Programs" / "ThemeScheduler").resolve(),
            (root / "ThemeScheduler").resolve(),
        )

    def test_plan_is_explicitly_read_only_and_lists_owned_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(Path(directory))
            before = set(Path(directory).rglob("*"))

            plan = build_read_only_plan(layout)

            self.assertTrue(plan["liveUninstallAvailable"])
            self.assertFalse(plan["windowsChanged"])
            self.assertFalse(plan["filesChanged"])
            self.assertEqual(plan["programRoot"], str(layout.program_root))
            self.assertEqual(
                plan["targets"]["scheduledTask"],  # type: ignore[index]
                r"\ThemeScheduler",
            )
            self.assertEqual(set(Path(directory).rglob("*")), before)

    def test_cancelled_interactive_uninstall_has_no_side_effect(self) -> None:
        notices: list[tuple[str, str]] = []

        result = uninstall_main(
            [],
            notifier=lambda title, message: notices.append((title, message)),
            choice_provider=lambda: None,
        )

        self.assertEqual(result, 0)
        self.assertEqual(notices, [])

    def test_windowed_process_tolerates_missing_standard_streams(self) -> None:
        class Stager:
            def stage(self, options):
                return {
                    "launched": True,
                    "processId": 123,
                }

        with (
            patch.object(sys, "stdout", None),
            patch.object(
                sys,
                "stderr",
                None,
            ),
        ):
            result = uninstall_main(
                [],
                choice_provider=lambda: UninstallOptions(
                    AppearanceChoice.KEEP,
                    True,
                    True,
                ),
                stager_factory=lambda layout, executable: Stager(),
                current_executable=Path("Uninstall.exe"),
            )
            blocked = uninstall_main(
                [
                    "stage",
                    "--appearance",
                    AppearanceChoice.KEEP.value,
                ]
            )

        self.assertEqual(result, 0)
        self.assertEqual(blocked, 3)

    def test_interactive_confirmation_only_stages_temporary_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            local = root / "Local"
            layout = InstallLayout(
                local / "Programs" / "ThemeScheduler",
                local / "ThemeScheduler",
            )
            layout.maintenance.mkdir(parents=True)
            layout.uninstaller.write_bytes(b"exe")
            calls = []
            notices: list[tuple[str, str]] = []

            class Stager:
                def stage(self, options):
                    calls.append(options)
                    return {
                        "launched": True,
                        "processId": 123,
                        "windowsChanged": False,
                        "installedFilesChanged": False,
                    }

            options = UninstallOptions(
                AppearanceChoice.KEEP,
                True,
                False,
            )
            output = io.StringIO()
            with (
                patch.dict(
                    "os.environ",
                    {"LOCALAPPDATA": str(local)},
                    clear=False,
                ),
                redirect_stdout(output),
            ):
                result = uninstall_main(
                    [],
                    notifier=lambda title, message: notices.append((title, message)),
                    choice_provider=lambda: options,
                    stager_factory=lambda actual, executable: Stager(),
                    current_executable=layout.uninstaller,
                )

            self.assertEqual(result, 0)
            self.assertEqual(calls, [options])
            self.assertEqual(notices, [])
            self.assertTrue(json.loads(output.getvalue())["launched"])

    def test_stage_without_confirmation_stops_before_factory(self) -> None:
        called = []

        result = uninstall_main(
            [
                "stage",
                "--appearance",
                AppearanceChoice.KEEP.value,
            ],
            stager_factory=lambda layout, executable: called.append(True),
        )

        self.assertEqual(result, 3)
        self.assertEqual(called, [])

    def test_no_argument_staged_copy_resumes_without_new_choices(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            local = root / "Local"
            temp_root = root / "Temp"
            layout = InstallLayout(
                local / "Programs" / "ThemeScheduler",
                local / "ThemeScheduler",
            )
            transaction = "uninstall-" + "d" * 32
            workspace = temp_root / "ThemeScheduler" / transaction
            workspace.mkdir(parents=True)
            executable = workspace / "Uninstall.exe"
            executable.write_bytes(b"standalone")
            cleanup = workspace / "cleanup.ps1"
            cleanup.write_text("fixed", encoding="utf-8")
            request = UninstallRequest(
                transaction_id=transaction,
                created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                program_root=str(layout.program_root),
                data_root=str(layout.data_root),
                launcher_process_id=1234,
                source_uninstaller_sha256=file_sha256(executable),
                cleanup_script_sha256=file_sha256(cleanup),
                authorization_token="e" * 64,
                options=UninstallOptions(
                    AppearanceChoice.KEEP,
                    True,
                    True,
                ),
            )
            request_path = workspace / "request.json"
            request_path.write_text(
                json.dumps(request.as_dict()),
                encoding="utf-8",
            )
            calls = []
            notices = []

            class Service:
                def run(self):
                    return SimpleNamespace(
                        result="completed",
                        as_dict=lambda: {
                            "result": "completed",
                            "verified": True,
                        },
                    )

            blocked_calls = []
            with (
                patch.dict(
                    "os.environ",
                    {
                        "LOCALAPPDATA": str(local),
                        "TEMP": str(temp_root),
                        "TMP": str(temp_root),
                    },
                    clear=False,
                ),
                redirect_stdout(io.StringIO()),
            ):
                blocked = uninstall_main(
                    [],
                    notifier=lambda title, message: notices.append((title, message)),
                    current_executable=executable,
                    launcher_waiter=lambda pid, timeout: False,
                    service_factory=lambda actual, actual_workspace: (
                        blocked_calls.append(True) or Service()
                    ),
                )
                result = uninstall_main(
                    [],
                    notifier=lambda title, message: notices.append((title, message)),
                    choice_provider=lambda: self.fail(
                        "Staged retry must not ask for new choices."
                    ),
                    current_executable=executable,
                    launcher_waiter=lambda pid, timeout: (
                        pid == 1234 and timeout == 15_000
                    ),
                    service_factory=lambda actual, actual_workspace: (
                        calls.append((actual, actual_workspace)) or Service()
                    ),
                )

            self.assertEqual(blocked, 2)
            self.assertEqual(blocked_calls, [])
            self.assertEqual(result, 0)
            self.assertEqual(calls, [(request, workspace)])
            self.assertEqual(len(notices), 2)

    def test_inspect_prints_plan_without_creating_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout = self._layout(root)
            output = io.StringIO()

            with redirect_stdout(output):
                result = uninstall_main(
                    [
                        "inspect",
                        "--program-root",
                        str(layout.program_root),
                        "--data-root",
                        str(layout.data_root),
                    ]
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(result, 0)
            self.assertFalse(payload["filesChanged"])
            self.assertFalse(layout.program_root.exists())
            self.assertFalse(layout.data_root.exists())

    def test_execute_request_is_hash_token_time_and_temp_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            local = root / "Local"
            temp_root = root / "Temp"
            layout = InstallLayout(
                local / "Programs" / "ThemeScheduler",
                local / "ThemeScheduler",
            )
            transaction = "uninstall-" + "a" * 32
            workspace = temp_root / "ThemeScheduler" / transaction
            workspace.mkdir(parents=True)
            executable = workspace / "Uninstall.exe"
            executable.write_bytes(b"standalone")
            cleanup = workspace / "cleanup.ps1"
            cleanup.write_text(
                "fixed",
                encoding="utf-8",
            )
            now = datetime.fromisoformat("2026-07-25T14:00:00+08:00")
            request = UninstallRequest(
                transaction_id=transaction,
                created_at=now.isoformat(timespec="seconds"),
                program_root=str(layout.program_root),
                data_root=str(layout.data_root),
                launcher_process_id=1234,
                source_uninstaller_sha256=file_sha256(executable),
                cleanup_script_sha256=file_sha256(cleanup),
                authorization_token="b" * 64,
                options=UninstallOptions(
                    AppearanceChoice.KEEP,
                    True,
                    True,
                ),
            )
            request_path = workspace / "request.json"
            request_path.write_text(
                json.dumps(request.as_dict()),
                encoding="utf-8",
            )

            with patch.dict(
                "os.environ",
                {"LOCALAPPDATA": str(local)},
                clear=False,
            ):
                loaded, actual_workspace = _validate_execute_request(
                    request_path,
                    request.authorization_token,
                    executable,
                    now=now,
                    temp_root=temp_root,
                )
                self.assertEqual(loaded, request)
                self.assertEqual(actual_workspace, workspace)
                with self.assertRaisesRegex(ValueError, "token"):
                    _validate_execute_request(
                        request_path,
                        "c" * 64,
                        executable,
                        now=now,
                        temp_root=temp_root,
                    )
                with self.assertRaisesRegex(ValueError, "expired"):
                    _validate_execute_request(
                        request_path,
                        request.authorization_token,
                        executable,
                        now=now + timedelta(minutes=16),
                        temp_root=temp_root,
                    )

            executable.write_bytes(b"tampered")
            with (
                patch.dict(
                    "os.environ",
                    {"LOCALAPPDATA": str(local)},
                    clear=False,
                ),
                self.assertRaisesRegex(ValueError, "hash"),
            ):
                _validate_execute_request(
                    request_path,
                    request.authorization_token,
                    executable,
                    now=now,
                    temp_root=temp_root,
                )

            executable.write_bytes(b"standalone")
            cleanup.write_text("tampered", encoding="utf-8")
            with (
                patch.dict(
                    "os.environ",
                    {"LOCALAPPDATA": str(local)},
                    clear=False,
                ),
                self.assertRaisesRegex(ValueError, "cleanup script hash"),
            ):
                _validate_execute_request(
                    request_path,
                    request.authorization_token,
                    executable,
                    now=now,
                    temp_root=temp_root,
                )


if __name__ == "__main__":
    unittest.main()
