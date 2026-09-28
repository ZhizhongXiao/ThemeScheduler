"""Frozen stage-3 logging and runtime-retention parameters."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from .errors import DataError

LOG_KIND = "themescheduler.event"
LOG_SCHEMA_VERSION = 2
LOG_FILE_NAME = "events.jsonl"
LOG_MAX_BYTES = 1_048_576
LOG_BACKUP_COUNT = 5

SUCCESSFUL_TRANSACTION_KEEP = 10
FAILED_TRANSACTION_RETENTION_DAYS = 30
FAILED_TRANSACTION_MINIMUM_KEEP = 10

LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
LOG_RESULTS = frozenset({"success", "skipped", "partial", "failed"})
LOG_VERIFICATIONS = frozenset({"passed", "failed"})
LOG_TRIGGERS = frozenset(
    {"auto", "manual", "install", "upgrade", "uninstall", "repair", "system"}
)
_EVENT_PATTERN = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")


class LogEventValidationError(DataError):
    """Raised when a structured event would violate the privacy-safe schema."""


def _optional_text(value: Any, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise LogEventValidationError(
            f"{field} must be null or 1-{maximum} characters."
        )
    return value


@dataclass(frozen=True)
class LogEvent:
    occurred_at: str
    level: str
    event: str
    result: str
    trigger: str
    target_profile: str | None = None
    transaction_id: str | None = None
    error_code: str | None = None
    message: str | None = None
    verification: str | None = None
    rollback_attempted: bool = False
    rollback_succeeded: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.occurred_at, str):
            raise LogEventValidationError("occurredAt must be ISO 8601.")
        try:
            parsed = datetime.fromisoformat(self.occurred_at)
        except (TypeError, ValueError) as exc:
            raise LogEventValidationError("occurredAt must be ISO 8601.") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise LogEventValidationError("occurredAt must include a UTC offset.")
        if not isinstance(self.level, str) or self.level not in LOG_LEVELS:
            raise LogEventValidationError("level is unsupported.")
        if not isinstance(self.event, str) or not _EVENT_PATTERN.fullmatch(self.event):
            raise LogEventValidationError("event must use lowercase dotted tokens.")
        if len(self.event) > 64:
            raise LogEventValidationError("event is longer than 64 characters.")
        if not isinstance(self.result, str) or self.result not in LOG_RESULTS:
            raise LogEventValidationError("result is unsupported.")
        if not isinstance(self.trigger, str) or self.trigger not in LOG_TRIGGERS:
            raise LogEventValidationError("trigger is unsupported.")
        if self.target_profile is not None and (
            not isinstance(self.target_profile, str)
            or self.target_profile not in {"day", "night"}
        ):
            raise LogEventValidationError("targetProfile is unsupported.")
        _optional_text(self.transaction_id, "transactionId", 96)
        _optional_text(self.error_code, "errorCode", 64)
        _optional_text(self.message, "message", 500)
        if self.verification is not None and self.verification not in LOG_VERIFICATIONS:
            raise LogEventValidationError("verification is unsupported.")
        if not isinstance(self.rollback_attempted, bool):
            raise LogEventValidationError("rollbackAttempted must be boolean.")
        if self.rollback_succeeded is not None and not isinstance(
            self.rollback_succeeded, bool
        ):
            raise LogEventValidationError("rollbackSucceeded must be boolean or null.")
        if not self.rollback_attempted and self.rollback_succeeded is not None:
            raise LogEventValidationError(
                "rollbackSucceeded requires a rollback attempt."
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": LOG_KIND,
            "schemaVersion": LOG_SCHEMA_VERSION,
            "occurredAt": self.occurred_at,
            "level": self.level,
            "event": self.event,
            "result": self.result,
            "trigger": self.trigger,
            "targetProfile": self.target_profile,
            "transactionId": self.transaction_id,
            "errorCode": self.error_code,
            "message": self.message,
            "verification": self.verification,
            "rollbackAttempted": self.rollback_attempted,
            "rollbackSucceeded": self.rollback_succeeded,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> LogEvent:
        v1_fields = {
            "kind",
            "schemaVersion",
            "occurredAt",
            "level",
            "event",
            "result",
            "trigger",
            "targetProfile",
            "transactionId",
            "errorCode",
            "message",
        }
        v2_fields = v1_fields | {
            "verification",
            "rollbackAttempted",
            "rollbackSucceeded",
        }
        version = payload.get("schemaVersion")
        expected = (
            v1_fields
            if version == 1
            else v2_fields
            if version == LOG_SCHEMA_VERSION
            else None
        )
        if expected is None:
            raise LogEventValidationError("Unsupported log event schemaVersion.")
        if set(payload) != expected:
            raise LogEventValidationError(
                "Log event fields do not match its versioned schema."
            )
        if payload.get("kind") != LOG_KIND:
            raise LogEventValidationError("JSON is not a ThemeScheduler log event.")
        return cls(
            occurred_at=payload["occurredAt"],
            level=payload["level"],
            event=payload["event"],
            result=payload["result"],
            trigger=payload["trigger"],
            target_profile=payload["targetProfile"],
            transaction_id=payload["transactionId"],
            error_code=payload["errorCode"],
            message=payload["message"],
            verification=(payload["verification"] if version == 2 else None),
            rollback_attempted=(
                payload["rollbackAttempted"] if version == 2 else False
            ),
            rollback_succeeded=(payload["rollbackSucceeded"] if version == 2 else None),
        )


class EventLogSink(Protocol):
    """Append an event; in-memory sinks need not return a file path."""

    def append(self, event: LogEvent) -> Path | None: ...


class EventLogWriter:
    """Append validated JSONL events and rotate before crossing the size limit."""

    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = LOG_MAX_BYTES,
        backup_count: int = LOG_BACKUP_COUNT,
    ) -> None:
        if max_bytes < 256:
            raise ValueError("Log max_bytes must be at least 256.")
        if backup_count < 1:
            raise ValueError("Log backup_count must be at least 1.")
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.backup_count = backup_count

    def _backup_path(self, index: int) -> Path:
        return self.path.with_name(f"{self.path.name}.{index}")

    def _rotate(self) -> None:
        oldest = self._backup_path(self.backup_count)
        oldest.unlink(missing_ok=True)
        for index in range(self.backup_count - 1, 0, -1):
            source = self._backup_path(index)
            if source.exists():
                os.replace(source, self._backup_path(index + 1))
        if self.path.exists():
            os.replace(self.path, self._backup_path(1))

    def append(self, event: LogEvent) -> Path:
        payload = json.dumps(
            event.as_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        encoded = (payload + "\n").encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if (
            self.path.exists()
            and self.path.stat().st_size > 0
            and self.path.stat().st_size + len(encoded) > self.max_bytes
        ):
            self._rotate()
        with self.path.open("ab") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        return self.path
