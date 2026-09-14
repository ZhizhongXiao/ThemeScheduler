"""Windows-only staging, process, and self-cleanup adapters for uninstall."""

from __future__ import annotations

import ctypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Any, Protocol

from .errors import ThemeSchedulerRuntimeError
from .lifecycle import InstallLayout, file_sha256
from .persistence import atomic_write_json, captured_at
from .resources import resource_path
from .uninstall_contracts import (
    UninstallOptions,
    UninstallRequest,
)
from .windows_subprocess import no_window_options

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
WM_CLOSE = 0x0010
MAX_PATH_CHARS = 32768


class WindowsUninstallError(ThemeSchedulerRuntimeError):
    """Raised when Windows staging or cleanup cannot be trusted."""


class ProcessApi(Protocol):
    def matching_pids(self, executable: Path) -> tuple[int, ...]: ...

    def request_close(self, pid: int) -> None: ...

    def wait(self, pid: int, timeout_ms: int) -> bool: ...


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class WindowsProcessApi:
    """Enumerate exact executable paths without a third-party dependency."""

    def __init__(self, kernel32: Any | None = None, user32: Any | None = None):
        if os.name != "nt" and kernel32 is None:
            raise OSError("Windows process inspection requires Windows.")
        if kernel32 is None:
            kernel32 = ctypes.windll.kernel32
            kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
            kernel32.Process32FirstW.restype = wintypes.BOOL
            kernel32.Process32NextW.restype = wintypes.BOOL
        self.kernel32 = kernel32
        self.user32 = user32 or ctypes.windll.user32

    def _process_path(self, pid: int) -> Path | None:
        handle = self.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,
            False,
            pid,
        )
        if not handle:
            return None
        try:
            size = wintypes.DWORD(MAX_PATH_CHARS)
            buffer = ctypes.create_unicode_buffer(MAX_PATH_CHARS)
            if not self.kernel32.QueryFullProcessImageNameW(
                handle,
                0,
                buffer,
                ctypes.byref(size),
            ):
                return None
            return Path(buffer.value).resolve(strict=False)
        finally:
            self.kernel32.CloseHandle(handle)

    def matching_pids(self, executable: Path) -> tuple[int, ...]:
        expected = os.path.normcase(str(Path(executable).resolve(strict=False)))
        snapshot = self.kernel32.CreateToolhelp32Snapshot(
            TH32CS_SNAPPROCESS,
            0,
        )
        invalid = ctypes.c_void_p(-1).value
        if not snapshot or snapshot == invalid:
            raise WindowsUninstallError("Cannot enumerate current-user processes.")
        matches: list[int] = []
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = self.kernel32.Process32FirstW(
                snapshot,
                ctypes.byref(entry),
            )
            while ok:
                pid = int(entry.th32ProcessID)
                if pid > 0 and pid != os.getpid():
                    path = self._process_path(pid)
                    if path is not None and os.path.normcase(str(path)) == expected:
                        matches.append(pid)
                ok = self.kernel32.Process32NextW(
                    snapshot,
                    ctypes.byref(entry),
                )
        finally:
            self.kernel32.CloseHandle(snapshot)
        return tuple(sorted(set(matches)))

    def request_close(self, pid: int) -> None:
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HWND,
            wintypes.LPARAM,
        )

        @callback_type
        def callback(hwnd, _lparam):
            owner = wintypes.DWORD()
            self.user32.GetWindowThreadProcessId(
                hwnd,
                ctypes.byref(owner),
            )
            if owner.value == pid:
                self.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            return True

        if not self.user32.EnumWindows(callback, 0):
            raise WindowsUninstallError(f"Cannot enumerate windows for process {pid}.")

    def wait(self, pid: int, timeout_ms: int) -> bool:
        handle = self.kernel32.OpenProcess(
            SYNCHRONIZE,
            False,
            pid,
        )
        if not handle:
            return True
        try:
            result = self.kernel32.WaitForSingleObject(
                handle,
                timeout_ms,
            )
            if result == WAIT_OBJECT_0:
                return True
            if result == WAIT_TIMEOUT:
                return False
            raise WindowsUninstallError(
                f"Waiting for process {pid} failed with code {result}."
            )
        finally:
            self.kernel32.CloseHandle(handle)


