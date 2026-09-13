from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from theme_scheduler import auto_transaction, runtime_retention, state, switch_override
from theme_scheduler.accent_profile import AccentProfile
from theme_scheduler.cli import auto as auto_cli
from theme_scheduler.health_contracts import HealthReport
from theme_scheduler.lifecycle import _validation as lifecycle_validation
from theme_scheduler.log_policy import LogEvent
from theme_scheduler.scheduler import triggers

UTC_TIMESTAMP = "2026-08-09T12:00:00Z"


class TimestampContractTests(unittest.TestCase):
    def test_contract_validators_accept_utc_z_suffix(self) -> None:
        self.assertEqual(
            auto_transaction._timestamp(UTC_TIMESTAMP, "preparedAt"),
            UTC_TIMESTAMP,
        )
        self.assertEqual(
            lifecycle_validation._timestamp(UTC_TIMESTAMP, "capturedAt"),
            UTC_TIMESTAMP,
        )
        self.assertEqual(
            lifecycle_validation._parsed_timestamp(
                UTC_TIMESTAMP,
                "capturedAt",
            ).utcoffset(),
            timedelta(0),
        )
        self.assertEqual(
            auto_cli._parse_aware_time(UTC_TIMESTAMP).utcoffset(), timedelta(0)
        )
        self.assertEqual(
            triggers._aware_timestamp(UTC_TIMESTAMP, "task.trigger.startAt"),
            "2026-08-09T12:00:00+00:00",
        )
        self.assertEqual(
            state._timestamp_or_none(UTC_TIMESTAMP, "lastAttemptAt"),
            UTC_TIMESTAMP,
        )
        self.assertEqual(
            switch_override._parse_timestamp(
                UTC_TIMESTAMP,
                "scheduledAt",
            ).utcoffset(),
            timedelta(0),
        )

        profile = AccentProfile("day", UTC_TIMESTAMP, False, 0xC4744DA9, "26200")
        event = LogEvent(
            UTC_TIMESTAMP,
            "INFO",
            "auto.applied",
            "success",
            "auto",
        )
        self.assertEqual(profile.captured_at, UTC_TIMESTAMP)
        self.assertEqual(event.occurred_at, UTC_TIMESTAMP)

    def test_invalid_timestamp_is_rejected_at_each_contract_boundary(self) -> None:
        cases = (
            lambda: auto_transaction._timestamp("invalid", "preparedAt"),
            lambda: auto_cli._parse_aware_time("invalid"),
            lambda: lifecycle_validation._timestamp("invalid", "capturedAt"),
            lambda: triggers._aware_timestamp("invalid", "task.trigger.startAt"),
            lambda: state._timestamp_or_none("invalid", "lastAttemptAt"),
            lambda: switch_override._parse_timestamp("invalid", "scheduledAt"),
            lambda: AccentProfile("day", "invalid", False, 0xC4744DA9, "26200"),
            lambda: HealthReport("invalid", ()),
            lambda: LogEvent(
                "invalid",
                "INFO",
                "auto.applied",
                "success",
                "auto",
            ),
        )
        for invoke in cases:
            with self.subTest(invoke=invoke), self.assertRaises(ValueError):
                invoke()

    def test_runtime_retention_skips_invalid_primary_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            parsed = runtime_retention._timestamp(
                directory,
                {
                    "completedAt": "invalid",
                    "preparedAt": UTC_TIMESTAMP,
                },
            )

        self.assertEqual(parsed.utcoffset(), timedelta(0))


if __name__ == "__main__":
    unittest.main()
