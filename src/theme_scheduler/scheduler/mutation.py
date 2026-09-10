"""Verified task mutation with exact previous-definition recovery."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

from ._validation import _boolean, _exact_keys, _text
from .constants import (
    DEFAULT_TASK_PATH,
    TASK_BACKUP_KIND,
    TASK_BACKUP_SCHEMA_VERSION,
)
from .errors import SchedulerContractError, SchedulerMutationError
from .inspection import compare_task_specs
from .models import TaskSpec
from .specification import validate_desired_task


class TaskSchedulerBackend(Protocol):
    """Normalized boundary implemented by the isolated Windows bridge."""

    def current_user_id(self) -> str: ...

    def read(self, task_path: str) -> TaskSpec | None: ...

    def register(self, task: TaskSpec) -> None: ...

    def capture(self, task_path: str) -> TaskDefinitionBackup | None: ...

    def restore(self, backup: TaskDefinitionBackup) -> None: ...

    def delete(self, task_path: str) -> None: ...


@dataclass(frozen=True)
class TaskDefinitionBackup:
    task_path: str
    definition_xml: str
    enabled: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.task_path, str)
            or not self.task_path.startswith("\\")
            or self.task_path == "\\"
        ):
            raise SchedulerContractError("Task backup taskPath is invalid.")
        if (
            not isinstance(self.definition_xml, str)
            or not self.definition_xml.strip()
            or len(self.definition_xml) > 1_048_576
        ):
            raise SchedulerContractError(
                "Task backup definitionXml is empty or too large."
            )
        if not isinstance(self.enabled, bool):
            raise SchedulerContractError("Task backup enabled must be boolean.")

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": TASK_BACKUP_KIND,
            "schemaVersion": TASK_BACKUP_SCHEMA_VERSION,
            "taskPath": self.task_path,
            "definitionXml": self.definition_xml,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> TaskDefinitionBackup:
        _exact_keys(
            payload,
            {
                "kind",
                "schemaVersion",
                "taskPath",
                "definitionXml",
                "enabled",
            },
            "taskBackup",
        )
        if payload["kind"] != TASK_BACKUP_KIND:
            raise SchedulerContractError(
                "JSON is not a ThemeScheduler task definition backup."
            )
        if payload["schemaVersion"] != TASK_BACKUP_SCHEMA_VERSION:
            raise SchedulerContractError(
                "Unsupported task definition backup schemaVersion."
            )
        return cls(
            _text(payload["taskPath"], "taskBackup.taskPath"),
            _text(
                payload["definitionXml"],
                "taskBackup.definitionXml",
            ),
            _boolean(payload["enabled"], "taskBackup.enabled"),
        )


@dataclass(frozen=True)
class TaskMutationOutcome:
    operation: str
    changed: bool
    verified: bool
    before: TaskSpec | None
    after: TaskSpec | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "changed": self.changed,
            "verified": self.verified,
            "before": self.before.as_dict() if self.before else None,
            "after": self.after.as_dict() if self.after else None,
        }


def _restore_previous(
    backend: TaskSchedulerBackend,
    task_path: str,
    before: TaskSpec | None,
    backup: TaskDefinitionBackup | None,
) -> bool:
    try:
        if before is None:
            backend.delete(task_path)
            return backend.read(task_path) is None
        if backup is None:
            return False
        backend.restore(backup)
        restored = backend.read(task_path)
        return restored is not None and not compare_task_specs(before, restored)
    except Exception:
        return False


def reconcile_task(
    backend: TaskSchedulerBackend, desired: TaskSpec
) -> TaskMutationOutcome:
    """Atomically replace one definition and verify, restoring on failure."""

    validate_desired_task(desired)
    before = backend.read(desired.task_path)
    if before is not None and not compare_task_specs(desired, before):
        return TaskMutationOutcome("unchanged", False, True, before, before)
    backup = backend.capture(desired.task_path)
    if (before is None) != (backup is None):
        raise SchedulerMutationError(
            "Task changed while its recovery definition was captured.",
            rollback_attempted=False,
            rollback_succeeded=False,
        )
    try:
        backend.register(desired)
        after = backend.read(desired.task_path)
        if after is None or compare_task_specs(desired, after):
            raise OSError("Task Scheduler readback did not match desired task.")
    except Exception as exc:
        rollback_succeeded = _restore_previous(
            backend, desired.task_path, before, backup
        )
        raise SchedulerMutationError(
            f"Task registration failed: {exc}",
            rollback_attempted=True,
            rollback_succeeded=rollback_succeeded,
        ) from exc
    return TaskMutationOutcome(
        "created" if before is None else "repaired",
        True,
        True,
        before,
        after,
    )


def set_task_enabled(
    backend: TaskSchedulerBackend,
    task_path: str,
    enabled: bool,
) -> TaskMutationOutcome:
    before = backend.read(task_path)
    if before is None:
        raise SchedulerMutationError(
            "Cannot change enabled state because the task is absent.",
            rollback_attempted=False,
            rollback_succeeded=False,
        )
    return reconcile_task(backend, replace(before, enabled=enabled))


def delete_task(
    backend: TaskSchedulerBackend, task_path: str = DEFAULT_TASK_PATH
) -> TaskMutationOutcome:
    before = backend.read(task_path)
    if before is None:
        return TaskMutationOutcome("absent", False, True, None, None)
    try:
        backend.delete(task_path)
        after = backend.read(task_path)
    except Exception as exc:
        raise SchedulerMutationError(
            f"Task deletion failed: {exc}",
            rollback_attempted=False,
            rollback_succeeded=False,
        ) from exc
    if after is not None:
        raise SchedulerMutationError(
            "Task deletion readback still found the task.",
            rollback_attempted=False,
            rollback_succeeded=False,
        )
    return TaskMutationOutcome("deleted", True, True, before, None)
