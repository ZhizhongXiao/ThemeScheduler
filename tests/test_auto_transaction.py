from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from theme_scheduler.accent_theme import ThemeVisualState
from theme_scheduler.auto_transaction import (
    AutoTransaction,
    AutoTransactionError,
    AutoTransactionStore,
    file_sha256,
    json_document_sha256,
)
from theme_scheduler.persistence import atomic_write_json
from theme_scheduler.state import AppState


def _state_after() -> AppState:
    return AppState(
        False,
        "day",
        "2026-07-24T06:15:00+08:00",
        "day",
        "success",
    )


def _planned() -> AutoTransaction:
    state_after = _state_after()
    return AutoTransaction(
        transaction_id="accent-20260724T061500-1234abcd",
        status="planned",
        started_at="2026-07-24T06:15:00+08:00",
        updated_at="2026-07-24T06:15:00+08:00",
        target_profile="day",
        target_apps_theme="light",
        learn_profile="night",
        state_before_sha256="a" * 64,
        state_after=state_after,
        state_after_sha256=json_document_sha256(state_after.as_dict()),
    )


def _visual() -> ThemeVisualState:
    return ThemeVisualState("0", 0xC4744DA9, "Light", "Dark")


class AutoTransactionTests(unittest.TestCase):
    def test_round_trip_and_canonical_state_hash(self) -> None:
        transaction = _planned()

        self.assertEqual(AutoTransaction.from_dict(transaction.as_dict()), transaction)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            atomic_write_json(path, transaction.state_after.as_dict())
            self.assertEqual(file_sha256(path), transaction.state_after_sha256)

    def test_unknown_fields_and_unbound_state_are_rejected(self) -> None:
        payload = _planned().as_dict()
        payload["unknown"] = True
        with self.assertRaisesRegex(AutoTransactionError, "fields"):
            AutoTransaction.from_dict(payload)

        with self.assertRaisesRegex(AutoTransactionError, "does not bind"):
            replace(_planned(), state_after_sha256="b" * 64)

    def test_verified_and_terminal_failure_invariants(self) -> None:
        with self.assertRaisesRegex(AutoTransactionError, "windowsTarget"):
            replace(_planned(), status="windows-verified")
        failed = replace(
            _planned(),
            status="failed",
            error_code="apply.failed",
            message="Apply failed and rolled back.",
            rollback_succeeded=True,
        )
        self.assertEqual(failed.status, "failed")
        with self.assertRaisesRegex(AutoTransactionError, "errorCode"):
            replace(_planned(), status="partial")

    def test_store_enforces_transitions_and_immutable_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AutoTransactionStore(Path(directory) / "auto.json")
            planned = store.create(_planned())
            verified = store.save(
                replace(
                    planned,
                    status="windows-verified",
                    updated_at="2026-07-24T06:15:01+08:00",
                    windows_target=_visual(),
                )
            )
            committed = store.save(
                replace(
                    verified,
                    status="state-committed",
                    updated_at="2026-07-24T06:15:02+08:00",
                )
            )
            completed = store.save(
                replace(
                    committed,
                    status="completed",
                    updated_at="2026-07-24T06:15:03+08:00",
                )
            )
            self.assertEqual(completed.status, "completed")
            with self.assertRaisesRegex(AutoTransactionError, "Illegal"):
                store.save(replace(completed, status="completed"))

    def test_store_rejects_identity_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AutoTransactionStore(Path(directory) / "auto.json")
            planned = store.create(_planned())
            with self.assertRaisesRegex(AutoTransactionError, "immutable"):
                store.save(
                    replace(
                        planned,
                        status="failed",
                        target_apps_theme="dark",
                        error_code="test.failed",
                        message="Failure.",
                    )
                )


if __name__ == "__main__":
    unittest.main()
