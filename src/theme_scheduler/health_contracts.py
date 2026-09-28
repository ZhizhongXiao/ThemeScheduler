"""Strict Stage 9 health-check result and repair-disposition contracts."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TypedDict

HEALTH_REPORT_KIND = "themescheduler.health-report"
HEALTH_REPORT_SCHEMA_VERSION = 1

_IDENTIFIER_PATTERN = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")


class HealthCheckPayload(TypedDict):
    id: str
    category: str
    status: str
    message: str
    repairAction: str | None


class HealthReportPayload(TypedDict):
    kind: str
    schemaVersion: int
    capturedAt: str
    status: str
    summary: dict[str, int]
    checks: list[HealthCheckPayload]


class HealthCategory(str, Enum):  # noqa: UP042 - Preserve str(Enum) output pending a dedicated migration.
    CONFIGURATION = "configuration"
    DATA = "data"
    FILES = "files"
    PERMISSIONS = "permissions"
    TASK = "task"
    WINDOWS = "windows"
    NOTIFICATION = "notification"


class HealthStatus(str, Enum):  # noqa: UP042 - Preserve str(Enum) output pending a dedicated migration.
    HEALTHY = "healthy"
    WARNING = "warning"
    REPAIRABLE = "repairable"
    ACTION_REQUIRED = "action-required"


_STATUS_PRIORITY = {
    HealthStatus.HEALTHY: 0,
    HealthStatus.WARNING: 1,
    HealthStatus.REPAIRABLE: 2,
    HealthStatus.ACTION_REQUIRED: 3,
}


def _identifier(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 64
        or not _IDENTIFIER_PATTERN.fullmatch(value)
    ):
        raise ValueError(f"{field} must use bounded lowercase dotted tokens.")
    return value


@dataclass(frozen=True)
class HealthCheck:
    check_id: str
    category: HealthCategory
    status: HealthStatus
    message: str
    repair_action: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.check_id, "check_id")
        if not isinstance(self.category, HealthCategory):
            raise ValueError("category must be a HealthCategory.")
        if not isinstance(self.status, HealthStatus):
            raise ValueError("status must be a HealthStatus.")
        if (
            not isinstance(self.message, str)
            or not self.message
            or len(self.message) > 500
        ):
            raise ValueError("message must contain 1-500 characters.")
        if self.repair_action is not None:
            _identifier(self.repair_action, "repair_action")
        if self.status is HealthStatus.REPAIRABLE and self.repair_action is None:
            raise ValueError("A repairable check must name an explicit repair action.")
        if (
            self.status is not HealthStatus.REPAIRABLE
            and self.repair_action is not None
        ):
            raise ValueError("Only repairable checks can expose a repair action.")

    def as_dict(self) -> HealthCheckPayload:
        return {
            "id": self.check_id,
            "category": self.category.value,
            "status": self.status.value,
            "message": self.message,
            "repairAction": self.repair_action,
        }


@dataclass(frozen=True)
class HealthReport:
    captured_at: str
    checks: tuple[HealthCheck, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.captured_at, str):
            raise ValueError("captured_at must be ISO 8601 text.")
        try:
            instant = datetime.fromisoformat(self.captured_at)
        except ValueError as exc:
            raise ValueError("captured_at must be ISO 8601.") from exc
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("captured_at must include a UTC offset.")
        if not isinstance(self.checks, tuple) or not self.checks:
            raise ValueError("checks must be a non-empty tuple.")
        identifiers = [check.check_id for check in self.checks]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Health check identifiers must be unique.")

    @property
    def status(self) -> HealthStatus:
        return max(
            (check.status for check in self.checks),
            key=_STATUS_PRIORITY.__getitem__,
        )

    def as_dict(self) -> HealthReportPayload:
        counts = Counter(check.status.value for check in self.checks)
        return {
            "kind": HEALTH_REPORT_KIND,
            "schemaVersion": HEALTH_REPORT_SCHEMA_VERSION,
            "capturedAt": self.captured_at,
            "status": self.status.value,
            "summary": {status.value: counts[status.value] for status in HealthStatus},
            "checks": [check.as_dict() for check in self.checks],
        }
