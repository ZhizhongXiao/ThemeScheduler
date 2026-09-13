from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from theme_scheduler.notification_protocol import (
    NotificationAction,
)
from theme_scheduler.switch_override import (
    ExecutionDecision,
    PendingSwitch,
    PendingSwitchStore,
    SwitchDecision,
    SwitchOverrideError,
)

TOKEN_1 = "0123456789abcdef0123456789abcdef"
TOKEN_2 = "fedcba9876543210fedcba9876543210"
UTC8 = timezone(timedelta(hours=8))


class PendingSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scheduled = datetime(2026, 7, 26, 20, 0, tzinfo=UTC8)
        self.next_fixed = datetime(2026, 7, 27, 8, 0, tzinfo=UTC8)
        self.prepare = self.scheduled - timedelta(minutes=5)
        self.value = PendingSwitch.create(
            target_profile="night",
            scheduled_at=self.scheduled,
            next_fixed_at=self.next_fixed,
            now=self.prepare,
        ).arm(now=self.prepare, token=TOKEN_1)

    def test_confirm_keeps_original_execution_time(self) -> None:
        confirmed = self.value.apply_action(
            NotificationAction.CONFIRM,
            token=TOKEN_1,
            now=self.prepare + timedelta(minutes=1),
        )
        self.assertIs(confirmed.decision, SwitchDecision.CONFIRMED)
        self.assertEqual(confirmed.scheduled_at, self.scheduled)
        self.assertIsNone(confirmed.action_token)
        self.assertIs(
            confirmed.execution_decision(now=self.scheduled),
            ExecutionDecision.APPLY,
        )

    def test_no_action_applies_at_original_execution_time(self) -> None:
        self.assertIs(
            self.value.execution_decision(now=self.prepare),
            ExecutionDecision.WAIT,
        )
        self.assertIs(
            self.value.execution_decision(now=self.scheduled),
            ExecutionDecision.APPLY,
        )

    def test_skip_is_consumed_at_execution_time(self) -> None:
        skipped = self.value.apply_action(
            NotificationAction.SKIP,
            token=TOKEN_1,
            now=self.prepare + timedelta(minutes=1),
        )
        self.assertIs(
            skipped.execution_decision(now=self.scheduled),
            ExecutionDecision.SKIP,
        )

    def test_delay_creates_a_new_unarmed_five_minute_window(self) -> None:
        clicked_at = self.prepare + timedelta(minutes=1)
        delayed = self.value.apply_action(
            NotificationAction.DELAY,
            token=TOKEN_1,
            now=clicked_at,
        )
        self.assertEqual(delayed.scheduled_at, self.scheduled + timedelta(minutes=30))
        self.assertEqual(delayed.prepare_at, self.scheduled + timedelta(minutes=25))
        self.assertEqual(delayed.defer_count, 1)
        self.assertTrue(delayed.can_delay)
        self.assertIsNone(delayed.action_token)
        self.assertIs(
            delayed.execution_decision(now=self.scheduled),
            ExecutionDecision.WAIT,
        )

        rearmed = delayed.arm(now=delayed.prepare_at, token=TOKEN_2)
        delayed_again = rearmed.apply_action(
            NotificationAction.DELAY,
            token=TOKEN_2,
            now=delayed.prepare_at + timedelta(minutes=1),
        )
        self.assertEqual(
            delayed_again.scheduled_at,
            self.scheduled + timedelta(minutes=60),
        )
        self.assertEqual(
            delayed_again.prepare_at,
            self.scheduled + timedelta(minutes=55),
        )
        self.assertEqual(delayed_again.defer_count, 2)

    def test_delay_never_reaches_or_crosses_next_fixed_boundary(self) -> None:
        scheduled = self.next_fixed - timedelta(minutes=30)
        value = PendingSwitch.create(
            target_profile="day",
            scheduled_at=scheduled,
            next_fixed_at=self.next_fixed,
            now=scheduled - timedelta(minutes=5),
        ).arm(now=scheduled - timedelta(minutes=5), token=TOKEN_1)
        with self.assertRaisesRegex(SwitchOverrideError, "next fixed"):
            value.apply_action(
                NotificationAction.DELAY,
                token=TOKEN_1,
                now=scheduled - timedelta(minutes=4),
            )
        self.assertFalse(value.can_delay)

    def test_token_is_one_time_and_expires_at_execution(self) -> None:
        confirmed = self.value.apply_action(
            NotificationAction.CONFIRM,
            token=TOKEN_1,
            now=self.prepare + timedelta(minutes=1),
        )
        with self.assertRaisesRegex(SwitchOverrideError, "already"):
            confirmed.apply_action(
                NotificationAction.SKIP,
                token=TOKEN_1,
                now=self.prepare + timedelta(minutes=2),
            )
        with self.assertRaisesRegex(SwitchOverrideError, "expired"):
            self.value.apply_action(
                NotificationAction.SKIP,
                token=TOKEN_1,
                now=self.scheduled,
            )

    def test_old_token_is_invalid_after_delay(self) -> None:
        delayed = self.value.apply_action(
            NotificationAction.DELAY,
            token=TOKEN_1,
            now=self.prepare + timedelta(minutes=1),
        )
        with self.assertRaisesRegex(SwitchOverrideError, "absent, stale"):
            delayed.apply_action(
                NotificationAction.SKIP,
                token=TOKEN_1,
                now=self.scheduled,
            )

    def test_next_fixed_boundary_supersedes_old_target(self) -> None:
        self.assertIs(
            self.value.execution_decision(now=self.next_fixed),
            ExecutionDecision.SUPERSEDED,
        )

    def test_strict_json_round_trip_and_store_readback(self) -> None:
        payload = self.value.as_dict()
        self.assertEqual(
            set(payload),
            {
                "kind",
                "schemaVersion",
                "targetProfile",
                "originalScheduledAt",
                "scheduledAt",
                "nextFixedAt",
                "decision",
                "deferCount",
                "actionToken",
                "preparedAt",
                "updatedAt",
            },
        )
        self.assertEqual(PendingSwitch.from_dict(payload), self.value)

        with tempfile.TemporaryDirectory() as raw:
            store = PendingSwitchStore(Path(raw) / "pending-switch.json")
            self.assertEqual(store.create(self.value), self.value)
            confirmed = self.value.apply_action(
                NotificationAction.CONFIRM,
                token=TOKEN_1,
                now=self.prepare + timedelta(minutes=1),
            )
            self.assertEqual(store.save(confirmed), confirmed)

    def test_store_canonicalizes_real_clock_microseconds(self) -> None:
        prepare = self.prepare.replace(microsecond=847362)
        value = PendingSwitch.create(
            target_profile="night",
            scheduled_at=self.scheduled,
            next_fixed_at=self.next_fixed,
            now=prepare,
        ).arm(now=prepare, token=TOKEN_1)

        self.assertEqual(value.prepared_at.microsecond, 0)
        self.assertEqual(value.updated_at.microsecond, 0)
        with tempfile.TemporaryDirectory() as raw:
            store = PendingSwitchStore(Path(raw) / "pending-switch.json")
            self.assertEqual(store.create(value), value)

    def test_schema_rejects_unknown_fields_and_inconsistent_deferral(self) -> None:
        payload = self.value.as_dict()
        payload["unknown"] = True
        with self.assertRaisesRegex(SwitchOverrideError, "unknown"):
            PendingSwitch.from_dict(payload)

        payload = self.value.as_dict()
        payload["deferCount"] = 1
        with self.assertRaisesRegex(SwitchOverrideError, "all deferrals"):
            PendingSwitch.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
