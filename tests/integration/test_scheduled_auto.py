from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from theme_scheduler.accent_profile import (
    AccentProfile,
    AccentProfileStore,
)
from theme_scheduler.automation import AutoRunOutcome
from theme_scheduler.config import AppConfig, ConfigStore
from theme_scheduler.core import AutoResultKind
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
    TaskDefinitionBackup,
    TaskSpec,
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
    def __init__(self) -> None:
        self.acquired = 0
        self.released = 0

    def acquire(self) -> bool:
        self.acquired += 1
        return True

    def release(self) -> None:
        self.released += 1


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
        sleeps: list[float] | None = None,
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
            FakeLock(),
            core or StubCore(),
            task_backend,
            notifier or FakeNotifier(),
            executable=EXECUTABLE,
            user_id=USER_ID,
            clock=clock,
            event_log=log or MemoryLog(),
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


if __name__ == "__main__":
    unittest.main()
