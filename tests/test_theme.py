from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

from theme_scheduler.cli.theme import main as theme_cli_main
from theme_scheduler.theme import (
    APPS_THEME_VALUE,
    REG_DWORD,
    SYSTEM_THEME_VALUE,
    RegistryValue,
    ThemeApplyError,
    ThemeMode,
    ThemeProfile,
    ThemeReadError,
    ThemeState,
    apply_theme_profile,
    read_theme_snapshot,
    restore_theme_snapshot,
    theme_snapshot_from_dict,
)


class FakeThemeBackend:
    def __init__(self, windows: int = 0, apps: int = 1) -> None:
        self.values = {
            SYSTEM_THEME_VALUE: RegistryValue(True, windows, REG_DWORD),
            APPS_THEME_VALUE: RegistryValue(True, apps, REG_DWORD),
        }
        self.writes: list[tuple[str, int]] = []
        self.notification_count = 0
        self.write_failure_on_call: int | None = None
        self.notify_failure_on_call: int | None = None
        self.ignore_write_names: set[str] = set()
        self.read_failure_names: set[str] = set()

    def read_value(self, name: str) -> RegistryValue:
        if name in self.read_failure_names:
            raise PermissionError("simulated read failure")
        return self.values[name]

    def write_dword(self, name: str, value: int) -> None:
        self.writes.append((name, value))
        if self.write_failure_on_call == len(self.writes):
            raise OSError("simulated write failure")
        if name not in self.ignore_write_names:
            self.values[name] = RegistryValue(True, value, REG_DWORD)

    def delete_value(self, name: str) -> None:
        self.values[name] = RegistryValue(False)

    def notify_theme_change(self) -> None:
        self.notification_count += 1
        if self.notify_failure_on_call == self.notification_count:
            raise OSError("simulated notification failure")


class ThemeReadTests(unittest.TestCase):
    def test_reads_all_four_theme_combinations(self) -> None:
        expected = {
            (0, 0): ThemeState(ThemeMode.DARK, ThemeMode.DARK),
            (0, 1): ThemeState(ThemeMode.DARK, ThemeMode.LIGHT),
            (1, 0): ThemeState(ThemeMode.LIGHT, ThemeMode.DARK),
            (1, 1): ThemeState(ThemeMode.LIGHT, ThemeMode.LIGHT),
        }
        for raw, state in expected.items():
            with self.subTest(raw=raw):
                snapshot = read_theme_snapshot(FakeThemeBackend(*raw))
                self.assertEqual(snapshot.state, state)

    def test_missing_value_is_reported_as_unset(self) -> None:
        backend = FakeThemeBackend()
        backend.values[APPS_THEME_VALUE] = RegistryValue(False)
        snapshot = read_theme_snapshot(backend)
        self.assertIsNone(snapshot.state.apps)
        self.assertEqual(snapshot.state.windows, ThemeMode.DARK)

    def test_wrong_registry_type_is_rejected(self) -> None:
        backend = FakeThemeBackend()
        backend.values[SYSTEM_THEME_VALUE] = RegistryValue(True, 0, 1)
        with self.assertRaisesRegex(ThemeReadError, "REG_DWORD"):
            read_theme_snapshot(backend)

    def test_invalid_dword_is_rejected(self) -> None:
        backend = FakeThemeBackend()
        backend.values[SYSTEM_THEME_VALUE] = RegistryValue(True, 2, REG_DWORD)
        with self.assertRaisesRegex(ThemeReadError, "invalid"):
            read_theme_snapshot(backend)

    def test_registry_access_failure_is_wrapped(self) -> None:
        backend = FakeThemeBackend()
        backend.read_failure_names.add(SYSTEM_THEME_VALUE)
        with self.assertRaisesRegex(ThemeReadError, "Failed to read"):
            read_theme_snapshot(backend)


