from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from theme_scheduler.auto_transaction import (
    AutoTransaction,
    AutoTransactionStore,
    file_sha256,
    json_document_sha256,
)
from theme_scheduler.control import (
    ControlResultKind,
    plan_pause_transition,
)
from theme_scheduler.control_service import ControlService
from theme_scheduler.state import AppState, StateStore
from theme_scheduler.storage import UserDataLayout

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


class FakeClock:
    def now(self) -> datetime:
        return NOW


class FakeLock:
    def __init__(
        self,
        *,
        available: bool = True,
        acquire_error: Exception | None = None,
        release_error: Exception | None = None,
    ) -> None:
        self.available = available
        self.acquire_error = acquire_error
        self.release_error = release_error
        self.acquired = 0
        self.released = 0

    def acquire(self) -> bool:
        self.acquired += 1
        if self.acquire_error is not None:
            raise self.acquire_error
        return self.available

    def release(self) -> None:
        self.released += 1
        if self.release_error is not None:
            raise self.release_error


class MemoryLog:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.events = []

    def append(self, event) -> None:
        if self.fail:
            raise OSError("log failed")
        self.events.append(event)


class FailingStateStore:
    def __init__(self, inner: StateStore) -> None:
        self.inner = inner

    def load(self) -> AppState:
        return self.inner.load()

    def save(self, state: AppState) -> AppState:
        raise OSError("save failed")


def _state(*, paused: bool = False) -> AppState:
    return AppState(
        paused,
        "night",
        "2026-07-23T23:45:00+00:00",
        "night",
        "success",
    )


class ControlContractTests(unittest.TestCase):
    def test_transition_changes_only_paused_and_is_idempotent(self) -> None:
        before = _state()
        transition = plan_pause_transition(before, True)

        self.assertTrue(transition.changed)
        self.assertTrue(transition.after.paused)
        self.assertEqual(
            transition.after.__dict__ | {"paused": before.paused},
            before.__dict__,
        )
        repeated = plan_pause_transition(transition.after, True)
        self.assertFalse(repeated.changed)
        self.assertIs(repeated.before, repeated.after)


class ControlServiceTests(unittest.TestCase):
    def _layout(self, root: Path, *, paused: bool = False) -> UserDataLayout:
        layout = UserDataLayout(root / "data")
        layout.runtime.mkdir(parents=True)
        StateStore(layout.state).initialize(_state(paused=paused))
        return layout

    def _service(
        self,
        layout: UserDataLayout,
        *,
        lock: FakeLock | None = None,
        log: MemoryLog | None = None,
        state_store=None,
    ) -> ControlService:
        return ControlService(
            layout,
            lock or FakeLock(),
            state_store=state_store,
            event_log=log or MemoryLog(),
            clock=FakeClock(),
        )

    def test_timestamp_rejects_naive_clock_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "UTC offset"):
            ControlService._timestamp(datetime(2026, 7, 24, 12, 0))

    def test_pause_resume_persist_and_preserve_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(Path(directory))
            before = StateStore(layout.state).load()
            log = MemoryLog()

            paused = self._service(layout, log=log).set_paused(True)
            reloaded = StateStore(layout.state).load()

            self.assertIs(paused.result, ControlResultKind.CHANGED)
            self.assertTrue(reloaded.paused)
            self.assertEqual(reloaded.active_profile, before.active_profile)
            self.assertEqual(reloaded.last_run_at, before.last_run_at)
            self.assertEqual(
                reloaded.last_applied_profile,
                before.last_applied_profile,
            )
            self.assertEqual(reloaded.last_result, before.last_result)
            self.assertEqual(log.events[-1].event, "control.paused")
            self.assertEqual(log.events[-1].trigger, "manual")

            resumed = self._service(layout, log=log).set_paused(False)
            self.assertIs(resumed.result, ControlResultKind.CHANGED)
            self.assertFalse(StateStore(layout.state).load().paused)
            self.assertEqual(log.events[-1].event, "control.resumed")
            self.assertIn("not synchronized", log.events[-1].message)

    def test_repeat_control_is_no_change_but_audited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(Path(directory), paused=True)
            log = MemoryLog()

            outcome = self._service(layout, log=log).set_paused(True)

            self.assertIs(outcome.result, ControlResultKind.NO_CHANGE)
            self.assertFalse(outcome.state_changed)
            self.assertTrue(outcome.log_written)
            self.assertEqual(log.events[-1].result, "skipped")

    def test_busy_untrusted_and_pending_never_change_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(Path(directory))
            before = layout.state.read_bytes()
            busy = self._service(layout, lock=FakeLock(available=False)).set_paused(
                True
            )
            self.assertIs(busy.result, ControlResultKind.ALREADY_RUNNING)
            self.assertEqual(layout.state.read_bytes(), before)

            layout.state.write_text("{", encoding="utf-8")
            untrusted = self._service(layout).set_paused(True)
            self.assertIs(untrusted.result, ControlResultKind.DATA_UNTRUSTED)
            self.assertEqual(layout.state.read_text(encoding="utf-8"), "{")

        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(Path(directory))
            before_state = StateStore(layout.state).load()
            after_state = AppState(
                False,
                "day",
                "2026-07-24T12:00:00+00:00",
                "day",
                "success",
            )
            transaction_id = "accent-20260724T120000-1234abcd"
            transaction_directory = layout.runtime / transaction_id
            transaction_directory.mkdir()
            AutoTransactionStore(transaction_directory / "auto.json").create(
                AutoTransaction(
                    transaction_id=transaction_id,
                    status="planned",
                    started_at="2026-07-24T12:00:00+00:00",
                    updated_at="2026-07-24T12:00:00+00:00",
                    target_profile="day",
                    target_apps_theme="light",
                    learn_profile="night",
                    state_before_sha256=file_sha256(layout.state),
                    state_after=after_state,
                    state_after_sha256=json_document_sha256(after_state.as_dict()),
                )
            )

            pending = self._service(layout).set_paused(True)

            self.assertIs(pending.result, ControlResultKind.DATA_UNTRUSTED)
            self.assertEqual(StateStore(layout.state).load(), before_state)

    def test_state_save_and_log_failures_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(Path(directory))
            store = StateStore(layout.state)
            failed = self._service(
                layout,
                state_store=FailingStateStore(store),
            ).set_paused(True)
            self.assertIs(failed.result, ControlResultKind.FATAL_FAILURE)
            self.assertFalse(failed.state_changed)
            self.assertFalse(store.load().paused)

            partial = self._service(
                layout,
                log=MemoryLog(fail=True),
            ).set_paused(True)
            self.assertIs(partial.result, ControlResultKind.PARTIAL_FAILURE)
            self.assertTrue(partial.state_changed)
            self.assertFalse(partial.log_written)
            self.assertTrue(store.load().paused)

    def test_lock_failures_do_not_hide_completed_state_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(Path(directory))
            acquire = self._service(
                layout,
                lock=FakeLock(acquire_error=OSError("create failed")),
            ).set_paused(True)
            self.assertIs(acquire.result, ControlResultKind.FATAL_FAILURE)
            self.assertFalse(StateStore(layout.state).load().paused)

            release = self._service(
                layout,
                lock=FakeLock(release_error=OSError("release failed")),
            ).set_paused(True)
            self.assertIs(release.result, ControlResultKind.PARTIAL_FAILURE)
            self.assertTrue(release.state_changed)
            self.assertTrue(StateStore(layout.state).load().paused)


if __name__ == "__main__":
    unittest.main()