class WindowsInstalledProcessGuard:
    """Ask exact installed GUI instances to close; never force terminate."""

    def __init__(
        self,
        api: ProcessApi | None = None,
        *,
        timeout_seconds: float = 10.0,
    ) -> None:
        if timeout_seconds <= 0 or timeout_seconds > 60:
            raise ValueError("Process close timeout must be in (0, 60].")
        self.api = api or WindowsProcessApi()
        self.timeout_seconds = timeout_seconds

    def ensure_idle(self, executable: Path) -> None:
        pids = self.api.matching_pids(executable)
        for pid in pids:
            self.api.request_close(pid)
        deadline = time.monotonic() + self.timeout_seconds
        remaining = list(pids)
        while remaining and time.monotonic() < deadline:
            next_remaining: list[int] = []
            for pid in remaining:
                timeout_ms = max(
                    1,
                    int(
                        min(
                            0.25,
                            max(0.0, deadline - time.monotonic()),
                        )
                        * 1000
                    ),
                )
                if not self.api.wait(pid, timeout_ms):
                    next_remaining.append(pid)
            remaining = next_remaining
        if remaining:
            raise WindowsUninstallError(
                "Installed ThemeScheduler process is still running: "
                + ", ".join(str(pid) for pid in remaining)
            )
        if self.api.matching_pids(executable):
            raise WindowsUninstallError(
                "A new installed ThemeScheduler process appeared during uninstall."
            )


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve(strict=False))) == os.path.normcase(
        str(right.resolve(strict=False))
    )


def _temporary_product_root(temp_root: Path | None = None) -> Path:
    if temp_root is None:
        raw = os.environ.get("TEMP") or os.environ.get("TMP")
        if not raw:
            raise OSError("TEMP is unavailable.")
        temp_root = Path(raw)
    root = Path(temp_root).resolve(strict=False)
    if not root.is_absolute():
        raise WindowsUninstallError("TEMP root is not absolute.")
    return root / "ThemeScheduler"


class IndependentUninstallerStager:
    """Copy the trusted standalone executable before any owned deletion."""

    def __init__(
        self,
        layout: InstallLayout,
        source_executable: Path,
        *,
        temp_root: Path | None = None,
        cleanup_script: Path | None = None,
        popen: Any = subprocess.Popen,
    ) -> None:
        self.layout = layout
        self.source_executable = Path(source_executable).resolve(strict=False)
        if not _same_path(
            self.source_executable,
            layout.uninstaller,
        ):
            raise WindowsUninstallError(
                "Only the installed independent uninstaller may stage itself."
            )
        self.temp_product_root = _temporary_product_root(temp_root)
        self.cleanup_script = (
            Path(cleanup_script).resolve(strict=False)
            if cleanup_script is not None
            else resource_path(
                "entrypoints",
                "uninstall_cleanup.ps1",
            )
        )
        self.popen = popen

    def stage(self, options: UninstallOptions) -> dict[str, Any]:
        if not self.source_executable.is_file():
            raise WindowsUninstallError("Installed independent uninstaller is missing.")
        if not self.cleanup_script.is_file():
            raise WindowsUninstallError("Fixed uninstall cleanup script is missing.")
        transaction_id = f"uninstall-{uuid.uuid4().hex}"
        workspace = self.temp_product_root / transaction_id
        if workspace.exists():
            raise WindowsUninstallError("New uninstall workspace unexpectedly exists.")
        workspace.mkdir(parents=True, exist_ok=False)
        staged_executable = workspace / "Uninstall.exe"
        staged_cleanup = workspace / "cleanup.ps1"
        request_path = workspace / "request.json"
        try:
            source_hash = file_sha256(self.source_executable)
            cleanup_hash = file_sha256(self.cleanup_script)
            shutil.copy2(self.source_executable, staged_executable)
            shutil.copy2(self.cleanup_script, staged_cleanup)
            if (
                file_sha256(self.source_executable) != source_hash
                or file_sha256(staged_executable) != source_hash
            ):
                raise WindowsUninstallError("Staged uninstaller hash mismatch.")
            if (
                file_sha256(self.cleanup_script) != cleanup_hash
                or file_sha256(staged_cleanup) != cleanup_hash
            ):
                raise WindowsUninstallError("Staged cleanup script hash mismatch.")
            request = UninstallRequest(
                transaction_id=transaction_id,
                created_at=captured_at(),
                program_root=str(self.layout.program_root),
                data_root=str(self.layout.data_root),
                launcher_process_id=os.getpid(),
                source_uninstaller_sha256=source_hash,
                cleanup_script_sha256=cleanup_hash,
                authorization_token=secrets.token_hex(32),
                options=options,
            )
            atomic_write_json(request_path, request.as_dict())
            command = [
                str(staged_executable),
                "execute",
                "--request",
                str(request_path),
                "--authorization-token",
                request.authorization_token,
            ]
            process = self.popen(
                command,
                cwd=str(workspace),
                close_fds=True,
            )
            if getattr(process, "pid", None) is None or int(process.pid) <= 0:
                raise WindowsUninstallError(
                    "Temporary uninstaller did not return a process ID."
                )
            return {
                "transactionId": transaction_id,
                "workspace": str(workspace),
                "stagedExecutable": str(staged_executable),
                "request": str(request_path),
                "processId": int(process.pid),
                "launched": True,
                "windowsChanged": False,
                "installedFilesChanged": False,
            }
        except Exception:
            if workspace.exists():
                shutil.rmtree(workspace, ignore_errors=True)
            raise


