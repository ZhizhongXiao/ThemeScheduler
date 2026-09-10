"""Explicit, last-resort Explorer restart adapter."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .errors import ThemeSchedulerRuntimeError
from .resources import resource_path
from .windows_subprocess import no_window_options


class ExplorerRecoveryError(ThemeSchedulerRuntimeError):
    """Raised when the scoped Explorer recovery bridge cannot restore the shell."""


class WindowsExplorerRecoveryBackend:
    TIMEOUT_SECONDS = 30

    def restart(self) -> dict[str, Any]:
        if os.name != "nt":
            raise OSError("Explorer recovery requires Windows.")
        bridge = resource_path("entrypoints", "explorer_recovery_bridge.ps1")
        if not bridge.is_file():
            raise ExplorerRecoveryError(
                f"Explorer recovery bridge is missing: {bridge}"
            )
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        powershell = (
            Path(system_root)
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        if not powershell.is_file():
            raise ExplorerRecoveryError(f"Windows PowerShell is missing: {powershell}")
        command = [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(bridge),
            "-Action",
            "Restart",
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8-sig",
                errors="replace",
                timeout=self.TIMEOUT_SECONDS,
                **no_window_options(),
            )
        except subprocess.TimeoutExpired as exc:
            raise ExplorerRecoveryError("Explorer recovery bridge timed out.") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise ExplorerRecoveryError(
                f"Explorer recovery bridge exited with {completed.returncode}: "
                f"{detail or 'no output'}"
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ExplorerRecoveryError(
                f"Explorer recovery bridge returned invalid JSON: {completed.stdout!r}"
            ) from exc
        if (
            payload.get("ok") is not True
            or payload.get("action") != "Restart"
            or payload.get("recovered") is not True
        ):
            raise ExplorerRecoveryError(
                f"Explorer recovery bridge returned an unexpected result: {payload!r}"
            )
        return payload
