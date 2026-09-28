from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from theme_scheduler.config import AppConfig
from theme_scheduler.scheduler import (
    DAY_PREPARE_TRIGGER_ID,
    DAY_TRIGGER_ID,
    DEFAULT_TASK_PATH,
    DEFERRED_PREPARE_TRIGGER_ID,
    DEFERRED_TRIGGER_ID,
    NIGHT_PREPARE_TRIGGER_ID,
    NIGHT_TRIGGER_ID,
    DailyTrigger,
    SchedulerContractError,
    SchedulerMutationError,
    TaskAction,
    TaskDefinitionBackup,
    TaskSpec,
    TimeTrigger,
    build_task_spec,
    compare_task_specs,
    delete_task,
    inspect_task,
    reconcile_task,
    set_task_enabled,
)
from theme_scheduler.switch_override import PendingSwitch

EXECUTABLE = (
    r"C:\Users\Example\AppData\Local\Programs"
    r"\ThemeScheduler\ThemeScheduler.exe"
)
USER_ID = r"DESKTOP-TEST\Example"


class MemoryBackend:
    def __init__(
        self,
        task: TaskSpec | None = None,
        *,
        register_error: bool = False,
        readback_override: TaskSpec | None = None,
        partial_on_error: TaskSpec | None = None,
        delete_keeps_task: bool = False,
    ) -> None:
        self.task = task
        self.register_error = register_error
        self.readback_override = readback_override
        self.partial_on_error = partial_on_error
        self.delete_keeps_task = delete_keeps_task
        self.register_count = 0
        self.delete_count = 0

    def current_user_id(self) -> str:
        return USER_ID

    def read(self, task_path: str) -> TaskSpec | None:
        if self.task is None or self.task.task_path != task_path:
            return None
        if self.readback_override is not None:
            value = self.readback_override
            self.readback_override = None
            return value
        return self.task

    def register(self, task: TaskSpec) -> None:
        self.register_count += 1
        if self.register_error:
            self.register_error = False
            self.task = self.partial_on_error
            raise OSError("register failed")
        self.task = task

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
        self.delete_count += 1
        if not self.delete_keeps_task:
            self.task = None


def task_spec(
    config: AppConfig | None = None,
    *,
    executable: str = EXECUTABLE,
) -> TaskSpec:
    return build_task_spec(
        config or AppConfig.defaults(),
        executable=executable,
        user_id=USER_ID,
    )