class WindowsSelfCleanupScheduler:
    """Launch the fixed PowerShell cleanup script without a visible console."""

    def __init__(
        self,
        popen: Any = subprocess.Popen,
        *,
        expected_product_temp: Path | None = None,
        bootloader_parent_pid: int | None = None,
    ) -> None:
        self.popen = popen
        self.expected_product_temp = (
            Path(expected_product_temp).resolve(strict=False)
            if expected_product_temp is not None
            else _temporary_product_root()
        )
        if bootloader_parent_pid is None:
            bootloader_parent_pid = self._matching_parent_pid(Path(sys.executable))
        self.bootloader_parent_pid = bootloader_parent_pid

    @staticmethod
    def _matching_parent_pid(executable: Path) -> int | None:
        """Return the PyInstaller bootloader parent only when path-bound."""
        parent_pid = os.getppid()
        if parent_pid <= 0:
            return None
        try:
            parent_executable = WindowsProcessApi()._process_path(parent_pid)
        except (OSError, WindowsUninstallError):
            return None
        if parent_executable is None or not _same_path(
            parent_executable,
            executable,
        ):
            return None
        return parent_pid

    @staticmethod
    def _powershell() -> Path:
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        path = (
            Path(system_root)
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        if not path.is_file():
            raise WindowsUninstallError(f"Windows PowerShell is missing: {path}")
        return path

    def schedule(
        self,
        workspace: Path,
        *,
        wait_pid: int,
    ) -> None:
        workspace = Path(workspace).resolve(strict=True)
        if (
            re.fullmatch(
                r"uninstall-[0-9a-f]{32}",
                workspace.name,
            )
            is None
            or workspace.parent.name != "ThemeScheduler"
            or not _same_path(
                workspace.parent,
                self.expected_product_temp,
            )
        ):
            raise WindowsUninstallError(
                "Self-cleanup workspace is outside its fixed boundary."
            )
        script = workspace / "cleanup.ps1"
        if not script.is_file():
            raise WindowsUninstallError("Staged self-cleanup script is missing.")
        if isinstance(wait_pid, bool) or not isinstance(wait_pid, int) or wait_pid <= 0:
            raise WindowsUninstallError("Self-cleanup wait process is invalid.")
        command = [
            str(self._powershell()),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Workspace",
            str(workspace),
            "-WaitPid",
            str(wait_pid),
            "-FinalizeProductRegistration",
        ]
        if self.bootloader_parent_pid is not None:
            if (
                isinstance(self.bootloader_parent_pid, bool)
                or not isinstance(self.bootloader_parent_pid, int)
                or self.bootloader_parent_pid <= 0
            ):
                raise WindowsUninstallError(
                    "Self-cleanup bootloader process is invalid."
                )
            command.extend(
                [
                    "-WaitParentPid",
                    str(self.bootloader_parent_pid),
                ]
            )
        process = self.popen(
            command,
            cwd=str(workspace.parent.parent),
            close_fds=True,
            **no_window_options(),
        )
        if getattr(process, "pid", None) is None or int(process.pid) <= 0:
            raise WindowsUninstallError(
                "Self-cleanup helper did not return a process ID."
            )
