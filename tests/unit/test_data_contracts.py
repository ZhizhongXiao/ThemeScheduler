from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from tests.fixtures.theme_files import windows_11_variant_theme
from theme_scheduler.accent_profile import (
    AccentProfile,
    AccentProfileStore,
)
from theme_scheduler.accent_theme import ThemeVisualState, read_visual_state
from theme_scheduler.appearance import AppearanceRegistrySnapshot, RegistryValue
from theme_scheduler.backup import (
    InstallBackup,
    InstallBackupStore,
    InstallBackupValidationError,
    capture_install_backup,
)
from theme_scheduler.config import (
    AppConfig,
    ConfigStore,
    ConfigValidationError,
)
from theme_scheduler.log_policy import (
    FAILED_TRANSACTION_MINIMUM_KEEP,
    FAILED_TRANSACTION_RETENTION_DAYS,
    LOG_BACKUP_COUNT,
    LOG_FILE_NAME,
    LOG_MAX_BYTES,
    LOG_SCHEMA_VERSION,
    SUCCESSFUL_TRANSACTION_KEEP,
    EventLogWriter,
    LogEvent,
    LogEventValidationError,
)
from theme_scheduler.persistence import atomic_write_json


class SharedEventLogLockFactory:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._guard = threading.Lock()
        self.active = 0
        self.maximum_active = 0

    def __call__(self) -> SharedEventLogLock:
        return SharedEventLogLock(self)


class SharedEventLogLock:
    def __init__(self, factory: SharedEventLogLockFactory) -> None:
        self._factory = factory

    def acquire(self) -> bool:
        self._factory._lock.acquire()
        with self._factory._guard:
            self._factory.active += 1
            self._factory.maximum_active = max(
                self._factory.maximum_active,
                self._factory.active,
            )
        return True

    def release(self) -> None:
        with self._factory._guard:
            self._factory.active -= 1
        self._factory._lock.release()
from theme_scheduler.runtime_retention import (
    execute_runtime_cleanup,
    plan_runtime_cleanup,
)
from theme_scheduler.state import (
    AppState,
    StateStore,
    StateValidationError,
    UntrustedStateError,
    load_trusted_state,
)
from theme_scheduler.storage import UserDataLayout


