"""Stable field-level inspection of normalized task definitions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .models import TaskSpec


@dataclass(frozen=True)
class TaskDifference:
    field: str
    expected: Any
    actual: Any

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "expected": self.expected,
            "actual": self.actual,
        }


def compare_task_specs(
    expected: TaskSpec, actual: TaskSpec
) -> tuple[TaskDifference, ...]:
    """Compare normalized task definitions using stable field paths."""

    differences: list[TaskDifference] = []

    def compare(field: str, expected_value: Any, actual_value: Any) -> None:
        if isinstance(expected_value, Mapping) and isinstance(actual_value, Mapping):
            for key in sorted(set(expected_value) | set(actual_value)):
                compare(
                    f"{field}.{key}" if field else key,
                    expected_value.get(key),
                    actual_value.get(key),
                )
            return
        if isinstance(expected_value, list) and isinstance(actual_value, list):
            if expected_value != actual_value:
                differences.append(TaskDifference(field, expected_value, actual_value))
            return
        if expected_value != actual_value:
            differences.append(TaskDifference(field, expected_value, actual_value))

    compare("", expected.as_dict(), actual.as_dict())
    return tuple(differences)


@dataclass(frozen=True)
class TaskInspection:
    exists: bool
    valid: bool
    differences: tuple[TaskDifference, ...]
    actual: TaskSpec | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "exists": self.exists,
            "valid": self.valid,
            "differences": [difference.as_dict() for difference in self.differences],
            "actual": self.actual.as_dict() if self.actual else None,
        }


def inspect_task(expected: TaskSpec, actual: TaskSpec | None) -> TaskInspection:
    if actual is None:
        return TaskInspection(
            False,
            False,
            (TaskDifference("task", expected.as_dict(), None),),
            None,
        )
    differences = compare_task_specs(expected, actual)
    return TaskInspection(True, not differences, differences, actual)
