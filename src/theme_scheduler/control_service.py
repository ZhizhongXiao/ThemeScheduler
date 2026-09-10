"""Stage-6 pause/resume orchestration with conservative transaction guards."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .auto_transaction import (
    AUTO_TRANSACTION_FILE_NAME,
    AutoTransactionStore,
)
from .control import (
    ControlOutcome,
    ControlResultKind,
    plan_pause_transition,
)
from .core import Clock, ExecutionLock, SystemClock
from .errors import ThemeSchedulerRuntimeError
from .log_policy import EventLogWriter, LogEvent
from .state import AppState, StateStore
from .storage import UserDataLayout

_UNFINISHED_AUTO_STATUSES = frozenset(
    {"planned", "windows-verified", "state-committed", "partial"}
)


class PendingAutoTransactionError(ThemeSchedulerRuntimeError):
    """Raised when a control write would invalidate recovery evidence."""


def ensure_no_pending_auto_transaction(runtime: Path) -> None:
    """Conservatively reject any unfinished or malformed automatic evidence."""

    runtime = Path(runtime)
    if not runtime.exists():
        return
    pending: list[str] = []
    for directory in sorted(runtime.iterdir(), key=lambda value: value.name):
        if not directory.is_dir() or not directory.name.startswith("accent-"):
            continue
        transaction_path = directory / AUTO_TRANSACTION_FILE_NAME
        if not transaction_path.exists():
            continue
        transaction = AutoTransactionStore(transaction_path).load()
        if transaction.transaction_id != directory.name:
            raise PendingAutoTransactionError(
                "Automatic transaction directory identity mismatch."
            )
        if transaction.status in _UNFINISHED_AUTO_STATUSES:
            pending.append(directory.name)
    if pending:
        raise PendingAutoTransactionError(
            "Unfinished automatic transaction requires recovery before "
            f"changing pause state: {', '.join(pending)}"
        )


class ControlService:
    """Coordinate one trusted pause/resume state mutation."""

    def __init__(
        self,
        layout: UserDataLayout,
        execution_lock: ExecutionLock,
        *,
        state_store: StateStore | None = None,
        event_log: EventLogWriter | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.layout = layout
        self.execution_lock = execution_lock
        self.state_store = state_store or StateStore(layout.state)
        self.event_log = event_log or EventLogWriter(layout.event_log)
        self.clock = clock or SystemClock()

    @staticmethod
    def _timestamp(instant: datetime) -> str:
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("Control clock must include a UTC offset.")
        return instant.isoformat(timespec="seconds")

    def set_paused(self, paused: bool) -> ControlOutcome:
        action = "pause" if paused else "resume"
        try:
            acquired = self.execution_lock.acquire()
        except Exception as exc:
            return ControlOutcome(
                action,
                ControlResultKind.FATAL_FAILURE,
                None,
                None,
                None,
                False,
                f"Execution lock failed: {type(exc).__name__}: {exc}",
            )
        if not acquired:
            return ControlOutcome(
                action,
                ControlResultKind.ALREADY_RUNNING,
                None,
                None,
                False,
                False,
                "Another automatic or control operation owns the execution lock.",
            )

        outcome: ControlOutcome
        try:
            outcome = self._set_paused_locked(paused)
        except Exception as exc:
            outcome = ControlOutcome(
                action,
                ControlResultKind.FATAL_FAILURE,
                None,
                None,
                None,
                False,
                f"Unexpected control failure: {type(exc).__name__}: {exc}",
            )
        try:
            self.execution_lock.release()
        except Exception as exc:
            if outcome.exit_code == 0:
                return ControlOutcome(
                    action,
                    ControlResultKind.PARTIAL_FAILURE,
                    outcome.paused_before,
                    outcome.paused_after,
                    outcome.state_changed,
                    outcome.log_written,
                    (
                        f"Control operation completed but lock release failed: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                )
        return outcome

    def _set_paused_locked(self, paused: bool) -> ControlOutcome:
        action = "pause" if paused else "resume"
        try:
            ensure_no_pending_auto_transaction(self.layout.runtime)
            before = self.state_store.load()
        except Exception as exc:
            return ControlOutcome(
                action,
                ControlResultKind.DATA_UNTRUSTED,
                None,
                None,
                False,
                False,
                f"Control state is untrusted: {type(exc).__name__}: {exc}",
            )

        transition = plan_pause_transition(before, paused)
        if transition.changed:
            try:
                after = self.state_store.save(transition.after)
            except Exception as exc:
                return self._save_failure_outcome(action, transition, exc)
        else:
            after = before

        timestamp = self._timestamp(self.clock.now())
        try:
            self.event_log.append(
                LogEvent(
                    occurred_at=timestamp,
                    level="INFO",
                    event="control.paused" if paused else "control.resumed",
                    result="success" if transition.changed else "skipped",
                    trigger="manual",
                    message=(
                        "Automatic switching paused."
                        if paused
                        else (
                            "Automatic switching resumed; the current theme "
                            "was not synchronized."
                        )
                    ),
                )
            )
        except Exception as exc:
            return ControlOutcome(
                action,
                ControlResultKind.PARTIAL_FAILURE,
                before.paused,
                after.paused,
                transition.changed,
                False,
                (
                    "Pause state was committed but control logging failed: "
                    f"{type(exc).__name__}: {exc}"
                    if transition.changed
                    else (
                        "Pause state already matched, but control logging failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
                ),
            )

        return ControlOutcome(
            action,
            (
                ControlResultKind.CHANGED
                if transition.changed
                else ControlResultKind.NO_CHANGE
            ),
            before.paused,
            after.paused,
            transition.changed,
            True,
            (
                "Automatic switching is paused; scheduled runs will not start "
                "new theme changes."
                if paused
                else (
                    "Automatic switching is resumed; the current theme is "
                    "unchanged until the next boundary or an explicit sync."
                )
            ),
        )

    def _save_failure_outcome(
        self,
        action: str,
        transition,
        error: Exception,
    ) -> ControlOutcome:
        actual: AppState | None
        try:
            actual = self.state_store.load()
        except Exception:
            actual = None
        if actual == transition.before:
            result = ControlResultKind.FATAL_FAILURE
            changed: bool | None = False
            paused_after = transition.before.paused
        elif actual == transition.after:
            result = ControlResultKind.PARTIAL_FAILURE
            changed = True
            paused_after = transition.after.paused
        else:
            result = ControlResultKind.PARTIAL_FAILURE
            changed = None
            paused_after = actual.paused if actual is not None else None
        return ControlOutcome(
            action,
            result,
            transition.before.paused,
            paused_after,
            changed,
            False,
            f"Pause state save failed: {type(error).__name__}: {error}",
        )
