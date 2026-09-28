"""Narrow pywebview API for the classic independent uninstaller."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from .lifecycle import InstallLayout
from .uninstall_contracts import (
    AppearanceChoice,
    UninstallOptions,
    build_uninstall_plan,
)


class UninstallRuntime(Protocol):
    preview_mode: bool
    mode: str

    def get_status(self) -> Mapping[str, Any]: ...

    def uninstall(
        self,
        options: UninstallOptions,
        *,
        progress: Callable[[str], None],
    ) -> Mapping[str, Any]: ...


class SelectionUninstallRuntime:
    """Stage the installed standalone uninstaller and hand off to its copy."""

    preview_mode = False
    mode = "selection"

    def __init__(
        self,
        layout: InstallLayout,
        stager: Any,
    ) -> None:
        self.layout = layout
        self.stager = stager

    @staticmethod
    def default_options() -> UninstallOptions:
        return UninstallOptions(
            AppearanceChoice.RESTORE,
            False,
            False,
        )

    def get_status(self) -> Mapping[str, Any]:
        if not self.layout.uninstaller.is_file():
            raise FileNotFoundError(
                f"Independent uninstaller is missing: {self.layout.uninstaller}"
            )
        options = self.default_options()
        return {
            "mode": self.mode,
            "programRoot": str(self.layout.program_root),
            "dataRoot": str(self.layout.data_root),
            "options": options.as_dict(),
            "plan": build_uninstall_plan(
                self.layout,
                options,
            ).as_dict(),
            "autoStart": False,
        }

    def uninstall(
        self,
        options: UninstallOptions,
        *,
        progress: Callable[[str], None],
    ) -> Mapping[str, Any]:
        progress("staging-copy")
        staged = self.stager.stage(options)
        progress("handoff")
        return {
            "result": "handoff",
            "verified": True,
            "message": (
                "The verified temporary uninstaller was launched. "
                "This window can now close."
            ),
            "options": options.as_dict(),
            **dict(staged),
        }


class ExecuteUninstallRuntime:
    """Run the validated request from the temporary standalone copy."""

    preview_mode = False
    mode = "execute"

    def __init__(
        self,
        request: Any,
        workspace: Path,
        service: Any,
    ) -> None:
        self.request = request
        self.workspace = Path(workspace)
        self.service = service

    def get_status(self) -> Mapping[str, Any]:
        options = self.request.options
        return {
            "mode": self.mode,
            "programRoot": str(self.request.layout.program_root),
            "dataRoot": str(self.request.layout.data_root),
            "options": options.as_dict(),
            "plan": build_uninstall_plan(
                self.request.layout,
                options,
            ).as_dict(),
            "transactionId": self.request.transaction_id,
            "autoStart": True,
        }

    def uninstall(
        self,
        options: UninstallOptions,
        *,
        progress: Callable[[str], None],
    ) -> Mapping[str, Any]:
        if options != self.request.options:
            raise ValueError(
                "Temporary uninstall options do not match the authorized request."
            )
        progress("waiting-for-launcher")
        return self.service.run(progress=progress).as_dict()


class UninstallGuiApi:
    """The only object exposed to the uninstaller frontend."""

    def __init__(
        self,
        runtime: UninstallRuntime,
        *,
        close_window: Callable[[], None] | None = None,
        close_guard: Callable[[], None] | None = None,
    ) -> None:
        self.runtime = runtime
        self._close_window = close_window
        self._close_guard = close_guard
        self._operation_guard = threading.Lock()
        self._state_guard = threading.Lock()
        self._operation: dict[str, Any] | None = None

    def bind_close_window(self, callback: Callable[[], None]) -> None:
        if self._close_window is not None:
            raise RuntimeError("Uninstall window close callback is already bound.")
        if not callable(callback):
            raise TypeError("Uninstall window close callback must be callable.")
        self._close_window = callback

    def bind_close_guard(self, callback: Callable[[], None]) -> None:
        if self._close_guard is not None:
            raise RuntimeError("Uninstall close guard is already bound.")
        if not callable(callback):
            raise TypeError("Uninstall close guard must be callable.")
        self._close_guard = callback

    def window_close_allowed(self) -> bool:
        with self._state_guard:
            return not (
                self._operation is not None and self._operation["phase"] == "running"
            )

    def window_closing(self) -> bool:
        """Handle the native close request without blocking the exit guard."""

        if not self.window_close_allowed():
            return False
        close_guard = self._close_guard
        if close_guard is None:
            return True
        # pywebview cancels a native close when a closing callback returns
        # False. Keep the window alive briefly so this callback can return and
        # the Python timer can acquire the GIL before terminating the temporary
        # execute process.
        close_guard()
        return False

    def can_close(self) -> dict[str, bool | str]:
        allowed = self.window_close_allowed()
        return {
            "ok": True,
            "allowed": allowed,
            "reason": ("" if allowed else "卸载正在进行，完成前不能关闭窗口。"),  # noqa: RUF001 - Preserve native Chinese UI punctuation.
        }

    def close_window(self) -> dict[str, bool | str]:
        if not self.window_close_allowed():
            return {
                "ok": False,
                "error": "卸载正在进行，完成前不能关闭窗口。",  # noqa: RUF001 - Preserve native Chinese UI punctuation.
            }
        callback = self._close_window
        if callback is None:
            return {
                "ok": False,
                "error": "卸载窗口关闭接口尚未就绪。",
            }
        try:
            close_guard = self._close_guard
            if close_guard is not None:
                # Do not call WebView2 destroy() for the temporary execute
                # process. It can block while holding the GIL, which prevents
                # a Python exit timer from ever running. Return to JavaScript
                # first and let the delayed guard end the process.
                close_guard()
            else:
                callback()
            return {"ok": True}
        except Exception as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }

    def request_exit(self) -> dict[str, bool | str]:
        """Signal the temporary execute process from the result page."""

        if not self.window_close_allowed():
            return {
                "ok": False,
                "error": "卸载正在进行，完成前不能退出。",  # noqa: RUF001 - Preserve native Chinese UI punctuation.
            }
        close_guard = self._close_guard
        if close_guard is None:
            return {
                "ok": False,
                "error": "临时卸载器退出信号尚未就绪。",
            }
        try:
            close_guard()
            return {"ok": True}
        except Exception as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }

    def get_status(self) -> dict[str, Any]:
        try:
            status = dict(self.runtime.get_status())
            return {
                "ok": True,
                "previewMode": bool(self.runtime.preview_mode),
                **status,
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }

    def validate_options(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            options = UninstallOptions.from_dict(payload)
            plan = build_uninstall_plan(
                InstallLayout(
                    Path(self.runtime.get_status()["programRoot"]),
                    Path(self.runtime.get_status()["dataRoot"]),
                ),
                options,
            )
            return {
                "ok": True,
                "options": options.as_dict(),
                "plan": plan.as_dict(),
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }

    def start_uninstall(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            options = UninstallOptions.from_dict(payload)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }
        if not self._operation_guard.acquire(blocking=False):
            return {
                "ok": False,
                "error": "卸载操作已在进行中。",
            }
        operation_id = uuid4().hex
        with self._state_guard:
            self._operation = {
                "operationId": operation_id,
                "phase": "running",
                "stage": "starting",
                "stages": ["starting"],
                "outcome": None,
                "error": None,
            }
        worker = threading.Thread(
            target=self._run_uninstall,
            args=(operation_id, options),
            name=f"ThemeSchedulerUninstall-{operation_id[:8]}",
            daemon=False,
        )
        try:
            worker.start()
        except Exception as exc:
            with self._state_guard:
                self._operation = None
            self._operation_guard.release()
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
            if operation["stages"][-1] != stage:
                operation["stages"].append(stage)

    def _run_uninstall(
        self,
        operation_id: str,
        options: UninstallOptions,
    ) -> None:
        outcome: dict[str, Any] | None = None
        error: str | None = None
        try:
            outcome = dict(
                self.runtime.uninstall(
                    options,
                    progress=lambda stage: self._set_stage(
                        operation_id,
                        stage,
                    ),
                )
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:500]
            self._set_stage(operation_id, "failed")
        finally:
            with self._state_guard:
                operation = self._operation
                if operation is not None and operation["operationId"] == operation_id:
                    operation["outcome"] = outcome
                    operation["error"] = error
                    operation["phase"] = "finished"
            self._operation_guard.release()

    def get_uninstall_status(
        self,
        operation_id: str,
    ) -> dict[str, Any]:
        if not isinstance(operation_id, str) or not operation_id:
            return {"ok": False, "error": "卸载会话标识无效。"}
        with self._state_guard:
            operation = self._operation
            if operation is None or operation["operationId"] != operation_id:
                return {"ok": False, "error": "卸载会话不存在或已过期。"}
            return {
                "ok": True,
                "operationId": operation_id,
                "phase": operation["phase"],
                "stage": operation["stage"],
                "stages": list(operation["stages"]),
                "outcome": deepcopy(operation["outcome"]),
                "error": operation["error"],
            }
