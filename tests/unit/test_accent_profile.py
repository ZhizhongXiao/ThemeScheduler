from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

from tests.fixtures.appearance_settings import ScriptedAppearanceSettings
from tests.fixtures.theme_files import windows_11_variant_theme
from theme_scheduler.accent_profile import (
    AccentProfile,
    RgbColor,
    profile_from_theme,
)
from theme_scheduler.accent_service import (
    apply_accent_profile,
    apply_install_backup_appearance,
    rollback_accent_transaction,
)
from theme_scheduler.accent_theme import (
    LiveThemeApplyError,
    ThemeVisualState,
    read_visual_state,
)
from theme_scheduler.appearance import ThemeMode
from theme_scheduler.backup import InstallBackup
from theme_scheduler.storage import UserDataLayout


def _theme_bytes(color: str = "0XC4FFB900", *, auto: str = "0") -> bytes:
    return (
        b"[Theme]\r\n"
        b"DisplayName=Test Theme\r\n"
        b"ThemeId={65CC0448-76B8-4EB2-ADF7-D3186669AAC9}\r\n\r\n"
        b"[VisualStyles]\r\n"
        b"Path=%SystemRoot%\\resources\\themes\\Aero\\Aero.msstyles\r\n"
        + f"AutoColorization={auto}\r\n".encode("ascii")
        + f"ColorizationColor={color}\r\n".encode("ascii")
        + b"SystemMode=Dark\r\n"
        b"AppMode=Dark\r\n"
        b"VisualStyleVersion=10\r\n\r\n"
        b"[Sounds]\r\nSchemeName=@mmres.dll,-800\r\n"
    )


class DynamicThemeBackend:
    def __init__(self, active: Path, *, fail_readback: bool = False) -> None:
        self.active = active
        self.original = active
        self.fail_readback = fail_readback
        self.index = 6
        self.set_indices: list[int] = []

    def current_theme_path(self) -> Path:
        return self.active

    def current_v2_index(self) -> int:
        return self.index

    def current_v2_indices(self) -> tuple[int, int]:
        return self.index, 0

    def apply_theme_v2(self, path: Path) -> tuple[int, int]:
        before = self.index
        self.index = 13
        if not self.fail_readback:
            self.active = path
        return before, 13

    def set_v2_index(self, index: int) -> int:
        self.set_indices.append(index)
        self.index = index
        self.active = self.original
        return index


class AccentProfileTests(unittest.TestCase):
    def test_rgb_hex_decimal_and_colorization_round_trip(self) -> None:
        yellow = RgbColor.from_hex("#FFB900")

        self.assertEqual(
            yellow.as_dict(),
            {
                "hex": "#FFB900",
                "red": 255,
                "green": 185,
                "blue": 0,
            },
        )
        self.assertEqual(
            RgbColor.from_colorization_color(0xC4FFB900),
            yellow,
        )
        self.assertEqual(
            RgbColor(116, 77, 169).replace_colorization_rgb(0xD0FFB900),
            0xD0744DA9,
        )

    def test_rgb_rejects_invalid_hex_channels_and_dword(self) -> None:
        with self.assertRaisesRegex(ValueError, "#RRGGBB"):
            RgbColor.from_hex("FFB900")
        with self.assertRaisesRegex(ValueError, "red"):
            RgbColor(256, 185, 0)
        with self.assertRaisesRegex(ValueError, "DWORD"):
            RgbColor.from_colorization_color(-1)

    def test_round_trip_preserves_raw_color_and_metadata(self) -> None:
        profile = AccentProfile(
            "day",
            "2026-07-23T10:00:00+08:00",
            False,
            0xC4744DA9,
            "26200",
        )

        actual = AccentProfile.from_dict(profile.as_dict())

        self.assertEqual(actual, profile)
        self.assertEqual(profile.as_dict()["accent"]["colorizationColor"], "0XC4744DA9")

    def test_rejects_registry_snapshot_as_profile(self) -> None:
        with self.assertRaisesRegex(ValueError, "not a ThemeScheduler accent profile"):
            AccentProfile.from_dict(
                {
                    "kind": "themescheduler.registry-baseline",
                    "schemaVersion": 1,
                }
            )

    def test_rejects_unknown_fields_and_untrusted_timestamp(self) -> None:
        profile = AccentProfile(
            "day",
            "2026-07-23T10:00:00+08:00",
            False,
            0xC4744DA9,
            "26200",
        ).as_dict()
        profile["unknown"] = True
        with self.assertRaisesRegex(ValueError, "unknown"):
            AccentProfile.from_dict(profile)

        with self.assertRaisesRegex(ValueError, "UTC offset"):
            AccentProfile("day", "2026-07-23T10:00:00", False, 0xC4744DA9, "26200")

    def test_capture_reads_theme_semantics_only(self) -> None:
        profile = profile_from_theme(
            _theme_bytes(auto="1"),
            profile="night",
            captured_at="2026-07-23T10:00:00+08:00",
            windows_build="26200",
        )

        self.assertTrue(profile.auto_colorization)
        self.assertEqual(profile.colorization_color, 0xC4FFB900)
        self.assertNotIn("registry", profile.as_dict())


