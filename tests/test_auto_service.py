from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from theme_scheduler.accent_profile import (
    AccentProfile,
    AccentProfileStore,
)
from theme_scheduler.accent_service import AccentApplyOutcome
from theme_scheduler.accent_theme import (
    LiveThemeApplyError,
    ThemeVisualState,
)
from theme_scheduler.auto_transaction import (
    AutoTransaction,
    AutoTransactionStore,
    file_sha256,
    json_document_sha256,
)
from theme_scheduler.automation import AutoRunner
from theme_scheduler.automation.recovery import (
    AutoRecoveryMixin,
    _string_key_mapping,
)
from theme_scheduler.config import AppConfig, ConfigStore
from theme_scheduler.core import AutoResultKind
from theme_scheduler.initial_setup import (
    create_initial_setup_marker,
)
from theme_scheduler.persistence import atomic_write_json
from theme_scheduler.state import AppState, StateStore
from theme_scheduler.storage import UserDataLayout
from theme_scheduler.theme import ThemeMode

NOW = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)
NOW_TEXT = "2026-07-24T08:00:00+00:00"


def _committed_transaction() -> AutoTransaction:
    state = AppState(False, "day", NOW_TEXT, "day", "success")
    return AutoTransaction(
        transaction_id="accent-20260724T080000-12345678",
        status="state-committed",
        started_at=NOW_TEXT,
        updated_at=NOW_TEXT,
        target_profile="day",
        target_apps_theme="light",
        learn_profile="night",
        state_before_sha256="0" * 64,
        state_after=state,
        state_after_sha256=json_document_sha256(state.as_dict()),
        windows_target=ThemeVisualState("0", 0xC4744DA9, "Light", "Dark"),
    )


class _RecordingTransactionStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.saved: AutoTransaction | None = None

    def save(self, transaction: AutoTransaction) -> AutoTransaction:
        if self.fail:
            raise OSError("marker write failed")
        self.saved = transaction
        return transaction


class AutoRecoveryHelperTests(unittest.TestCase):
    def test_json_boundary_accepts_only_string_key_mappings(self) -> None:
        self.assertEqual(_string_key_mapping({"kind": "value"}), {"kind": "value"})
        self.assertIsNone(_string_key_mapping({1: "value"}))
        self.assertIsNone(_string_key_mapping("not-an-object"))

    def test_partial_marker_reports_its_own_write_failure(self) -> None:
        transaction = _committed_transaction()
        recording = _RecordingTransactionStore()

        marker_error = AutoRecoveryMixin._try_mark_partial(
            recording,  # type: ignore[arg-type]
            transaction,
            error_code="log.write-failed",
            error=OSError("primary failure"),
        )

        self.assertEqual(marker_error, "")
        self.assertEqual(recording.saved.status, "partial")  # type: ignore[union-attr]
        self.assertEqual(recording.saved.error_code, "log.write-failed")  # type: ignore[union-attr]

        marker_error = AutoRecoveryMixin._try_mark_partial(
            _RecordingTransactionStore(fail=True),  # type: ignore[arg-type]
            transaction,
            error_code="transaction.complete-failed",
            error=OSError("primary failure"),
        )

        self.assertIn("transaction marker failed", marker_error)
        self.assertIn("marker write failed", marker_error)


class FakeClock:
    def now(self) -> datetime:
        return NOW


class FakeLock:
    def __init__(
        self, *, available: bool = True, acquire_error: Exception | None = None
    ) -> None:
        self.available = available
        self.acquire_error = acquire_error
        self.acquired = 0
        self.released = 0

    def acquire(self) -> bool:
        self.acquired += 1
        if self.acquire_error is not None:
            raise self.acquire_error
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


