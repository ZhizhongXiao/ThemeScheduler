from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[2]

from theme_scheduler.lifecycle import InstallLayout
from theme_scheduler.uninstall_contracts import (
    AppearanceChoice,
    UninstallOptions,
    UninstallRequest,
)
from theme_scheduler.uninstall_windows import (
    IndependentUninstallerStager,
    WindowsInstalledProcessGuard,
    WindowsSelfCleanupScheduler,
    WindowsUninstallError,
)


class FakeProcess:
    pid = 4321


class RecordingPopen:
    def __init__(self) -> None:
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return FakeProcess()


class FakeProcessApi:
    def __init__(self, pids=(10, 20), *, never_exit=False) -> None:
        self.pids = tuple(pids)
        self.never_exit = never_exit
        self.closed: list[int] = []
        self.waited: list[int] = []
        self.match_calls = 0

    def matching_pids(self, executable: Path) -> tuple[int, ...]:
        self.match_calls += 1
        if self.match_calls > 1 and not self.never_exit:
            return ()
        return self.pids

    def request_close(self, pid: int) -> None:
        self.closed.append(pid)

    def wait(self, pid: int, timeout_ms: int) -> bool:
        self.waited.append(pid)
        return not self.never_exit


class UninstallWindowsAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name).resolve()
        self.layout = InstallLayout(
            root / "Programs" / "ThemeScheduler",
            root / "ThemeScheduler",
        )
        self.layout.maintenance.mkdir(parents=True)
        self.layout.uninstaller.write_bytes(b"standalone-exe")
        self.cleanup_script = root / "uninstall_cleanup.ps1"
        self.cleanup_script.write_text(
            "param([string]$Workspace,[int]$WaitPid)",
            encoding="utf-8",
        )
        self.temp_root = root / "Temp"

    def options(self) -> UninstallOptions:
        return UninstallOptions(
            AppearanceChoice.RESTORE,
            True,
            False,
        )

    def test_stager_copies_hashes_writes_request_and_launches(self) -> None:
        popen = RecordingPopen()
        stager = IndependentUninstallerStager(
            self.layout,
            self.layout.uninstaller,
            temp_root=self.temp_root,
            cleanup_script=self.cleanup_script,
            popen=popen,
        )

        result = stager.stage(self.options())

        workspace = Path(result["workspace"])
        request = UninstallRequest.from_dict(
            __import__("json").loads(
                (workspace / "request.json").read_text(encoding="utf-8")
            )
        )
        self.assertEqual(request.options, self.options())
        self.assertEqual(request.launcher_process_id, os.getpid())
        self.assertEqual(
            (workspace / "Uninstall.exe").read_bytes(),
            b"standalone-exe",
        )
        self.assertTrue((workspace / "cleanup.ps1").is_file())
        self.assertEqual(result["processId"], 4321)
        command, kwargs = popen.calls[0]
        self.assertEqual(Path(command[0]), workspace / "Uninstall.exe")
        self.assertEqual(command[1], "execute")
        self.assertIn(request.authorization_token, command)
        self.assertEqual(kwargs["cwd"], str(workspace))
        self.assertTrue(kwargs["close_fds"])
        self.assertNotIn("creationflags", kwargs)
        self.assertNotIn("startupinfo", kwargs)

    def test_stager_rejects_noninstalled_source(self) -> None:
        other = self.layout.program_root.parent / "Uninstall.exe"
        other.write_bytes(b"other")

        with self.assertRaises(WindowsUninstallError):
            IndependentUninstallerStager(
                self.layout,
                other,
                temp_root=self.temp_root,
                cleanup_script=self.cleanup_script,
            )

    def test_process_guard_requests_close_and_refuses_survivors(self) -> None:
        api = FakeProcessApi()
        guard = WindowsInstalledProcessGuard(
            api,
            timeout_seconds=0.01,
        )
        guard.ensure_idle(self.layout.executable)
        self.assertEqual(api.closed, [10, 20])
        self.assertEqual(api.waited, [10, 20])

        stuck = FakeProcessApi(pids=(30,), never_exit=True)
        with self.assertRaises(WindowsUninstallError):
            WindowsInstalledProcessGuard(
                stuck,
                timeout_seconds=0.01,
            ).ensure_idle(self.layout.executable)

    def test_self_cleanup_scheduler_uses_fixed_script_and_hidden_process(
        self,
    ) -> None:
        workspace = self.temp_root / "ThemeScheduler" / ("uninstall-" + "a" * 32)
        workspace.mkdir(parents=True)
        (workspace / "cleanup.ps1").write_text(
            "fixed",
            encoding="utf-8",
        )
        powershell = self.temp_root / "powershell.exe"
        powershell.parent.mkdir(parents=True, exist_ok=True)
        powershell.write_bytes(b"exe")
        popen = RecordingPopen()
        scheduler = WindowsSelfCleanupScheduler(
            popen=popen,
            expected_product_temp=workspace.parent,
            bootloader_parent_pid=44,
        )

        with patch.object(
            scheduler,
            "_powershell",
            return_value=powershell,
        ):
            scheduler.schedule(workspace, wait_pid=55)

        command, kwargs = popen.calls[0]
        self.assertEqual(Path(command[0]), powershell)
        self.assertIn(str(workspace / "cleanup.ps1"), command)
        self.assertIn(str(workspace), command)
        self.assertIn("55", command)
        self.assertIn("-FinalizeProductRegistration", command)
        self.assertIn("-WaitParentPid", command)
        self.assertIn("44", command)
        self.assertEqual(kwargs["cwd"], str(workspace.parent.parent))

    def test_self_cleanup_omits_unmatched_bootloader_parent(self) -> None:
        workspace = self.temp_root / "ThemeScheduler" / ("uninstall-" + "b" * 32)
        workspace.mkdir(parents=True)
        (workspace / "cleanup.ps1").write_text(
            "fixed",
            encoding="utf-8",
        )
        powershell = self.temp_root / "powershell.exe"
        powershell.write_bytes(b"exe")
        popen = RecordingPopen()
        scheduler = WindowsSelfCleanupScheduler(
            popen=popen,
            expected_product_temp=workspace.parent,
            bootloader_parent_pid=None,
        )

        with (
            patch.object(
                scheduler,
                "_powershell",
                return_value=powershell,
            ),
            patch.object(
                scheduler,
                "_matching_parent_pid",
                return_value=None,
            ),
        ):
            scheduler.bootloader_parent_pid = None
            scheduler.schedule(workspace, wait_pid=55)

        command, _ = popen.calls[0]
        self.assertNotIn("-WaitParentPid", command)

    def test_cleanup_script_is_fixed_auditable_and_bounded(self) -> None:
        script = (PROJECT_ROOT / "entrypoints" / "uninstall_cleanup.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("^uninstall-[0-9a-f]{32}$", script)
        self.assertIn("GetTempPath", script)
        self.assertIn("ReparsePoint", script)
        self.assertIn("WaitForExit", script)
        self.assertIn("$WaitParentPid", script)
        self.assertIn("'exit-requested'", script)
        self.assertIn("$SignalWaitMilliseconds = 120000", script)
        self.assertIn(
            "$signalWait.ElapsedMilliseconds -ge $SignalWaitMilliseconds",
            script,
        )
        self.assertIn("$process.MainModule.FileName", script)
        self.assertIn("$process.Kill()", script)
        self.assertIn("$FinalizeProductRegistration", script)
        self.assertIn("$journal.status -ne 'completed'", script)
        self.assertIn(
            "$journal.completedSteps -notcontains 'registration-removed'",
            script,
        )
        self.assertIn("GetFolderPath('LocalApplicationData')", script)
        self.assertIn(
            "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\",
            script,
        )
        self.assertIn("$registration.GetSubKeyNames()", script)
        self.assertIn("Remove-Item -LiteralPath $registrationPath -Force", script)
        self.assertIn(
            "$process.WaitForExit($naturalExitMilliseconds)",
            script,
        )
        self.assertIn("Remove-Item -LiteralPath $resolved", script)
        self.assertIn("$maximumAttempts = 60", script)
        self.assertIn("$retryMilliseconds = 250", script)
        self.assertIn(
            "Start-Sleep -Milliseconds $retryMilliseconds",
            script,
        )
        self.assertNotIn("EncodedCommand", script)
        self.assertNotIn("FromBase64String", script)
        self.assertNotIn("Invoke-Expression", script)

    @unittest.skipUnless(sys.platform == "win32", "Windows-only cleanup test")
    def test_cleanup_signal_terminates_exact_staged_processes(self) -> None:
        product_temp = Path(tempfile.gettempdir()) / "ThemeScheduler"
        workspace = product_temp / f"uninstall-{uuid4().hex}"
        workspace.mkdir(parents=True, exist_ok=False)
        staged_executable = workspace / "Uninstall.exe"
        staged_cleanup = workspace / "cleanup.ps1"
        shutil.copy2(
            Path(os.environ["SYSTEMROOT"]) / "System32" / "cmd.exe",
            staged_executable,
        )
        shutil.copy2(
            PROJECT_ROOT / "entrypoints" / "uninstall_cleanup.ps1",
            staged_cleanup,
        )
        command = [
            str(staged_executable),
            "/d",
            "/s",
            "/c",
            (
                f'"{Path(os.environ["SYSTEMROOT"]) / "System32" / "ping.exe"}" '
                "-n 60 127.0.0.1 > nul"
            ),
        ]
        process_options = {
            "cwd": str(workspace),
            "close_fds": True,
            "creationflags": subprocess.CREATE_NO_WINDOW,
        }
        first = subprocess.Popen(command, **process_options)
        second = subprocess.Popen(command, **process_options)
        powershell = (
            Path(os.environ["SYSTEMROOT"])
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        cleanup = None
        try:
            cleanup = subprocess.Popen(
                [
                    str(powershell),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(staged_cleanup),
                    "-Workspace",
                    str(workspace),
                    "-WaitPid",
                    str(first.pid),
                    "-WaitParentPid",
                    str(second.pid),
                ],
                cwd=str(product_temp.parent),
                close_fds=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            (workspace / "exit-requested").write_text(
                "test\n",
                encoding="ascii",
            )
            self.assertEqual(cleanup.wait(timeout=15), 0)
            self.assertIsNotNone(first.poll())
            self.assertIsNotNone(second.poll())
            self.assertFalse(workspace.exists())
        finally:
            for process in (first, second, cleanup):
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
            if workspace.exists():
                shutil.rmtree(workspace)


if __name__ == "__main__":
    unittest.main()