class TaskContractTests(unittest.TestCase):
    def test_builds_one_task_with_four_identified_daily_triggers(self) -> None:
        task = task_spec()

        self.assertEqual(task.task_path, DEFAULT_TASK_PATH)
        self.assertEqual(
            {trigger.trigger_id for trigger in task.triggers},
            {
                DAY_PREPARE_TRIGGER_ID,
                DAY_TRIGGER_ID,
                NIGHT_PREPARE_TRIGGER_ID,
                NIGHT_TRIGGER_ID,
            },
        )
        daily_triggers = [
            trigger for trigger in task.triggers if isinstance(trigger, DailyTrigger)
        ]
        self.assertEqual(len(daily_triggers), len(task.triggers))
        self.assertEqual(
            {trigger.local_time for trigger in daily_triggers},
            {"06:10", "06:15", "23:40", "23:45"},
        )
        self.assertTrue(all(trigger.days_interval == 1 for trigger in daily_triggers))
        self.assertEqual(task.action.arguments, "auto")
        self.assertEqual(
            task.action.working_directory,
            r"C:\Users\Example\AppData\Local\Programs\ThemeScheduler",
        )

    def test_deferred_switch_adds_exact_one_time_prepare_and_boundary(self) -> None:
        scheduled = datetime(2026, 7, 26, 20, 30, tzinfo=timezone(timedelta(hours=8)))
        pending = PendingSwitch.create(
            target_profile="night",
            scheduled_at=scheduled - timedelta(minutes=30),
            next_fixed_at=datetime(
                2026,
                7,
                27,
                6,
                15,
                tzinfo=timezone(timedelta(hours=8)),
            ),
            now=scheduled - timedelta(minutes=35),
        )
        pending = replace(
            pending,
            scheduled_at=scheduled,
            defer_count=1,
            updated_at=scheduled - timedelta(minutes=29),
        )

        task = build_task_spec(
            AppConfig.defaults(),
            executable=EXECUTABLE,
            user_id=USER_ID,
            pending_switch=pending,
        )
        deferred = {
            trigger.trigger_id: trigger
            for trigger in task.triggers
            if isinstance(trigger, TimeTrigger)
        }

        self.assertEqual(
            set(deferred),
            {DEFERRED_PREPARE_TRIGGER_ID, DEFERRED_TRIGGER_ID},
        )
        self.assertEqual(
            datetime.fromisoformat(deferred[DEFERRED_PREPARE_TRIGGER_ID].start_at),
            scheduled - timedelta(minutes=5),
        )
        self.assertEqual(
            datetime.fromisoformat(deferred[DEFERRED_TRIGGER_ID].start_at),
            scheduled,
        )

    def test_freezes_current_user_and_low_disturbance_settings(self) -> None:
        task = task_spec()

        self.assertEqual(task.principal.logon_type, "InteractiveToken")
        self.assertEqual(task.principal.run_level, "LeastPrivilege")
        self.assertTrue(task.settings.start_when_available)
        self.assertFalse(task.settings.wake_to_run)
        self.assertEqual(task.settings.multiple_instances, "IgnoreNew")
        self.assertTrue(task.settings.allow_start_on_batteries)
        self.assertFalse(task.settings.stop_if_going_on_batteries)
        self.assertFalse(task.settings.run_only_if_network_available)
        self.assertFalse(task.settings.hidden)
        self.assertEqual(task.settings.execution_time_limit, "PT5M")

    def test_round_trip_is_strict_and_order_normalized(self) -> None:
        original = task_spec()
        payload = original.as_dict()
        payload["triggers"].reverse()

        restored = TaskSpec.from_dict(payload)

        self.assertEqual(restored.as_dict(), original.as_dict())
        payload["unexpected"] = True
        with self.assertRaises(SchedulerContractError):
            TaskSpec.from_dict(payload)

    def test_rejects_relative_executable_and_non_daily_contract(self) -> None:
        with self.assertRaises(SchedulerContractError):
            task_spec(executable=r"ThemeScheduler.exe")
        desired = task_spec()
        with self.assertRaises(SchedulerContractError):
            reconcile_task(
                MemoryBackend(),
                replace(
                    desired,
                    triggers=(
                        DailyTrigger(DAY_TRIGGER_ID, "06:15", 2),
                        DailyTrigger(NIGHT_TRIGGER_ID, "23:45"),
                    ),
                ),
            )

    def test_rejects_duplicate_boundary_times(self) -> None:
        desired = task_spec()
        with self.assertRaises(SchedulerContractError):
            reconcile_task(
                MemoryBackend(),
                replace(
                    desired,
                    triggers=(
                        DailyTrigger(DAY_TRIGGER_ID, "06:15"),
                        DailyTrigger(NIGHT_TRIGGER_ID, "06:15"),
                    ),
                ),
            )

    def test_inspection_reports_exact_drift(self) -> None:
        expected = task_spec()
        actual = replace(
            expected,
            action=TaskAction(
                r"C:\Wrong\ThemeScheduler.exe",
                "auto",
                r"C:\Wrong",
            ),
        )

        inspection = inspect_task(expected, actual)

        self.assertTrue(inspection.exists)
        self.assertFalse(inspection.valid)
        self.assertIn(
            "action.executable",
            {difference.field for difference in inspection.differences},
        )
        self.assertFalse(inspect_task(expected, None).exists)