class FakeWindows:
    def __init__(
        self,
        current: ThemeVisualState,
        *,
        apply_failure: bool = False,
        rollback_succeeded: bool = True,
        probe_error: Exception | None = None,
    ) -> None:
        self.current = current
        self.apply_failure = apply_failure
        self.rollback_succeeded = rollback_succeeded
        self.probe_error = probe_error
        self.probe_count = 0
        self.apply_count = 0
        self.capture_count = 0
        self.rollback_count = 0
        self.before_by_transaction: dict[Path, ThemeVisualState] = {}

    def probe(self) -> None:
        self.probe_count += 1
        if self.probe_error is not None:
            raise self.probe_error

    def capture_profile(self, profile: str, timestamp: str) -> AccentProfile:
        self.capture_count += 1
        return AccentProfile(
            profile,
            timestamp,
            self.current.auto_colorization == "1",
            self.current.colorization_color,
            "26200",
        )

    def apply_profile(
        self,
        profile: AccentProfile,
        apps_theme: ThemeMode,
        transaction_directory: Path,
    ) -> AccentApplyOutcome:
        self.apply_count += 1
        before = self.current
        target = ThemeVisualState(
            "1" if profile.auto_colorization else "0",
            profile.colorization_color,
            apps_theme.value.title(),
            before.system_mode,
        )
        transaction_directory = transaction_directory.resolve()
        self.before_by_transaction[transaction_directory] = before
        before_path = transaction_directory / "before.theme"
        managed_path = transaction_directory / "managed.theme"
        before_path.write_bytes(b"before")
        managed_path.write_bytes(b"managed")
        if self.apply_failure:
            if not self.rollback_succeeded:
                self.current = target
            atomic_write_json(
                transaction_directory / "journal.json",
                {
                    "kind": "themescheduler.accent-transaction",
                    "schemaVersion": 1,
                    "status": "failed",
                    "before": before.as_dict(),
                    "target": target.as_dict(),
                    "rollbackSucceeded": self.rollback_succeeded,
                },
            )
            raise LiveThemeApplyError(
                "injected apply failure",
                rollback_succeeded=self.rollback_succeeded,
            )
        self.current = target
        atomic_write_json(
            transaction_directory / "journal.json",
            {
                "kind": "themescheduler.accent-transaction",
                "schemaVersion": 1,
                "status": "applied",
                "before": before.as_dict(),
                "target": target.as_dict(),
                "actual": target.as_dict(),
            },
        )
        return AccentApplyOutcome(
            transaction_directory=transaction_directory,
            active_theme_before=before_path,
            active_theme_after=managed_path,
            before=before.as_dict(),
            target=target.as_dict(),
            actual=target.as_dict(),
            index_before=6,
            bridge_target_index=13,
            index_after=0,
        )

    def read_visual_state(self) -> ThemeVisualState:
        return self.current

    def rollback(self, transaction_directory: Path) -> bool:
        self.rollback_count += 1
        if not self.rollback_succeeded:
            return False
        self.current = self.before_by_transaction[transaction_directory.resolve()]
        return True


class FailOnceStateStore:
    def __init__(self, inner: StateStore) -> None:
        self.inner = inner
        self.failed = False

    def load(self) -> AppState:
        return self.inner.load()

    def save(self, state: AppState) -> AppState:
        if not self.failed:
            self.failed = True
            raise OSError("state commit failed")
        return self.inner.save(state)


def _profile(name: str, color: int) -> AccentProfile:
    return AccentProfile(
        name,
        "2026-07-24T00:00:00+00:00",
        False,
        color,
        "26200",
    )


def _successful_state(active: str) -> AppState:
    return AppState(
        False,
        active,
        "2026-07-23T23:45:00+00:00",
        active,
        "success",
    )