class ThemeApplyTests(unittest.TestCase):
    def test_applies_both_app_modes_without_changing_windows_mode(self) -> None:
        for requested in (ThemeProfile(ThemeMode.DARK), ThemeProfile(ThemeMode.LIGHT)):
            with self.subTest(requested=requested):
                backend = FakeThemeBackend(
                    windows=1,
                    apps=1 - requested.apps.registry_data,
                )
                result = apply_theme_profile(requested, backend)
                self.assertTrue(result.after.matches(requested))
                self.assertTrue(result.verified)
                self.assertEqual(result.before.windows, ThemeMode.LIGHT)
                self.assertEqual(result.after.windows, ThemeMode.LIGHT)
                self.assertNotIn(
                    SYSTEM_THEME_VALUE, [name for name, _ in backend.writes]
                )

    def test_applies_only_app_value_notifies_and_verifies(self) -> None:
        backend = FakeThemeBackend(windows=1, apps=1)
        requested = ThemeProfile(ThemeMode.DARK)

        result = apply_theme_profile(requested, backend)

        self.assertEqual(
            backend.writes,
            [(APPS_THEME_VALUE, 0)],
        )
        self.assertEqual(backend.notification_count, 1)
        self.assertTrue(result.after.matches(requested))
        self.assertEqual(result.after.windows, ThemeMode.LIGHT)
        self.assertTrue(result.verified)

    def test_repeated_apply_is_idempotent(self) -> None:
        backend = FakeThemeBackend(windows=0, apps=1)
        requested = ThemeProfile(ThemeMode.LIGHT)

        first = apply_theme_profile(requested, backend)
        second = apply_theme_profile(requested, backend)

        self.assertFalse(first.changed)
        self.assertFalse(second.changed)
        self.assertEqual(backend.writes, [])
        self.assertEqual(backend.notification_count, 0)

    def test_write_failure_rolls_back_original_profile(self) -> None:
        backend = FakeThemeBackend(windows=1, apps=1)
        backend.write_failure_on_call = 1
        requested = ThemeProfile(ThemeMode.DARK)

        with self.assertRaises(ThemeApplyError) as caught:
            apply_theme_profile(requested, backend)

        self.assertTrue(caught.exception.rollback_succeeded)
        self.assertEqual(
            read_theme_snapshot(backend).state,
            ThemeState(ThemeMode.LIGHT, ThemeMode.LIGHT),
        )

    def test_notification_failure_rolls_back_original_profile(self) -> None:
        backend = FakeThemeBackend(windows=0, apps=1)
        backend.notify_failure_on_call = 1
        requested = ThemeProfile(ThemeMode.DARK)

        with self.assertRaises(ThemeApplyError) as caught:
            apply_theme_profile(requested, backend)

        self.assertTrue(caught.exception.rollback_succeeded)
        self.assertEqual(read_theme_snapshot(backend).state.apps, ThemeMode.LIGHT)
        self.assertEqual(backend.notification_count, 2)

    def test_readback_mismatch_rolls_back_original_profile(self) -> None:
        backend = FakeThemeBackend(windows=0, apps=1)
        backend.ignore_write_names.add(APPS_THEME_VALUE)
        requested = ThemeProfile(ThemeMode.DARK)

        with self.assertRaises(ThemeApplyError) as caught:
            apply_theme_profile(requested, backend)

        self.assertTrue(caught.exception.rollback_succeeded)
        self.assertEqual(read_theme_snapshot(backend).state.apps, ThemeMode.LIGHT)

    def test_incomplete_rollback_is_reported(self) -> None:
        backend = FakeThemeBackend(windows=1, apps=1)
        backend.notify_failure_on_call = 1
        backend.write_failure_on_call = 2
        requested = ThemeProfile(ThemeMode.DARK)

        with self.assertRaises(ThemeApplyError) as caught:
            apply_theme_profile(requested, backend)

        self.assertFalse(caught.exception.rollback_succeeded)
        self.assertTrue(caught.exception.rollback_errors)

    def test_failure_after_creating_missing_values_deletes_them_on_rollback(
        self,
    ) -> None:
        backend = FakeThemeBackend()
        backend.values[SYSTEM_THEME_VALUE] = RegistryValue(False)
        backend.values[APPS_THEME_VALUE] = RegistryValue(False)
        backend.notify_failure_on_call = 1
        requested = ThemeProfile(ThemeMode.LIGHT)

        with self.assertRaises(ThemeApplyError) as caught:
            apply_theme_profile(requested, backend)

        self.assertTrue(caught.exception.rollback_succeeded)
        self.assertFalse(backend.values[SYSTEM_THEME_VALUE].exists)
        self.assertFalse(backend.values[APPS_THEME_VALUE].exists)


class ThemeRestoreTests(unittest.TestCase):
    def test_restores_values_to_prior_absence(self) -> None:
        backend = FakeThemeBackend(windows=0, apps=1)
        target = theme_snapshot_from_dict(
            {
                "registryKey": (
                    "HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\"
                    "CurrentVersion\\Themes\\Personalize"
                ),
                "values": {
                    SYSTEM_THEME_VALUE: {"exists": False},
                    APPS_THEME_VALUE: {"exists": False},
                },
            }
        )

        result = restore_theme_snapshot(target, backend)

        self.assertTrue(result.verified)
        self.assertTrue(backend.values[SYSTEM_THEME_VALUE].exists)
        self.assertEqual(backend.values[SYSTEM_THEME_VALUE].data, 0)
        self.assertFalse(backend.values[APPS_THEME_VALUE].exists)
        self.assertEqual(backend.notification_count, 1)

    def test_invalid_snapshot_type_is_rejected_before_restore(self) -> None:
        with self.assertRaisesRegex(ThemeReadError, "REG_DWORD"):
            theme_snapshot_from_dict(
                {
                    "registryKey": (
                        "HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\"
                        "CurrentVersion\\Themes\\Personalize"
                    ),
                    "values": {
                        SYSTEM_THEME_VALUE: {
                            "exists": True,
                            "data": 0,
                            "typeCode": 1,
                        },
                        APPS_THEME_VALUE: {"exists": False},
                    },
                }
            )


class ThemeCliSafetyTests(unittest.TestCase):
    def test_apply_without_confirmation_does_not_create_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / "before.json"
            with redirect_stderr(StringIO()):
                exit_code = theme_cli_main(
                    [
                        "apply",
                        "--apps",
                        "light",
                        "--backup",
                        str(backup),
                    ]
                )
            self.assertEqual(exit_code, 3)
            self.assertFalse(backup.exists())

    def test_restore_without_confirmation_does_not_create_safety_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / "before.json"
            safety_backup = Path(directory) / "safety.json"
            backup.write_text("{}", encoding="utf-8")
            with redirect_stderr(StringIO()):
                exit_code = theme_cli_main(
                    [
                        "restore",
                        "--backup",
                        str(backup),
                        "--safety-backup",
                        str(safety_backup),
                    ]
                )
            self.assertEqual(exit_code, 3)
            self.assertFalse(safety_backup.exists())


if __name__ == "__main__":
    unittest.main()
