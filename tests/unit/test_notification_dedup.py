from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from theme_scheduler.notification_dedup import (
    NotificationDedupState,
    NotificationDedupStore,
)

UTC8 = timezone(timedelta(hours=8))
NOW = datetime(2026, 7, 26, 16, 0, tzinfo=UTC8)


class NotificationDedupTests(unittest.TestCase):
    def test_first_error_sends_and_six_hour_window_suppresses(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = NotificationDedupStore(Path(raw) / "dedup.json")
            key = "auto.failed|theme.apply-failed"

            self.assertTrue(store.should_send(key, now=NOW))
            store.record(key, now=NOW)
            self.assertFalse(
                store.should_send(key, now=NOW + timedelta(hours=5, minutes=59))
            )
            self.assertTrue(store.should_send(key, now=NOW + timedelta(hours=6)))

    def test_different_error_key_is_not_suppressed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = NotificationDedupStore(Path(raw) / "dedup.json")
            store.record("auto.failed|theme.apply-failed", now=NOW)

            self.assertTrue(
                store.should_send(
                    "auto.failed|state.commit-failed",
                    now=NOW + timedelta(minutes=1),
                )
            )

    def test_real_clock_microseconds_round_trip_as_persisted_seconds(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = NotificationDedupStore(Path(raw) / "dedup.json")
            now = datetime.now().astimezone()
            key = "auto.failed|data-untrusted"

            recorded = store.record(key, now=now)

            self.assertEqual(recorded, store.load())
            self.assertEqual(recorded.entries[0][1].microsecond, 0)
            self.assertFalse(store.should_send(key, now=now))

    def test_schema_rejects_unknown_fields_and_invalid_keys(self) -> None:
        with self.assertRaisesRegex(ValueError, "fields"):
            NotificationDedupState.from_dict(
                {
                    "kind": "themescheduler.notification-dedup",
                    "schemaVersion": 1,
                    "entries": [],
                    "extra": True,
                }
            )
        with self.assertRaisesRegex(ValueError, "key"):
            NotificationDedupState((("INVALID KEY", NOW),))


if __name__ == "__main__":
    unittest.main()
