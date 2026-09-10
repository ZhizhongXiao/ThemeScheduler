"""Normalized scheduled-task action, principal, settings and spec."""

from __future__ import annotations

# These contracts deliberately revalidate annotated values after JSON input,
# and package-private validators are shared across the scheduler package.
# pyright: strict, reportPrivateUsage=false, reportUnnecessaryIsInstance=false
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from ._validation import (
    _boolean,
    _exact_keys,
    _mapping,
    _string,
    _text,
)
from .constants import (
    EXECUTION_TIME_LIMIT,
    TASK_SPEC_KIND,
    TASK_SPEC_SCHEMA_VERSION,
)
from .errors import SchedulerContractError
from .triggers import TaskTrigger, _trigger_from_dict


@dataclass(frozen=True)
class TaskAction:
    executable: str
    arguments: str
    working_directory: str
    action_type: str = "Exec"
    action_count: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.executable, str):
            raise SchedulerContractError("task.action.executable must be a string.")
        if not isinstance(self.working_directory, str):
            raise SchedulerContractError(
                "task.action.workingDirectory must be a string."
            )
        if any(
            character in self.executable + self.working_directory
            for character in ('"', "\r", "\n")
        ):
            raise SchedulerContractError("Task action paths contain unsafe characters.")
        if not isinstance(self.arguments, str):
            raise SchedulerContractError("task.action.arguments must be a string.")
        if "\r" in self.arguments or "\n" in self.arguments:
            raise SchedulerContractError("Task action arguments are unsafe.")
        _text(self.action_type, "task.action.type")
        if (
            not isinstance(self.action_count, int)
            or isinstance(self.action_count, bool)
            or self.action_count < 0
        ):
            raise SchedulerContractError(
                "task.action.count must be a non-negative integer."
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "executable": self.executable,
            "arguments": self.arguments,
            "workingDirectory": self.working_directory,
            "type": self.action_type,
            "count": self.action_count,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> TaskAction:
        _exact_keys(
            payload,
            {
                "executable",
                "arguments",
                "workingDirectory",
                "type",
                "count",
            },
            "task.action",
        )
        return cls(
            _string(payload["executable"], "task.action.executable"),
            _string(payload["arguments"], "task.action.arguments"),
            _string(
                payload["workingDirectory"],
                "task.action.workingDirectory",
            ),
            _text(payload["type"], "task.action.type"),
            payload["count"],
        )


@dataclass(frozen=True)
class TaskPrincipal:
    user_id: str
    logon_type: str = "InteractiveToken"
    run_level: str = "LeastPrivilege"

    def __post_init__(self) -> None:
        _text(self.user_id, "task.principal.userId")
        _text(self.logon_type, "task.principal.logonType")
        _text(self.run_level, "task.principal.runLevel")

    def as_dict(self) -> dict[str, Any]:
        return {
            "userId": self.user_id,
            "logonType": self.logon_type,
            "runLevel": self.run_level,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> TaskPrincipal:
        _exact_keys(
            payload,
            {"userId", "logonType", "runLevel"},
            "task.principal",
        )
        return cls(
            _text(payload["userId"], "task.principal.userId"),
            _text(payload["logonType"], "task.principal.logonType"),
            _text(payload["runLevel"], "task.principal.runLevel"),
        )


@dataclass(frozen=True)
class TaskSettings:
    start_when_available: bool = True
    wake_to_run: bool = False
    multiple_instances: str = "IgnoreNew"
    allow_start_on_batteries: bool = True
    stop_if_going_on_batteries: bool = False
    run_only_if_network_available: bool = False
    hidden: bool = False
    execution_time_limit: str = EXECUTION_TIME_LIMIT

    def __post_init__(self) -> None:
        for name in (
            "start_when_available",
            "wake_to_run",
            "allow_start_on_batteries",
            "stop_if_going_on_batteries",
            "run_only_if_network_available",
            "hidden",
        ):
            if not isinstance(getattr(self, name), bool):
                raise SchedulerContractError(f"Task setting {name} must be boolean.")
        _text(self.multiple_instances, "task.settings.multipleInstances")
        _text(self.execution_time_limit, "task.settings.executionTimeLimit")

    def as_dict(self) -> dict[str, Any]:
        return {
            "startWhenAvailable": self.start_when_available,
            "wakeToRun": self.wake_to_run,
            "multipleInstances": self.multiple_instances,
            "allowStartOnBatteries": self.allow_start_on_batteries,
            "stopIfGoingOnBatteries": self.stop_if_going_on_batteries,
            "runOnlyIfNetworkAvailable": self.run_only_if_network_available,
            "hidden": self.hidden,
            "executionTimeLimit": self.execution_time_limit,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> TaskSettings:
        _exact_keys(
            payload,
            {
                "startWhenAvailable",
                "wakeToRun",
                "multipleInstances",
                "allowStartOnBatteries",
                "stopIfGoingOnBatteries",
                "runOnlyIfNetworkAvailable",
                "hidden",
                "executionTimeLimit",
            },
            "task.settings",
        )
        return cls(
            _boolean(
                payload["startWhenAvailable"],
                "task.settings.startWhenAvailable",
            ),
            _boolean(payload["wakeToRun"], "task.settings.wakeToRun"),
            _text(
                payload["multipleInstances"],
                "task.settings.multipleInstances",
            ),
            _boolean(
                payload["allowStartOnBatteries"],
                "task.settings.allowStartOnBatteries",
            ),
            _boolean(
                payload["stopIfGoingOnBatteries"],
                "task.settings.stopIfGoingOnBatteries",
            ),
            _boolean(
                payload["runOnlyIfNetworkAvailable"],
                "task.settings.runOnlyIfNetworkAvailable",
            ),
            _boolean(payload["hidden"], "task.settings.hidden"),
            _text(
                payload["executionTimeLimit"],
                "task.settings.executionTimeLimit",
            ),
        )


@dataclass(frozen=True)
class TaskSpec:
    task_path: str
    enabled: bool
    triggers: tuple[TaskTrigger, ...]
    action: TaskAction
    principal: TaskPrincipal
    settings: TaskSettings

    def __post_init__(self) -> None:
        if (
            not isinstance(self.task_path, str)
            or not self.task_path.startswith("\\")
            or self.task_path == "\\"
            or self.task_path.endswith("\\")
        ):
            raise SchedulerContractError("taskPath is invalid.")
        if not isinstance(self.enabled, bool):
            raise SchedulerContractError("Task enabled must be boolean.")
        if not isinstance(self.triggers, tuple):
            raise SchedulerContractError("Task triggers must be a tuple.")
        ids = [trigger.trigger_id for trigger in self.triggers]
        if len(ids) != len(set(ids)):
            raise SchedulerContractError("Task trigger ids must be unique.")
        object.__setattr__(
            self,
            "triggers",
            tuple(sorted(self.triggers, key=lambda item: item.trigger_id)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": TASK_SPEC_KIND,
            "schemaVersion": TASK_SPEC_SCHEMA_VERSION,
            "taskPath": self.task_path,
            "enabled": self.enabled,
            "triggers": [
                trigger.as_dict()
                for trigger in sorted(self.triggers, key=lambda item: item.trigger_id)
            ],
            "action": self.action.as_dict(),
            "principal": self.principal.as_dict(),
            "settings": self.settings.as_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> TaskSpec:
        _exact_keys(
            payload,
            {
                "kind",
                "schemaVersion",
                "taskPath",
                "enabled",
                "triggers",
                "action",
                "principal",
                "settings",
            },
            "task",
        )
        if payload["kind"] != TASK_SPEC_KIND:
            raise SchedulerContractError("JSON is not a ThemeScheduler task spec.")
        schema_version = payload["schemaVersion"]
        if schema_version not in {1, TASK_SPEC_SCHEMA_VERSION}:
            raise SchedulerContractError("Unsupported task spec schemaVersion.")
        triggers = payload["triggers"]
        if not isinstance(triggers, list):
            raise SchedulerContractError("task.triggers must be an array.")
        return cls(
            _text(payload["taskPath"], "task.taskPath"),
            _boolean(payload["enabled"], "task.enabled"),
            tuple(
                _trigger_from_dict(_mapping(item, "task.trigger"))
                for item in cast(list[object], triggers)
            ),
            TaskAction.from_dict(_mapping(payload["action"], "task.action")),
            TaskPrincipal.from_dict(_mapping(payload["principal"], "task.principal")),
            TaskSettings.from_dict(_mapping(payload["settings"], "task.settings")),
        )
