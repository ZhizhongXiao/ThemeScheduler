from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests._support import capture_payload
from theme_scheduler.backup import InstallBackup
from theme_scheduler.lifecycle import (
    InstalledAppRegistration,
    InstallLayout,
    PayloadManifest,
)
from theme_scheduler.lifecycle.deployment import (
    FileDeploymentService,
    verify_active_payload,
)
from theme_scheduler.setup_contracts import SetupOptions
from theme_scheduler.setup_data import SetupDataService
from theme_scheduler.setup_service import SetupService
from theme_scheduler.storage import UserDataLayout
from theme_scheduler.system_integration import ShortcutPlan

THEME = (
    b"[Theme]\r\nDisplayName=Setup Test\r\n"
    b"ThemeId={65CC0448-76B8-4EB2-ADF7-D3186669AAC9}\r\n\r\n"
    b"[VisualStyles]\r\nAutoColorization=0\r\n"
    b"ColorizationColor=0XC4FFB900\r\n"
    b"SystemMode=Dark\r\nAppMode=Light\r\n"
)
TRANSACTION_ID = "lifecycle-20260725T120000-11111111"
SECOND_TRANSACTION_ID = "lifecycle-20260725T120100-22222222"


def _capture(store, *, created_by_version: str, windows_build: str):
    value = InstallBackup(
        "2026-07-25T12:00:00+08:00",
        created_by_version,
        windows_build,
        True,
        4,
        1,
        r"C:\Users\tester\AppData\Local\Themes\Custom.theme",
        hashlib.sha256(THEME).hexdigest(),
        False,
        "0XC4FFB900",
        "Light",
        "Dark",
    )
    return store.create(value, THEME)


def _payload(
    root: Path,
    version: str = "0.1.0",
    marker: bytes = b"main",
) -> PayloadManifest:
    return capture_payload(root, version, main=marker)


class StubIntegration:
    def __init__(self, *, verified: bool) -> None:
        self.registry = object()
        self.shortcuts = object()
        self.tasks = object()
        self.verified = verified
        self.apply_count = 0

    def apply(self, **kwargs):
        self.apply_count += 1
        return SimpleNamespace(
            verified=self.verified,
            message=("verified" if self.verified else "simulated integration failure"),
        )


