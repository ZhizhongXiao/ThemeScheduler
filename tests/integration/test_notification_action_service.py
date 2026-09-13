from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from theme_scheduler.config import AppConfig, ConfigStore
from theme_scheduler.notification_action_service import (
    NotificationActionResult,
    NotificationActionService,
)
from theme_scheduler.notification_protocol import (
    NotificationAction,
    build_action_uri,
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
NOW = datetime(2026, 7, 26, 6, 11, tzinfo=UTC8)
SCHEDULED = datetime(2026, 7, 26, 6, 15, tzinfo=UTC8)
TOKEN = "0123456789abcdef0123456789abcdef"
EXECUTABLE = (
    r"C:\Users\Example\AppData\Local\Programs"
    r"\ThemeScheduler\app\ThemeScheduler.exe"
)
USER_ID = "S-1-5-21-1000-1000-1000-1001"


class FakeLock:
    def acquire(self) -> bool:
        return True

    def release(self) -> None:
        pass


class FixedClock:
    def now(self) -> datetime:
        return NOW


class MemoryLog:
    def __init__(self) -> None:
        self.events = []

    def append(self, event) -> None:
        self.events.append(event)


class MemoryTasks:
    def __init__(self, task: TaskSpec, *, fail: bool = False) -> None:
        self.task = task
        self.fail = fail

    def read(self, task_path: str):
        return self.task if self.task.task_path == task_path else None

    def register(self, task: TaskSpec) -> None:
        if self.fail:
            raise OSError("injected task failure")
        self.task = task

    def capture(self, task_path: str):
        return TaskDefinitionBackup(
            task_path,
            json.dumps(self.task.as_dict()),
            self.task.enabled,
        )

    def restore(self, backup: TaskDefinitionBackup) -> None:
        self.task = TaskSpec.from_dict(json.loads(backup.definition_xml))

    def delete(self, task_path: str) -> None:
        raise AssertionError("delete is not expected")


class NotificationActionServiceTests(unittest.TestCase):
    def _fixture(self, root: Path, *, task_fail: bool = False):
        layout = UserDataLayout(root / "data")
        layout.ensure_directories()
        config = ConfigStore(layout.config).initialize(AppConfig.defaults())
        StateStore(layout.state).initialize(AppState.initial())
        pending = PendingSwitch.create(
            target_profile="day",
            scheduled_at=SCHEDULED,
            next_fixed_at=datetime(2026, 7, 26, 23, 45, tzinfo=UTC8),
            now=NOW - timedelta(minutes=1),
        ).arm(now=NOW - timedelta(minutes=1), token=TOKEN)
        PendingSwitchStore(layout.pending_switch).create(pending)
        tasks = MemoryTasks(
            build_task_spec(
                config,
                executable=EXECUTABLE,
                user_id=USER_ID,
            ),
            fail=task_fail,
        )
        log = MemoryLog()
        service = NotificationActionService(
            layout,
            FakeLock(),
            tasks,
            executable=EXECUTABLE,
            user_id=USER_ID,
            clock=FixedClock(),
            event_log=log,
        )
        return layout, tasks, log, service

    def test_confirm_and_skip_change_only_pending_decision(self) -> None:
        for action, expected in (
            (NotificationAction.CONFIRM, NotificationActionResult.CONFIRMED),
            (NotificationAction.SKIP, NotificationActionResult.SKIPPED),
        ):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as raw:
                layout, tasks, log, service = self._fixture(Path(raw))
                before_task = tasks.task

                outcome = service.handle(build_action_uri(action, TOKEN))

                self.assertIs(outcome.result, expected)
                self.assertEqual(tasks.task, before_task)
                self.assertFalse(outcome.task_changed)
                self.assertEqual(
                    PendingSwitchStore(layout.pending_switch).load().decision.value,
                    expected.value,
                )
                self.assertEqual(len(log.events), 1)

    def test_delay_replaces_task_with_new_prepare_and_execution_pair(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout, tasks, _log, service = self._fixture(Path(raw))

            outcome = service.handle(build_action_uri(NotificationAction.DELAY, TOKEN))

            self.assertIs(outcome.result, NotificationActionResult.DELAYED)
            self.assertTrue(outcome.task_changed)
            pending = PendingSwitchStore(layout.pending_switch).load()
            self.assertEqual(pending.scheduled_at, SCHEDULED + timedelta(minutes=30))
            self.assertIsNone(pending.action_token)
            self.assertEqual(len(tasks.task.triggers), 6)

    def test_delay_task_failure_restores_original_pending_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout, _tasks, _log, service = self._fixture(Path(raw), task_fail=True)
            before = PendingSwitchStore(layout.pending_switch).load()

            outcome = service.handle(build_action_uri(NotificationAction.DELAY, TOKEN))

            self.assertIs(outcome.result, NotificationActionResult.FAILED)
            self.assertTrue(outcome.rollback_succeeded)
            self.assertEqual(PendingSwitchStore(layout.pending_switch).load(), before)

    def test_replay_and_malformed_uri_are_rejected_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            layout, _tasks, _log, service = self._fixture(Path(raw))
            first = service.handle(build_action_uri(NotificationAction.CONFIRM, TOKEN))
            self.assertIs(first.result, NotificationActionResult.CONFIRMED)
            after = PendingSwitchStore(layout.pending_switch).load()

            replay = service.handle(build_action_uri(NotificationAction.SKIP, TOKEN))
            malformed = service.handle("https://example.invalid/")

            self.assertIs(replay.result, NotificationActionResult.REJECTED)
            self.assertIs(malformed.result, NotificationActionResult.REJECTED)
            self.assertEqual(PendingSwitchStore(layout.pending_switch).load(), after)


if __name__ == "__main__":
    unittest.main()
