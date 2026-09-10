"""Guarded notification-button actions; never applies a Windows theme."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any

from .config import ConfigStore
from .core import AutoExitCode, Clock, ExecutionLock, SystemClock
from .log_policy import EventLogWriter, LogEvent
from .notification_protocol import (
    NotificationAction,
    parse_action_uri,
)
from .scheduler import TaskSchedulerBackend, build_task_spec, reconcile_task
from .state import StateStore
from .storage import UserDataLayout
from .switch_override import PendingSwitchStore


class NotificationActionResult(str, Enum):
    CONFIRMED = "confirmed"
    SKIPPED = "skipped"
    DELAYED = "delayed"
    ALREADY_RUNNING = "already-running"
    REJECTED = "rejected"
    DATA_UNTRUSTED = "data-untrusted"
    FAILED = "failed"
    PARTIAL_FAILURE = "partial-failure"


@dataclass(frozen=True)
class NotificationActionOutcome:
    result: NotificationActionResult
    action: NotificationAction | None
    pending_changed: bool | None
    task_changed: bool | None
    rollback_attempted: bool
    rollback_succeeded: bool | None
    message: str

    @property
    def exit_code(self) -> AutoExitCode:
        if self.result in {
            NotificationActionResult.CONFIRMED,
            NotificationActionResult.SKIPPED,
            NotificationActionResult.DELAYED,
        }:
            return AutoExitCode.SUCCESS
        if self.result is NotificationActionResult.ALREADY_RUNNING:
            return AutoExitCode.ALREADY_RUNNING
        if self.result in {
            NotificationActionResult.REJECTED,
            NotificationActionResult.DATA_UNTRUSTED,
        }:
            return AutoExitCode.DATA_UNTRUSTED
        if self.result is NotificationActionResult.PARTIAL_FAILURE:
            return AutoExitCode.PARTIAL_FAILURE
        return AutoExitCode.FATAL_FAILURE

    def as_dict(self) -> dict[str, Any]:
        return {
            "result": self.result.value,
            "exitCode": int(self.exit_code),
            "action": self.action.value if self.action is not None else None,
            "pendingSwitchChanged": self.pending_changed,
            "taskSchedulerChanged": self.task_changed,
            "rollbackAttempted": self.rollback_attempted,
            "rollbackSucceeded": self.rollback_succeeded,
            "windowsThemeChanged": False,
            "message": self.message,
        }


class NotificationActionService:
    def __init__(
        self,
        layout: UserDataLayout,
        execution_lock: ExecutionLock,
        tasks: TaskSchedulerBackend,
        *,
        executable: str,
        user_id: str,
        clock: Clock | None = None,
        config_store: ConfigStore | None = None,
        state_store: StateStore | None = None,
        pending_store: PendingSwitchStore | None = None,
        event_log: EventLogWriter | None = None,
    ) -> None:
        self.layout = layout
        self.execution_lock = execution_lock
        self.tasks = tasks
        self.executable = executable
        self.user_id = user_id
        self.clock = clock or SystemClock()
        self.config_store = config_store or ConfigStore(layout.config)
        self.state_store = state_store or StateStore(layout.state)
        self.pending_store = pending_store or PendingSwitchStore(layout.pending_switch)
        self.event_log = event_log or EventLogWriter(layout.event_log)

    @staticmethod
    def _timestamp(value: datetime) -> str:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Notification action clock must be aware.")
        return value.isoformat(timespec="seconds")

    def handle(self, uri: str) -> NotificationActionOutcome:
        try:
            action, token = parse_action_uri(uri)
        except Exception as exc:
            return NotificationActionOutcome(
                NotificationActionResult.REJECTED,
                None,
                False,
                False,
                False,
                None,
                f"Notification action URI was rejected: {exc}",
            )
        try:
            acquired = self.execution_lock.acquire()
        except Exception as exc:
            return NotificationActionOutcome(
                NotificationActionResult.FAILED,
                action,
                None,
                None,
                False,
                None,
                f"Execution lock failed: {type(exc).__name__}: {exc}",
            )
        if not acquired:
            return NotificationActionOutcome(
                NotificationActionResult.ALREADY_RUNNING,
                action,
                False,
                False,
                False,
                None,
                "Another operation owns the execution lock.",
            )
        try:
            outcome = self._handle_locked(action, token)
        except Exception as exc:
            outcome = NotificationActionOutcome(
                NotificationActionResult.FAILED,
                action,
                None,
                None,
                False,
                None,
                f"Unexpected notification action failure: {type(exc).__name__}: {exc}",
            )
        try:
            self.execution_lock.release()
        except Exception as exc:
            if outcome.exit_code is AutoExitCode.SUCCESS:
                outcome = replace(
                    outcome,
                    result=NotificationActionResult.PARTIAL_FAILURE,
                    message=(
                        f"{outcome.message} Lock release failed: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                )
        return outcome

    def _handle_locked(
        self, action: NotificationAction, token: str
    ) -> NotificationActionOutcome:
        now = self.clock.now()
        try:
            config = self.config_store.load()
            state = self.state_store.load()
            before = self.pending_store.load()
        except Exception as exc:
            return NotificationActionOutcome(
                NotificationActionResult.DATA_UNTRUSTED,
                action,
                False,
                False,
                False,
                None,
                f"Notification action data is untrusted: {type(exc).__name__}: {exc}",
            )
        if not config.notify_status_changes:
            return NotificationActionOutcome(
                NotificationActionResult.REJECTED,
                action,
                False,
                False,
                False,
                None,
                "Status notifications are disabled.",
            )
        if state.paused:
            return NotificationActionOutcome(
                NotificationActionResult.REJECTED,
                action,
                False,
                False,
                False,
                None,
                "Automatic switching is paused.",
            )
        try:
            updated = before.apply_action(
                action,
                token=token,
                now=now,
            )
            self.pending_store.save(updated)
        except Exception as exc:
            return NotificationActionOutcome(
                NotificationActionResult.REJECTED,
                action,
                False,
                False,
                False,
                None,
                f"Notification action was rejected: {exc}",
            )

        task_changed = False
        if action is NotificationAction.DELAY:
            try:
                task_outcome = reconcile_task(
                    self.tasks,
                    build_task_spec(
                        config,
                        executable=self.executable,
                        user_id=self.user_id,
                        pending_switch=updated,
                    ),
                )
                task_changed = task_outcome.changed
            except Exception as exc:
                try:
                    self.pending_store.save(before)
                    restored = self.pending_store.load() == before
                except Exception:
                    restored = False
                return NotificationActionOutcome(
                    (
                        NotificationActionResult.FAILED
                        if restored
                        else NotificationActionResult.PARTIAL_FAILURE
                    ),
                    action,
                    False if restored else None,
                    None,
                    True,
                    restored,
                    (
                        f"Deferred task update failed: "
                        f"{type(exc).__name__}: {exc}. "
                        + (
                            "The prior notification decision was restored."
                            if restored
                            else "The pending decision could not be restored."
                        )
                    ),
                )

        result = {
            NotificationAction.CONFIRM: NotificationActionResult.CONFIRMED,
            NotificationAction.SKIP: NotificationActionResult.SKIPPED,
            NotificationAction.DELAY: NotificationActionResult.DELAYED,
        }[action]
        try:
            self.event_log.append(
                LogEvent(
                    occurred_at=self._timestamp(now),
                    level="INFO",
                    event=f"notification.action-{action.value}",
                    result="success",
                    trigger="manual",
                    target_profile=before.target_profile,
                    message=(
                        "Notification action was accepted; no theme was "
                        "changed by the protocol handler."
                    ),
                )
            )
        except Exception as exc:
            return NotificationActionOutcome(
                NotificationActionResult.PARTIAL_FAILURE,
                action,
                True,
                task_changed,
                False,
                None,
                f"Notification action was committed, but logging failed: "
                f"{type(exc).__name__}: {exc}",
            )
        return NotificationActionOutcome(
            result,
            action,
            True,
            task_changed,
            False,
            None,
            "Notification action was accepted; the theme remains unchanged.",
        )
