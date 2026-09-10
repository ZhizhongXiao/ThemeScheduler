"""Explicit transactional repair of product-owned notification identity."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import Clock, ExecutionLock, SystemClock
from .log_policy import EventLogWriter, LogEvent
from .protocol_registration import (
    NotificationProtocolBackend,
    ProtocolRegistration,
    RegistryTreeBackup,
)
from .storage import UserDataLayout
from .system_integration import ShortcutBackend, ShortcutPlan


@dataclass(frozen=True)
class NotificationIdentityRepairOutcome:
    result: str
    shortcuts_changed: bool | None
    protocol_changed: bool | None
    verified: bool
    rollback_attempted: bool
    rollback_succeeded: bool | None
    log_written: bool
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": "notification-identity-repair",
            "result": self.result,
            "shortcutsChanged": self.shortcuts_changed,
            "notificationProtocolChanged": self.protocol_changed,
            "verified": self.verified,
            "rollbackAttempted": self.rollback_attempted,
            "rollbackSucceeded": self.rollback_succeeded,
            "logWritten": self.log_written,
            "dataChanged": self.log_written,
            "windowsChanged": (
                True
                if self.shortcuts_changed is True or self.protocol_changed is True
                else (
                    None
                    if self.shortcuts_changed is None or self.protocol_changed is None
                    else False
                )
            ),
            "taskSchedulerChanged": False,
            "message": self.message,
        }


class NotificationIdentityRepairService:
    """Repair only the start shortcut, an existing desktop shortcut, and URI."""

    def __init__(
        self,
        layout: UserDataLayout,
        execution_lock: ExecutionLock,
        shortcuts: ShortcutBackend,
        shortcut_plan: ShortcutPlan,
        protocol: NotificationProtocolBackend,
        *,
        event_log: EventLogWriter | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.layout = layout
        self.execution_lock = execution_lock
        self.shortcuts = shortcuts
        self.shortcut_plan = shortcut_plan
        self.protocol = protocol
        self.event_log = event_log or EventLogWriter(layout.event_log)
        self.clock = clock or SystemClock()

    def _timestamp(self) -> str:
        now = self.clock.now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Identity repair clock must include a UTC offset.")
        return now.isoformat(timespec="seconds")

    def _rollback(
        self,
        shortcut_backups: dict[Path, bytes | None],
        protocol_backup: RegistryTreeBackup | None,
    ) -> bool:
        succeeded = True
        try:
            self.protocol.restore(protocol_backup)
            succeeded = self.protocol.capture() == protocol_backup and succeeded
        except Exception:
            succeeded = False
        for path, backup in reversed(tuple(shortcut_backups.items())):
            try:
                self.shortcuts.restore(path, backup)
                succeeded = self.shortcuts.capture(path) == backup and succeeded
            except Exception:
                succeeded = False
        return succeeded

    def repair(self) -> NotificationIdentityRepairOutcome:
        try:
            acquired = self.execution_lock.acquire()
        except Exception as exc:
            return NotificationIdentityRepairOutcome(
                "fatal-failure",
                None,
                None,
                False,
                False,
                None,
                False,
                f"Execution lock failed: {type(exc).__name__}: {exc}",
            )
        if not acquired:
            return NotificationIdentityRepairOutcome(
                "already-running",
                False,
                False,
                False,
                False,
                None,
                False,
                "Another automatic or maintenance operation owns the lock.",
            )
        try:
            outcome = self._repair_locked()
        except Exception as exc:
            outcome = NotificationIdentityRepairOutcome(
                "fatal-failure",
                None,
                None,
                False,
                False,
                None,
                False,
                f"Unexpected identity repair failure: {type(exc).__name__}: {exc}",
            )
        try:
            self.execution_lock.release()
        except Exception as exc:
            if outcome.result in {"changed", "no-change"}:
                outcome = NotificationIdentityRepairOutcome(
                    "partial-failure",
                    outcome.shortcuts_changed,
                    outcome.protocol_changed,
                    outcome.verified,
                    outcome.rollback_attempted,
                    outcome.rollback_succeeded,
                    outcome.log_written,
                    f"{outcome.message} Lock release failed: "
                    f"{type(exc).__name__}: {exc}",
                )
        return outcome

    def _repair_locked(self) -> NotificationIdentityRepairOutcome:
        paths = self.shortcut_plan.managed_paths
        shortcut_backups: dict[Path, bytes | None] = {}
        try:
            for path in paths:
                shortcut_backups[path] = self.shortcuts.capture(path)
            protocol_backup = self.protocol.capture()
            start_actual = self.shortcuts.read(self.shortcut_plan.start_menu.path)
            desktop_actual = (
                self.shortcuts.read(self.shortcut_plan.desktop.path)
                if shortcut_backups[self.shortcut_plan.desktop.path] is not None
                else None
            )
            expected_protocol = ProtocolRegistration(
                self.shortcut_plan.start_menu.target
            )
            protocol_actual = self.protocol.read()
        except Exception as exc:
            return NotificationIdentityRepairOutcome(
                "fatal-failure",
                None,
                None,
                False,
                False,
                None,
                False,
                f"Identity capture failed: {type(exc).__name__}: {exc}",
            )

        shortcut_changed = start_actual != self.shortcut_plan.start_menu or (
            shortcut_backups[self.shortcut_plan.desktop.path] is not None
            and desktop_actual != self.shortcut_plan.desktop
        )
        protocol_changed = protocol_actual != expected_protocol
        try:
            if start_actual != self.shortcut_plan.start_menu:
                self.shortcuts.write(self.shortcut_plan.start_menu)
            if (
                shortcut_backups[self.shortcut_plan.desktop.path] is not None
                and desktop_actual != self.shortcut_plan.desktop
            ):
                self.shortcuts.write(self.shortcut_plan.desktop)
            if protocol_changed:
                self.protocol.write(expected_protocol)

            start_verified = (
                self.shortcuts.read(self.shortcut_plan.start_menu.path)
                == self.shortcut_plan.start_menu
            )
            desktop_verified = (
                self.shortcuts.capture(self.shortcut_plan.desktop.path) is None
                if shortcut_backups[self.shortcut_plan.desktop.path] is None
                else self.shortcuts.read(self.shortcut_plan.desktop.path)
                == self.shortcut_plan.desktop
            )
            protocol_verified = self.protocol.read() == expected_protocol
            if not (start_verified and desktop_verified and protocol_verified):
                raise OSError("Notification identity readback mismatch.")
        except Exception as exc:
            rolled_back = self._rollback(shortcut_backups, protocol_backup)
            return NotificationIdentityRepairOutcome(
                ("fatal-failure" if rolled_back else "partial-failure"),
                False if rolled_back else None,
                False if rolled_back else None,
                False,
                True,
                rolled_back,
                False,
                f"Identity repair failed: {type(exc).__name__}: {exc}. "
                + (
                    "Original identity was restored."
                    if rolled_back
                    else "Rollback was incomplete."
                ),
            )

        result = "changed" if shortcut_changed or protocol_changed else "no-change"
        try:
            self.event_log.append(
                LogEvent(
                    occurred_at=self._timestamp(),
                    level="INFO",
                    event="notification.identity-repaired",
                    result=("success" if result == "changed" else "skipped"),
                    trigger="repair",
                    message="Notification identity is consistent.",
                )
            )
        except Exception as exc:
            return NotificationIdentityRepairOutcome(
                "partial-failure",
                shortcut_changed,
                protocol_changed,
                True,
                False,
                None,
                False,
                f"Identity repair was verified, but logging failed: "
                f"{type(exc).__name__}: {exc}",
            )
        return NotificationIdentityRepairOutcome(
            result,
            shortcut_changed,
            protocol_changed,
            True,
            False,
            None,
            True,
            "Notification identity is consistent.",
        )
