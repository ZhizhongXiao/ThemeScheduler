from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from theme_scheduler.accent_profile import (
    AccentProfile,
    AccentProfileStore,
    RgbColor,
)
from theme_scheduler.config import AppConfig, ConfigStore
from theme_scheduler.configuration_service import (
    ConfigurationResultKind,
    ConfigurationService,
)
from theme_scheduler.scheduler import (
    TaskDefinitionBackup,
    TaskSpec,
    build_task_spec,
)
from theme_scheduler.storage import UserDataLayout
from theme_scheduler.switch_override import (
    PendingSwitch,
    PendingSwitchStore,
    SwitchDecision,
)

EXECUTABLE = (
    r"C:\Users\Example\AppData\Local\Programs"
    r"\ThemeScheduler\ThemeScheduler.exe"
)
USER_ID = r"DESKTOP-TEST\Example"
NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


class FakeClock:
    def now(self) -> datetime:
        return NOW


class FakeLock:
    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.acquired = 0
        self.released = 0

    def acquire(self) -> bool:
        self.acquired += 1
        return self.available

    def release(self) -> None:
        self.released += 1


class MemoryLog:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.events = []

    def append(self, event) -> None:
        if self.fail:
            raise OSError("log failed")
        self.events.append(event)


class MemoryBackend:
    def __init__(
        self,
        task: TaskSpec | None = None,
        *,
        register_error: bool = False,
        partial_on_error: TaskSpec | None = None,
        delete_keeps_task: bool = False,
    ) -> None:
        self.task = task
        self.register_error = register_error
        self.partial_on_error = partial_on_error
        self.delete_keeps_task = delete_keeps_task
        self.register_count = 0

    def current_user_id(self) -> str:
        return USER_ID

    def read(self, task_path: str) -> TaskSpec | None:
        if self.task is None or self.task.task_path != task_path:
            return None
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
            task.task_path,
            json.dumps(task.as_dict()),
            task.enabled,
        )

    def restore(self, backup: TaskDefinitionBackup) -> None:
        self.task = TaskSpec.from_dict(json.loads(backup.definition_xml))

    def delete(self, task_path: str) -> None:
        if not self.delete_keeps_task:
            self.task = None


class FailingReplaceProfileStore(AccentProfileStore):
    def replace_if_valid(self, profile: AccentProfile) -> AccentProfile:
        raise OSError("profile write failed")


class FailingRestoreProfileStore(AccentProfileStore):
    def restore_trusted(self, profile: AccentProfile) -> AccentProfile:
        raise OSError("profile rollback failed")


class FailingSaveConfigStore(ConfigStore):
    def save(self, config: AppConfig) -> AppConfig:
        raise OSError("config write failed")


def changed_config() -> AppConfig:
    return AppConfig("07:10", "22:50", "light", "dark", True, False)


class ConfigurationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.layout = UserDataLayout(Path(self.temporary.name))
        self.layout.ensure_directories()
        ConfigStore(self.layout.config).initialize(AppConfig.defaults())

    def create_profiles(
        self,
        *,
        day_color: int = 0xD0744DA9,
        night_color: int = 0xC4FFB900,
        day_auto: bool = False,
    ) -> dict[str, AccentProfile]:
        profiles = {
            "day": AccentProfile(
                "day",
                "2026-07-23T10:00:00+08:00",
                day_auto,
                day_color,
                "26200",
            ),
            "night": AccentProfile(
                "night",
                "2026-07-23T10:00:00+08:00",
                False,
                night_color,
                "26200",
            ),
        }
        for name, profile in profiles.items():
            AccentProfileStore(
                self.layout.profile_path(name),
                name,
            ).create(profile)
        return profiles

    def service(
        self,
        backend: MemoryBackend,
        *,
        lock: FakeLock | None = None,
        log: MemoryLog | None = None,
    ) -> ConfigurationService:
        return ConfigurationService(
            self.layout,
            lock or FakeLock(),
            backend,
            executable=EXECUTABLE,
            user_id=USER_ID,
            event_log=log or MemoryLog(),
            clock=FakeClock(),
        )

    def test_updates_config_and_task_then_verifies_both(self) -> None:
        backend = MemoryBackend()
        log = MemoryLog()

        outcome = self.service(backend, log=log).update(changed_config())

        self.assertEqual(outcome.result, ConfigurationResultKind.CHANGED)
        self.assertTrue(outcome.config_changed)
        self.assertTrue(outcome.task_changed)
        self.assertTrue(outcome.task_verified)
        self.assertEqual(ConfigStore(self.layout.config).load(), changed_config())
        self.assertEqual(backend.task.triggers[0].local_time, "07:10")  # type: ignore[union-attr]
        self.assertEqual(len(log.events), 1)

    def test_repeated_update_is_no_change_but_audited(self) -> None:
        first_backend = MemoryBackend()
        first = self.service(first_backend).update(changed_config())
        self.assertEqual(first.result, ConfigurationResultKind.CHANGED)
        log = MemoryLog()

        second = self.service(first_backend, log=log).update(changed_config())

        self.assertEqual(second.result, ConfigurationResultKind.NO_CHANGE)
        self.assertFalse(second.config_changed)
        self.assertFalse(second.task_changed)
        self.assertEqual(len(log.events), 1)

    def test_bundle_updates_profiles_config_and_task_preserving_alpha(
        self,
    ) -> None:
        self.create_profiles()
        backend = MemoryBackend()

        outcome = self.service(backend).update_bundle(
            changed_config(),
            {
                "day": RgbColor.from_hex("#FFB900"),
                "night": RgbColor.from_hex("#744DA9"),
            },
        )

        self.assertEqual(outcome.result, ConfigurationResultKind.CHANGED)
        self.assertEqual(
            dict(outcome.profiles_changed or {}),
            {"day": True, "night": True},
        )
        self.assertTrue(outcome.profiles_verified)
        self.assertEqual(
            AccentProfileStore(self.layout.profile_path("day"), "day")
            .load()
            .colorization_color,
            0xD0FFB900,
        )
        self.assertEqual(
            AccentProfileStore(self.layout.profile_path("night"), "night")
            .load()
            .colorization_color,
            0xC4744DA9,
        )
        self.assertEqual(ConfigStore(self.layout.config).load(), changed_config())
        self.assertTrue(outcome.as_dict()["dataChanged"])
        self.assertFalse(outcome.as_dict()["windowsChanged"])

    def test_repeated_bundle_is_no_change_and_preserves_timestamps(self) -> None:
        before = self.create_profiles()
        backend = MemoryBackend(
            build_task_spec(
                AppConfig.defaults(),
                executable=EXECUTABLE,
                user_id=USER_ID,
            )
        )
        colors = {
            "day": RgbColor.from_hex("#744DA9"),
            "night": RgbColor.from_hex("#FFB900"),
        }

        outcome = self.service(backend).update_bundle(
            AppConfig.defaults(),
            colors,
        )

        self.assertEqual(outcome.result, ConfigurationResultKind.NO_CHANGE)
        self.assertEqual(
            dict(outcome.profiles_changed or {}),
            {"day": False, "night": False},
        )
        for name in ("day", "night"):
            self.assertEqual(
                AccentProfileStore(self.layout.profile_path(name), name).load(),
                before[name],
            )

    def test_bundle_task_failure_restores_profiles_and_config(self) -> None:
        before = self.create_profiles()
        backend = MemoryBackend(register_error=True)

        outcome = self.service(backend).update_bundle(
            changed_config(),
            {
                "day": RgbColor.from_hex("#102030"),
                "night": RgbColor.from_hex("#405060"),
            },
        )

        self.assertEqual(outcome.result, ConfigurationResultKind.FATAL_FAILURE)
        self.assertTrue(outcome.profile_rollback_attempted)
        self.assertTrue(outcome.profile_rollback_succeeded)
        self.assertEqual(ConfigStore(self.layout.config).load(), AppConfig.defaults())
        for name in ("day", "night"):
            self.assertEqual(
                AccentProfileStore(self.layout.profile_path(name), name).load(),
                before[name],
            )

    def test_bundle_second_profile_failure_restores_first(self) -> None:
        before = self.create_profiles()
        stores = {
            "day": AccentProfileStore(self.layout.profile_path("day"), "day"),
            "night": FailingReplaceProfileStore(
                self.layout.profile_path("night"), "night"
            ),
        }
        service = ConfigurationService(
            self.layout,
            FakeLock(),
            MemoryBackend(),
            executable=EXECUTABLE,
            user_id=USER_ID,
            event_log=MemoryLog(),
            clock=FakeClock(),
            profile_stores=stores,
        )

        outcome = service.update_bundle(
            changed_config(),
            {
                "day": RgbColor.from_hex("#102030"),
                "night": RgbColor.from_hex("#405060"),
            },
        )

        self.assertEqual(outcome.result, ConfigurationResultKind.FATAL_FAILURE)
        self.assertTrue(outcome.profile_rollback_succeeded)
        for name in ("day", "night"):
            self.assertEqual(
                AccentProfileStore(self.layout.profile_path(name), name).load(),
                before[name],
            )

    def test_bundle_first_profile_failure_leaves_both_profiles_unchanged(
        self,
    ) -> None:
        before = self.create_profiles()
        stores = {
            "day": FailingReplaceProfileStore(self.layout.profile_path("day"), "day"),
            "night": AccentProfileStore(self.layout.profile_path("night"), "night"),
        }
        service = ConfigurationService(
            self.layout,
            FakeLock(),
            MemoryBackend(),
            executable=EXECUTABLE,
            user_id=USER_ID,
            event_log=MemoryLog(),
            clock=FakeClock(),
            profile_stores=stores,
        )

        outcome = service.update_bundle(
            changed_config(),
            {
                "day": RgbColor.from_hex("#102030"),
                "night": RgbColor.from_hex("#405060"),
            },
        )

        self.assertEqual(outcome.result, ConfigurationResultKind.FATAL_FAILURE)
        self.assertTrue(outcome.profile_rollback_succeeded)
        for name in ("day", "night"):
            self.assertEqual(
                AccentProfileStore(self.layout.profile_path(name), name).load(),
                before[name],
            )

    def test_bundle_config_write_failure_restores_both_profiles(self) -> None:
        before = self.create_profiles()
        service = ConfigurationService(
            self.layout,
            FakeLock(),
            MemoryBackend(),
            executable=EXECUTABLE,
            user_id=USER_ID,
            config_store=FailingSaveConfigStore(self.layout.config),
            event_log=MemoryLog(),
            clock=FakeClock(),
        )

        outcome = service.update_bundle(
            changed_config(),
            {
                "day": RgbColor.from_hex("#102030"),
                "night": RgbColor.from_hex("#405060"),
            },
        )

        self.assertEqual(outcome.result, ConfigurationResultKind.FATAL_FAILURE)
        self.assertTrue(outcome.profile_rollback_succeeded)
        self.assertEqual(ConfigStore(self.layout.config).load(), AppConfig.defaults())
        for name in ("day", "night"):
            self.assertEqual(
                AccentProfileStore(self.layout.profile_path(name), name).load(),
                before[name],
            )

    def test_bundle_reports_incomplete_profile_rollback(self) -> None:
        before = self.create_profiles()
        stores = {
            "day": AccentProfileStore(self.layout.profile_path("day"), "day"),
            "night": FailingRestoreProfileStore(
                self.layout.profile_path("night"), "night"
            ),
        }
        service = ConfigurationService(
            self.layout,
            FakeLock(),
            MemoryBackend(register_error=True),
            executable=EXECUTABLE,
            user_id=USER_ID,
            event_log=MemoryLog(),
            clock=FakeClock(),
            profile_stores=stores,
        )

        outcome = service.update_bundle(
            changed_config(),
            {
                "day": RgbColor.from_hex("#102030"),
                "night": RgbColor.from_hex("#405060"),
            },
        )

        self.assertEqual(outcome.result, ConfigurationResultKind.PARTIAL_FAILURE)
        self.assertFalse(outcome.profile_rollback_succeeded)
        self.assertEqual(
            AccentProfileStore(self.layout.profile_path("day"), "day").load(),
            before["day"],
        )
        self.assertNotEqual(
            AccentProfileStore(self.layout.profile_path("night"), "night").load(),
            before["night"],
        )

    def test_bundle_unknown_profile_version_is_blocked_before_writes(
        self,
    ) -> None:
        self.create_profiles()
        day_path = self.layout.profile_path("day")
        payload = json.loads(day_path.read_text(encoding="utf-8"))
        payload["schemaVersion"] = 99
        day_path.write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
        backend = MemoryBackend()

        outcome = self.service(backend).update_bundle(
            changed_config(),
            {
                "day": RgbColor.from_hex("#102030"),
                "night": RgbColor.from_hex("#405060"),
            },
        )

        self.assertEqual(
            outcome.result,
            ConfigurationResultKind.DATA_UNTRUSTED,
        )
        self.assertEqual(ConfigStore(self.layout.config).load(), AppConfig.defaults())
        self.assertEqual(backend.register_count, 0)

    def test_bundle_turns_off_auto_colorization_without_changing_alpha(
        self,
    ) -> None:
        self.create_profiles(day_auto=True)
        backend = MemoryBackend(
            build_task_spec(
                AppConfig.defaults(),
                executable=EXECUTABLE,
                user_id=USER_ID,
            )
        )

        outcome = self.service(backend).update_bundle(
            AppConfig.defaults(),
            {
                "day": RgbColor.from_hex("#744DA9"),
                "night": RgbColor.from_hex("#FFB900"),
            },
        )

        self.assertEqual(outcome.result, ConfigurationResultKind.CHANGED)
        actual = AccentProfileStore(self.layout.profile_path("day"), "day").load()
        self.assertFalse(actual.auto_colorization)
        self.assertEqual(actual.colorization_color, 0xD0744DA9)

    def test_bundle_missing_profile_is_blocked_before_writes(self) -> None:
        AccentProfileStore(
            self.layout.profile_path("day"),
            "day",
        ).create(
            AccentProfile(
                "day",
                "2026-07-23T10:00:00+08:00",
                False,
                0xC4744DA9,
                "26200",
            )
        )
        backend = MemoryBackend()

        outcome = self.service(backend).update_bundle(
            changed_config(),
            {
                "day": RgbColor.from_hex("#102030"),
                "night": RgbColor.from_hex("#405060"),
            },
        )

        self.assertEqual(
            outcome.result,
            ConfigurationResultKind.DATA_UNTRUSTED,
        )
        self.assertEqual(ConfigStore(self.layout.config).load(), AppConfig.defaults())
        self.assertEqual(backend.register_count, 0)

    def test_task_failure_restores_previous_config_and_task(self) -> None:
        original = AppConfig.defaults()
        backend = MemoryBackend(register_error=True)

        outcome = self.service(backend).update(changed_config())

        self.assertEqual(outcome.result, ConfigurationResultKind.FATAL_FAILURE)
        self.assertTrue(outcome.rollback_attempted)
        self.assertTrue(outcome.rollback_succeeded)
        self.assertEqual(ConfigStore(self.layout.config).load(), original)
        self.assertIsNone(backend.task)

    def test_incomplete_task_rollback_is_reported_as_partial(self) -> None:
        partial_task = TaskSpec.from_dict(
            build_task_spec(
                AppConfig.defaults(),
                executable=EXECUTABLE,
                user_id=USER_ID,
            ).as_dict()
        )
        backend = MemoryBackend(
            register_error=True,
            partial_on_error=partial_task,
            delete_keeps_task=True,
        )

        outcome = self.service(backend).update(changed_config())

        self.assertEqual(outcome.result, ConfigurationResultKind.PARTIAL_FAILURE)
        self.assertFalse(outcome.rollback_succeeded)
        self.assertEqual(ConfigStore(self.layout.config).load(), AppConfig.defaults())

    def test_busy_lock_prevents_config_and_task_access(self) -> None:
        backend = MemoryBackend()
        lock = FakeLock(False)

        outcome = self.service(backend, lock=lock).update(changed_config())

        self.assertEqual(outcome.result, ConfigurationResultKind.ALREADY_RUNNING)
        self.assertEqual(backend.register_count, 0)
        self.assertEqual(ConfigStore(self.layout.config).load(), AppConfig.defaults())

    def test_log_failure_reports_committed_partial_success(self) -> None:
        backend = MemoryBackend()

        outcome = self.service(
            backend,
            log=MemoryLog(fail=True),
        ).update(changed_config())

        self.assertEqual(outcome.result, ConfigurationResultKind.PARTIAL_FAILURE)
        self.assertTrue(outcome.config_changed)
        self.assertTrue(outcome.task_changed)
        self.assertFalse(outcome.log_written)
        self.assertEqual(ConfigStore(self.layout.config).load(), changed_config())

    def test_update_cancels_delayed_pair_and_pending_token(self) -> None:
        original = AppConfig.defaults()
        scheduled = NOW + timedelta(minutes=35)
        pending = PendingSwitch.create(
            target_profile="night",
            scheduled_at=scheduled - timedelta(minutes=30),
            next_fixed_at=NOW + timedelta(hours=8),
            now=NOW - timedelta(minutes=1),
        )
        delayed = PendingSwitch(
            target_profile=pending.target_profile,
            original_scheduled_at=pending.original_scheduled_at,
            scheduled_at=scheduled,
            next_fixed_at=pending.next_fixed_at,
            decision=pending.decision,
            defer_count=1,
            action_token=None,
            prepared_at=None,
            updated_at=NOW,
        )
        PendingSwitchStore(self.layout.pending_switch).create(delayed)
        backend = MemoryBackend(
            build_task_spec(
                original,
                executable=EXECUTABLE,
                user_id=USER_ID,
                pending_switch=delayed,
            )
        )

        outcome = self.service(backend).update(original)

        self.assertEqual(outcome.result, ConfigurationResultKind.CHANGED)
        self.assertTrue(outcome.pending_switch_changed)
        self.assertFalse(self.layout.pending_switch.exists())
        self.assertEqual(len(backend.task.triggers), 4)  # type: ignore[union-attr]

    def test_task_failure_preserves_pending_state_and_dynamic_pair(self) -> None:
        original = AppConfig.defaults()
        scheduled = NOW + timedelta(minutes=35)
        pending = PendingSwitch(
            target_profile="night",
            original_scheduled_at=scheduled - timedelta(minutes=30),
            scheduled_at=scheduled,
            next_fixed_at=NOW + timedelta(hours=8),
            decision=SwitchDecision.PENDING,
            defer_count=1,
            action_token=None,
            prepared_at=None,
            updated_at=NOW,
        )
        PendingSwitchStore(self.layout.pending_switch).create(pending)
        dynamic = build_task_spec(
            original,
            executable=EXECUTABLE,
            user_id=USER_ID,
            pending_switch=pending,
        )
        backend = MemoryBackend(
            dynamic,
            register_error=True,
            partial_on_error=dynamic,
        )

        outcome = self.service(backend).update(original)

        self.assertEqual(outcome.result, ConfigurationResultKind.FATAL_FAILURE)
        self.assertEqual(PendingSwitchStore(self.layout.pending_switch).load(), pending)
        self.assertEqual(len(backend.task.triggers), 6)  # type: ignore[union-attr]
