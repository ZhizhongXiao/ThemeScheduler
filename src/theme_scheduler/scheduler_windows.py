"""Isolated Windows Task Scheduler 2.0 COM adapter."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .errors import ThemeSchedulerRuntimeError
from .resources import resource_path
from .scheduler import (
    DEFAULT_TASK_PATH,
    TaskDefinitionBackup,
    TaskSpec,
    validate_desired_task,
)
from .windows_subprocess import no_window_options


class TaskSchedulerBridgeError(ThemeSchedulerRuntimeError):
    """Raised when the isolated Task Scheduler bridge fails."""


class WindowsTaskSchedulerBackend:
    """Current-user adapter with JSON-only bridge communication."""

    TIMEOUT_SECONDS = 30

    def __init__(self, bridge_path: Path | None = None) -> None:
        self._bridge_path = Path(bridge_path) if bridge_path else None

    def _resolve_bridge(self) -> Path:
        if self._bridge_path is not None:
            bridge = self._bridge_path.resolve()
        else:
            bridge = resource_path("entrypoints", "task_scheduler_bridge.ps1")
        if not bridge.is_file():
            raise TaskSchedulerBridgeError(
                f"Task Scheduler bridge is missing: {bridge}"
            )
        return bridge

    @staticmethod
    def _resolve_powershell() -> Path:
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        powershell = (
            Path(system_root)
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        if not powershell.is_file():
            raise TaskSchedulerBridgeError(
                f"Windows PowerShell is missing: {powershell}"
            )
        return powershell

    def _run(
        self,
        action: str,
        *,
        task_path: str = DEFAULT_TASK_PATH,
        request: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if os.name != "nt":
            raise OSError("Windows Task Scheduler access requires Windows.")
        command = [
            str(self._resolve_powershell()),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self._resolve_bridge()),
            "-Action",
            action,
            "-TaskPath",
            task_path,
        ]
        request_text = (
            json.dumps(request, ensure_ascii=False, separators=(",", ":"))
            if request is not None
            else None
        )
        try:
            with tempfile.TemporaryDirectory(
                prefix="themescheduler-task-bridge-"
            ) as temporary_directory:
                if request_text is not None:
                    request_path = Path(temporary_directory) / "request.json"
                    request_path.write_text(
                        request_text,
                        encoding="utf-8-sig",
                        newline="\n",
                    )
                    command.extend(("-RequestPath", str(request_path)))
                completed = subprocess.run(
                    command,
                    input=None,
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8-sig",
                    errors="replace",
                    timeout=self.TIMEOUT_SECONDS,
                    **no_window_options(),
                )
        except subprocess.TimeoutExpired as exc:
            raise TaskSchedulerBridgeError(
                f"Task Scheduler bridge timed out after {self.TIMEOUT_SECONDS} seconds."
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise TaskSchedulerBridgeError(
                f"Task Scheduler bridge exited with {completed.returncode}: "
                f"{detail or 'no output'}"
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise TaskSchedulerBridgeError(
                "Task Scheduler bridge returned invalid JSON: "
                f"{completed.stdout.strip()!r}"
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("ok") is not True
            or payload.get("action") != action
        ):
            raise TaskSchedulerBridgeError(
                f"Task Scheduler bridge returned an unexpected result: {payload!r}"
            )
        return payload

    def probe(self) -> dict[str, Any]:
        payload = self._run("Probe")
        user_id = payload.get("currentUserId")
        task_exists = payload.get("taskExists")
        if (
            not isinstance(user_id, str)
            or not user_id
            or not isinstance(task_exists, bool)
            or payload.get("taskSchedulerChanged") is not False
        ):
            raise TaskSchedulerBridgeError(
                f"Task Scheduler probe returned invalid fields: {payload!r}"
            )
        return payload

    def current_user_id(self) -> str:
        return str(self.probe()["currentUserId"])

    def read(self, task_path: str = DEFAULT_TASK_PATH) -> TaskSpec | None:
        payload = self._run("Read", task_path=task_path)
        exists = payload.get("exists")
        if not isinstance(exists, bool):
            raise TaskSchedulerBridgeError(
                f"Task read returned invalid existence state: {payload!r}"
            )
        if not exists:
            if payload.get("task") is not None:
                raise TaskSchedulerBridgeError(
                    "Absent task read unexpectedly returned a definition."
                )
            return None
        task = payload.get("task")
        if not isinstance(task, Mapping):
            raise TaskSchedulerBridgeError(
                f"Task read returned no normalized definition: {payload!r}"
            )
        parsed = TaskSpec.from_dict(task)
        if parsed.task_path != task_path:
            raise TaskSchedulerBridgeError(
                f"Task readback path mismatch: {parsed.task_path!r}"
            )
        return parsed

    def register(self, task: TaskSpec) -> None:
        validate_desired_task(task)
        payload = self._run(
            "Register",
            task_path=task.task_path,
            request={"task": task.as_dict()},
        )
        if (
            payload.get("taskPath") != task.task_path
            or payload.get("taskSchedulerChanged") is not True
        ):
            raise TaskSchedulerBridgeError(
                f"Task registration returned invalid fields: {payload!r}"
            )

    def capture(
        self, task_path: str = DEFAULT_TASK_PATH
    ) -> TaskDefinitionBackup | None:
        payload = self._run("Export", task_path=task_path)
        exists = payload.get("exists")
        if not isinstance(exists, bool):
            raise TaskSchedulerBridgeError(
                f"Task export returned invalid existence state: {payload!r}"
            )
        if not exists:
            if payload.get("backup") is not None:
                raise TaskSchedulerBridgeError(
                    "Absent task export unexpectedly returned a backup."
                )
            return None
        backup = payload.get("backup")
        if not isinstance(backup, Mapping):
            raise TaskSchedulerBridgeError(
                f"Task export returned no backup: {payload!r}"
            )
        parsed = TaskDefinitionBackup.from_dict(backup)
        if parsed.task_path != task_path:
            raise TaskSchedulerBridgeError(
                f"Task backup path mismatch: {parsed.task_path!r}"
            )
        return parsed

    def restore(self, backup: TaskDefinitionBackup) -> None:
        payload = self._run(
            "Restore",
            task_path=backup.task_path,
            request={"backup": backup.as_dict()},
        )
        if (
            payload.get("taskPath") != backup.task_path
            or payload.get("taskSchedulerChanged") is not True
        ):
            raise TaskSchedulerBridgeError(
                f"Task restore returned invalid fields: {payload!r}"
            )

    def delete(self, task_path: str = DEFAULT_TASK_PATH) -> None:
        payload = self._run("Delete", task_path=task_path)
        if not isinstance(payload.get("deleted"), bool) or not isinstance(
            payload.get("taskSchedulerChanged"), bool
        ):
            raise TaskSchedulerBridgeError(
                f"Task deletion returned invalid fields: {payload!r}"
            )

    def run_now(self, task_path: str = DEFAULT_TASK_PATH) -> dict[str, Any]:
        payload = self._run("Run", task_path=task_path)
        if (
            payload.get("taskPath") != task_path
            or payload.get("taskStarted") is not True
            or payload.get("taskSchedulerChanged") is not False
            or not isinstance(payload.get("instanceGuid"), str)
            or not payload.get("instanceGuid")
        ):
            raise TaskSchedulerBridgeError(
                f"Task run returned invalid fields: {payload!r}"
            )
        return payload
