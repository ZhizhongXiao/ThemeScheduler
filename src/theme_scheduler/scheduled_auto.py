"""Stage-9 scheduler coordination around the existing transactional core."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from enum import Enum
from time import sleep
from typing import Any, Protocol

from .accent_profile import AccentProfileStore
from .automation import AutoRunOutcome
from .config import AppConfig, ConfigStore
from .core import AutoExitCode, AutoResultKind, Clock, ExecutionLock, SystemClock
from .execution_lock import WindowsNamedMutexLock, named_mutex_name_for_path
from .log_policy import EventLogSink, EventLogWriter, LogEvent
from .notification_contracts import (
    NotificationDelivery,
    NotificationDeliveryResult,
)
from .notification_dedup import NotificationDedupStore
from .notification_protocol import new_action_token
from .scheduled_notifications import (
    PrepareSwitchNotification,
    ScheduledNotificationBackend,
    error_notification,
    success_notification,
)
from .scheduler import (
    AutoRetryOutcome,
    AutoRetryStatus,
    TaskSchedulerBackend,
    build_task_spec,
    ensure_future_auto_retry,
    reconcile_task,
)
from .state import AppState, StateStore
from .storage import UserDataLayout
from .switch_override import (
    ExecutionDecision,
    PendingSwitch,
    PendingSwitchStore,
    SwitchDecision,
    SwitchOverrideError,
)


class ScheduledRunKind(str, Enum):  # noqa: UP042 - Preserve str(Enum) output pending a dedicated migration.
    PREPARED = "prepared"
    PREPARE_SUPPRESSED = "prepare-suppressed"
    WAITING = "waiting"
    SKIPPED = "skipped"
    CORE = "core"
    ALREADY_RUNNING = "already-running"
    DATA_UNTRUSTED = "data-untrusted"
    PARTIAL_FAILURE = "partial-failure"
    FATAL_FAILURE = "fatal-failure"


@dataclass(frozen=True)
class FixedBoundary:
    profile: str
    scheduled_at: datetime
    next_fixed_at: datetime


class ScheduledCoreRunner(Protocol):
    def run_locked(self) -> AutoRunOutcome: ...


@dataclass(frozen=True)
class ScheduledRunOutcome:
    result: ScheduledRunKind
    message: str
    core: AutoRunOutcome | None = None
    target_profile: str | None = None
    pending_changed: bool = False
    task_changed: bool = False
    notification: NotificationDelivery | None = None
    retry_status: AutoRetryStatus | None = None
    recovery_guaranteed: bool | None = None
    scheduler_cleanup_error: str | None = None
    maintenance_required: bool = False

    @property
    def exit_code(self) -> AutoExitCode:
        if self.maintenance_required:
            return AutoExitCode.PARTIAL_FAILURE
        if self.core is not None:
            return self.core.exit_code
        if self.result in {
            ScheduledRunKind.PREPARED,
            ScheduledRunKind.PREPARE_SUPPRESSED,
            ScheduledRunKind.WAITING,
            ScheduledRunKind.SKIPPED,
        }:
            return AutoExitCode.SUCCESS
        if self.result is ScheduledRunKind.ALREADY_RUNNING:
            return AutoExitCode.ALREADY_RUNNING
        if self.result is ScheduledRunKind.DATA_UNTRUSTED:
            return AutoExitCode.DATA_UNTRUSTED
        if self.result is ScheduledRunKind.PARTIAL_FAILURE:
            return AutoExitCode.PARTIAL_FAILURE
        return AutoExitCode.FATAL_FAILURE

    def as_dict(self) -> dict[str, Any]:
        return {
            "result": self.result.value,
            "exitCode": int(self.exit_code),
            "message": self.message,
            "targetProfile": self.target_profile,
            "pendingSwitchChanged": self.pending_changed,
            "taskSchedulerChanged": self.task_changed,
            "autoRetryStatus": (
                self.retry_status.value if self.retry_status is not None else None
            ),
            "recoveryGuaranteed": self.recovery_guaranteed,
            "schedulerCleanupError": self.scheduler_cleanup_error,
            "maintenanceRequired": self.maintenance_required,
            "notification": (
                self.notification.as_dict() if self.notification is not None else None
            ),
            "core": self.core.as_dict() if self.core is not None else None,
        }


@dataclass
class _RunContext:
    now: datetime
    config: AppConfig
    state: AppState
    pending: PendingSwitch | None
    upcoming: FixedBoundary
    pending_changed: bool = False
    task_changed: bool = False


def _wall_time(value: str) -> time:
    hour, minute = (int(part) for part in value.split(":", 1))
    return time(hour, minute)


def _candidate(day: date, value: str, template: datetime) -> datetime:
    parsed = _wall_time(value)
    return datetime.combine(day, parsed, tzinfo=template.tzinfo)


def upcoming_fixed_boundary(config: AppConfig, now: datetime) -> FixedBoundary:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Schedule coordination requires an aware datetime.")
    candidates: list[tuple[datetime, str]] = []
    for offset in range(3):
        candidate_day = now.date() + timedelta(days=offset)
        candidates.extend(
            (
                (_candidate(candidate_day, config.day_start, now), "day"),
                (
                    _candidate(candidate_day, config.night_start, now),
                    "night",
                ),
            )
        )
    future = sorted(
        ((instant, profile) for instant, profile in candidates if instant > now),
        key=lambda item: item[0],
    )
    if len(future) < 2:
        raise ValueError("Cannot derive the next two fixed boundaries.")
    return FixedBoundary(
        future[0][1],
        future[0][0],
        future[1][0],
    )


class ScheduledAutoCoordinator:
    """Own one task invocation while preserving AutoRunner's transaction."""

    def __init__(
        self,
        layout: UserDataLayout,
        execution_lock: ExecutionLock,
        core_runner: ScheduledCoreRunner,
        tasks: TaskSchedulerBackend,
        notifier: ScheduledNotificationBackend,
        *,
        executable: str,
        user_id: str,
        clock: Clock | None = None,
        config_store: ConfigStore | None = None,
        state_store: StateStore | None = None,
        pending_store: PendingSwitchStore | None = None,
        event_log: EventLogSink | None = None,
        dedup_store: NotificationDedupStore | None = None,
        notification_lock: ExecutionLock | None = None,
        scheduler_mutation_lock: ExecutionLock | None = None,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        self.layout = layout
        self.execution_lock = execution_lock
        self.core_runner = core_runner
        self.tasks = tasks
        self.notifier = notifier
        self.executable = executable
        self.user_id = user_id
        self.clock = clock or SystemClock()
        self.config_store = config_store or ConfigStore(layout.config)
        self.state_store = state_store or StateStore(layout.state)
        self.pending_store = pending_store or PendingSwitchStore(layout.pending_switch)
        self.event_log = event_log or EventLogWriter(layout.event_log)
        self.dedup_store = dedup_store or NotificationDedupStore(
            layout.notification_dedup
        )
        self.notification_lock = (
            notification_lock
            if notification_lock is not None
            else WindowsNamedMutexLock(
                named_mutex_name_for_path(
                    layout.notification_dedup,
                    purpose="NotificationDedup",
                ),
                wait_timeout_ms=30_000,
            )
        )
        self.sleeper = sleeper
        self.scheduler_mutation_lock = scheduler_mutation_lock

    @staticmethod
    def _timestamp(value: datetime) -> str:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Schedule clock must include a UTC offset.")
        return value.isoformat(timespec="seconds")

    def _log(
        self,
        now: datetime,
        *,
        level: str,
        event: str,
        result: str,
        target: str | None,
        message: str,
        error_code: str | None = None,
    ) -> None:
        self.event_log.append(
            LogEvent(
                occurred_at=self._timestamp(now),
                level=level,
                event=event,
                result=result,
                trigger="auto",
                target_profile=target,
                error_code=error_code,
                message=message,
            )
        )

    def _load_pending(self) -> PendingSwitch | None:
        if not self.pending_store.exists:
            return None
        return self.pending_store.load()

    def _desired_task(self, config: AppConfig, pending: PendingSwitch | None):
        return build_task_spec(
            config,
            executable=self.executable,
            user_id=self.user_id,
            pending_switch=pending,
        )

    def _remove_pending(self, pending: PendingSwitch) -> tuple[bool, bool]:
        self.pending_store.clear(pending)
        return True, False

    def _notification_fallback(
        self,
        now: datetime,
        delivery: NotificationDelivery,
        target: str | None,
    ) -> NotificationDelivery:
        if delivery.result is not NotificationDeliveryResult.FAILED:
            return delivery
        try:
            self._log(
                now,
                level="WARNING",
                event="notification.delivery-failed",
                result="failed",
                target=target,
                error_code="notification.delivery-failed",
                message=delivery.message,
            )
        except Exception:
            return delivery
        return replace(delivery, fallback_logged=True)

    def _send_prepare_safely(
        self,
        now: datetime,
        pending: PendingSwitch,
    ) -> NotificationDelivery:
        try:
            delivery = self.notifier.send_prepare(
                PrepareSwitchNotification.from_pending(pending)
            )
        except Exception as exc:
            delivery = NotificationDelivery(
                NotificationDeliveryResult.FAILED,
                False,
                (f"Prepare notification backend failed: {type(exc).__name__}: {exc}")[
                    :500
                ],
            )
        return self._notification_fallback(now, delivery, pending.target_profile)

    def _clear_prepare_safely(
        self,
        now: datetime,
        target: str | None,
    ) -> None:
        try:
            delivery = self.notifier.clear_prepare()
        except Exception as exc:
            delivery = NotificationDelivery(
                NotificationDeliveryResult.FAILED,
                False,
                (f"Prepare notification cleanup failed: {type(exc).__name__}: {exc}")[
                    :500
                ],
            )
        self._notification_fallback(now, delivery, target)

    def _log_notification_failure_safely(
        self,
        *,
        target: str | None,
        message: str,
    ) -> bool:
        try:
            self._log(
                self.clock.now(),
                level="WARNING",
                event="notification.delivery-failed",
                result="failed",
                target=target,
                error_code="notification.delivery-failed",
                message=message[:500],
            )
        except Exception:
            return False
        return True

    def _release_execution_lock(
        self,
        outcome: ScheduledRunOutcome,
    ) -> ScheduledRunOutcome:
        try:
            self.execution_lock.release()
        except Exception as exc:
            if outcome.exit_code is AutoExitCode.SUCCESS:
                return replace(
                    outcome,
                    result=ScheduledRunKind.PARTIAL_FAILURE,
                    message=(
                        f"{outcome.message} Lock release failed: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                )
        return outcome

    def _retry_after_lock_timeout(
        self,
        now: datetime,
    ) -> ScheduledRunOutcome:
        try:
            context = self._load_run_context(now)
            desired = self._desired_task(context.config, context.pending)
        except Exception as exc:
            retry = AutoRetryOutcome(
                AutoRetryStatus.UNAVAILABLE,
                None,
                False,
                False,
                f"Trusted schedule data is unavailable: {type(exc).__name__}: {exc}",
            )
        else:
            retry = ensure_future_auto_retry(
                self.tasks,
                desired,
                now,
                mutation_lock=self.scheduler_mutation_lock,
            )
        detail = (
            f"AutoRetry {retry.status.value}"
            if retry.wakeup_guaranteed
            else "No future AutoRetry wakeup could be confirmed"
        )
        if retry.error:
            detail = f"{detail}: {retry.error}"
        return ScheduledRunOutcome(
            ScheduledRunKind.ALREADY_RUNNING,
            f"Another operation owns the execution lock. {detail}",
            task_changed=retry.status is AutoRetryStatus.CREATED,
            retry_status=retry.status,
            recovery_guaranteed=retry.wakeup_guaranteed,
        )

    def _reconcile_before_decision(
        self,
        context: _RunContext,
    ) -> ScheduledRunOutcome | None:
        try:
            desired = self._desired_task(context.config, context.pending)
            task_outcome = reconcile_task(
                self.tasks,
                desired,
                now=context.now,
                mutation_lock=self.scheduler_mutation_lock,
            )
        except Exception as exc:
            try:
                desired = self._desired_task(context.config, context.pending)
                retry = ensure_future_auto_retry(
                    self.tasks,
                    desired,
                    context.now,
                    mutation_lock=self.scheduler_mutation_lock,
                )
            except Exception as retry_exc:
                retry = AutoRetryOutcome(
                    AutoRetryStatus.UNAVAILABLE,
                    None,
                    False,
                    False,
                    f"{type(retry_exc).__name__}: {retry_exc}",
                )
            detail = (
                "A future AutoRetry is confirmed."
                if retry.wakeup_guaranteed
                else "No recovery wakeup is guaranteed."
            )
            return ScheduledRunOutcome(
                ScheduledRunKind.PARTIAL_FAILURE,
                f"Task Scheduler reconciliation failed: "
                f"{type(exc).__name__}: {exc}. {detail}",
                pending_changed=context.pending_changed,
                task_changed=context.task_changed
                or retry.status is AutoRetryStatus.CREATED,
                retry_status=retry.status,
                recovery_guaranteed=retry.wakeup_guaranteed,
            )
        context.task_changed = context.task_changed or task_outcome.changed
        return None

    def _cleanup_retry_after_outcome(
        self,
        context: _RunContext,
        outcome: ScheduledRunOutcome,
    ) -> ScheduledRunOutcome:
        try:
            task_outcome = reconcile_task(
                self.tasks,
                self._desired_task(context.config, context.pending),
                now=context.now,
                clear_auto_retry=True,
                mutation_lock=self.scheduler_mutation_lock,
            )
        except Exception as exc:
            message = f"AutoRetry cleanup failed: {type(exc).__name__}: {exc}"
            return replace(
                outcome,
                result=ScheduledRunKind.PARTIAL_FAILURE,
                message=f"{outcome.message} {message}",
                task_changed=context.task_changed,
                scheduler_cleanup_error=message,
                maintenance_required=True,
            )
        context.task_changed = context.task_changed or task_outcome.changed
        return replace(
            outcome,
            task_changed=outcome.task_changed or context.task_changed,
            retry_status=None,
            recovery_guaranteed=False,
        )

    def _send_success_notification(
        self,
        outcome: ScheduledRunOutcome,
    ) -> ScheduledRunOutcome:
        try:
            config = self.config_store.load()
            if not config.notify_status_changes:
                return outcome
            self.sleeper(5.0)
            delivery = self.notifier.send_status(
                success_notification(outcome.target_profile or "")
            )
            delivery = self._notification_fallback(
                self.clock.now(), delivery, outcome.target_profile
            )
            return replace(outcome, notification=delivery)
        except Exception as exc:
            self._log_notification_failure_safely(
                target=outcome.target_profile,
                message=f"Success notification failed: {type(exc).__name__}: {exc}",
            )
            return outcome

    def _send_error_notification(
        self,
        outcome: ScheduledRunOutcome,
    ) -> ScheduledRunOutcome:
        if outcome.core is None:
            return outcome
        try:
            if not self.notification_lock.acquire():
                raise TimeoutError("Notification dedup lock timed out.")
            try:
                return self._send_error_notification_locked(outcome)
            finally:
                self.notification_lock.release()
        except Exception as exc:
            self._log_notification_failure_safely(
                target=outcome.target_profile,
                message=(
                    "Error notification coordination failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )
            return outcome

    def _send_error_notification_locked(
        self,
        outcome: ScheduledRunOutcome,
    ) -> ScheduledRunOutcome:
        if outcome.core is None:
            return outcome
        try:
            config = self.config_store.load()
            if not config.notify_errors:
                return outcome
            now = self.clock.now()
            key = f"auto.failed|{outcome.core.result.value}"
            if not self.dedup_store.should_send(key, now=now):
                delivery = NotificationDelivery(
                    NotificationDeliveryResult.SUPPRESSED,
                    False,
                    "A matching error notification was sent within six hours.",
                )
                return replace(outcome, notification=delivery)
            try:
                delivery = self.notifier.send_status(
                    error_notification(outcome.core.result.value)
                )
            except Exception as exc:
                delivery = NotificationDelivery(
                    NotificationDeliveryResult.FAILED,
                    False,
                    (f"Error notification backend failed: {type(exc).__name__}: {exc}")[
                        :500
                    ],
                )
            delivery = self._notification_fallback(
                now,
                delivery,
                outcome.target_profile,
            )
            if delivery.result is NotificationDeliveryResult.SENT:
                self.dedup_store.record(key, now=now)
            return replace(outcome, notification=delivery)
        except Exception as exc:
            self._log_notification_failure_safely(
                target=outcome.target_profile,
                message=(
                    "Error notification state or delivery failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )
            return outcome

    def _complete_notifications(
        self,
        outcome: ScheduledRunOutcome,
    ) -> ScheduledRunOutcome:
        if (
            outcome.core is not None
            and outcome.core.result is AutoResultKind.APPLIED
            and outcome.target_profile is not None
        ):
            return self._send_success_notification(outcome)
        if outcome.core is not None and outcome.core.result in {
            AutoResultKind.DATA_UNTRUSTED,
            AutoResultKind.APPLY_FAILED_ROLLED_BACK,
            AutoResultKind.PARTIAL_FAILURE,
            AutoResultKind.FATAL_FAILURE,
        }:
            return self._send_error_notification(outcome)
        return outcome

    def run(self) -> ScheduledRunOutcome:
        try:
            acquired = self.execution_lock.acquire()
        except Exception as exc:
            return ScheduledRunOutcome(
                ScheduledRunKind.FATAL_FAILURE,
                f"Execution lock failed: {type(exc).__name__}: {exc}",
            )
        if not acquired:
            return self._complete_notifications(
                self._retry_after_lock_timeout(self.clock.now())
            )
        try:
            outcome = self.run_locked()
        except Exception as exc:
            outcome = ScheduledRunOutcome(
                ScheduledRunKind.FATAL_FAILURE,
                f"Unexpected schedule coordination failure: "
                f"{type(exc).__name__}: {exc}",
            )
        outcome = self._release_execution_lock(outcome)
        return self._complete_notifications(outcome)

    def _load_run_context(self, now: datetime) -> _RunContext:
        config = self.config_store.load()
        return _RunContext(
            now=now,
            config=config,
            state=self.state_store.load(),
            pending=self._load_pending(),
            upcoming=upcoming_fixed_boundary(config, now),
        )

    def _clear_pending(
        self,
        context: _RunContext,
        *,
        failure_prefix: str,
        core: AutoRunOutcome | None = None,
        target_profile: str | None = None,
    ) -> ScheduledRunOutcome | None:
        pending = context.pending
        if pending is None:
            return None
        try:
            cleared, repaired = self._remove_pending(pending)
        except Exception as exc:
            return ScheduledRunOutcome(
                ScheduledRunKind.PARTIAL_FAILURE,
                f"{failure_prefix}: {type(exc).__name__}: {exc}",
                core=core,
                target_profile=target_profile,
                pending_changed=context.pending_changed,
                task_changed=context.task_changed,
            )
        context.pending_changed = context.pending_changed or cleared
        context.task_changed = context.task_changed or repaired
        context.pending = None
        self._clear_prepare_safely(context.now, pending.target_profile)
        return None

    def _cleanup_disabled_notifications(
        self,
        context: _RunContext,
    ) -> ScheduledRunOutcome | None:
        if context.pending is None or context.config.notify_status_changes:
            return None
        return self._clear_pending(
            context,
            failure_prefix="Disabled notification cleanup failed",
        )

    def _cleanup_superseded_switch(
        self,
        context: _RunContext,
    ) -> ScheduledRunOutcome | None:
        pending = context.pending
        if pending is None or context.now < pending.next_fixed_at:
            return None
        return self._clear_pending(
            context,
            failure_prefix="Superseded switch cleanup failed",
            target_profile=pending.target_profile,
        )

    def _core_outcome(
        self,
        context: _RunContext,
        core: AutoRunOutcome,
        *,
        target_profile: str | None = None,
    ) -> ScheduledRunOutcome:
        return ScheduledRunOutcome(
            ScheduledRunKind.CORE,
            core.message,
            core=core,
            target_profile=target_profile or core.target_profile,
            pending_changed=context.pending_changed,
            task_changed=context.task_changed,
        )

    def _run_paused(self, context: _RunContext) -> ScheduledRunOutcome:
        core = self.core_runner.run_locked()
        pending = context.pending
        if pending is not None and context.now >= pending.scheduled_at:
            failure = self._clear_pending(
                context,
                failure_prefix="Paused switch cleanup failed",
                core=core,
                target_profile=pending.target_profile,
            )
            if failure is not None:
                return failure
        return self._core_outcome(context, core)

    def _waiting(
        self,
        context: _RunContext,
        message: str,
        target_profile: str,
    ) -> ScheduledRunOutcome:
        return ScheduledRunOutcome(
            ScheduledRunKind.WAITING,
            message,
            target_profile=target_profile,
            pending_changed=context.pending_changed,
            task_changed=context.task_changed,
        )

    def _prepare_deferred_reminder(
        self,
        context: _RunContext,
        pending: PendingSwitch,
    ) -> ScheduledRunOutcome:
        if (
            pending.decision is not SwitchDecision.PENDING
            or pending.action_token is not None
        ):
            return self._waiting(
                context,
                "The scheduled switch is waiting for its execution point.",
                pending.target_profile,
            )
        try:
            pending = self.pending_store.save(
                pending.arm(now=context.now, token=new_action_token())
            )
            context.pending = pending
            context.pending_changed = True
            delivery = self._send_prepare_safely(context.now, pending)
        except Exception as exc:
            return ScheduledRunOutcome(
                ScheduledRunKind.PARTIAL_FAILURE,
                f"Deferred reminder failed: {type(exc).__name__}: {exc}",
                target_profile=pending.target_profile,
                pending_changed=True,
                task_changed=context.task_changed,
            )
        return ScheduledRunOutcome(
            ScheduledRunKind.PREPARED,
            "A new five-minute deferred reminder was prepared.",
            target_profile=pending.target_profile,
            pending_changed=True,
            task_changed=context.task_changed,
            notification=delivery,
        )

    def _skip_pending(
        self,
        context: _RunContext,
        pending: PendingSwitch,
    ) -> ScheduledRunOutcome:
        failure = self._clear_pending(
            context,
            failure_prefix="Skipped switch cleanup failed",
            target_profile=pending.target_profile,
        )
        if failure is not None:
            return failure
        return ScheduledRunOutcome(
            ScheduledRunKind.SKIPPED,
            "The user skipped this scheduled switch.",
            target_profile=pending.target_profile,
            pending_changed=context.pending_changed,
            task_changed=context.task_changed,
        )

    def _execute_pending(
        self,
        context: _RunContext,
        pending: PendingSwitch,
    ) -> ScheduledRunOutcome:
        decision = pending.execution_decision(now=context.now)
        if decision is ExecutionDecision.SKIP:
            return self._skip_pending(context, pending)
        if decision is ExecutionDecision.WAIT:
            return self._waiting(
                context,
                "The delayed execution point has not arrived.",
                pending.target_profile,
            )
        if decision is ExecutionDecision.SUPERSEDED:
            raise SwitchOverrideError("A superseded switch survived cleanup.")

        core = self.core_runner.run_locked()
        failure = self._clear_pending(
            context,
            failure_prefix=f"{core.message} Pending switch cleanup failed",
            core=core,
            target_profile=pending.target_profile,
        )
        if failure is not None:
            return failure
        return self._core_outcome(
            context,
            core,
            target_profile=pending.target_profile,
        )

    def _run_pending(self, context: _RunContext) -> ScheduledRunOutcome:
        pending = context.pending
        if pending is None:
            raise SwitchOverrideError("Pending switch handler received no switch.")
        if context.now < pending.prepare_at:
            return self._waiting(
                context,
                "The deferred switch is waiting for its new five-minute reminder.",
                pending.target_profile,
            )
        if context.now < pending.scheduled_at:
            return self._prepare_deferred_reminder(context, pending)
        return self._execute_pending(context, pending)

    def _run_fixed_boundary(self, context: _RunContext) -> ScheduledRunOutcome:
        upcoming = context.upcoming
        until_boundary = upcoming.scheduled_at - context.now
        if not (timedelta(0) < until_boundary <= timedelta(minutes=5)):
            return self._core_outcome(context, self.core_runner.run_locked())
        if not context.config.notify_status_changes:
            return ScheduledRunOutcome(
                ScheduledRunKind.PREPARE_SUPPRESSED,
                "The five-minute reminder is disabled; the boundary remains scheduled.",
                target_profile=upcoming.profile,
                pending_changed=context.pending_changed,
                task_changed=context.task_changed,
            )
        try:
            AccentProfileStore(
                self.layout.profile_path(upcoming.profile),
                upcoming.profile,
            ).load()
            pending = PendingSwitch.create(
                target_profile=upcoming.profile,
                scheduled_at=upcoming.scheduled_at,
                next_fixed_at=upcoming.next_fixed_at,
                now=context.now,
            ).arm(now=context.now, token=new_action_token())
            pending = self.pending_store.create(pending)
            context.pending = pending
            context.pending_changed = True
            delivery = self._send_prepare_safely(context.now, pending)
        except Exception as exc:
            return ScheduledRunOutcome(
                ScheduledRunKind.PARTIAL_FAILURE,
                f"Fixed-boundary reminder failed: {type(exc).__name__}: {exc}",
                target_profile=upcoming.profile,
                pending_changed=self.pending_store.exists,
                task_changed=context.task_changed,
            )
        return ScheduledRunOutcome(
            ScheduledRunKind.PREPARED,
            "The five-minute switch reminder was prepared.",
            target_profile=upcoming.profile,
            pending_changed=True,
            task_changed=context.task_changed,
            notification=delivery,
        )

    def run_locked(self) -> ScheduledRunOutcome:
        now = self.clock.now()
        try:
            context = self._load_run_context(now)
        except Exception as exc:
            return ScheduledRunOutcome(
                ScheduledRunKind.DATA_UNTRUSTED,
                f"Schedule data is untrusted: {type(exc).__name__}: {exc}",
            )

        failure = self._cleanup_superseded_switch(context)
        if failure is not None:
            return failure
        failure = self._cleanup_disabled_notifications(context)
        if failure is not None:
            return failure
        failure = self._reconcile_before_decision(context)
        if failure is not None:
            return failure
        if context.state.paused:
            outcome = self._run_paused(context)
        elif context.pending is not None:
            outcome = self._run_pending(context)
        else:
            outcome = self._run_fixed_boundary(context)
        return self._cleanup_retry_after_outcome(context, outcome)
