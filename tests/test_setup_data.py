from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from theme_scheduler.backup import InstallBackup
from theme_scheduler.config import AppConfig, ConfigStore
from theme_scheduler.initial_setup import (
    initial_setup_pending,
)
from theme_scheduler.setup_contracts import SetupOptions
from theme_scheduler.setup_data import (
    SetupDataError,
    SetupDataService,
)
from theme_scheduler.state import StateStore
from theme_scheduler.storage import UserDataLayout

THEME = (
    b"[Theme]\r\n"
    b"DisplayName=Setup Test\r\n"
    b"ThemeId={65CC0448-76B8-4EB2-ADF7-D3186669AAC9}\r\n\r\n"
    b"[VisualStyles]\r\n"
    b"AutoColorization=0\r\n"
    b"ColorizationColor=0XC4FFB900\r\n"
    b"SystemMode=Dark\r\n"
    b"AppMode=Light\r\n"
)


def _capture(store, *, created_by_version: str, windows_build: str):
    manifest = InstallBackup(
        captured_at="2026-07-25T12:00:00+08:00",
        created_by_version=created_by_version,
        windows_build=windows_build,
        apps_value_exists=True,
        apps_value_type_code=4,
        apps_value_data=1,
        source_theme_path=(
            r"C:\Users\tester\AppData\Local\Microsoft"
            r"\Windows\Themes\Custom.theme"
        ),
        theme_sha256=hashlib.sha256(THEME).hexdigest(),
        auto_colorization=False,
        colorization_color="0XC4FFB900",
        app_mode="Light",
        system_mode="Dark",
    )
    return store.create(manifest, THEME)


class SetupDataTests(unittest.TestCase):
    def test_initializes_core_data_but_preserves_existing_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = UserDataLayout(Path(directory) / "ThemeScheduler")
            layout.logs.mkdir(parents=True)
            log = layout.logs / "events.jsonl"
            log.write_text("keep\n", encoding="utf-8")
            service = SetupDataService(layout, backup_capture=_capture)
            options = SetupOptions(True)

            outcome = service.prepare(
                options,
                target_version="0.1.0",
                windows_build="26200",
            )

            self.assertFalse(outcome.retained)
            self.assertEqual(
                ConfigStore(layout.config).load(),
                AppConfig.defaults(),
            )
            state = StateStore(layout.state).load()
            self.assertTrue(state.paused)
            self.assertTrue(initial_setup_pending(layout.initial_setup_marker))
            self.assertEqual(log.read_text(encoding="utf-8"), "keep\n")
            self.assertTrue(layout.profile_path("day").is_file())
            self.assertTrue(layout.profile_path("night").is_file())
            self.assertTrue(layout.install_backup_manifest.is_file())

    def test_complete_retained_data_is_validated_and_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = UserDataLayout(Path(directory) / "ThemeScheduler")
            service = SetupDataService(layout, backup_capture=_capture)
            first = service.prepare(
                SetupOptions.defaults(),
                target_version="0.1.0",
                windows_build="26200",
            )
            before = {
                path.relative_to(layout.root): path.read_bytes()
                for path in layout.root.rglob("*")
                if path.is_file()
            }

            retained = service.prepare(
                SetupOptions(True),
                target_version="0.2.0",
                windows_build="26200",
            )

            self.assertFalse(first.retained)
            self.assertTrue(retained.retained)
            after = {
                path.relative_to(layout.root): path.read_bytes()
                for path in layout.root.rglob("*")
                if path.is_file()
            }
            self.assertEqual(after, before)

    def test_partial_retained_data_blocks_without_filling_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = UserDataLayout(Path(directory) / "ThemeScheduler")
            layout.root.mkdir()
            layout.config.write_text("{}", encoding="utf-8")
            service = SetupDataService(layout, backup_capture=_capture)

            with self.assertRaisesRegex(SetupDataError, "incomplete"):
                service.prepare(
                    SetupOptions.defaults(),
                    target_version="0.1.0",
                    windows_build="26200",
                )

            self.assertEqual(layout.config.read_text(encoding="utf-8"), "{}")
            self.assertFalse(layout.state.exists())

    def test_damaged_initial_setup_marker_blocks_retained_install(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = UserDataLayout(Path(directory) / "ThemeScheduler")
            service = SetupDataService(layout, backup_capture=_capture)
            service.prepare(
                SetupOptions.defaults(),
                target_version="0.1.0",
                windows_build="26200",
            )
            layout.initial_setup_marker.write_text(
                "{}",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "marker fields",
            ):
                service.prepare(
                    SetupOptions.defaults(),
                    target_version="0.1.0",
                    windows_build="26200",
                )

    def test_failed_initialization_removes_only_new_files(self) -> None:
        def fail_capture(*args, **kwargs):
            raise OSError("capture failed")

        with tempfile.TemporaryDirectory() as directory:
            layout = UserDataLayout(Path(directory) / "ThemeScheduler")
            layout.logs.mkdir(parents=True)
            log = layout.logs / "events.jsonl"
            log.write_text("keep\n", encoding="utf-8")
            service = SetupDataService(
                layout,
                backup_capture=fail_capture,
            )

            with self.assertRaisesRegex(OSError, "capture failed"):
                service.prepare(
                    SetupOptions.defaults(),
                    target_version="0.1.0",
                    windows_build="26200",
                )

            self.assertEqual(log.read_text(encoding="utf-8"), "keep\n")
            self.assertFalse(layout.config.exists())
            self.assertFalse(layout.state.exists())


if __name__ == "__main__":
    unittest.main()
