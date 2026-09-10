"""Stage 8.4 lightweight maintenance and install-appearance restore."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .accent_service import (
    AccentApplyOutcome,
    apply_install_backup_appearance,
)
from .accent_theme import LiveThemeApplyError
from .backup import InstallBackup, InstallBackupStore
from .control_service import ensure_no_pending_auto_transaction
from .core import Clock, ExecutionLock, SystemClock
from .log_policy import EventLogWriter, LogEvent
from .state import AppState, StateStore
from .storage import UserDataLayout

AppearanceApplier = Callable[
    [InstallBackup, UserDataLayout],
    AccentApplyOutcome,
]


class MaintenanceRestoreResult(str, Enum):
    RESTORED = "restored"
    ALREADY_RUNNING = "already-running"
    DATA_UNTRUSTED = "data-untrusted"
    FAILED = "failed"
    PARTIAL = "partial"


@dataclass(frozen=True)
class MaintenanceRestoreOutcome:
    result: MaintenanceRestoreResult
    paused_before: bool | None
    paused_after: bool | None
    pause_changed: bool | None
    appearance_applied: bool | None
    windows_verified: bool
    system_mode_preserved: bool | None
    rollback_succeeded: bool | None
    log_written: bool
    transaction_directory: Path | None
    message: str

    def as_dict(self) -> dict[str, Any]:
        if self.appearance_applied is True:
            windows_changed: bool | None = True
        elif self.rollback_succeeded is True:
            windows_changed = False
        elif self.appearance_applied is None:
            windows_changed = None
        else:
            windows_changed = False
        return {
            "action": "restore-install-appearance",
            "result": self.result.value,
            "pausedBefore": self.paused_before,
            "pausedAfter": self.paused_after,
            "pauseChanged": self.pause_changed,
            "appearanceApplied": self.appearance_applied,
            "windowsVerified": self.windows_verified,
            "systemModePreserved": self.system_mode_preserved,
            "rollbackSucceeded": self.rollback_succeeded,
            "logWritten": self.log_written,
            "transactionDirectory": (
                str(self.transaction_directory.resolve())
                if self.transaction_directory is not None
                else None
            ),
            "dataChanged": (
                True
                if self.pause_changed is True or self.log_written
                else self.pause_changed
            ),
            "windowsChanged": windows_changed,
            "taskSchedulerAccessed": False,
            "taskSchedulerChanged": False,
            "message": self.message,
        }


class MaintenanceService:
    """Restore only the appearance fields owned by ThemeScheduler."""

    def __init__(
        self,
        layout: UserDataLayout,
        execution_lock: ExecutionLock,
        *,
        backup_store: InstallBackupStore | None = None,
        state_store: StateStore | None = None,
        event_log: EventLogWriter | None = None,
        clock: Clock | None = None,
        appearance_applier: AppearanceApplier | None = None,
    ) -> None:
        self.layout = layout
        self.execution_lock = execution_lock
        self.backup_store = backup_store or InstallBackupStore(
            layout.install_backup_manifest,
            layout.install_backup_theme,
        )
        self.state_store = state_store or StateStore(layout.state)
        self.event_log = event_log or EventLogWriter(layout.event_log)
        self.clock = clock or SystemClock()
        self.appearance_applier = appearance_applier or apply_install_backup_appearance

    @staticmethod
    def _timestamp(instant: datetime) -> str:
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("Maintenance clock must include a UTC offset.")
        return instant.isoformat(timespec="seconds")

    def restore_install_appearance(self) -> MaintenanceRestoreOutcome:
        try:
            acquired = self.execution_lock.acquire()
        except Exception as exc:
            return MaintenanceRestoreOutcome(
                MaintenanceRestoreResult.FAILED,
                None,
                None,
                None,
                False,
                False,
                None,
                None,
                False,
                None,
                f"Execution lock failed: {type(exc).__name__}: {exc}",
            )
        if not acquired:
            return MaintenanceRestoreOutcome(
                MaintenanceRestoreResult.ALREADY_RUNNING,
                None,
                None,
                False,
                False,
                False,
                None,
                None,
                False,
                None,
                "Another automatic or maintenance operation owns the lock.",
            )
        try:
            outcome = self._restore_locked()
        except Exception as exc:
            outcome = MaintenanceRestoreOutcome(
                MaintenanceRestoreResult.PARTIAL,
                None,
                None,
                None,
                None,
                False,
                None,
                None,
                False,
                None,
                f"Unexpected maintenance failure: {type(exc).__name__}: {exc}",
            )
        try:
            self.execution_lock.release()
        except Exception as exc:
            if outcome.result is MaintenanceRestoreResult.RESTORED:
                outcome = replace(
                    outcome,
                    result=MaintenanceRestoreResult.PARTIAL,
                    message=(
                        f"{outcome.message} Lock release failed: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                )
        return outcome

    def restore_install_appearance_locked(
        self,
    ) -> MaintenanceRestoreOutcome:
        """Restore while the caller already owns the shared execution lock.

        The independent uninstaller acquires the lifecycle and automatic
        locks in a fixed order, so reacquiring the automatic mutex here would
        deadlock.  All other callers must use restore_install_appearance().
        """

        return self._restore_locked()

    def _restore_locked(self) -> MaintenanceRestoreOutcome:
        try:
            ensure_no_pending_auto_transaction(self.layout.runtime)
            backup = self.backup_store.load_verified()
            before = self.state_store.load()
        except Exception as exc:
            return MaintenanceRestoreOutcome(
                MaintenanceRestoreResult.DATA_UNTRUSTED,
                None,
                None,
                False,
                False,
                False,
                None,
                None,
                False,
                None,
                f"Install appearance restore is blocked: {type(exc).__name__}: {exc}",
            )

        pause_changed = not before.paused
        pause_uncertain = False
        after_pause = before
        if pause_changed:
            paused = replace(before, paused=True)
            try:
                after_pause = self.state_store.save(paused)
            except Exception as exc:
                try:
                    actual = self.state_store.load()
                except Exception:
                    actual = None
                if actual != paused:
                    return MaintenanceRestoreOutcome(
                        MaintenanceRestoreResult.PARTIAL,
                        before.paused,
                        actual.paused if actual is not None else None,
                        (False if actual == before else None),
                        False,
                        False,
                        None,
                        None,
                        False,
                        None,
                        "Pause state could not be trusted; Windows was not "
                        f"changed: {type(exc).__name__}: {exc}",
                    )
                after_pause = paused
                pause_uncertain = True

        try:
            applied = self.appearance_applier(backup, self.layout)
        except LiveThemeApplyError as exc:
            return self._appearance_failure(
                before,
                after_pause,
                pause_changed,
                exc,
                exc.rollback_succeeded,
            )
        except Exception as exc:
            return self._appearance_failure(
                before,
                after_pause,
                pause_changed,
                exc,
                None,
            )

        system_preserved = applied.actual.get("systemMode") == applied.before.get(
            "systemMode"
        )
        verified = bool(
            applied.actual.get("appMode") == backup.app_mode
            and applied.actual.get("colorizationColor") == backup.colorization_color
            and system_preserved
        )
        if not verified:
            return self._appearance_failure(
                before,
                after_pause,
                pause_changed,
                RuntimeError("Appearance result did not match backup."),
                None,
                transaction_directory=applied.transaction_directory,
            )
        log_written, log_error = self._write_log(
            result="success",
            level="INFO",
            message=(
                "Install-time app mode and accent were restored; "
                "automatic switching remains paused."
            ),
            transaction_id=applied.transaction_directory.name,
        )
        partial = pause_uncertain or not log_written
        return MaintenanceRestoreOutcome(
            (
                MaintenanceRestoreResult.PARTIAL
                if partial
                else MaintenanceRestoreResult.RESTORED
            ),
            before.paused,
            after_pause.paused,
            pause_changed,
            True,
            True,
            True,
            None,
            log_written,
            applied.transaction_directory,
            (
                "Install-time app mode and accent were restored; automatic "
                "switching remains paused."
                + (f" Logging failed: {log_error}" if log_error is not None else "")
                + (
                    " Pause readback succeeded after a save error."
                    if pause_uncertain
                    else ""
                )
            ),
        )

    def _appearance_failure(
        self,
        before: AppState,
        after_pause: AppState,
        pause_changed: bool,
        error: Exception,
        rollback_succeeded: bool | None,
        *,
        transaction_directory: Path | None = None,
    ) -> MaintenanceRestoreOutcome:
        log_written, log_error = self._write_log(
            result=("failed" if rollback_succeeded is True else "partial"),
            level=("ERROR" if rollback_succeeded is True else "CRITICAL"),
            message=(
                "Install appearance restore failed; automatic switching remains paused."
            ),
            transaction_id=(
                transaction_directory.name
                if transaction_directory is not None
                else None
            ),
        )
        return MaintenanceRestoreOutcome(
            (
                MaintenanceRestoreResult.FAILED
                if rollback_succeeded is True
                else MaintenanceRestoreResult.PARTIAL
            ),
            before.paused,
            after_pause.paused,
            pause_changed,
            False if rollback_succeeded is True else None,
            False,
            None,
            rollback_succeeded,
            log_written,
            transaction_directory,
            (
                f"Appearance restore failed: {type(error).__name__}: {error}. "
                "Automatic switching remains paused."
                + (f" Logging failed: {log_error}" if log_error is not None else "")
            )[:500],
        )

    def _write_log(
        self,
        *,
        result: str,
        level: str,
        message: str,
        transaction_id: str | None,
    ) -> tuple[bool, str | None]:
        try:
            self.event_log.append(
                LogEvent(
                    occurred_at=self._timestamp(self.clock.now()),
                    level=level,
                    event="maintenance.appearance-restored",
                    result=result,
                    trigger="repair",
                    transaction_id=transaction_id,
                    message=message,
                )
            )
            return True, None
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"