class UserDataLayoutTests(unittest.TestCase):
    def test_uses_purpose_oriented_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = UserDataLayout(Path(directory))
            layout.ensure_directories()

            self.assertEqual(layout.profile_path("day"), layout.profiles / "day.json")
            for path in (
                layout.profiles,
                layout.runtime,
                layout.backup,
                layout.logs,
            ):
                self.assertTrue(path.is_dir())


class ExplorerRecoveryScriptTests(unittest.TestCase):
    def test_waits_for_windows_recovery_before_starting_explorer(self) -> None:
        script = (
            PROJECT_ROOT / "entrypoints" / "explorer_recovery_bridge.ps1"
        ).read_text(encoding="utf-8")

        wait_position = script.index("$automaticRecoveryDeadline")
        start_position = script.index("Start-Process -FilePath $explorerPath")
        self.assertLess(wait_position, start_position)
        self.assertIn('"windows-auto-restart"', script)
        self.assertNotIn("Get-Process -Name", script)


class AccentServiceTests(unittest.TestCase):
    def test_variant_theme_uses_raw_evidence_and_applyable_rollback_copy(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = windows_11_variant_theme()
            active = root / "Custom.theme"
            active.write_bytes(source)
            layout = UserDataLayout(root / "data")
            backend = DynamicThemeBackend(active)
            before = ThemeVisualState("0", 0xC40078D4, "Light", "Dark")
            settings = ScriptedAppearanceSettings(
                backend,
                fallback_visual=before,
            )
            profile = AccentProfile(
                "night",
                "2026-09-14T23:45:00+08:00",
                False,
                0xC4744DA9,
                "26200",
            )

            result = apply_accent_profile(
                profile,
                layout,
                apps_theme=ThemeMode.DARK,
                system_theme=ThemeMode.DARK,
                start_taskbar_accent=True,
                title_borders_accent=True,
                backend=backend,
                appearance_backend=settings,
                settle_seconds=0,
            )

            transaction = result.transaction_directory
            self.assertEqual((transaction / "before.theme").read_bytes(), source)
            self.assertEqual(
                read_visual_state((transaction / "rollback.theme").read_bytes()),
                before,
            )
            self.assertIn(
                b"[Theme.W]\r\nDisplayName=Custom",
                (transaction / "managed.theme").read_bytes(),
            )
            self.assertTrue(
                rollback_accent_transaction(
                    transaction,
                    backend=backend,
                    appearance_backend=settings,
                    settle_seconds=0,
                )
            )
            self.assertEqual(backend.current_theme_path(), active)

    def test_apply_sets_complete_scheduled_appearance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / "active.theme"
            active.write_bytes(_theme_bytes())
            layout = UserDataLayout(root / "data")
            backend = DynamicThemeBackend(active)
            settings = ScriptedAppearanceSettings(backend)
            profile = AccentProfile(
                "day",
                "2026-09-13T06:15:00+08:00",
                False,
                0xC4744DA9,
                "26200",
            )

            result = apply_accent_profile(
                profile,
                layout,
                apps_theme=ThemeMode.LIGHT,
                system_theme=ThemeMode.LIGHT,
                start_taskbar_accent=False,
                title_borders_accent=False,
                backend=backend,
                appearance_backend=settings,
                settle_seconds=0,
            )

            self.assertEqual(result.actual["appMode"], "Light")
            self.assertEqual(result.actual["systemMode"], "Light")
            self.assertFalse(settings.start_taskbar)
            self.assertFalse(settings.title_borders)
            journal = json.loads(
                (result.transaction_directory / "journal.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(journal["schemaVersion"], 2)
            self.assertEqual(journal["status"], "applied")
            self.assertEqual(journal["settingsBefore"]["appsTheme"]["data"], 0)

    def test_complete_appearance_transaction_rolls_back_theme_and_switches(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / "active.theme"
            active.write_bytes(_theme_bytes())
            layout = UserDataLayout(root / "data")
            backend = DynamicThemeBackend(active)
            settings = ScriptedAppearanceSettings(backend)
            profile = AccentProfile(
                "day",
                "2026-09-13T06:15:00+08:00",
                False,
                0xC4744DA9,
                "26200",
            )
            result = apply_accent_profile(
                profile,
                layout,
                apps_theme=ThemeMode.LIGHT,
                system_theme=ThemeMode.LIGHT,
                start_taskbar_accent=False,
                title_borders_accent=False,
                backend=backend,
                appearance_backend=settings,
                settle_seconds=0,
            )

            restored = rollback_accent_transaction(
                result.transaction_directory,
                backend=backend,
                appearance_backend=settings,
                settle_seconds=0,
            )

            self.assertTrue(restored)
            self.assertEqual(backend.current_theme_path(), active)
            self.assertTrue(settings.start_taskbar)
            self.assertTrue(settings.title_borders)

    def test_registry_verification_failure_rolls_back_complete_appearance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / "active.theme"
            active.write_bytes(_theme_bytes())
            layout = UserDataLayout(root / "data")
            backend = DynamicThemeBackend(active)
            settings = ScriptedAppearanceSettings(backend, fail_verify=True)
            profile = AccentProfile(
                "night",
                "2026-09-13T23:45:00+08:00",
                False,
                0xC4744DA9,
                "26200",
            )

            with self.assertRaises(LiveThemeApplyError) as raised:
                apply_accent_profile(
                    profile,
                    layout,
                    apps_theme=ThemeMode.LIGHT,
                    system_theme=ThemeMode.LIGHT,
                    start_taskbar_accent=False,
                    title_borders_accent=False,
                    backend=backend,
                    appearance_backend=settings,
                    settle_seconds=0,
                )

            self.assertTrue(raised.exception.rollback_succeeded)
            self.assertEqual(backend.current_theme_path(), active)
            self.assertTrue(settings.start_taskbar)
            self.assertTrue(settings.title_borders)

    def test_partial_registry_write_is_rolled_back_before_theme_application(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / "active.theme"
            active.write_bytes(_theme_bytes())
            layout = UserDataLayout(root / "data")
            backend = DynamicThemeBackend(active)
            settings = ScriptedAppearanceSettings(
                backend,
                fail_write_after_start=True,
            )
            profile = AccentProfile(
                "day",
                "2026-09-13T12:00:00+08:00",
                False,
                0xC400A5D8,
                "26200",
            )

            with self.assertRaises(LiveThemeApplyError) as raised:
                apply_accent_profile(
                    profile,
                    layout,
                    apps_theme=ThemeMode.LIGHT,
                    system_theme=ThemeMode.DARK,
                    start_taskbar_accent=False,
                    title_borders_accent=False,
                    backend=backend,
                    appearance_backend=settings,
                    settle_seconds=0,
                )

            self.assertTrue(raised.exception.rollback_succeeded)
            self.assertEqual(backend.current_theme_path(), active)
            self.assertTrue(settings.start_taskbar)
            self.assertTrue(settings.title_borders)
            self.assertEqual(settings.restore_calls, 1)

    def test_secondary_registry_rollback_failure_is_reported_as_partial(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / "active.theme"
            active.write_bytes(_theme_bytes())
            layout = UserDataLayout(root / "data")
            backend = DynamicThemeBackend(active)
            settings = ScriptedAppearanceSettings(
                backend,
                fail_verify=True,
                fail_restore=True,
            )
            profile = AccentProfile(
                "night",
                "2026-09-13T23:45:00+08:00",
                False,
                0xC4744DA9,
                "26200",
            )

            with self.assertRaises(LiveThemeApplyError) as raised:
                apply_accent_profile(
                    profile,
                    layout,
                    apps_theme=ThemeMode.DARK,
                    system_theme=ThemeMode.LIGHT,
                    start_taskbar_accent=False,
                    title_borders_accent=True,
                    backend=backend,
                    appearance_backend=settings,
                    settle_seconds=0,
                )

            self.assertFalse(raised.exception.rollback_succeeded)
            self.assertEqual(backend.current_theme_path(), active)
            journal = json.loads(
                (next(layout.runtime.glob("accent-*")) / "journal.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertFalse(journal["rollbackSucceeded"])

    def test_install_restore_uses_current_theme_and_preserves_system_mode(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / "active.theme"
            active.write_bytes(_theme_bytes())
            layout = UserDataLayout(root / "data")
            backend = DynamicThemeBackend(active)
            backup = InstallBackup(
                captured_at="2026-07-23T10:00:00+08:00",
                created_by_version="0.1.0",
                windows_build="26200",
                apps_value_exists=True,
                apps_value_type_code=4,
                apps_value_data=1,
                source_theme_path=str(active),
                theme_sha256="0" * 64,
                auto_colorization=False,
                colorization_color="0XC4744DA9",
                app_mode="Light",
                system_mode="Light",
            )

            result = apply_install_backup_appearance(
                backup,
                layout,
                backend=backend,
                settle_seconds=0,
            )

            self.assertEqual(result.actual["appMode"], "Light")
            self.assertEqual(
                result.actual["colorizationColor"],
                "0XC4744DA9",
            )
            self.assertEqual(result.before["systemMode"], "Dark")
            self.assertEqual(result.actual["systemMode"], "Dark")

    def test_install_restore_v2_restores_complete_appearance(self) -> None:
        from theme_scheduler.appearance import AppearanceRegistrySnapshot, RegistryValue

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / "active.theme"
            active.write_bytes(_theme_bytes())
            layout = UserDataLayout(root / "data")
            backend = DynamicThemeBackend(active)
            settings = ScriptedAppearanceSettings(
                backend,
                start_taskbar=True,
                title_borders=False,
            )
            backup = InstallBackup(
                captured_at="2026-09-13T12:00:00+08:00",
                created_by_version="0.1.5",
                windows_build="26200",
                apps_value_exists=True,
                apps_value_type_code=4,
                apps_value_data=1,
                source_theme_path=str(active),
                theme_sha256="0" * 64,
                auto_colorization=False,
                colorization_color="0XC4744DA9",
                app_mode="Light",
                system_mode="Light",
                appearance_registry=AppearanceRegistrySnapshot(
                    apps_theme=RegistryValue(True, 1, 4),
                    system_theme=RegistryValue(True, 1, 4),
                    start_taskbar_accent=RegistryValue(True, 0, 4),
                    title_borders_accent=RegistryValue(True, 1, 4),
                ),
            )

            result = apply_install_backup_appearance(
                backup,
                layout,
                backend=backend,
                appearance_backend=settings,
                settle_seconds=0,
            )

            self.assertEqual(result.actual["systemMode"], "Light")
            self.assertFalse(settings.start_taskbar)
            self.assertTrue(settings.title_borders)
            journal = json.loads(
                (result.transaction_directory / "journal.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(journal["settingsActual"], settings.capture().as_dict())

    def test_apply_creates_hashed_transaction_and_preserves_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / "active.theme"
            active.write_bytes(_theme_bytes())
            layout = UserDataLayout(root / "data")
            backend = DynamicThemeBackend(active)
            profile = AccentProfile(
                "day",
                "2026-07-23T10:00:00+08:00",
                False,
                0xC4744DA9,
                "26200",
            )

            result = apply_accent_profile(
                profile, layout, backend=backend, settle_seconds=0
            )

            self.assertEqual(result.actual["colorizationColor"], "0XC4744DA9")
            self.assertEqual(result.actual["appMode"], "Dark")
            self.assertTrue((result.transaction_directory / "before.theme").is_file())
            self.assertTrue((result.transaction_directory / "managed.theme").is_file())
            journal = json.loads(
                (result.transaction_directory / "journal.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(journal["status"], "applied")
            self.assertEqual(len(journal["files"]["beforeSha256"]), 64)
            self.assertEqual(len(journal["files"]["managedSha256"]), 64)

    def test_apply_can_combine_app_mode_and_accent_in_one_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / "active.theme"
            active.write_bytes(_theme_bytes())
            layout = UserDataLayout(root / "data")
            profile = AccentProfile(
                "day",
                "2026-07-24T06:15:00+08:00",
                False,
                0xC4744DA9,
                "26200",
            )

            result = apply_accent_profile(
                profile,
                layout,
                apps_theme=ThemeMode.LIGHT,
                backend=DynamicThemeBackend(active),
                settle_seconds=0,
            )

            self.assertEqual(result.actual["appMode"], "Light")
            self.assertEqual(result.actual["systemMode"], "Dark")
            self.assertEqual(result.actual["colorizationColor"], "0XC4744DA9")

    def test_apply_can_join_precreated_auto_transaction_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / "active.theme"
            active.write_bytes(_theme_bytes())
            layout = UserDataLayout(root / "data")
            layout.ensure_directories()
            transaction = layout.new_transaction_directory()
            transaction.mkdir()
            (transaction / "auto.json").write_text("{}", encoding="utf-8")
            profile = AccentProfile(
                "day",
                "2026-07-24T06:15:00+08:00",
                False,
                0xC4744DA9,
                "26200",
            )

            result = apply_accent_profile(
                profile,
                layout,
                transaction_directory=transaction,
                backend=DynamicThemeBackend(active),
                settle_seconds=0,
            )

            self.assertEqual(result.transaction_directory, transaction.resolve())
            self.assertTrue((transaction / "auto.json").is_file())
            self.assertTrue((transaction / "journal.json").is_file())

    def test_completed_transaction_can_restore_complete_before_theme(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / "active.theme"
            active.write_bytes(_theme_bytes())
            layout = UserDataLayout(root / "data")
            backend = DynamicThemeBackend(active)
            profile = AccentProfile(
                "day",
                "2026-07-24T06:15:00+08:00",
                False,
                0xC4744DA9,
                "26200",
            )
            result = apply_accent_profile(
                profile,
                layout,
                apps_theme=ThemeMode.LIGHT,
                backend=backend,
                settle_seconds=0,
            )

            restored = rollback_accent_transaction(
                result.transaction_directory,
                backend=backend,
                settle_seconds=0,
            )

            self.assertTrue(restored)
            self.assertEqual(backend.current_theme_path(), active)

    def test_failed_apply_records_rollback_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / "active.theme"
            active.write_bytes(_theme_bytes())
            layout = UserDataLayout(root / "data")
            backend = DynamicThemeBackend(active, fail_readback=True)
            profile = AccentProfile(
                "night",
                "2026-07-23T10:00:00+08:00",
                False,
                0xC4744DA9,
                "26200",
            )

            with self.assertRaises(LiveThemeApplyError):
                apply_accent_profile(
                    profile,
                    layout,
                    backend=backend,
                    settle_seconds=0,
                    verification_timeout_seconds=0,
                )

            transaction = next(layout.runtime.iterdir())
            journal = json.loads(
                (transaction / "journal.json").read_text(encoding="utf-8")
            )
            self.assertEqual(journal["status"], "failed")
            self.assertTrue(journal["rollbackSucceeded"])
            diagnostics = journal["verificationDiagnostics"]
            self.assertEqual(
                diagnostics["expected"]["colorizationColor"],
                "0XC4744DA9",
            )
            samples = diagnostics["samples"]
            self.assertIsInstance(samples, list)
            self.assertEqual(
                samples[-1]["actual"]["colorizationColor"],
                "0XC4FFB900",
            )
            self.assertIn("activeThemePath", samples[-1])
            self.assertIn("observedAt", samples[-1])
            self.assertIn("currentIndex", samples[-1])
            self.assertEqual(backend.set_indices, [6])


if __name__ == "__main__":
    unittest.main()