class ConfigContractTests(unittest.TestCase):
    def test_defaults_round_trip(self) -> None:
        config = AppConfig.defaults()

        self.assertEqual(AppConfig.from_dict(config.as_dict()), config)
        self.assertEqual(config.day_start, "06:15")
        self.assertEqual(config.night_start, "23:45")
        self.assertEqual(config.day_system_theme, "light")
        self.assertEqual(config.night_system_theme, "dark")
        self.assertFalse(config.day_start_taskbar_accent)
        self.assertTrue(config.night_start_taskbar_accent)

    def test_version_1_config_loads_with_preserve_semantics(self) -> None:
        legacy = {
            "kind": "themescheduler.config",
            "schemaVersion": 1,
            "schedule": {"dayStart": "06:15", "nightStart": "23:45"},
            "profiles": {
                "day": {"appsTheme": "light"},
                "night": {"appsTheme": "dark"},
            },
            "notifications": {"errors": True, "statusChanges": True},
        }

        config = AppConfig.from_dict(legacy)

        self.assertIsNone(config.day_system_theme)
        self.assertIsNone(config.night_system_theme)
        self.assertIsNone(config.day_start_taskbar_accent)
        self.assertIsNone(config.night_start_taskbar_accent)
        self.assertIsNone(config.day_title_borders_accent)
        self.assertIsNone(config.night_title_borders_accent)
        self.assertEqual(config.as_dict()["schemaVersion"], 2)

    def test_unknown_field_is_rejected(self) -> None:
        payload = AppConfig.defaults().as_dict()
        payload["surprise"] = True

        with self.assertRaisesRegex(ConfigValidationError, "unknown"):
            AppConfig.from_dict(payload)

        wrong_type = AppConfig.defaults().as_dict()
        wrong_type["profiles"]["day"]["appsTheme"] = ["light"]
        with self.assertRaises(ConfigValidationError):
            AppConfig.from_dict(wrong_type)

    def test_invalid_or_equal_times_are_rejected(self) -> None:
        with self.assertRaises(ConfigValidationError):
            AppConfig("6:15", "23:45", "light", "dark", True, True)
        with self.assertRaisesRegex(ConfigValidationError, "must differ"):
            AppConfig("06:15", "06:15", "light", "dark", True, True)

    def test_complete_appearance_fields_are_strict_and_profile_scoped(self) -> None:
        config = AppConfig.defaults()
        self.assertEqual(
            config.profile_appearance("night"),
            ("dark", "dark", True, True),
        )
        with self.assertRaises(ValueError):
            config.profile_appearance("unknown")

        invalid_mode = config.as_dict()
        invalid_mode["profiles"]["day"]["systemTheme"] = "sepia"
        with self.assertRaisesRegex(ConfigValidationError, "systemTheme"):
            AppConfig.from_dict(invalid_mode)

        invalid_surface = config.as_dict()
        invalid_surface["profiles"]["night"]["accentSurfaces"]["startTaskbar"] = 1
        with self.assertRaisesRegex(ConfigValidationError, "boolean or null"):
            AppConfig.from_dict(invalid_surface)

    def test_store_requires_explicit_initialization_and_valid_current_file(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            store = ConfigStore(path)
            with self.assertRaises(FileNotFoundError):
                store.load()
            self.assertEqual(store.initialize(), AppConfig.defaults())
            with self.assertRaises(FileExistsError):
                store.initialize()

            updated = AppConfig("07:00", "22:30", "light", "dark", True, False)
            self.assertEqual(store.save(updated), updated)
            path.write_text('{"kind":', encoding="utf-8")
            with self.assertRaises(ValueError):
                store.save(AppConfig.defaults())
            self.assertEqual(path.read_text(encoding="utf-8"), '{"kind":')


class StateContractTests(unittest.TestCase):
    def test_initial_state_is_explicit_and_round_trips(self) -> None:
        state = AppState.initial()

        self.assertEqual(AppState.from_dict(state.as_dict()), state)
        self.assertEqual(state.last_result, "never")
        self.assertFalse(state.paused)

    def test_success_requires_timestamp_and_applied_profile(self) -> None:
        with self.assertRaisesRegex(StateValidationError, "lastRunAt"):
            AppState(False, "day", None, "day", "success")
        with self.assertRaisesRegex(StateValidationError, "lastAppliedProfile"):
            AppState(
                False,
                "day",
                "2026-07-23T06:15:00+08:00",
                None,
                "success",
            )

    def test_missing_or_damaged_state_is_untrusted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            with self.assertRaises(UntrustedStateError):
                load_trusted_state(path)
            path.write_text('{"kind":', encoding="utf-8")
            with self.assertRaises(UntrustedStateError):
                load_trusted_state(path)

    def test_unknown_state_field_is_rejected(self) -> None:
        payload = AppState.initial().as_dict()
        payload["unknown"] = "value"
        with self.assertRaisesRegex(StateValidationError, "unknown"):
            AppState.from_dict(payload)

        wrong_type = AppState.initial().as_dict()
        wrong_type["activeProfile"] = ["day"]
        with self.assertRaises(StateValidationError):
            AppState.from_dict(wrong_type)

    def test_store_never_overwrites_untrusted_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            store = StateStore(path)
            store.initialize()
            path.write_text("[]", encoding="utf-8")

            with self.assertRaises(UntrustedStateError):
                store.save(
                    AppState(
                        False,
                        "day",
                        "2026-07-23T06:15:00+08:00",
                        "day",
                        "success",
                    )
                )
            self.assertEqual(path.read_text(encoding="utf-8"), "[]")


class AccentProfileStoreTests(unittest.TestCase):
    def _profile(self, color: int = 0xC4744DA9) -> AccentProfile:
        return AccentProfile(
            "day",
            "2026-07-23T10:00:00+08:00",
            False,
            color,
            "26200",
        )

    def test_replace_requires_a_valid_existing_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "day.json"
            store = AccentProfileStore(path, "day")
            store.create(self._profile())
            replacement = self._profile(0xC4FFB900)
            self.assertEqual(store.replace_if_valid(replacement), replacement)

            path.write_text('{"kind":', encoding="utf-8")
            with self.assertRaises(ValueError):
                store.replace_if_valid(self._profile())
            self.assertEqual(path.read_text(encoding="utf-8"), '{"kind":')


class InstallBackupContractTests(unittest.TestCase):
    def _backup(
        self,
        *,
        exists: bool = True,
        complete: bool = False,
    ) -> InstallBackup:
        theme = self._theme()
        return InstallBackup(
            captured_at="2026-07-23T21:00:00+08:00",
            created_by_version="0.1.0.dev0",
            windows_build="26200",
            apps_value_exists=exists,
            apps_value_type_code=4 if exists else None,
            apps_value_data=1 if exists else None,
            source_theme_path=r"C:\Users\tester\AppData\Local\Themes\Custom.theme",
            theme_sha256=hashlib.sha256(theme).hexdigest(),
            auto_colorization=False,
            colorization_color="0XC4FFB900",
            app_mode="Light",
            system_mode="Dark",
            appearance_registry=(
                AppearanceRegistrySnapshot(
                    apps_theme=RegistryValue(
                        exists,
                        1 if exists else None,
                        4 if exists else None,
                    ),
                    system_theme=RegistryValue(True, 0, 4),
                    start_taskbar_accent=RegistryValue(True, 1, 4),
                    title_borders_accent=RegistryValue(True, 0, 4),
                )
                if complete
                else None
            ),
        )

    @staticmethod
    def _theme() -> bytes:
        return (
            b"[Theme]\r\n"
            b"DisplayName=Install Backup\r\n"
            b"ThemeId={65CC0448-76B8-4EB2-ADF7-D3186669AAC9}\r\n\r\n"
            b"[VisualStyles]\r\n"
            b"AutoColorization=0\r\n"
            b"ColorizationColor=0XC4FFB900\r\n"
            b"SystemMode=Dark\r\n"
            b"AppMode=Light\r\n"
        )

    def test_present_and_missing_registry_states_round_trip(self) -> None:
        for backup in (
            self._backup(),
            self._backup(exists=False),
            self._backup(complete=True),
        ):
            with self.subTest(exists=backup.apps_value_exists):
                self.assertEqual(InstallBackup.from_dict(backup.as_dict()), backup)
        self.assertEqual(self._backup().as_dict()["schemaVersion"], 1)
        self.assertEqual(self._backup(complete=True).as_dict()["schemaVersion"], 2)

        inconsistent = deepcopy(self._backup(complete=True).as_dict())
        inconsistent["activeTheme"]["systemMode"] = "Light"
        with self.assertRaisesRegex(
            InstallBackupValidationError,
            "SystemUsesLightTheme",
        ):
            InstallBackup.from_dict(inconsistent)

    def test_manifest_binds_exact_install_theme_name(self) -> None:
        payload = self._backup().as_dict()
        payload["activeTheme"]["backupPath"] = "other.theme"

        with self.assertRaisesRegex(
            InstallBackupValidationError, "must be install.theme"
        ):
            InstallBackup.from_dict(payload)

    def test_unknown_or_partial_backup_is_rejected(self) -> None:
        payload = self._backup().as_dict()
        payload["activeTheme"]["unexpected"] = True
        with self.assertRaises(InstallBackupValidationError):
            InstallBackup.from_dict(payload)

        partial = deepcopy(self._backup().as_dict())
        del partial["appsUseLightTheme"]["data"]
        with self.assertRaises(InstallBackupValidationError):
            InstallBackup.from_dict(partial)

        boolean_data = deepcopy(self._backup().as_dict())
        boolean_data["appsUseLightTheme"]["data"] = True
        with self.assertRaises(InstallBackupValidationError):
            InstallBackup.from_dict(boolean_data)

        mismatched_mode = deepcopy(self._backup().as_dict())
        mismatched_mode["appsUseLightTheme"]["data"] = 0
        with self.assertRaisesRegex(
            InstallBackupValidationError,
            "does not match",
        ):
            InstallBackup.from_dict(mismatched_mode)

    def test_store_creates_once_and_verifies_theme_hash_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = InstallBackupStore(root / "install.json", root / "install.theme")
            manifest = self._backup()
            self.assertEqual(store.create(manifest, self._theme()), manifest)
            self.assertEqual(store.load_verified(), manifest)
            with self.assertRaises(FileExistsError):
                store.create(manifest, self._theme())

            (root / "install.theme").write_bytes(self._theme() + b"tampered")
            with self.assertRaisesRegex(InstallBackupValidationError, "SHA-256"):
                store.load_verified()

    def test_store_resumes_only_an_identical_orphan_theme(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            theme_path = root / "install.theme"
            theme_path.write_bytes(self._theme())
            store = InstallBackupStore(root / "install.json", theme_path)

            self.assertEqual(
                store.create(self._backup(), self._theme()), self._backup()
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            theme_path = root / "install.theme"
            theme_path.write_bytes(b"different")
            store = InstallBackupStore(root / "install.json", theme_path)
            with self.assertRaisesRegex(FileExistsError, "unmatched orphan"):
                store.create(self._backup(), self._theme())

    def test_manifest_failure_cleans_theme_created_by_same_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = InstallBackupStore(root / "install.json", root / "install.theme")
            with (
                patch(
                    "theme_scheduler.backup.atomic_write_json",
                    side_effect=OSError("simulated manifest failure"),
                ),
                self.assertRaises(OSError),
            ):
                store.create(self._backup(), self._theme())

            self.assertFalse((root / "install.json").exists())
            self.assertFalse((root / "install.theme").exists())

    def test_live_capture_creates_once_from_theme_and_exact_app_value(
        self,
    ) -> None:
        class ThemeBackend:
            def __init__(self, path: Path) -> None:
                self.path = path

            def current_theme_path(self) -> Path:
                return self.path

        class AppBackend:
            def read_value(self, name: str) -> RegistryValue:
                return RegistryValue(True, 1, 4)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Custom.theme"
            source.write_bytes(self._theme())
            store = InstallBackupStore(
                root / "backup" / "install.json",
                root / "backup" / "install.theme",
            )

            captured = capture_install_backup(
                store,
                created_by_version="0.1.0",
                windows_build="26200",
                theme_backend=ThemeBackend(source),  # type: ignore[arg-type]
                app_backend=AppBackend(),
                colorization_reader=lambda: 0xC4FFB900,
                timestamp="2026-07-25T12:00:00+08:00",
            )

            self.assertEqual(captured.app_mode, "Light")
            self.assertEqual(captured.system_mode, "Dark")
            self.assertEqual(captured.apps_value_data, 1)
            self.assertEqual(store.load_verified(), captured)
            with self.assertRaises(FileExistsError):
                capture_install_backup(
                    store,
                    created_by_version="0.1.0",
                    windows_build="26200",
                    theme_backend=ThemeBackend(source),  # type: ignore[arg-type]
                    app_backend=AppBackend(),
                    colorization_reader=lambda: 0xC4FFB900,
                    timestamp="2026-07-25T12:01:00+08:00",
                )

    def test_live_capture_normalizes_registry_theme_semantic_mismatch(
        self,
    ) -> None:
        class ThemeBackend:
            def __init__(self, path: Path) -> None:
                self.path = path

            def current_theme_path(self) -> Path:
                return self.path

        class AppBackend:
            def read_value(self, name: str) -> RegistryValue:
                return RegistryValue(True, 0, 4)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Custom.theme"
            source.write_bytes(self._theme())
            store = InstallBackupStore(
                root / "backup" / "install.json",
                root / "backup" / "install.theme",
            )

            source_before = source.read_bytes()
            captured = capture_install_backup(
                store,
                created_by_version="0.1.0",
                windows_build="26200",
                theme_backend=ThemeBackend(source),  # type: ignore[arg-type]
                app_backend=AppBackend(),
                colorization_reader=lambda: 0xC4744DA9,
                timestamp="2026-07-25T12:00:00+08:00",
            )

            self.assertEqual(captured.app_mode, "Dark")
            self.assertEqual(captured.colorization_color, "0XC4744DA9")
            self.assertEqual(source.read_bytes(), source_before)
            recovery = read_visual_state(store.theme_path.read_bytes())
            self.assertEqual(recovery.app_mode, "Dark")
            self.assertEqual(recovery.colorization_color, 0xC4744DA9)

    def test_live_capture_records_complete_appearance_snapshot(self) -> None:
        class ThemeBackend:
            def __init__(self, path: Path) -> None:
                self.path = path

            def current_theme_path(self) -> Path:
                return self.path

        snapshot = AppearanceRegistrySnapshot(
            apps_theme=RegistryValue(False),
            system_theme=RegistryValue(True, 0, 4),
            start_taskbar_accent=RegistryValue(True, 0, 4),
            title_borders_accent=RegistryValue(True, 1, 4),
        )

        class AppearanceBackend:
            def read_visual_state(self) -> ThemeVisualState:
                return ThemeVisualState("0", 0xC4744DA9, "Dark", "Dark")

            def capture(self) -> AppearanceRegistrySnapshot:
                return snapshot

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Custom.theme"
            source.write_bytes(self._theme())
            store = InstallBackupStore(
                root / "backup" / "install.json",
                root / "backup" / "install.theme",
            )

            captured = capture_install_backup(
                store,
                created_by_version="0.1.5",
                windows_build="26200",
                theme_backend=ThemeBackend(source),  # type: ignore[arg-type]
                appearance_backend=AppearanceBackend(),  # type: ignore[arg-type]
                colorization_reader=lambda: 0xC4744DA9,
                timestamp="2026-09-13T12:00:00+08:00",
            )

            self.assertEqual(captured.appearance_registry, snapshot)
            self.assertFalse(captured.apps_value_exists)
            self.assertEqual(captured.system_mode, "Dark")
            self.assertEqual(captured.as_dict()["schemaVersion"], 2)
            self.assertEqual(store.load_verified(), captured)

    def test_live_capture_materializes_windows_11_variant_for_recovery(self) -> None:
        class ThemeBackend:
            def __init__(self, path: Path) -> None:
                self.path = path

            def current_theme_path(self) -> Path:
                return self.path

        snapshot = AppearanceRegistrySnapshot(
            apps_theme=RegistryValue(True, 1, 4),
            system_theme=RegistryValue(True, 0, 4),
            start_taskbar_accent=RegistryValue(True, 1, 4),
            title_borders_accent=RegistryValue(True, 1, 4),
        )
        visual = ThemeVisualState("0", 0xC4744DA9, "Light", "Dark")

        class AppearanceBackend:
            def read_visual_state(self) -> ThemeVisualState:
                return visual

            def capture(self) -> AppearanceRegistrySnapshot:
                return snapshot

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Custom.theme"
            source_bytes = windows_11_variant_theme()
            source.write_bytes(source_bytes)
            store = InstallBackupStore(
                root / "backup" / "install.json",
                root / "backup" / "install.theme",
            )

            captured = capture_install_backup(
                store,
                created_by_version="1.0.0",
                windows_build="26200",
                theme_backend=ThemeBackend(source),  # type: ignore[arg-type]
                appearance_backend=AppearanceBackend(),  # type: ignore[arg-type]
                colorization_reader=lambda: visual.colorization_color,
                timestamp="2026-09-14T12:00:00+08:00",
            )

            self.assertEqual(source.read_bytes(), source_bytes)
            recovery = store.theme_path.read_bytes()
            self.assertIn(b"[Theme.A]\r\nDisplayName=Custom", recovery)
            self.assertEqual(read_visual_state(recovery), visual)
            self.assertEqual(store.load_verified(), captured)

    def test_live_capture_rejects_colorization_change_without_committing(
        self,
    ) -> None:
        class ThemeBackend:
            def __init__(self, path: Path) -> None:
                self.path = path

            def current_theme_path(self) -> Path:
                return self.path

        class AppBackend:
            def read_value(self, name: str) -> RegistryValue:
                return RegistryValue(True, 1, 4)

        colors = iter((0xC4FFB900, 0xC4744DA9))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Custom.theme"
            source.write_bytes(self._theme())
            store = InstallBackupStore(
                root / "backup" / "install.json",
                root / "backup" / "install.theme",
            )

            with self.assertRaisesRegex(
                InstallBackupValidationError,
                "appearance changed",
            ):
                capture_install_backup(
                    store,
                    created_by_version="0.1.0",
                    windows_build="26200",
                    theme_backend=ThemeBackend(source),  # type: ignore[arg-type]
                    app_backend=AppBackend(),
                    colorization_reader=lambda: next(colors),
                    timestamp="2026-07-25T12:00:00+08:00",
                )

            self.assertFalse(store.manifest_path.exists())
            self.assertFalse(store.theme_path.exists())


class LogPolicyTests(unittest.TestCase):
    def test_frozen_rotation_and_retention_parameters(self) -> None:
        self.assertEqual(LOG_FILE_NAME, "events.jsonl")
        self.assertEqual(LOG_MAX_BYTES, 1_048_576)
        self.assertEqual(LOG_BACKUP_COUNT, 5)
        self.assertEqual(SUCCESSFUL_TRANSACTION_KEEP, 10)
        self.assertEqual(FAILED_TRANSACTION_RETENTION_DAYS, 30)
        self.assertEqual(FAILED_TRANSACTION_MINIMUM_KEEP, 10)

    def test_event_round_trip_uses_fixed_privacy_safe_fields(self) -> None:
        event = LogEvent(
            occurred_at="2026-07-23T21:00:00+08:00",
            level="INFO",
            event="accent.apply",
            result="success",
            trigger="manual",
            target_profile="night",
            transaction_id="accent-20260723T205026-b5c4040f",
            verification="passed",
        )

        self.assertEqual(LogEvent.from_dict(event.as_dict()), event)
        self.assertEqual(event.as_dict()["schemaVersion"], 2)
        self.assertNotIn("details", event.as_dict())

    def test_v1_log_event_is_migrated_in_memory_to_v2(self) -> None:
        current = LogEvent(
            occurred_at="2026-07-23T21:00:00+08:00",
            level="INFO",
            event="state.save",
            result="success",
            trigger="system",
        ).as_dict()
        legacy = {
            key: value
            for key, value in current.items()
            if key
            not in {
                "verification",
                "rollbackAttempted",
                "rollbackSucceeded",
            }
        }
        legacy["schemaVersion"] = 1

        migrated = LogEvent.from_dict(legacy)

        self.assertEqual(LOG_SCHEMA_VERSION, 2)
        self.assertIsNone(migrated.verification)
        self.assertFalse(migrated.rollback_attempted)
        self.assertIsNone(migrated.rollback_succeeded)
        self.assertEqual(migrated.as_dict()["schemaVersion"], 2)

    def test_unknown_event_fields_and_unbounded_messages_are_rejected(self) -> None:
        event = LogEvent(
            occurred_at="2026-07-23T21:00:00+08:00",
            level="ERROR",
            event="config.load",
            result="failed",
            trigger="auto",
            error_code="CONFIG_INVALID",
        )
        payload = event.as_dict()
        payload["userPath"] = r"C:\Users\tester"
        with self.assertRaises(LogEventValidationError):
            LogEvent.from_dict(payload)
        with self.assertRaises(LogEventValidationError):
            LogEvent(
                occurred_at="2026-07-23T21:00:00+08:00",
                level="ERROR",
                event="config.load",
                result="failed",
                trigger="auto",
                message="x" * 501,
            )
        with self.assertRaisesRegex(
            LogEventValidationError,
            "rollbackSucceeded",
        ):
            LogEvent(
                occurred_at="2026-07-23T21:00:00+08:00",
                level="ERROR",
                event="config.load",
                result="failed",
                trigger="auto",
                rollback_attempted=True,
                rollback_succeeded=1,  # type: ignore[arg-type]
            )

    def test_writer_emits_json_lines_and_rotates_to_bounded_backups(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            writer = EventLogWriter(path, max_bytes=400, backup_count=2)
            for index in range(8):
                writer.append(
                    LogEvent(
                        occurred_at=f"2026-07-23T21:00:{index:02d}+08:00",
                        level="INFO",
                        event="state.save",
                        result="success",
                        trigger="manual",
                        message=f"event-{index}",
                    )
                )

            files = sorted(path.parent.glob("events.jsonl*"))
            self.assertLessEqual(len(files), 3)
            self.assertTrue((path.parent / "events.jsonl.1").is_file())
            for file in files:
                for line in file.read_text(encoding="utf-8").splitlines():
                    self.assertEqual(
                        LogEvent.from_dict(json.loads(line)).as_dict(),
                        json.loads(line),
                    )

    def test_concurrent_writers_serialize_rotation_and_preserve_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            lock_factory = SharedEventLogLockFactory()
            writers = [
                EventLogWriter(
                    path,
                    max_bytes=600,
                    backup_count=60,
                    lock_factory=lock_factory,
                )
                for _ in range(8)
            ]
            failures: list[Exception] = []

            def append_event(index: int) -> None:
                try:
                    writers[index % len(writers)].append(
                        LogEvent(
                            occurred_at=(
                                f"2026-07-23T21:00:{index:02d}+08:00"
                            ),
                            level="INFO",
                            event="state.save",
                            result="success",
                            trigger="manual",
                            message=f"event-{index}",
                        )
                    )
                except Exception as exc:
                    failures.append(exc)

            threads = [
                threading.Thread(target=append_event, args=(index,))
                for index in range(48)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(failures, [])
            self.assertEqual(lock_factory.maximum_active, 1)
            files = list(path.parent.glob("events.jsonl*"))
            self.assertGreater(len(files), 2)
            messages = [
                LogEvent.from_dict(json.loads(line)).message
                for file in files
                for line in file.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(messages), 48)
            self.assertEqual(
                set(messages),
                {f"event-{index}" for index in range(48)},
            )


class LayoutContractTests(unittest.TestCase):
    def test_frozen_paths_are_unambiguous(self) -> None:
        layout = UserDataLayout(Path("data"))

        self.assertEqual(layout.config, Path("data/config.json"))
        self.assertEqual(layout.state, Path("data/state.json"))
        self.assertEqual(
            layout.install_backup_manifest, Path("data/backup/install.json")
        )
        self.assertEqual(layout.install_backup_theme, Path("data/backup/install.theme"))
        self.assertEqual(layout.event_log, Path("data/logs/events.jsonl"))


class RuntimeRetentionTests(unittest.TestCase):
    @staticmethod
    def _transaction(root: Path, index: int, *, status: str, stamp: datetime) -> Path:
        path = root / f"accent-202607{index:02d}T010101-{index:08x}"
        path.mkdir()
        atomic_write_json(
            path / "journal.json",
            {
                "status": status,
                "completedAt": stamp.isoformat(timespec="seconds"),
            },
        )
        return path

    def test_plan_keeps_recent_successes_and_minimum_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime(2026, 7, 23, tzinfo=UTC)
            successes = [
                self._transaction(
                    root,
                    index,
                    status="applied",
                    stamp=now - timedelta(hours=index),
                )
                for index in range(12)
            ]
            failures = [
                self._transaction(
                    root,
                    index + 20,
                    status="failed",
                    stamp=now - timedelta(days=40, hours=index),
                )
                for index in range(12)
            ]
            unrelated = root / "other-data"
            unrelated.mkdir()

            plan = plan_runtime_cleanup(root, now=now)

            self.assertEqual(len(plan.delete), 4)
            self.assertIn(successes[-1].resolve(), plan.delete)
            self.assertIn(failures[-1].resolve(), plan.delete)
            removed = execute_runtime_cleanup(root, plan)
            self.assertEqual(set(removed), set(plan.delete))
            self.assertTrue(unrelated.is_dir())

    def test_corrupt_transaction_is_treated_as_failed_and_protection_wins(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corrupt = root / "accent-corrupt"
            corrupt.mkdir()
            (corrupt / "journal.json").write_text("{", encoding="utf-8")

            plan = plan_runtime_cleanup(root, protected=(corrupt,))

            self.assertIn(corrupt.resolve(), plan.keep)
            self.assertNotIn(corrupt.resolve(), plan.delete)


if __name__ == "__main__":
    unittest.main()
