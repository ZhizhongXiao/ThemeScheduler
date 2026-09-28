"""Narrow pywebview API for the classic ThemeScheduler Setup wizard."""

from __future__ import annotations

import os
import tempfile
import threading
from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from .persistence import atomic_write_json, captured_at
from .setup_contracts import SetupOptions, SetupPlan
from .setup_service import SetupOutcome
from .setup_windows import WindowsSetupRuntime

SETUP_SESSION_KIND = "themescheduler.setup-session"
SETUP_SESSION_SCHEMA_VERSION = 1


class SetupRuntime(Protocol):
    def preflight(self) -> SetupPlan: ...

    def default_options(self, plan: SetupPlan) -> SetupOptions: ...

    def launch_installed_app(self) -> None: ...

    def install(
        self,
        options: SetupOptions,
        *,
        progress: Callable[[str], None],
    ) -> SetupOutcome: ...


class SetupGuiApi:
    def __init__(
        self,
        runtime: SetupRuntime | None = None,
        *,
        close_window: Callable[[], None] | None = None,
        log_root: Path | None = None,
        open_path: Callable[[Path], None] | None = None,
    ) -> None:
        self.runtime: SetupRuntime = runtime or WindowsSetupRuntime()
        self._install_guard = threading.Lock()
        self._state_guard = threading.Lock()
        self._close_window = close_window
        self._open_path = open_path
        if log_root is not None:
            resolved_log_root = Path(log_root)
        else:
            runtime_layout = getattr(self.runtime, "layout", None)
            data_root = getattr(runtime_layout, "data_root", None)
            resolved_log_root = (
                Path(data_root) / "logs"
                if data_root is not None
                else Path(tempfile.gettempdir()) / "ThemeScheduler" / "setup-logs"
            )
        self._log_root = resolved_log_root.resolve(strict=False)
        self._operation: dict[str, Any] | None = None
        self._worker: threading.Thread | None = None

    def bind_close_window(self, callback: Callable[[], None]) -> None:
        if self._close_window is not None:
            raise RuntimeError("Setup window close callback is already bound.")
        if not callable(callback):
            raise TypeError("Setup window close callback must be callable.")
        self._close_window = callback

    def window_close_allowed(self) -> bool:
        with self._state_guard:
            return not (
                self._operation is not None and self._operation["phase"] == "running"
            )

    def can_close(self) -> dict[str, bool | str]:
        allowed = self.window_close_allowed()
        return {
            "ok": True,
            "allowed": allowed,
            "reason": ("" if allowed else "安装或回滚正在进行，完成前不能关闭窗口。"),  # noqa: RUF001 - Preserve native Chinese UI punctuation.
        }

    def close_window(self) -> dict[str, bool | str]:
        if not self.window_close_allowed():
            return {
                "ok": False,
                "error": "安装或回滚正在进行，完成前不能关闭窗口。",  # noqa: RUF001 - Preserve native Chinese UI punctuation.
            }
        callback = self._close_window
        if callback is None:
            return {
                "ok": False,
                "error": "安装窗口关闭接口尚未就绪。",
            }
        try:
            callback()
            return {"ok": True}
        except Exception as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }

    def complete(self, open_app: bool) -> dict[str, bool | str]:
        """Finish a completed session and optionally launch the installed GUI."""

        if not isinstance(open_app, bool):
            return {"ok": False, "error": "完成操作参数无效。"}
        with self._state_guard:
            operation = self._operation
            outcome = None if operation is None else operation.get("outcome")
            finished = operation is not None and operation.get("phase") == "finished"
        if not finished:
            return {"ok": False, "error": "安装会话尚未完成。"}
        succeeded = (
            isinstance(outcome, Mapping)
            and outcome.get("result")
            in {
                "success",
                "success-with-warning",
            }
            and outcome.get("verified") is True
        )
        if open_app and not succeeded:
            return {
                "ok": False,
                "error": "安装未成功验证，不能启动已安装程序。",  # noqa: RUF001 - Preserve native Chinese UI punctuation.
            }
        try:
            if open_app:
                self.runtime.launch_installed_app()
            callback = self._close_window
            if callback is None:
                raise RuntimeError("安装窗口关闭接口尚未就绪。")
            callback()
            return {
                "ok": True,
                "launched": open_app,
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }

    def get_status(self) -> dict[str, Any]:
        try:
            plan = self.runtime.preflight()
            defaults = self.runtime.default_options(plan)
            return {
                "ok": True,
                "installRoot": plan.install_root,
                "dataRoot": plan.data_root,
                "plan": plan.as_dict(),
                "defaults": defaults.as_dict(),
                "previewMode": bool(getattr(self.runtime, "preview_mode", False)),
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }

    def validate_options(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            options = SetupOptions.from_dict(payload)
            return {"ok": True, "options": options.as_dict()}
        except Exception as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }

    def start_install(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            options = SetupOptions.from_dict(payload)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }
        if not self._install_guard.acquire(blocking=False):
            return {
                "ok": False,
                "error": "安装操作已在进行中。",
            }

        operation_id = uuid4().hex
        started_at = captured_at()
        with self._state_guard:
            self._operation = {
                "operationId": operation_id,
                "phase": "running",
                "stage": "starting",
                "stages": ["starting"],
                "startedAt": started_at,
                "completedAt": None,
                "outcome": None,
                "error": None,
                "logPath": None,
                "logError": None,
            }
        worker = threading.Thread(
            target=self._run_install,
            args=(operation_id, options),
            name=f"ThemeSchedulerSetup-{operation_id[:8]}",
            daemon=False,
        )
        self._worker = worker
        try:
            worker.start()
        except Exception as exc:
            with self._state_guard:
                self._operation = None
            self._install_guard.release()
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }
        return {"ok": True, "operationId": operation_id}

    def _set_stage(self, operation_id: str, stage: str) -> None:
        if not isinstance(stage, str) or not stage:
            return
        with self._state_guard:
            operation = self._operation
            if (
                operation is None
                or operation["operationId"] != operation_id
                or operation["phase"] != "running"
            ):
                return
            operation["stage"] = stage
            if not operation["stages"] or operation["stages"][-1] != stage:
                operation["stages"].append(stage)

    def _run_install(
        self,
        operation_id: str,
        options: SetupOptions,
    ) -> None:
        outcome: dict[str, Any] | None = None
        unexpected_error: str | None = None
        try:
            result = self.runtime.install(
                options,
                progress=lambda stage: self._set_stage(operation_id, stage),
            )
            outcome = result.as_dict()
        except Exception as exc:
            unexpected_error = f"{type(exc).__name__}: {exc}"[:500]
            self._set_stage(operation_id, "failed")
        finally:
            completed_at = captured_at()
            with self._state_guard:
                operation = self._operation
                if operation is not None and operation["operationId"] == operation_id:
                    log_payload = {
                        "kind": SETUP_SESSION_KIND,
                        "schemaVersion": SETUP_SESSION_SCHEMA_VERSION,
                        "operationId": operation_id,
                        "startedAt": operation["startedAt"],
                        "completedAt": completed_at,
                        "stages": list(operation["stages"]),
                        "outcome": deepcopy(outcome),
                        "error": unexpected_error,
                    }
                else:
                    log_payload = None

            log_path: Path | None = None
            log_error: str | None = None
            if log_payload is not None:
                candidate = self._log_root / f"setup-{operation_id}.json"
                try:
                    atomic_write_json(candidate, log_payload)
                    log_path = candidate
                except Exception as exc:
                    log_error = f"{type(exc).__name__}: {exc}"[:500]
            with self._state_guard:
                operation = self._operation
                if operation is not None and operation["operationId"] == operation_id:
                    operation["completedAt"] = completed_at
                    operation["outcome"] = outcome
                    operation["error"] = unexpected_error
                    operation["logPath"] = log_path
                    operation["logError"] = log_error
                    operation["phase"] = "finished"
            self._install_guard.release()

    def get_install_status(self, operation_id: str) -> dict[str, Any]:
        if not isinstance(operation_id, str) or not operation_id:
            return {"ok": False, "error": "安装会话标识无效。"}
        with self._state_guard:
            operation = self._operation
            if operation is None or operation["operationId"] != operation_id:
                return {"ok": False, "error": "安装会话不存在或已过期。"}
            return {
                "ok": True,
                "operationId": operation_id,
                "phase": operation["phase"],
                "stage": operation["stage"],
                "stages": list(operation["stages"]),
                "startedAt": operation["startedAt"],
                "completedAt": operation["completedAt"],
                "outcome": deepcopy(operation["outcome"]),
                "error": operation["error"],
                "logAvailable": bool(operation["logPath"]),
                "logError": operation["logError"],
            }

    def open_setup_log(self) -> dict[str, bool | str]:
        with self._state_guard:
            operation = self._operation
            path = None if operation is None else operation["logPath"]
        if not isinstance(path, Path) or not path.is_file():
            return {"ok": False, "error": "本次安装日志尚不可用。"}
        try:
            if self._open_path is not None:
                self._open_path(path)
            else:
                if os.name != "nt":
                    raise OSError("Opening the Setup log requires Windows.")
                os.startfile(str(path))
            return {"ok": True}
        except Exception as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }
