from __future__ import annotations

import json
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from theme_scheduler.accent_profile import (
    AccentProfile,
    AccentProfileStore,
)
from theme_scheduler.automation import AutoRunOutcome
from theme_scheduler.config import AppConfig, ConfigStore
from theme_scheduler.core import AutoResultKind, ExecutionLock
from theme_scheduler.notification_contracts import (
    NotificationDelivery,
    NotificationDeliveryResult,
)
from theme_scheduler.notification_protocol import (
    NotificationAction,
)
from theme_scheduler.scheduled_auto import (
    ScheduledAutoCoordinator,
    ScheduledRunKind,
    upcoming_fixed_boundary,
)
from theme_scheduler.scheduler import (
    AutoRetryStatus,
    TaskDefinitionBackup,
    TaskSpec,
    TimeTrigger,
    build_task_spec,
)
from theme_scheduler.state import AppState, StateStore
from theme_scheduler.storage import UserDataLayout
from theme_scheduler.switch_override import (
    PendingSwitch,
    PendingSwitchStore,
)

UTC8 = timezone(timedelta(hours=8))
EXECUTABLE = (
    r"C:\Users\Example\AppData\Local\Programs"
    r"\ThemeScheduler\app\ThemeScheduler.exe"
)
USER_ID = "S-1-5-21-1000-1000-1000-1001"
TOKEN = "0123456789abcdef0123456789abcdef"
TOKEN_2 = "fedcba9876543210fedcba9876543210"


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self.instant = now

    def now(self) -> datetime:
        return self.instant


class FakeLock:
    def __init__(self, acquire_result: bool = True) -> None:
        self.acquire_result = acquire_result
        self.acquired = 0
        self.released = 0

    def acquire(self) -> bool:
        self.acquired += 1
        return self.acquire_result

    def release(self) -> None:
        self.released += 1


class SharedNotificationLock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._guard = threading.Lock()
        self.acquire_count = 0
        self.second_acquire_started = threading.Event()

    def acquire(self) -> bool:
        with self._guard:
            self.acquire_count += 1
            if self.acquire_count == 2:
                self.second_acquire_started.set()
        return self._lock.acquire()

    def release(self) -> None:
        self._lock.release()


class FailingNotificationLock:
    def acquire(self) -> bool:
        raise OSError("injected notification lock failure")

    def release(self) -> None:
        raise AssertionError("release must not run before successful acquire")


class StubCore:
    def __init__(self, result: AutoResultKind = AutoResultKind.APPLIED) -> None:
        self.result = result
        self.calls = 0

    def run_locked(self) -> AutoRunOutcome:
        self.calls += 1
        target = "day"
        return AutoRunOutcome(
            self.result,
            target,
            None,
            None,
            self.result is AutoResultKind.APPLIED,
            self.result is AutoResultKind.APPLIED,
            False,
            f"core {self.result.value}",
        )


class MemoryTaskBackend:
    def __init__(self, task: TaskSpec | None = None) -> None:
        self.task = task
        self.register_count = 0

    def current_user_id(self) -> str:
        return USER_ID

    def read(self, task_path: str) -> TaskSpec | None:
        if self.task is None or self.task.task_path != task_path:
            return None
        return self.task

    def register(self, task: TaskSpec) -> None:
        self.task = task
        self.register_count += 1

    def capture(self, task_path: str) -> TaskDefinitionBackup | None:
        task = self.read(task_path)
        if task is None:
            return None
        return TaskDefinitionBackup(
            task_path,
            json.dumps(task.as_dict()),
            task.enabled,
        )

    def restore(self, backup: TaskDefinitionBackup) -> None:
        self.task = TaskSpec.from_dict(json.loads(backup.definition_xml))

    def delete(self, task_path: str) -> None:
        self.task = None


class FakeNotifier:
    def __init__(
        self,
        *,
        fail: bool = False,
        raise_on_prepare: bool = False,
    ) -> None:
        self.fail = fail
        self.raise_on_prepare = raise_on_prepare
        self.prepares = []
        self.statuses = []
        self.prepare_clears = 0

    def _delivery(self) -> NotificationDelivery:
        if self.fail:
            return NotificationDelivery(
                NotificationDeliveryResult.FAILED,
                True,
                "injected notification failure",
            )
        return NotificationDelivery(
            NotificationDeliveryResult.SENT,
            False,
            "sent",
        )

    def send_prepare(self, request):
        self.prepares.append(request)
        if self.raise_on_prepare:
            raise OSError("injected backend exception")
        return self._delivery()

    def send_status(self, request):
        self.statuses.append(request)
        return self._delivery()

    def clear_prepare(self):
        self.prepare_clears += 1
        return self._delivery()


