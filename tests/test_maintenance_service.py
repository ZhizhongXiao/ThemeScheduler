from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

from theme_scheduler.accent_service import AccentApplyOutcome
from theme_scheduler.accent_theme import LiveThemeApplyError
from theme_scheduler.backup import InstallBackup
from theme_scheduler.maintenance_service import (
    MaintenanceRestoreResult,
    MaintenanceService,
)
from theme_scheduler.state import AppState, StateStore
from theme_scheduler.storage import UserDataLayout


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


class FakeLock:
    def __init__(
        self,
        *,
        acquired: bool = True,
        acquire_error: bool = False,
        release_error: bool = False,
    ) -> None:
        self.acquired = acquired
        self.acquire_error = acquire_error
        self.release_error = release_error
        self.released = False

    def acquire(self) -> bool:
        if self.acquire_error:
            raise OSError("lock acquire failed")
        return self.acquired

    def release(self) -> None:
        self.released = True
        if self.release_error:
            raise OSError("lock release failed")


class FakeBackupStore:
    def __init__(
        self,
        backup: InstallBackup | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.backup = backup
        self.error = error

    def load_verified(self) -> InstallBackup:
        if self.error is not None:
            raise self.error
        assert self.backup is not None
        return self.backup


class FailingEventLog:
    def append(self, event) -> None:
        raise OSError("log failed")


class SaveFailsBeforeCommit:
    def __init__(self, state: AppState) -> None:
        self.state = state

    def load(self) -> AppState:
        return self.state

    def save(self, state: AppState) -> AppState:
        raise OSError("state save failed")


class SaveCommitsThenRaises(SaveFailsBeforeCommit):
    def save(self, state: AppState) -> AppState:
        self.state = state
        raise OSError("late state save failure")


def install_backup() -> InstallBackup:
    theme = b"install theme evidence"
    return InstallBackup(
        captured_at="2026-07-25T08:00:00+08:00",
        created_by_version="0.1.0",
        windows_build="26200",
        apps_value_exists=True,
        apps_value_type_code=4,
        apps_value_data=1,
        source_theme_path=(
            r"C:\Users\tester\AppData\Local\Microsoft"
            r"\Windows\Themes\Custom.theme"
        ),
        theme_sha256=hashlib.sha256(theme).hexdigest(),
        auto_colorization=False,
        colorization_color="0XC4744DA9",
        app_mode="Light",
        system_mode="Dark",
    )


class FakeAppearanceApplier:
    def __init__(
        self,
        *,
        error: Exception | None = None,
        preserve_system: bool = True,
    ) -> None:
        self.error = error
        self.preserve_system = preserve_system
        self.calls = 0

    def __call__(
        self,
        backup: InstallBackup,
        layout: UserDataLayout,
    ) -> AccentApplyOutcome:
        self.calls += 1
        if self.error is not None:
            raise self.error
        transaction = layout.runtime / "accent-test-maintenance"
        transaction.mkdir(parents=True, exist_ok=True)
        before = {
            "autoColorization": "1",
            "colorizationColor": "0XC4FFB900",
            "appMode": "Dark",
            "systemMode": "Dark",
        }
        target = {
            "autoColorization": "0",
            "colorizationColor": backup.colorization_color,
            "appMode": backup.app_mode,
            "systemMode": "Dark",
        }
        actual = dict(target)
        if not self.preserve_system:
            actual["systemMode"] = "Light"
        return AccentApplyOutcome(
            transaction,
            Path("before.theme"),
            Path("after.theme"),
            before,
            target,
            actual,
            0,
            7,
            0,
        )


class MaintenanceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.layout = UserDataLayout(Path(self.temporary.name))
        self.layout.ensure_directories()
        StateStore(self.layout.state).initialize(AppState.initial())
        self.applier = FakeAppearanceApplier()
        self.lock = FakeLock()

    def service(self, **overrides) -> MaintenanceService:
        values = {
            "backup_store": FakeBackupStore(install_backup()),
            "clock": FixedClock(),
            "appearance_applier": self.applier,
        }
        values.update(overrides)
        return MaintenanceService(
            self.layout,
            values.pop("execution_lock", self.lock),
            **values,
        )

    def test_restore_pauses_first_applies_only_owned_appearance_and_logs(
        self,
    ) -> None:
        outcome = self.service().restore_install_appearance()

        self.assertEqual(
            outcome.result,
            MaintenanceRestoreResult.RESTORED,
        )
        self.assertTrue(StateStore(self.layout.state).load().paused)
        self.assertTrue(outcome.pause_changed)
        self.assertTrue(outcome.windows_verified)
        self.assertTrue(outcome.system_mode_preserved)
        self.assertEqual(self.applier.calls, 1)
        event = json.loads(self.layout.event_log.read_text(encoding="utf-8"))
        self.assertEqual(
            event["event"],
            "maintenance.appearance-restored",
        )

    def test_locked_entry_does_not_reacquire_or_release_mutex(self) -> None:
        lock = FakeLock(acquire_error=True, release_error=True)

        outcome = self.service(
            execution_lock=lock,
        ).restore_install_appearance_locked()

        self.assertEqual(
            outcome.result,
            MaintenanceRestoreResult.RESTORED,
        )
        self.assertFalse(lock.released)

    def test_already_paused_still_restores_without_rewriting_state(
        self,
    ) -> None:
        StateStore(self.layout.state).save(AppState(True, None, None, None, "never"))

        outcome = self.service().restore_install_appearance()

        self.assertEqual(
            outcome.result,
            MaintenanceRestoreResult.RESTORED,
        )
        self.assertFalse(outcome.pause_changed)
        self.assertTrue(outcome.appearance_applied)

    def test_untrusted_backup_or_pending_auto_never_changes_state_or_windows(
        self,
    ) -> None:
        missing = self.service(
            backup_store=FakeBackupStore(error=FileNotFoundError("missing backup"))
        ).restore_install_appearance()
        self.assertEqual(
            missing.result,
            MaintenanceRestoreResult.DATA_UNTRUSTED,
        )
        self.assertFalse(StateStore(self.layout.state).load().paused)
        self.assertEqual(self.applier.calls, 0)

        with patch(
            "theme_scheduler.maintenance_service.ensure_no_pending_auto_transaction",
            side_effect=RuntimeError("pending transaction"),
        ):
            pending = self.service().restore_install_appearance()
        self.assertEqual(
            pending.result,
            MaintenanceRestoreResult.DATA_UNTRUSTED,
        )
        self.assertFalse(StateStore(self.layout.state).load().paused)
        self.assertEqual(self.applier.calls, 0)

    def test_busy_or_failed_lock_never_reads_backup(self) -> None:
        backup_store = Mock()
        busy = self.service(
            execution_lock=FakeLock(acquired=False),
            backup_store=backup_store,
        ).restore_install_appearance()
        failed = self.service(
            execution_lock=FakeLock(acquire_error=True),
            backup_store=backup_store,
        ).restore_install_appearance()

        self.assertEqual(
            busy.result,
            MaintenanceRestoreResult.ALREADY_RUNNING,
        )
        self.assertEqual(
            failed.result,
            MaintenanceRestoreResult.FAILED,
        )
        backup_store.load_verified.assert_not_called()

    def test_pause_write_failure_stops_before_windows(self) -> None:
        original = StateStore(self.layout.state).load()
        outcome = self.service(
            state_store=SaveFailsBeforeCommit(original),
        ).restore_install_appearance()

        self.assertEqual(
            outcome.result,
            MaintenanceRestoreResult.PARTIAL,
        )
        self.assertFalse(outcome.appearance_applied)
        self.assertFalse(outcome.as_dict()["windowsChanged"])
        self.assertEqual(self.applier.calls, 0)

    def test_late_pause_save_error_can_continue_but_reports_partial(
        self,
    ) -> None:
        original = StateStore(self.layout.state).load()
        store = SaveCommitsThenRaises(original)

        outcome = self.service(
            state_store=store,
        ).restore_install_appearance()

        self.assertEqual(
            outcome.result,
            MaintenanceRestoreResult.PARTIAL,
        )
        self.assertTrue(store.load().paused)
        self.assertTrue(outcome.appearance_applied)
        self.assertIn("readback succeeded", outcome.message)

    def test_apply_failure_with_rollback_keeps_pause_and_reports_no_net_windows(
        self,
    ) -> None:
        self.applier.error = LiveThemeApplyError(
            "simulated apply failure",
            rollback_succeeded=True,
        )

        outcome = self.service().restore_install_appearance()

        self.assertEqual(
            outcome.result,
            MaintenanceRestoreResult.FAILED,
        )
        self.assertTrue(StateStore(self.layout.state).load().paused)
        self.assertTrue(outcome.rollback_succeeded)
        self.assertFalse(outcome.as_dict()["windowsChanged"])

    def test_incomplete_or_unknown_windows_failure_is_partial_and_paused(
        self,
    ) -> None:
        self.applier.error = OSError("unknown apply state")

        outcome = self.service().restore_install_appearance()

        self.assertEqual(
            outcome.result,
            MaintenanceRestoreResult.PARTIAL,
        )
        self.assertTrue(StateStore(self.layout.state).load().paused)
        self.assertIsNone(outcome.rollback_succeeded)
        self.assertIsNone(outcome.as_dict()["windowsChanged"])

    def test_logging_or_lock_release_failure_cannot_turn_restore_into_success(
        self,
    ) -> None:
        log_failure = self.service(
            event_log=FailingEventLog(),
        ).restore_install_appearance()
        self.assertEqual(
            log_failure.result,
            MaintenanceRestoreResult.PARTIAL,
        )
        self.assertTrue(log_failure.appearance_applied)

        other_root = Path(self.temporary.name) / "other"
        other_layout = UserDataLayout(other_root)
        other_layout.ensure_directories()
        StateStore(other_layout.state).initialize(AppState.initial())
        release_failure = MaintenanceService(
            other_layout,
            FakeLock(release_error=True),
            backup_store=FakeBackupStore(install_backup()),
            clock=FixedClock(),
            appearance_applier=FakeAppearanceApplier(),
        ).restore_install_appearance()
        self.assertEqual(
            release_failure.result,
            MaintenanceRestoreResult.PARTIAL,
        )
        self.assertTrue(release_failure.appearance_applied)

    def test_system_mode_drift_is_never_reported_as_verified(self) -> None:
        self.applier.preserve_system = False

        outcome = self.service().restore_install_appearance()

        self.assertEqual(
            outcome.result,
            MaintenanceRestoreResult.PARTIAL,
        )
        self.assertFalse(outcome.windows_verified)
        self.assertIsNone(outcome.as_dict()["windowsChanged"])


if __name__ == "__main__":
    unittest.main()
