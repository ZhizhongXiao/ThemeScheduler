from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.fixtures.theme_files import windows_11_variant_theme
from theme_scheduler.accent_profile import AccentProfile
from theme_scheduler.accent_theme import ThemeVisualState
from theme_scheduler.appearance import AppearanceRegistrySnapshot, ThemeMode
from theme_scheduler.automation.backend import WindowsAutoBackend
from theme_scheduler.persistence import atomic_write_json
from theme_scheduler.storage import UserDataLayout


def _theme_bytes() -> bytes:
    return (
        b"[Theme]\r\nDisplayName=Test\r\n\r\n"
        b"[VisualStyles]\r\nAutoColorization=0\r\n"
        b"ColorizationColor=0XC4744DA9\r\n"
        b"SystemMode=Dark\r\nAppMode=Light\r\n"
    )


class _ThemeBackend:
    def __init__(self, path: Path) -> None:
        self.path = path

    def current_theme_path(self) -> Path:
        return self.path

    def current_v2_indices(self) -> tuple[int, int]:
        return 2, 7


class _AppearanceBackend:
    def __init__(self) -> None:
        self.verify_calls: list[dict[str, object]] = []

    def read_visual_state(self) -> ThemeVisualState:
        return ThemeVisualState("0", 0xC4744DA9, "Light", "Dark")

    def capture(self) -> AppearanceRegistrySnapshot:
        raise AssertionError("capture is not used by these adapter tests")

    def write_accent_surfaces(
        self,
        *,
        start_taskbar: bool | None,
        title_borders: bool | None,
    ) -> None:
        raise AssertionError("write is not used by these adapter tests")

    def verify(
        self,
        *,
        apps_theme: ThemeMode | None,
        system_theme: ThemeMode | None,
        start_taskbar: bool | None,
        title_borders: bool | None,
    ) -> tuple[str, ...]:
        self.verify_calls.append(
            {
                "apps_theme": apps_theme,
                "system_theme": system_theme,
                "start_taskbar": start_taskbar,
                "title_borders": title_borders,
            }
        )
        return ()

    def restore(self, snapshot: AppearanceRegistrySnapshot) -> None:
        raise AssertionError("restore is not used by these adapter tests")


class WindowsAutoBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.active = self.root / "active.theme"
        self.active.write_bytes(_theme_bytes())
        self.layout = UserDataLayout(self.root / "data")
        self.theme = _ThemeBackend(self.active)
        self.settings = _AppearanceBackend()
        with patch("theme_scheduler.automation.backend.os.name", "nt"):
            self.backend = WindowsAutoBackend(
                self.layout,
                theme_backend=self.theme,  # type: ignore[arg-type]
                appearance_backend=self.settings,
                windows_build="26200",
                settle_seconds=0,
            )

    def test_probe_read_and_complete_transaction_verification(self) -> None:
        self.backend.probe()
        expected = ThemeVisualState("0", 0xC4744DA9, "Light", "Dark")
        self.assertEqual(self.backend.read_visual_state(), expected)
        transaction = self.root / "accent-20260913T120000-12345678"
        transaction.mkdir()
        atomic_write_json(
            transaction / "journal.json",
            {
                "settingsTarget": {
                    "appsTheme": "light",
                    "systemTheme": "dark",
                    "startTaskbarAccent": False,
                    "titleBordersAccent": True,
                }
            },
        )

        failures = self.backend.verify_transaction_target(transaction, expected)

        self.assertEqual(failures, ())
        self.assertEqual(
            self.settings.verify_calls,
            [
                {
                    "apps_theme": ThemeMode.LIGHT,
                    "system_theme": ThemeMode.DARK,
                    "start_taskbar": False,
                    "title_borders": True,
                }
            ],
        )

    def test_probe_accepts_windows_11_variant_using_live_settings(self) -> None:
        self.active.write_bytes(windows_11_variant_theme())

        self.backend.probe()

        self.assertEqual(
            self.backend.read_visual_state(),
            ThemeVisualState("0", 0xC4744DA9, "Light", "Dark"),
        )

    def test_transaction_verification_handles_legacy_and_invalid_targets(self) -> None:
        expected = self.backend.read_visual_state()
        transaction = self.root / "accent-20260913T120001-12345678"
        transaction.mkdir()
        atomic_write_json(transaction / "journal.json", {"settingsTarget": None})
        self.assertEqual(
            self.backend.verify_transaction_target(transaction, expected),
            (),
        )

        atomic_write_json(
            transaction / "journal.json",
            {"settingsTarget": {"appsTheme": 1}},
            force=True,
        )
        failures = self.backend.verify_transaction_target(
            transaction,
            ThemeVisualState("0", 0xC4FF8C00, "Dark", "Dark"),
        )
        self.assertEqual(len(failures), 2)
        self.assertIn("invalid", failures[1])

    def test_delegates_capture_apply_and_rollback(self) -> None:
        profile = AccentProfile(
            "day",
            "2026-09-13T12:00:00+08:00",
            False,
            0xC4744DA9,
            "26200",
        )
        transaction = self.root / "accent-20260913T120002-12345678"
        sentinel = object()
        with (
            patch(
                "theme_scheduler.automation.backend.capture_live_profile",
                return_value=profile,
            ) as capture,
            patch(
                "theme_scheduler.automation.backend.apply_accent_profile",
                return_value=sentinel,
            ) as apply,
            patch(
                "theme_scheduler.automation.backend.rollback_accent_transaction",
                return_value=True,
            ) as rollback,
        ):
            self.assertIs(
                self.backend.capture_profile("day", profile.captured_at),
                profile,
            )
            self.assertIs(
                self.backend.apply_profile(
                    profile,
                    ThemeMode.LIGHT,
                    ThemeMode.DARK,
                    False,
                    True,
                    transaction,
                ),
                sentinel,
            )
            self.assertTrue(self.backend.rollback(transaction))

        capture.assert_called_once()
        apply.assert_called_once()
        rollback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
