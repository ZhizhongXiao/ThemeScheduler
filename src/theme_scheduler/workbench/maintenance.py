"""Explicit maintenance operations exposed by the workbench."""

from __future__ import annotations

from typing import Any

from ..config import AppConfig, ConfigStore
from ..configuration_service import ConfigurationService
from ..scheduler import build_task_spec, inspect_task
from .contracts import HealthResult, WorkbenchBindings


class GuiMaintenanceMixin(WorkbenchBindings):
    def check_task(self) -> dict[str, Any]:
        with self._api_lock:
            try:
                config = ConfigStore(self._layout.config).load()
                desired = build_task_spec(
                    config,
                    executable=str(self._executable),
                    user_id=self._scheduler.current_user_id(),
                )
                inspection = inspect_task(
                    desired,
                    self._scheduler.read(desired.task_path),
                )
                return {
                    "action": "check-task",
                    "result": "success" if inspection.valid else "drift",
                    "message": (
                        "Task Scheduler definition is consistent."
                        if inspection.valid
                        else "Task Scheduler definition has drifted."
                    ),
                    "inspection": inspection.as_dict(),
                    "dataChanged": False,
                    "windowsChanged": False,
                    "taskSchedulerAccessed": True,
                    "taskSchedulerChanged": False,
                }
            except Exception as exc:
                return self._error("check-task", exc)

    def check_health(self) -> HealthResult:
        with self._api_lock:
            if self._health_service_factory is None:
                return HealthResult(
                    **self._error(
                        "check-health",
                        RuntimeError("Installed health inspection is unavailable."),
                    )
                )
            try:
                report = self._health_service_factory().inspect()
                response = report.as_dict()
                status = response["status"]
                messages = {
                    "healthy": "健康检查完成：全部正常。",  # noqa: RUF001 - Preserve native Chinese UI punctuation.
                    "warning": "健康检查完成：发现警告，请查看详细报告。",  # noqa: RUF001 - Preserve native Chinese UI punctuation.
                    "repairable": "健康检查完成：发现可修复问题，请查看报告后选择修复。",  # noqa: RUF001 - Preserve native Chinese UI punctuation.
                    "action-required": "健康检查完成：发现需要用户处理的问题。",  # noqa: RUF001 - Preserve native Chinese UI punctuation.
                }
                return HealthResult(
                    action="check-health",
                    result="success" if status == "healthy" else status,
                    message=messages[status],
                    dataChanged=False,
                    windowsChanged=False,
                    taskSchedulerChanged=False,
                    kind=response["kind"],
                    schemaVersion=response["schemaVersion"],
                    capturedAt=response["capturedAt"],
                    status=status,
                    summary=response["summary"],
                    checks=response["checks"],
                )
            except Exception as exc:
                return HealthResult(**self._error("check-health", exc))

    def repair_task(self, confirmed: bool = False) -> dict[str, Any]:
        with self._api_lock:
            if not self._allow_live_writes:
                return {
                    "action": "repair-task",
                    "result": "blocked",
                    "message": (
                        "Live task changes are disabled for this development launch."
                    ),
                    "dataChanged": False,
                    "windowsChanged": False,
                    "taskSchedulerChanged": False,
                }
            if confirmed is not True:
                return {
                    "action": "repair-task",
                    "result": "blocked",
                    "message": "Task repair requires explicit confirmation.",
                    "dataChanged": False,
                    "windowsChanged": False,
                    "taskSchedulerChanged": False,
                }
            try:
                config = ConfigStore(self._layout.config).load()
                service = ConfigurationService(
                    self._layout,
                    self._lock_factory(),
                    self._scheduler,
                    executable=str(self._executable),
                    user_id=self._scheduler.current_user_id(),
                    audit_event="task.repaired",
                    audit_trigger="repair",
                )
                response = service.update(config).as_dict()
                response["action"] = "repair-task"
                return response
            except Exception as exc:
                return self._error("repair-task", exc)

    def repair_notification_identity(self, confirmed: bool = False) -> dict[str, Any]:
        with self._api_lock:
            if not self._allow_live_writes:
                return {
                    "action": "notification-identity-repair",
                    "result": "blocked",
                    "message": ("Live notification identity repair is disabled."),
                    "dataChanged": False,
                    "windowsChanged": False,
                    "taskSchedulerChanged": False,
                }
            if confirmed is not True:
                return {
                    "action": "notification-identity-repair",
                    "result": "blocked",
                    "message": (
                        "Notification identity repair requires explicit confirmation."
                    ),
                    "dataChanged": False,
                    "windowsChanged": False,
                    "taskSchedulerChanged": False,
                }
            if self._identity_repair_service_factory is None:
                return self._error(
                    "notification-identity-repair",
                    RuntimeError("Notification identity repair is unavailable."),
                )
            try:
                return self._identity_repair_service_factory().repair().as_dict()
            except Exception as exc:
                return self._error("notification-identity-repair", exc)

    def reset_preferences(
        self,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        with self._api_lock:
            if not self._allow_live_writes:
                return {
                    "action": "reset-preferences",
                    "result": "blocked",
                    "message": ("Live configuration and task changes are disabled."),
                    "dataChanged": False,
                    "windowsChanged": False,
                    "taskSchedulerChanged": False,
                }
            if confirmed is not True:
                return {
                    "action": "reset-preferences",
                    "result": "blocked",
                    "message": (
                        "Default time and notification reset requires "
                        "explicit confirmation."
                    ),
                    "dataChanged": False,
                    "windowsChanged": False,
                    "taskSchedulerChanged": False,
                }
            try:
                current = ConfigStore(self._layout.config).load()
                defaults = AppConfig.defaults()
                target = AppConfig(
                    day_start=defaults.day_start,
                    night_start=defaults.night_start,
                    day_apps_theme=current.day_apps_theme,
                    night_apps_theme=current.night_apps_theme,
                    notify_errors=defaults.notify_errors,
                    notify_status_changes=(defaults.notify_status_changes),
                )
                service = ConfigurationService(
                    self._layout,
                    self._lock_factory(),
                    self._scheduler,
                    executable=str(self._executable),
                    user_id=self._scheduler.current_user_id(),
                    audit_event="maintenance.preferences-reset",
                    audit_trigger="repair",
                )
                response = service.update(target).as_dict()
                response["action"] = "reset-preferences"
                return response
            except Exception as exc:
                return self._error("reset-preferences", exc)

    def restore_install_appearance(
        self,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        with self._api_lock:
            if not self._allow_live_writes:
                return {
                    "action": "restore-install-appearance",
                    "result": "blocked",
                    "message": ("Live appearance restore is disabled for this launch."),
                    "dataChanged": False,
                    "windowsChanged": False,
                    "taskSchedulerChanged": False,
                }
            if confirmed is not True:
                return {
                    "action": "restore-install-appearance",
                    "result": "blocked",
                    "message": (
                        "Install appearance restore requires explicit confirmation."
                    ),
                    "dataChanged": False,
                    "windowsChanged": False,
                    "taskSchedulerChanged": False,
                }
            if self._maintenance_service_factory is None:
                return self._error(
                    "restore-install-appearance",
                    RuntimeError("Maintenance appearance service is unavailable."),
                )
            try:
                service = self._maintenance_service_factory()
                return service.restore_install_appearance().as_dict()
            except Exception as exc:
                return self._error(
                    "restore-install-appearance",
                    exc,
                )

    def launch_uninstaller(
        self,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        with self._api_lock:
            if not self._allow_live_writes:
                return {
                    "action": "launch-uninstaller",
                    "result": "blocked",
                    "message": (
                        "Installed maintenance actions are disabled for this launch."
                    ),
                    "dataChanged": False,
                    "windowsChanged": False,
                    "taskSchedulerChanged": False,
                }
            if confirmed is not True:
                return {
                    "action": "launch-uninstaller",
                    "result": "blocked",
                    "message": (
                        "Launching the independent uninstaller requires "
                        "explicit confirmation."
                    ),
                    "dataChanged": False,
                    "windowsChanged": False,
                    "taskSchedulerChanged": False,
                }
            try:
                self._shell.open("uninstaller")
                return {
                    "action": "launch-uninstaller",
                    "result": "success",
                    "message": "The independent uninstaller was launched.",
                    "dataChanged": False,
                    "windowsChanged": False,
                    "taskSchedulerChanged": False,
                }
            except Exception as exc:
                return self._error("launch-uninstaller", exc)

    def open_target(self, target: str) -> dict[str, Any]:
        with self._api_lock:
            try:
                if target not in {"colors", "logs", "data"}:
                    raise ValueError("Unsupported navigation target.")
                self._shell.open(target)
                return {
                    "action": "open-target",
                    "result": "success",
                    "target": target,
                    "message": "Windows opened the requested target.",
                    "dataChanged": False,
                    "windowsChanged": False,
                    "taskSchedulerChanged": False,
                }
            except Exception as exc:
                return self._error("open-target", exc)