class PausingNotifier(FakeNotifier):
    def __init__(self) -> None:
        super().__init__()
        self.send_entered = threading.Event()
        self.allow_send_to_finish = threading.Event()

    def send_status(self, request):
        self.statuses.append(request)
        self.send_entered.set()
        if not self.allow_send_to_finish.wait(timeout=10):
            raise TimeoutError("test did not release paused notifier")
        return self._delivery()


class MemoryLog:
    def __init__(self) -> None:
        self.events = []

    def append(self, event) -> None:
        self.events.append(event)


class ScheduledAutoTests(unittest.TestCase):
    def _layout(
        self,
        root: Path,
        *,
        notifications: bool = True,
        paused: bool = False,
    ) -> UserDataLayout:
        layout = UserDataLayout(root / "data")
        layout.ensure_directories()
        ConfigStore(layout.config).initialize(
            AppConfig(
                "06:15",
                "23:45",
                "light",
                "dark",
                True,
                notifications,
            )
        )
        StateStore(layout.state).initialize(
            AppState(
                paused,
                "night",
                "2026-07-25T23:45:00+08:00",
                "night",
                "success",
            )
        )
        for profile, color in (
            ("day", 0xC4744DA9),
            ("night", 0xC4FFB900),
        ):
            AccentProfileStore(layout.profile_path(profile), profile).create(
                AccentProfile(
                    profile,
                    "2026-07-25T20:00:00+08:00",
                    False,
                    color,
                    "26200",
                )
            )
        return layout

    def _coordinator(
        self,
        layout: UserDataLayout,
        now: datetime,
        *,
        core: StubCore | None = None,
        tasks: MemoryTaskBackend | None = None,
        notifier: FakeNotifier | None = None,
        log: MemoryLog | None = None,
        notification_lock: ExecutionLock | None = None,
        sleeps: list[float] | None = None,
        execution_lock: ExecutionLock | None = None,
    ) -> ScheduledAutoCoordinator:
        config = ConfigStore(layout.config).load()
        task_backend = tasks or MemoryTaskBackend(
            build_task_spec(
                config,
                executable=EXECUTABLE,
                user_id=USER_ID,
            )
        )
        clock = FixedClock(now)
        return ScheduledAutoCoordinator(
            layout,
            execution_lock or FakeLock(),
            core or StubCore(),
            task_backend,
            notifier or FakeNotifier(),
            executable=EXECUTABLE,
            user_id=USER_ID,
            clock=clock,
            event_log=log or MemoryLog(),
            notification_lock=notification_lock,
            sleeper=(
                (lambda seconds: sleeps.append(seconds))
                if sleeps is not None
                else (lambda _seconds: None)
            ),
        )

    def test_boundary_helper_returns_target_and_following_boundary(self) -> None:
        config = AppConfig.defaults()
        value = upcoming_fixed_boundary(
            config,
            datetime(2026, 7, 26, 6, 10, tzinfo=UTC8),
        )
        self.assertEqual(value.profile, "day")
        self.assertEqual(value.scheduled_at.hour, 6)
        self.assertEqual(value.scheduled_at.minute, 15)
        self.assertEqual(value.next_fixed_at.hour, 23)
        self.assertEqual(value.next_fixed_at.minute, 45)

    def test_mutex_timeout_schedules_a_future_retry(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))
            tasks = MemoryTaskBackend(
                build_task_spec(
                    ConfigStore(layout.config).load(),
                    executable=EXECUTABLE,
                    user_id=USER_ID,
                )
            )
            now = datetime(2026, 7, 26, 12, 0, 1, tzinfo=UTC8)
            outcome = self._coordinator(
                layout,
                now,
                tasks=tasks,
                execution_lock=FakeLock(acquire_result=False),
            ).run()

            self.assertIs(outcome.result, ScheduledRunKind.ALREADY_RUNNING)
            self.assertEqual(outcome.retry_status, AutoRetryStatus.CREATED)
            self.assertTrue(outcome.recovery_guaranteed)
            assert tasks.task is not None
            retries = [
                trigger
                for trigger in tasks.task.triggers
                if isinstance(trigger, TimeTrigger)
                and trigger.trigger_id == "AutoRetry"
            ]
            self.assertEqual(len(retries), 1)
            retry = retries[0]
            self.assertEqual(
                datetime.fromisoformat(retry.start_at),
                now.replace(second=0, microsecond=0) + timedelta(minutes=2),
            )

    def test_mutex_timeout_preserves_an_existing_future_retry(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))
            now = datetime(2026, 7, 26, 12, 0, 1, tzinfo=UTC8)
            retry_at = now.replace(second=0, microsecond=0) + timedelta(minutes=4)
            task = build_task_spec(
                ConfigStore(layout.config).load(),
                executable=EXECUTABLE,
                user_id=USER_ID,
            )
            tasks = MemoryTaskBackend(
                replace(
                    task,
                    triggers=(
                        *task.triggers,
                        TimeTrigger(
                            "AutoRetry",
                            retry_at.isoformat(timespec="seconds"),
                        ),
                    ),
                )
            )

            outcome = self._coordinator(
                layout,
                now,
                tasks=tasks,
                execution_lock=FakeLock(acquire_result=False),
            ).run()

            assert tasks.task is not None
            retry = next(
                trigger
                for trigger in tasks.task.triggers
                if isinstance(trigger, TimeTrigger)
                and trigger.trigger_id == "AutoRetry"
            )
            self.assertEqual(retry.start_at, retry_at.isoformat(timespec="seconds"))
            self.assertEqual(tasks.register_count, 0)
            self.assertEqual(outcome.retry_status, AutoRetryStatus.EXISTING)
            self.assertTrue(outcome.recovery_guaranteed)

    def test_mutex_timeout_replaces_an_expired_retry_with_a_future_one(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))
            now = datetime(2026, 7, 26, 12, 0, 1, tzinfo=UTC8)
            task = build_task_spec(
                ConfigStore(layout.config).load(),
                executable=EXECUTABLE,
                user_id=USER_ID,
            )
            tasks = MemoryTaskBackend(
                replace(
                    task,
                    triggers=(
                        *task.triggers,
                        TimeTrigger(
                            "AutoRetry",
                            (
                                now.replace(second=0, microsecond=0)
                                - timedelta(minutes=1)
                            ).isoformat(timespec="seconds"),
                        ),
                    ),
                )
            )

            outcome = self._coordinator(
                layout,
                now,
                tasks=tasks,
                execution_lock=FakeLock(acquire_result=False),
            ).run()

            assert tasks.task is not None
            retry = next(
                trigger
                for trigger in tasks.task.triggers
                if isinstance(trigger, TimeTrigger)
                and trigger.trigger_id == "AutoRetry"
            )
            self.assertGreater(datetime.fromisoformat(retry.start_at), now)
            self.assertEqual(
                datetime.fromisoformat(retry.start_at),
                now.replace(second=0, microsecond=0) + timedelta(minutes=2),
            )
            self.assertEqual(outcome.retry_status, AutoRetryStatus.CREATED)

    def test_retry_readback_failure_does_not_claim_wakeup_guarantee(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))

            class UnreadableTaskBackend(MemoryTaskBackend):
                def read(self, task_path: str) -> TaskSpec | None:
                    raise OSError("Task Scheduler readback unavailable")

            outcome = self._coordinator(
                layout,
                datetime(2026, 7, 26, 12, 0, 1, tzinfo=UTC8),
                tasks=UnreadableTaskBackend(),
                execution_lock=FakeLock(acquire_result=False),
            ).run()

            self.assertIs(outcome.retry_status, AutoRetryStatus.UNAVAILABLE)
            self.assertFalse(outcome.recovery_guaranteed)

    def test_reconciliation_failure_reports_existing_retry_as_guaranteed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))
            now = datetime(2026, 7, 26, 12, 0, 1, tzinfo=UTC8)
            config = ConfigStore(layout.config).load()
            before = build_task_spec(
                config,
                executable=EXECUTABLE,
                user_id=USER_ID,
            )
            before = replace(before, enabled=False)
            retry_at = now.replace(second=0, microsecond=0) + timedelta(minutes=4)
            before = replace(
                before,
                triggers=(
                    *before.triggers,
                    TimeTrigger("AutoRetry", retry_at.isoformat(timespec="seconds")),
                ),
            )

            class FailingRegisterBackend(MemoryTaskBackend):
                def register(self, task: TaskSpec) -> None:
                    raise OSError("temporary task mutation failure")

            tasks = FailingRegisterBackend(before)
            outcome = self._coordinator(
                layout,
                now,
                tasks=tasks,
                execution_lock=FakeLock(),
            ).run()

            self.assertIs(outcome.result, ScheduledRunKind.PARTIAL_FAILURE)
            self.assertIs(outcome.retry_status, AutoRetryStatus.EXISTING)
            self.assertTrue(outcome.recovery_guaranteed)
            self.assertEqual(tasks.task, before)

    def test_reconciliation_failure_without_retry_reports_no_guarantee(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))
            now = datetime(2026, 7, 26, 12, 0, 1, tzinfo=UTC8)
            fixed = build_task_spec(
                ConfigStore(layout.config).load(),
                executable=EXECUTABLE,
                user_id=USER_ID,
            )

            class FailingRegisterBackend(MemoryTaskBackend):
                def register(self, task: TaskSpec) -> None:
                    raise OSError("temporary task mutation failure")

            tasks = FailingRegisterBackend(replace(fixed, enabled=False))
            outcome = self._coordinator(
                layout,
                now,
                tasks=tasks,
                execution_lock=FakeLock(),
            ).run()

            self.assertIs(outcome.result, ScheduledRunKind.PARTIAL_FAILURE)
            self.assertIs(outcome.retry_status, AutoRetryStatus.UNAVAILABLE)
            self.assertFalse(outcome.recovery_guaranteed)

    def test_retry_cleanup_failure_preserves_successful_core_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))
            now = datetime(2026, 7, 26, 6, 16, tzinfo=UTC8)
            fixed = build_task_spec(
                ConfigStore(layout.config).load(),
                executable=EXECUTABLE,
                user_id=USER_ID,
            )
            retry_at = now.replace(second=0, microsecond=0) + timedelta(minutes=4)

            class CannotRemoveRetryBackend(MemoryTaskBackend):
                def register(self, task: TaskSpec) -> None:
                    if not any(t.trigger_id == "AutoRetry" for t in task.triggers):
                        raise OSError("injected retry cleanup failure")
                    super().register(task)

            tasks = CannotRemoveRetryBackend(
                replace(
                    fixed,
                    triggers=(
                        *fixed.triggers,
                        TimeTrigger(
                            "AutoRetry", retry_at.isoformat(timespec="seconds")
                        ),
                    ),
                )
            )
            core = StubCore()
            outcome = self._coordinator(
                layout,
                now,
                tasks=tasks,
                core=core,
            ).run()

            self.assertIs(outcome.result, ScheduledRunKind.PARTIAL_FAILURE)
            self.assertIsNotNone(outcome.core)
            assert outcome.core is not None
            self.assertIs(outcome.core.result, AutoResultKind.APPLIED)
            self.assertIsNotNone(outcome.scheduler_cleanup_error)
            assert outcome.scheduler_cleanup_error is not None
            self.assertIn("retry cleanup", outcome.scheduler_cleanup_error.lower())

    def test_verified_theme_failure_does_not_schedule_or_keep_retry(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))
            now = datetime(2026, 7, 26, 6, 16, tzinfo=UTC8)
            fixed = build_task_spec(
                ConfigStore(layout.config).load(),
                executable=EXECUTABLE,
                user_id=USER_ID,
            )
            existing_retry = now.replace(second=0, microsecond=0) + timedelta(minutes=3)
            tasks = MemoryTaskBackend(
                replace(
                    fixed,
                    triggers=(
                        *fixed.triggers,
                        TimeTrigger(
                            "AutoRetry",
                            existing_retry.isoformat(timespec="seconds"),
                        ),
                    ),
                )
            )

            outcome = self._coordinator(
                layout,
                now,
                tasks=tasks,
                core=StubCore(AutoResultKind.APPLY_FAILED_ROLLED_BACK),
            ).run()

            self.assertIs(outcome.result, ScheduledRunKind.CORE)
            assert tasks.task is not None
            self.assertNotIn(
                "AutoRetry", {trigger.trigger_id for trigger in tasks.task.triggers}
            )

    def test_fixed_prepare_creates_pending_state_and_three_actions(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))
            core = StubCore()
            notifier = FakeNotifier()
            outcome = self._coordinator(
                layout,
                datetime(2026, 7, 26, 6, 10, tzinfo=UTC8),
                core=core,
                notifier=notifier,
            ).run()

            self.assertIs(outcome.result, ScheduledRunKind.PREPARED)
            self.assertEqual(core.calls, 0)
            pending = PendingSwitchStore(layout.pending_switch).load()
            self.assertEqual(pending.target_profile, "day")
            self.assertIsNotNone(pending.action_token)
            self.assertEqual(len(notifier.prepares[0].buttons), 3)

    def test_fixed_prepare_with_real_clock_microseconds_round_trips(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))
            notifier = FakeNotifier()
            outcome = self._coordinator(
                layout,
                datetime(
                    2026,
                    7,
                    26,
                    6,
                    10,
                    1,
                    847362,
                    tzinfo=UTC8,
                ),
                notifier=notifier,
            ).run()

            self.assertIs(outcome.result, ScheduledRunKind.PREPARED)
            self.assertEqual(int(outcome.exit_code), 0)
            pending = PendingSwitchStore(layout.pending_switch).load()
            assert pending is not None
            prepared_at = pending.prepared_at
            assert prepared_at is not None
            self.assertEqual(prepared_at.microsecond, 0)
            self.assertEqual(pending.updated_at.microsecond, 0)
            self.assertEqual(len(notifier.prepares), 1)

    def test_disabled_status_suppresses_prepare_without_early_apply(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw), notifications=False)
            core = StubCore()
            notifier = FakeNotifier()
            outcome = self._coordinator(
                layout,
                datetime(2026, 7, 26, 6, 11, tzinfo=UTC8),
                core=core,
                notifier=notifier,
            ).run()

            self.assertIs(outcome.result, ScheduledRunKind.PREPARE_SUPPRESSED)
            self.assertEqual(core.calls, 0)
            self.assertEqual(notifier.prepares, [])
            self.assertFalse(layout.pending_switch.exists())

    def test_delay_waits_then_rearms_with_new_reminder(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))
            original = datetime(2026, 7, 26, 6, 15, tzinfo=UTC8)
            pending = PendingSwitch.create(
                target_profile="day",
                scheduled_at=original,
                next_fixed_at=datetime(2026, 7, 26, 23, 45, tzinfo=UTC8),
                now=original - timedelta(minutes=5),
            ).arm(
                now=original - timedelta(minutes=5),
                token=TOKEN,
            )
            delayed = pending.apply_action(
                NotificationAction.DELAY,
                token=TOKEN,
                now=original - timedelta(minutes=4),
            )
            PendingSwitchStore(layout.pending_switch).create(delayed)
            config = ConfigStore(layout.config).load()
            tasks = MemoryTaskBackend(
                build_task_spec(
                    config,
                    executable=EXECUTABLE,
                    user_id=USER_ID,
                    pending_switch=delayed,
                )
            )
            core = StubCore()

            at_original = self._coordinator(
                layout, original, core=core, tasks=tasks
            ).run()
            self.assertIs(at_original.result, ScheduledRunKind.WAITING)
            self.assertEqual(core.calls, 0)
            assert tasks.task is not None
            self.assertEqual(
                {trigger.trigger_id for trigger in tasks.task.triggers},
                {
                    "DayPrepare",
                    "DayBoundary",
                    "NightPrepare",
                    "NightBoundary",
                    "DeferredPrepare",
                    "DeferredBoundary",
                },
            )

            notifier = FakeNotifier()
            at_new_prepare = self._coordinator(
                layout,
                original + timedelta(minutes=25),
                core=core,
                tasks=tasks,
                notifier=notifier,
            ).run()
            self.assertIs(at_new_prepare.result, ScheduledRunKind.PREPARED)
            rearmed = PendingSwitchStore(layout.pending_switch).load()
            self.assertNotEqual(rearmed.action_token, TOKEN)
            self.assertEqual(len(notifier.prepares), 1)

    def test_delayed_boundary_applies_then_removes_pair_and_notifies(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))
            original = datetime(2026, 7, 26, 6, 15, tzinfo=UTC8)
            pending = (
                PendingSwitch.create(
                    target_profile="day",
                    scheduled_at=original,
                    next_fixed_at=datetime(2026, 7, 26, 23, 45, tzinfo=UTC8),
                    now=original - timedelta(minutes=5),
                )
                .arm(
                    now=original - timedelta(minutes=5),
                    token=TOKEN,
                )
                .apply_action(
                    NotificationAction.DELAY,
                    token=TOKEN,
                    now=original - timedelta(minutes=4),
                )
                .arm(
                    now=original + timedelta(minutes=25),
                    token=TOKEN_2,
                )
            )
            PendingSwitchStore(layout.pending_switch).create(pending)
            config = ConfigStore(layout.config).load()
            tasks = MemoryTaskBackend(
                build_task_spec(
                    config,
                    executable=EXECUTABLE,
                    user_id=USER_ID,
                    pending_switch=pending,
                )
            )
            core = StubCore()
            notifier = FakeNotifier()
            sleeps: list[float] = []

            outcome = self._coordinator(
                layout,
                original + timedelta(minutes=30),
                core=core,
                tasks=tasks,
                notifier=notifier,
                sleeps=sleeps,
            ).run()

            self.assertIs(outcome.result, ScheduledRunKind.CORE)
            self.assertEqual(core.calls, 1)
            self.assertFalse(layout.pending_switch.exists())
            assert tasks.task is not None
            self.assertEqual(len(tasks.task.triggers), 4)
            self.assertEqual(sleeps, [5.0])
            self.assertEqual(len(notifier.statuses), 1)
            self.assertEqual(notifier.prepare_clears, 1)

    def test_pending_is_not_applied_when_task_reconciliation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))
            scheduled = datetime(2026, 7, 26, 6, 15, tzinfo=UTC8)
            pending = replace(
                PendingSwitch.create(
                    target_profile="day",
                    scheduled_at=scheduled - timedelta(minutes=30),
                    next_fixed_at=datetime(2026, 7, 26, 23, 45, tzinfo=UTC8),
                    now=scheduled - timedelta(minutes=35),
                ),
                scheduled_at=scheduled,
                defer_count=1,
                updated_at=scheduled - timedelta(minutes=29),
            )
            PendingSwitchStore(layout.pending_switch).create(pending)

            class UnreadableTaskBackend(MemoryTaskBackend):
                def read(self, task_path: str) -> TaskSpec | None:
                    raise OSError("temporary scheduler read failure")

            tasks = UnreadableTaskBackend()
            core = StubCore()
            outcome = self._coordinator(
                layout,
                scheduled + timedelta(minutes=30),
                tasks=tasks,
                core=core,
            ).run()

            self.assertIs(outcome.result, ScheduledRunKind.PARTIAL_FAILURE)
            self.assertEqual(core.calls, 0)
            self.assertTrue(layout.pending_switch.exists())

    def test_expired_pending_is_cleared_before_task_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))
            boundary = datetime(2026, 7, 26, 23, 30, tzinfo=UTC8)
            pending = replace(
                PendingSwitch.create(
                    target_profile="night",
                    scheduled_at=boundary - timedelta(minutes=30),
                    next_fixed_at=datetime(2026, 7, 26, 23, 45, tzinfo=UTC8),
                    now=boundary - timedelta(minutes=35),
                ),
                scheduled_at=boundary,
                defer_count=1,
                updated_at=boundary - timedelta(minutes=29),
            )
            PendingSwitchStore(layout.pending_switch).create(pending)
            config = ConfigStore(layout.config).load()

            class CheckingTaskBackend(MemoryTaskBackend):
                def __init__(self) -> None:
                    super().__init__(
                        build_task_spec(
                            config,
                            executable=EXECUTABLE,
                            user_id=USER_ID,
                            pending_switch=pending,
                        )
                    )
                    self.pending_existed_at_first_read: bool | None = None

                def read(self, task_path: str) -> TaskSpec | None:
                    if self.pending_existed_at_first_read is None:
                        self.pending_existed_at_first_read = (
                            layout.pending_switch.exists()
                        )
                    return super().read(task_path)

            tasks = CheckingTaskBackend()
            outcome = self._coordinator(
                layout,
                datetime(2026, 7, 26, 23, 46, tzinfo=UTC8),
                tasks=tasks,
            ).run()

            self.assertIs(outcome.result, ScheduledRunKind.CORE)
            self.assertFalse(layout.pending_switch.exists())
            self.assertFalse(tasks.pending_existed_at_first_read)

    def test_skip_consumes_state_without_running_core(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))
            scheduled = datetime(2026, 7, 26, 6, 15, tzinfo=UTC8)
            pending = (
                PendingSwitch.create(
                    target_profile="day",
                    scheduled_at=scheduled,
                    next_fixed_at=datetime(2026, 7, 26, 23, 45, tzinfo=UTC8),
                    now=scheduled - timedelta(minutes=5),
                )
                .arm(
                    now=scheduled - timedelta(minutes=5),
                    token=TOKEN,
                )
                .apply_action(
                    NotificationAction.SKIP,
                    token=TOKEN,
                    now=scheduled - timedelta(minutes=4),
                )
            )
            PendingSwitchStore(layout.pending_switch).create(pending)
            core = StubCore()
            notifier = FakeNotifier()

            outcome = self._coordinator(
                layout,
                scheduled,
                core=core,
                notifier=notifier,
            ).run()

            self.assertIs(outcome.result, ScheduledRunKind.SKIPPED)
            self.assertEqual(core.calls, 0)
            self.assertFalse(layout.pending_switch.exists())
            self.assertEqual(notifier.prepare_clears, 1)

    def test_missed_prepare_after_boundary_runs_core_without_stale_notice(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))
            core = StubCore()
            notifier = FakeNotifier()

            outcome = self._coordinator(
                layout,
                datetime(2026, 7, 26, 6, 16, tzinfo=UTC8),
                core=core,
                notifier=notifier,
            ).run()

            self.assertIs(outcome.result, ScheduledRunKind.CORE)
            self.assertEqual(core.calls, 1)
            self.assertEqual(notifier.prepares, [])

    def test_notification_failure_is_logged_without_running_core_early(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))
            core = StubCore()
            log = MemoryLog()
            outcome = self._coordinator(
                layout,
                datetime(2026, 7, 26, 6, 10, tzinfo=UTC8),
                core=core,
                notifier=FakeNotifier(fail=True),
                log=log,
            ).run()

            self.assertIs(outcome.result, ScheduledRunKind.PREPARED)
            self.assertEqual(core.calls, 0)
            self.assertEqual(log.events[-1].event, "notification.delivery-failed")

    def test_prepare_backend_exception_keeps_pending_switch_armed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))
            core = StubCore()
            log = MemoryLog()

            outcome = self._coordinator(
                layout,
                datetime(2026, 7, 26, 6, 10, tzinfo=UTC8),
                core=core,
                notifier=FakeNotifier(raise_on_prepare=True),
                log=log,
            ).run()

            self.assertIs(outcome.result, ScheduledRunKind.PREPARED)
            self.assertEqual(core.calls, 0)
            self.assertTrue(layout.pending_switch.exists())
            assert outcome.notification is not None
            self.assertIs(
                outcome.notification.result,
                NotificationDeliveryResult.FAILED,
            )
            self.assertTrue(outcome.notification.fallback_logged)

    def test_matching_error_notification_is_deduplicated_for_six_hours(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))
            notifier = FakeNotifier()
            now = datetime(2026, 7, 26, 6, 16, tzinfo=UTC8)

            first = self._coordinator(
                layout,
                now,
                core=StubCore(AutoResultKind.PARTIAL_FAILURE),
                notifier=notifier,
            ).run()
            second = self._coordinator(
                layout,
                now + timedelta(hours=1),
                core=StubCore(AutoResultKind.PARTIAL_FAILURE),
                notifier=notifier,
            ).run()

            assert first.notification is not None
            assert second.notification is not None
            self.assertIs(
                first.notification.result,
                NotificationDeliveryResult.SENT,
            )
            self.assertIs(
                second.notification.result,
                NotificationDeliveryResult.SUPPRESSED,
            )
            self.assertEqual(len(notifier.statuses), 1)

    def test_concurrent_matching_failures_send_only_one_error_notification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))
            notifier = PausingNotifier()
            lock = SharedNotificationLock()
            now = datetime(2026, 7, 26, 6, 16, tzinfo=UTC8)
            outcomes = []
            failures = []

            def run_one() -> None:
                try:
                    outcomes.append(
                        self._coordinator(
                            layout,
                            now,
                            core=StubCore(AutoResultKind.PARTIAL_FAILURE),
                            notifier=notifier,
                            notification_lock=lock,
                        ).run()
                    )
                except Exception as exc:
                    failures.append(exc)

            first = threading.Thread(target=run_one)
            second = threading.Thread(target=run_one)
            first.start()
            self.assertTrue(notifier.send_entered.wait(timeout=10))
            second.start()
            self.assertTrue(lock.second_acquire_started.wait(timeout=10))
            notifier.allow_send_to_finish.set()
            first.join(timeout=10)
            second.join(timeout=10)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(len(outcomes), 2)
            self.assertTrue(all(item.core is not None for item in outcomes))
            results = {
                item.notification.result
                for item in outcomes
                if item.notification is not None
            }
            self.assertEqual(
                results,
                {
                    NotificationDeliveryResult.SENT,
                    NotificationDeliveryResult.SUPPRESSED,
                },
            )
            self.assertEqual(len(notifier.statuses), 1)
            self.assertEqual(len(self._load_dedup(layout).entries), 1)

    def test_concurrent_distinct_failures_keep_both_dedup_entries(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))
            notifier = PausingNotifier()
            lock = SharedNotificationLock()
            now = datetime(2026, 7, 26, 6, 16, tzinfo=UTC8)
            results = []
            failures = []

            def run_one(kind: AutoResultKind) -> None:
                try:
                    results.append(
                        self._coordinator(
                            layout,
                            now,
                            core=StubCore(kind),
                            notifier=notifier,
                            notification_lock=lock,
                        ).run()
                    )
                except Exception as exc:
                    failures.append(exc)

            first = threading.Thread(
                target=run_one,
                args=(AutoResultKind.PARTIAL_FAILURE,),
            )
            second = threading.Thread(
                target=run_one,
                args=(AutoResultKind.DATA_UNTRUSTED,),
            )
            first.start()
            self.assertTrue(notifier.send_entered.wait(timeout=10))
            second.start()
            self.assertTrue(lock.second_acquire_started.wait(timeout=10))
            notifier.allow_send_to_finish.set()
            first.join(timeout=10)
            second.join(timeout=10)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(len(results), 2)
            self.assertTrue(
                all(
                    item.notification is not None
                    and item.notification.result is NotificationDeliveryResult.SENT
                    for item in results
                )
            )
            self.assertEqual(len(notifier.statuses), 2)
            self.assertEqual(
                {key for key, _instant in self._load_dedup(layout).entries},
                {
                    "auto.failed|partial-failure",
                    "auto.failed|data-untrusted",
                },
            )

    def test_notification_lock_failure_preserves_core_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout = self._layout(Path(raw))
            core = StubCore(AutoResultKind.PARTIAL_FAILURE)

            outcome = self._coordinator(
                layout,
                datetime(2026, 7, 26, 6, 16, tzinfo=UTC8),
                core=core,
                notifier=FakeNotifier(),
                notification_lock=FailingNotificationLock(),
            ).run()

            self.assertIs(outcome.result, ScheduledRunKind.CORE)
            self.assertIsNotNone(outcome.core)
            assert outcome.core is not None
            self.assertIs(outcome.core.result, AutoResultKind.PARTIAL_FAILURE)
            self.assertEqual(outcome.exit_code, outcome.core.exit_code)
            self.assertEqual(core.calls, 1)
            self.assertIsNone(outcome.notification)

    @staticmethod
    def _load_dedup(layout: UserDataLayout):
        from theme_scheduler.notification_dedup import NotificationDedupStore

        return NotificationDedupStore(layout.notification_dedup).load()


if __name__ == "__main__":
    unittest.main()
