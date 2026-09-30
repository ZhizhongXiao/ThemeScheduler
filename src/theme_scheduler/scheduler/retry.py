"""Durable, one-shot scheduling for retryable automatic-run failures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from ..core import ExecutionLock
from .constants import AUTO_RETRY_TRIGGER_ID
from .models import TaskSpec
from .mutation import (
    TaskSchedulerBackend,
    _reconcile_task_locked,
    scheduler_mutation,
)
from .triggers import TimeTrigger


class AutoRetryStatus(str, Enum):  # noqa: UP042 - Preserve string enum wire values.
    """Whether a future AutoRetry trigger was created or already existed."""

    CREATED = "created"
    EXISTING = "existing"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class AutoRetryOutcome:
    status: AutoRetryStatus
    retry_at: datetime | None
    wakeup_guaranteed: bool
    reconciliation_succeeded: bool
    error: str | None = None


def ceil_to_whole_minute(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("AutoRetry requires an aware local datetime.")
    floor = value.replace(second=0, microsecond=0)
    return floor if floor == value else floor + timedelta(minutes=1)


def _future_retry(task: TaskSpec | None, now: datetime) -> TimeTrigger | None:
    if task is None:
        return None
    trigger = next(
        (item for item in task.triggers if item.trigger_id == AUTO_RETRY_TRIGGER_ID),
        None,
    )
    if (
        isinstance(trigger, TimeTrigger)
        and datetime.fromisoformat(trigger.start_at) > now
    ):
        return trigger
    return None


def ensure_future_auto_retry(
    backend: TaskSchedulerBackend,
    desired: TaskSpec,
    now: datetime,
    *,
    mutation_lock: ExecutionLock | None = None,
) -> AutoRetryOutcome:
    """Ensure one future AutoRetry without moving an existing appointment."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("AutoRetry requires an aware local datetime.")
    try:
        with scheduler_mutation(mutation_lock):
            try:
                before = backend.read(desired.task_path)
            except Exception as exc:
                return AutoRetryOutcome(
                    AutoRetryStatus.UNAVAILABLE,
                    None,
                    False,
                    False,
                    f"Task Scheduler readback failed: {type(exc).__name__}: {exc}",
                )
            existing = _future_retry(before, now)
            retry_at = (
                datetime.fromisoformat(existing.start_at)
                if existing is not None
                else ceil_to_whole_minute(now + timedelta(minutes=1))
            )
            try:
                _reconcile_task_locked(
                    backend,
                    desired,
                    now=now,
                    auto_retry_at=retry_at,
                )
            except Exception as exc:
                try:
                    after = backend.read(desired.task_path)
                except Exception as readback_exc:
                    return AutoRetryOutcome(
                        AutoRetryStatus.UNAVAILABLE,
                        None,
                        False,
                        False,
                        f"Retry reconciliation and readback failed: "
                        f"{type(exc).__name__}; {type(readback_exc).__name__}",
                    )
                confirmed = _future_retry(after, now)
                if confirmed is None:
                    return AutoRetryOutcome(
                        AutoRetryStatus.UNAVAILABLE,
                        None,
                        False,
                        False,
                        f"Retry reconciliation failed: {type(exc).__name__}: {exc}",
                    )
                status = (
                    AutoRetryStatus.EXISTING
                    if existing is not None
                    else AutoRetryStatus.CREATED
                )
                return AutoRetryOutcome(
                    status,
                    datetime.fromisoformat(confirmed.start_at),
                    True,
                    False,
                    f"Task reconciliation failed; a future retry is confirmed: "
                    f"{type(exc).__name__}: {exc}",
                )
            try:
                after = backend.read(desired.task_path)
            except Exception as exc:
                return AutoRetryOutcome(
                    AutoRetryStatus.UNAVAILABLE,
                    None,
                    False,
                    False,
                    f"Retry readback failed: {type(exc).__name__}: {exc}",
                )
            confirmed = _future_retry(after, now)
            if confirmed is None:
                return AutoRetryOutcome(
                    AutoRetryStatus.UNAVAILABLE,
                    None,
                    False,
                    False,
                    "Task Scheduler did not retain a future AutoRetry trigger.",
                )
            return AutoRetryOutcome(
                AutoRetryStatus.EXISTING
                if existing is not None
                else AutoRetryStatus.CREATED,
                datetime.fromisoformat(confirmed.start_at),
                True,
                True,
            )
    except Exception as exc:
        return AutoRetryOutcome(
            AutoRetryStatus.UNAVAILABLE,
            None,
            False,
            False,
            f"Scheduler mutation lock failed: {type(exc).__name__}: {exc}",
        )