class SetupServiceTests(unittest.TestCase):
    def _values(self, root: Path, *, verified: bool = True):
        layout = InstallLayout(
            (root / "Programs" / "ThemeScheduler").resolve(),
            (root / "ThemeScheduler").resolve(),
        )
        payload_root = root / "payload"
        manifest = _payload(payload_root)
        data = SetupDataService(
            UserDataLayout(layout.data_root),
            backup_capture=_capture,
        )
        integration = StubIntegration(verified=verified)
        restored: list[object] = []

        def capture(*args):
            return SimpleNamespace(name="before")

        def restore(backup, *args):
            restored.append(backup)
            return SimpleNamespace(verified=True)

        service = SetupService(
            layout,
            payload_root,
            manifest,
            deployment=FileDeploymentService(layout),
            data=data,
            integration=integration,  # type: ignore[arg-type]
            capture_integration=capture,  # type: ignore[arg-type]
            restore_integration=restore,  # type: ignore[arg-type]
        )
        shortcuts = ShortcutPlan.create(
            layout,
            programs_folder=root / "Start" / "Programs",
            desktop_folder=root / "Desktop",
            desktop_enabled=False,
        )
        registration = InstalledAppRegistration.create(
            layout,
            version=manifest.version,
            publisher="ThemeScheduler",
            estimated_size_kib=1,
        )
        return (
            layout,
            manifest,
            service,
            shortcuts,
            registration,
            restored,
        )

    def test_success_commits_payload_data_and_integration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (
                layout,
                manifest,
                service,
                shortcuts,
                registration,
                restored,
            ) = self._values(root)

            outcome = service.install(
                SetupOptions.defaults(),
                registration=registration,
                shortcut_plan=shortcuts,
                user_id=r"TEST\User",
                windows_build="26200",
                transaction_id=TRANSACTION_ID,
            )

            self.assertEqual(outcome.result, "success")
            self.assertTrue(outcome.verified)
            self.assertFalse(outcome.retained_data)
            self.assertEqual(restored, [])
            verify_active_payload(layout, manifest)
            self.assertTrue((layout.data_root / "config.json").is_file())

    def test_success_reports_real_transaction_stages_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (
                _layout,
                _manifest,
                service,
                shortcuts,
                registration,
                _restored,
            ) = self._values(root)
            stages: list[str] = []

            outcome = service.install(
                SetupOptions.defaults(),
                registration=registration,
                shortcut_plan=shortcuts,
                user_id=r"TEST\User",
                windows_build="26200",
                transaction_id=TRANSACTION_ID,
                progress=stages.append,
            )

            self.assertTrue(outcome.verified)
            self.assertEqual(
                stages,
                [
                    "preparing-data",
                    "capturing-integration",
                    "deploying-files",
                    "applying-integration",
                    "verifying-installation",
                    "committing-files",
                    "completed",
                ],
            )

    def test_progress_reporter_failure_does_not_break_install(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (
                _layout,
                _manifest,
                service,
                shortcuts,
                registration,
                _restored,
            ) = self._values(root)

            def fail_progress(_stage: str) -> None:
                raise RuntimeError("presentation unavailable")

            outcome = service.install(
                SetupOptions.defaults(),
                registration=registration,
                shortcut_plan=shortcuts,
                user_id=r"TEST\User",
                windows_build="26200",
                transaction_id=TRANSACTION_ID,
                progress=fail_progress,
            )

            self.assertEqual(outcome.result, "success")
            self.assertTrue(outcome.verified)

    def test_later_failure_restores_integration_files_and_new_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (
                layout,
                _manifest,
                service,
                shortcuts,
                registration,
                restored,
            ) = self._values(root, verified=False)
            logs = layout.data_root / "logs"
            logs.mkdir(parents=True)
            existing_log = logs / "events.jsonl"
            existing_log.write_text("keep\n", encoding="utf-8")

            outcome = service.install(
                SetupOptions.defaults(),
                registration=registration,
                shortcut_plan=shortcuts,
                user_id=r"TEST\User",
                windows_build="26200",
                transaction_id=TRANSACTION_ID,
            )

            self.assertEqual(outcome.result, "failed")
            self.assertTrue(outcome.rollback_attempted)
            self.assertTrue(outcome.rollback_succeeded)
            self.assertEqual(len(restored), 1)
            self.assertFalse(layout.app.exists())
            self.assertFalse((layout.data_root / "config.json").exists())
            self.assertEqual(
                existing_log.read_text(encoding="utf-8"),
                "keep\n",
            )

    def test_setup_never_immediately_synchronizes_windows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            (
                layout,
                manifest,
                service,
                shortcuts,
                registration,
                _restored,
            ) = self._values(Path(directory))
            outcome = service.install(
                SetupOptions.defaults(),
                registration=registration,
                shortcut_plan=shortcuts,
                user_id=r"TEST\User",
                windows_build="26200",
                transaction_id=TRANSACTION_ID,
            )

            self.assertEqual(outcome.result, "success")
            self.assertTrue(outcome.verified)
            self.assertFalse(outcome.immediate_sync_requested)
            self.assertIsNone(outcome.immediate_sync_succeeded)
            verify_active_payload(layout, manifest)

    def test_damaged_same_version_is_reinstalled_without_touching_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (
                layout,
                _manifest,
                service,
                shortcuts,
                registration,
                _restored,
            ) = self._values(root)
            first = service.install(
                SetupOptions.defaults(),
                registration=registration,
                shortcut_plan=shortcuts,
                user_id=r"TEST\User",
                windows_build="26200",
                transaction_id=TRANSACTION_ID,
            )
            self.assertEqual(first.result, "success")
            before_data = {
                path.relative_to(layout.data_root): path.read_bytes()
                for path in layout.data_root.rglob("*")
                if path.is_file()
            }
            (layout.executable).write_bytes(b"damaged")
            replacement_root = root / "replacement"
            replacement = _payload(
                replacement_root,
                "0.1.0",
                b"replacement",
            )
            replacement_service = SetupService(
                layout,
                replacement_root,
                replacement,
                deployment=FileDeploymentService(layout),
                data=service.data,
                integration=service.integration,
                capture_integration=service.capture_integration,
                restore_integration=service.restore_integration,
            )

            outcome = replacement_service.install(
                SetupOptions.defaults(),
                registration=registration,
                shortcut_plan=shortcuts,
                user_id=r"TEST\User",
                windows_build="26200",
                transaction_id=SECOND_TRANSACTION_ID,
            )

            self.assertEqual(outcome.operation, "reinstall")
            self.assertTrue(outcome.retained_data)
            self.assertEqual(layout.executable.read_bytes(), b"replacement")
            after_data = {
                path.relative_to(layout.data_root): path.read_bytes()
                for path in layout.data_root.rglob("*")
                if path.is_file()
            }
            self.assertEqual(after_data, before_data)

    def test_upgrade_replaces_program_and_preserves_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (
                layout,
                _manifest,
                service,
                shortcuts,
                registration,
                _restored,
            ) = self._values(root)
            service.install(
                SetupOptions.defaults(),
                registration=registration,
                shortcut_plan=shortcuts,
                user_id=r"TEST\User",
                windows_build="26200",
                transaction_id=TRANSACTION_ID,
            )
            before_config = (layout.data_root / "config.json").read_bytes()
            upgrade_root = root / "upgrade"
            upgrade = _payload(upgrade_root, "0.2.0", b"upgrade")
            upgrade_service = SetupService(
                layout,
                upgrade_root,
                upgrade,
                deployment=FileDeploymentService(layout),
                data=service.data,
                integration=service.integration,
                capture_integration=service.capture_integration,
                restore_integration=service.restore_integration,
            )
            upgrade_registration = InstalledAppRegistration.create(
                layout,
                version="0.2.0",
                publisher="ThemeScheduler",
                estimated_size_kib=1,
            )

            outcome = upgrade_service.install(
                SetupOptions.defaults(),
                registration=upgrade_registration,
                shortcut_plan=shortcuts,
                user_id=r"TEST\User",
                windows_build="26200",
                transaction_id=SECOND_TRANSACTION_ID,
            )

            self.assertEqual(outcome.operation, "upgrade")
            self.assertEqual(outcome.version, "0.2.0")
            self.assertEqual(layout.executable.read_bytes(), b"upgrade")
            self.assertEqual(
                (layout.data_root / "config.json").read_bytes(),
                before_config,
            )


if __name__ == "__main__":
    unittest.main()