class TaskMutationTests(unittest.TestCase):
    def test_create_repair_and_no_change_are_idempotent(self) -> None:
        desired = task_spec()
        backend = MemoryBackend()

        created = reconcile_task(backend, desired)
        unchanged = reconcile_task(backend, desired)
        updated = task_spec(AppConfig("07:00", "22:30", "light", "dark", True, True))
        repaired = reconcile_task(backend, updated)

        self.assertEqual(created.operation, "created")
        self.assertTrue(created.changed)
        self.assertEqual(unchanged.operation, "unchanged")
        self.assertFalse(unchanged.changed)
        self.assertEqual(repaired.operation, "repaired")
        self.assertEqual(backend.task, updated)
        self.assertEqual(backend.register_count, 2)

    def test_registration_error_restores_previous_definition(self) -> None:
        before = task_spec()
        desired = task_spec(AppConfig("07:00", "22:30", "light", "dark", True, True))
        backend = MemoryBackend(
            before,
            register_error=True,
            partial_on_error=desired,
        )

        with self.assertRaises(SchedulerMutationError) as caught:
            reconcile_task(backend, desired)

        self.assertTrue(caught.exception.rollback_attempted)
        self.assertTrue(caught.exception.rollback_succeeded)
        self.assertEqual(backend.task, before)

    def test_readback_mismatch_restores_previous_definition(self) -> None:
        before = task_spec()
        desired = task_spec(AppConfig("07:00", "22:30", "light", "dark", True, True))
        mismatch = replace(desired, enabled=False)
        backend = MemoryBackend(before, readback_override=before)

        # First read consumes the override, so arrange mismatch after registration.
        backend.readback_override = None
        original_register = backend.register

        def register_with_mismatch(task: TaskSpec) -> None:
            original_register(task)
            if task == desired:
                backend.readback_override = mismatch

        backend.register = register_with_mismatch

        with self.assertRaises(SchedulerMutationError) as caught:
            reconcile_task(backend, desired)

        self.assertTrue(caught.exception.rollback_succeeded)
        self.assertEqual(backend.task, before)

    def test_failed_create_removes_partial_definition(self) -> None:
        desired = task_spec()
        backend = MemoryBackend(
            register_error=True,
            partial_on_error=desired,
        )

        with self.assertRaises(SchedulerMutationError) as caught:
            reconcile_task(backend, desired)

        self.assertTrue(caught.exception.rollback_succeeded)
        self.assertIsNone(backend.task)

    def test_enable_state_and_delete_are_readback_verified(self) -> None:
        desired = task_spec()
        backend = MemoryBackend(desired)

        disabled = set_task_enabled(backend, DEFAULT_TASK_PATH, False)
        deleted = delete_task(backend)
        absent = delete_task(backend)

        self.assertTrue(disabled.changed)
        self.assertFalse(disabled.after.enabled)  # type: ignore[union-attr]
        self.assertEqual(deleted.operation, "deleted")
        self.assertEqual(absent.operation, "absent")
        self.assertFalse(absent.changed)

    def test_delete_readback_failure_is_reported(self) -> None:
        backend = MemoryBackend(task_spec(), delete_keeps_task=True)

        with self.assertRaises(SchedulerMutationError):
            delete_task(backend)

    def test_comparison_has_no_stale_trigger_after_full_repair(self) -> None:
        before = task_spec()
        stale = replace(
            before,
            triggers=(
                *before.triggers,
                DailyTrigger("OldBoundary", "12:00"),
            ),
        )
        after = task_spec(AppConfig("08:10", "21:20", "light", "dark", True, True))
        backend = MemoryBackend(stale)

        reconcile_task(backend, after)

        repaired = backend.task
        assert repaired is not None
        self.assertEqual(compare_task_specs(after, repaired), ())
        daily_triggers = [
            trigger
            for trigger in repaired.triggers
            if isinstance(trigger, DailyTrigger)
        ]
        self.assertEqual(len(daily_triggers), len(repaired.triggers))
        self.assertEqual(
            {trigger.local_time for trigger in daily_triggers},
            {"08:05", "08:10", "21:15", "21:20"},
        )


if __name__ == "__main__":
    unittest.main()