class AutoRunnerTests(unittest.TestCase):
    def _layout(
        self,
        root: Path,
        *,
        state: AppState | None = None,
    ) -> UserDataLayout:
        layout = UserDataLayout(root / "data")
        layout.ensure_directories()
        ConfigStore(layout.config).initialize(AppConfig.defaults())
        StateStore(layout.state).initialize(state or _successful_state("night"))
        AccentProfileStore(layout.profile_path("day"), "day").create(
            _profile("day", 0xC4744DA9)
        )
        AccentProfileStore(layout.profile_path("night"), "night").create(
            _profile("night", 0xC4FFB900)
        )
        return layout

    @staticmethod
    def _windows(**kwargs) -> FakeWindows:
        return FakeWindows(
            ThemeVisualState("0", 0xC4FFB900, "Dark", "Dark"),
            **kwargs,
        )

    def _runner(
        self,
        layout: UserDataLayout,
        windows: FakeWindows,
        *,
        lock: FakeLock | None = None,
        log: MemoryLog | None = None,
        state_store=None,
        cleanup=None,
        force_apply: bool = False,
        event_trigger: str = "auto",
    ) -> AutoRunner:
        return AutoRunner(
            layout,
            lock or FakeLock(),
            windows,
            clock=FakeClock(),
            state_store=state_store,
            event_log=log or MemoryLog(),
            cleanup_callback=cleanup or (lambda _: None),
            force_apply=force_apply,
            event_trigger=event_trigger,
        )

    def test_cross_profile_run_learns_applies_commits_and_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(Path(directory))
            windows = self._windows()
            log = MemoryLog()

            outcome = self._runner(layout, windows, log=log).run()

            self.assertIs(outcome.result, AutoResultKind.APPLIED)
            self.assertEqual(outcome.learned_profile, "night")
            self.assertEqual(windows.current.app_mode, "Light")
            self.assertEqual(windows.current.system_mode, "Dark")
            self.assertEqual(windows.current.colorization_color, 0xC4744DA9)
            state = StateStore(layout.state).load()
            self.assertEqual(state.active_profile, "day")
            self.assertEqual(state.last_result, "success")
            night = AccentProfileStore(layout.profile_path("night"), "night").load()
            self.assertEqual(night.colorization_color, 0xC4FFB900)
            transaction = AutoTransactionStore(
                outcome.transaction_directory / "auto.json"  # type: ignore[operator]
            ).load()
            self.assertEqual(transaction.status, "completed")
            self.assertEqual(log.events[-1].event, "auto.applied")
            self.assertEqual(log.events[-1].verification, "passed")
            self.assertEqual(
                log.events[-1].transaction_id,
                transaction.transaction_id,
            )

    def test_initial_setup_marker_blocks_automatic_windows_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(
                Path(directory),
                state=AppState.pending_initial_setup(),
            )
            create_initial_setup_marker(layout.initial_setup_marker)
            windows = self._windows()
            log = MemoryLog()

            outcome = self._runner(layout, windows, log=log).run()

            self.assertIs(outcome.result, AutoResultKind.PAUSED)
            self.assertFalse(outcome.windows_changed)
            self.assertEqual(windows.apply_count, 0)
            self.assertEqual(
                log.events[-1].event,
                "auto.initial-setup-pending",
            )

    def test_repeat_same_profile_is_no_change_and_preserves_manual_color(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(Path(directory), state=_successful_state("day"))
            windows = self._windows()
            windows.current = ThemeVisualState("0", 0xC40078D4, "Dark", "Dark")

            outcome = self._runner(layout, windows).run()

            self.assertIs(outcome.result, AutoResultKind.NO_CHANGE)
            self.assertEqual(windows.apply_count, 0)
            self.assertEqual(windows.capture_count, 0)
            self.assertEqual(windows.current.colorization_color, 0xC40078D4)
            self.assertEqual(list(layout.runtime.iterdir()), [])

    def test_manual_force_sync_reapplies_same_profile_and_uses_manual_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(Path(directory), state=_successful_state("day"))
            windows = self._windows()
            windows.current = ThemeVisualState("0", 0xC40078D4, "Dark", "Dark")
            log = MemoryLog()

            outcome = self._runner(
                layout,
                windows,
                log=log,
                force_apply=True,
                event_trigger="manual",
            ).run()

            self.assertIs(outcome.result, AutoResultKind.APPLIED)
            self.assertEqual(windows.apply_count, 1)
            self.assertEqual(windows.capture_count, 0)
            self.assertEqual(windows.current.colorization_color, 0xC4744DA9)
            self.assertEqual(log.events[-1].event, "auto.applied")
            self.assertEqual(log.events[-1].trigger, "manual")

    def test_manual_force_sync_cannot_bypass_pause(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paused = AppState(
                True,
                "day",
                "2026-07-23T23:45:00+00:00",
                "day",
                "success",
            )
            layout = self._layout(Path(directory), state=paused)
            windows = self._windows()
            log = MemoryLog()

            outcome = self._runner(
                layout,
                windows,
                log=log,
                force_apply=True,
                event_trigger="manual",
            ).run()

            self.assertIs(outcome.result, AutoResultKind.PAUSED)
            self.assertEqual(windows.probe_count, 0)
            self.assertEqual(windows.apply_count, 0)
            self.assertEqual(log.events[-1].trigger, "manual")

    def test_pause_busy_and_missing_state_never_touch_windows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paused = AppState(
                True,
                "night",
                "2026-07-23T23:45:00+00:00",
                "night",
                "success",
            )
            layout = self._layout(root, state=paused)
            windows = self._windows()
            paused_outcome = self._runner(layout, windows).run()
            self.assertIs(paused_outcome.result, AutoResultKind.PAUSED)

            busy_outcome = self._runner(
                layout, windows, lock=FakeLock(available=False)
            ).run()
            self.assertIs(busy_outcome.result, AutoResultKind.ALREADY_RUNNING)

            layout.state.unlink()
            missing_outcome = self._runner(layout, windows).run()
            self.assertIs(missing_outcome.result, AutoResultKind.DATA_UNTRUSTED)
            self.assertEqual(windows.apply_count, 0)

    def test_capability_probe_failure_prevents_learning_and_apply(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(Path(directory))
            windows = self._windows(probe_error=OSError("bridge unavailable"))

            outcome = self._runner(layout, windows).run()

            self.assertIs(outcome.result, AutoResultKind.FATAL_FAILURE)
            self.assertEqual(windows.probe_count, 1)
            self.assertEqual(windows.capture_count, 0)
            self.assertEqual(windows.apply_count, 0)
            self.assertEqual(list(layout.runtime.iterdir()), [])

    def test_apply_failure_distinguishes_rollback_and_partial(self) -> None:
        for rollback, expected in (
            (True, AutoResultKind.APPLY_FAILED_ROLLED_BACK),
            (False, AutoResultKind.PARTIAL_FAILURE),
        ):
            with (
                self.subTest(rollback=rollback),
                tempfile.TemporaryDirectory() as directory,
            ):
                layout = self._layout(Path(directory))
                windows = self._windows(
                    apply_failure=True,
                    rollback_succeeded=rollback,
                )
                log = MemoryLog()

                outcome = self._runner(layout, windows, log=log).run()

                self.assertIs(outcome.result, expected)
                transaction = AutoTransactionStore(
                    outcome.transaction_directory / "auto.json"  # type: ignore[operator]
                ).load()
                self.assertEqual(
                    transaction.status, "failed" if rollback else "partial"
                )
                self.assertEqual(
                    StateStore(layout.state).load().last_result,
                    "failed" if rollback else "partial",
                )
                self.assertEqual(log.events[-1].verification, "failed")
                self.assertTrue(log.events[-1].rollback_attempted)
                self.assertIs(
                    log.events[-1].rollback_succeeded,
                    rollback,
                )

    def test_partial_windows_failure_blocks_future_automatic_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(Path(directory))
            windows = self._windows(
                apply_failure=True,
                rollback_succeeded=False,
            )
            first = self._runner(layout, windows).run()
            self.assertIs(first.result, AutoResultKind.PARTIAL_FAILURE)
            apply_count = windows.apply_count
            windows.apply_failure = False

            second = self._runner(layout, windows).run()

            self.assertIs(second.result, AutoResultKind.DATA_UNTRUSTED)
            self.assertEqual(windows.apply_count, apply_count)

    def test_state_commit_failure_rolls_back_windows_and_preserves_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(Path(directory))
            before = StateStore(layout.state).load()
            windows = self._windows()
            failing_store = FailOnceStateStore(StateStore(layout.state))

            outcome = self._runner(
                layout,
                windows,
                state_store=failing_store,
            ).run()

            self.assertIs(outcome.result, AutoResultKind.APPLY_FAILED_ROLLED_BACK)
            self.assertEqual(windows.rollback_count, 1)
            self.assertEqual(windows.current.app_mode, "Dark")
            self.assertEqual(StateStore(layout.state).load(), before)

    def test_log_failure_is_recovered_without_reapplying_windows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(Path(directory))
            windows = self._windows()
            first = self._runner(layout, windows, log=MemoryLog(fail=True)).run()
            self.assertIs(first.result, AutoResultKind.PARTIAL_FAILURE)
            self.assertEqual(windows.apply_count, 1)

            recovery_log = MemoryLog()
            second = self._runner(layout, windows, log=recovery_log).run()

            self.assertIs(second.result, AutoResultKind.APPLIED)
            self.assertTrue(second.recovered)
            self.assertEqual(windows.apply_count, 1)
            self.assertEqual(recovery_log.events[-1].event, "auto.recovered")
            transaction = AutoTransactionStore(
                first.transaction_directory / "auto.json"  # type: ignore[operator]
            ).load()
            self.assertEqual(transaction.status, "completed")

    def test_cleanup_failure_is_recoverable_post_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(Path(directory))
            windows = self._windows()

            def fail_cleanup(_: Path) -> None:
                raise OSError("cleanup failed")

            first = self._runner(layout, windows, cleanup=fail_cleanup).run()
            self.assertIs(first.result, AutoResultKind.PARTIAL_FAILURE)
            second = self._runner(layout, windows).run()
            self.assertIs(second.result, AutoResultKind.APPLIED)
            self.assertTrue(second.recovered)
            self.assertEqual(windows.apply_count, 1)

    def test_windows_verified_interruption_commits_state_without_reapply(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(Path(directory))
            before_state = StateStore(layout.state).load()
            windows = self._windows()
            transaction_dir = layout.new_transaction_directory(NOW)
            transaction_dir.mkdir()
            after_state = AppState(
                False,
                "day",
                NOW_TEXT,
                "day",
                "success",
            )
            target = ThemeVisualState("0", 0xC4744DA9, "Light", "Dark")
            windows.current = target
            store = AutoTransactionStore(transaction_dir / "auto.json")
            planned = store.create(
                AutoTransaction(
                    transaction_id=transaction_dir.name,
                    status="planned",
                    started_at=NOW_TEXT,
                    updated_at=NOW_TEXT,
                    target_profile="day",
                    target_apps_theme="light",
                    learn_profile="night",
                    state_before_sha256=file_sha256(layout.state),
                    state_after=after_state,
                    state_after_sha256=json_document_sha256(after_state.as_dict()),
                )
            )
            store.save(
                replace(
                    planned,
                    status="windows-verified",
                    windows_target=target,
                )
            )

            outcome = self._runner(layout, windows).run()

            self.assertTrue(outcome.recovered)
            self.assertEqual(windows.apply_count, 0)
            self.assertNotEqual(StateStore(layout.state).load(), before_state)
            self.assertEqual(StateStore(layout.state).load().active_profile, "day")
            self.assertEqual(store.load().status, "completed")

    def test_planned_with_applied_accent_journal_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(Path(directory))
            after_state = AppState(False, "day", NOW_TEXT, "day", "success")
            transaction_dir = layout.new_transaction_directory(NOW)
            transaction_dir.mkdir()
            store = AutoTransactionStore(transaction_dir / "auto.json")
            store.create(
                AutoTransaction(
                    transaction_id=transaction_dir.name,
                    status="planned",
                    started_at=NOW_TEXT,
                    updated_at=NOW_TEXT,
                    target_profile="day",
                    target_apps_theme="light",
                    learn_profile="night",
                    state_before_sha256=file_sha256(layout.state),
                    state_after=after_state,
                    state_after_sha256=json_document_sha256(after_state.as_dict()),
                )
            )
            before = ThemeVisualState("0", 0xC4FFB900, "Dark", "Dark")
            target = ThemeVisualState("0", 0xC4744DA9, "Light", "Dark")
            atomic_write_json(
                transaction_dir / "journal.json",
                {
                    "kind": "themescheduler.accent-transaction",
                    "schemaVersion": 1,
                    "status": "applied",
                    "before": before.as_dict(),
                    "target": target.as_dict(),
                    "actual": target.as_dict(),
                },
            )
            windows = FakeWindows(target)

            outcome = self._runner(layout, windows).run()

            self.assertIs(outcome.result, AutoResultKind.APPLIED)
            self.assertTrue(outcome.recovered)
            self.assertEqual(windows.apply_count, 0)
            self.assertEqual(StateStore(layout.state).load().active_profile, "day")
            self.assertEqual(store.load().status, "completed")

    def test_state_committed_recovery_does_not_overwrite_manual_windows_change(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(Path(directory))
            before_hash = file_sha256(layout.state)
            after_state = AppState(False, "day", NOW_TEXT, "day", "success")
            transaction_dir = layout.new_transaction_directory(NOW)
            transaction_dir.mkdir()
            store = AutoTransactionStore(transaction_dir / "auto.json")
            planned = store.create(
                AutoTransaction(
                    transaction_id=transaction_dir.name,
                    status="planned",
                    started_at=NOW_TEXT,
                    updated_at=NOW_TEXT,
                    target_profile="day",
                    target_apps_theme="light",
                    learn_profile="night",
                    state_before_sha256=before_hash,
                    state_after=after_state,
                    state_after_sha256=json_document_sha256(after_state.as_dict()),
                )
            )
            target = ThemeVisualState("0", 0xC4744DA9, "Light", "Dark")
            verified = store.save(
                replace(
                    planned,
                    status="windows-verified",
                    windows_target=target,
                )
            )
            StateStore(layout.state).save(after_state)
            store.save(replace(verified, status="state-committed"))
            manual = ThemeVisualState("0", 0xC40078D4, "Dark", "Dark")
            windows = FakeWindows(manual)

            outcome = self._runner(layout, windows).run()

            self.assertTrue(outcome.recovered)
            self.assertEqual(windows.current, manual)
            self.assertEqual(windows.apply_count, 0)
            self.assertEqual(store.load().status, "completed")

    def test_multiple_pending_transactions_block_automatic_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(Path(directory))
            state_after = AppState(False, "day", NOW_TEXT, "day", "success")
            for instant in (
                NOW,
                NOW.replace(second=1),
            ):
                transaction_dir = layout.new_transaction_directory(instant)
                transaction_dir.mkdir()
                AutoTransactionStore(transaction_dir / "auto.json").create(
                    AutoTransaction(
                        transaction_id=transaction_dir.name,
                        status="planned",
                        started_at=NOW_TEXT,
                        updated_at=NOW_TEXT,
                        target_profile="day",
                        target_apps_theme="light",
                        learn_profile="night",
                        state_before_sha256=file_sha256(layout.state),
                        state_after=state_after,
                        state_after_sha256=json_document_sha256(state_after.as_dict()),
                    )
                )
            windows = self._windows()

            outcome = self._runner(layout, windows).run()

            self.assertIs(outcome.result, AutoResultKind.DATA_UNTRUSTED)
            self.assertEqual(windows.apply_count, 0)

    def test_planned_without_accent_evidence_is_closed_then_replanned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(Path(directory))
            state = StateStore(layout.state).load()
            transaction_dir = layout.new_transaction_directory(NOW)
            transaction_dir.mkdir()
            after = AppState(False, "day", NOW_TEXT, "day", "success")
            old_store = AutoTransactionStore(transaction_dir / "auto.json")
            old_store.create(
                AutoTransaction(
                    transaction_id=transaction_dir.name,
                    status="planned",
                    started_at=NOW_TEXT,
                    updated_at=NOW_TEXT,
                    target_profile="day",
                    target_apps_theme="light",
                    learn_profile="night",
                    state_before_sha256=file_sha256(layout.state),
                    state_after=after,
                    state_after_sha256=json_document_sha256(after.as_dict()),
                )
            )
            windows = self._windows()

            outcome = self._runner(layout, windows).run()

            self.assertIs(outcome.result, AutoResultKind.APPLIED)
            self.assertEqual(old_store.load().status, "failed")
            self.assertEqual(windows.apply_count, 1)
            self.assertNotEqual(outcome.transaction_directory, transaction_dir)
            self.assertEqual(state.active_profile, "night")

    def test_corrupt_auto_evidence_blocks_windows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(Path(directory))
            transaction = layout.new_transaction_directory(NOW)
            transaction.mkdir()
            (transaction / "auto.json").write_text('{"kind":', encoding="utf-8")
            windows = self._windows()

            outcome = self._runner(layout, windows).run()

            self.assertIs(outcome.result, AutoResultKind.DATA_UNTRUSTED)
            self.assertEqual(windows.apply_count, 0)


if __name__ == "__main__":
    unittest.main()
