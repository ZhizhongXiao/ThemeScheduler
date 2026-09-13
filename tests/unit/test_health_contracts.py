from __future__ import annotations

import unittest

from theme_scheduler.health_contracts import (
    HealthCategory,
    HealthCheck,
    HealthReport,
    HealthStatus,
)


class HealthCheckTests(unittest.TestCase):
    def test_repairable_check_requires_explicit_action(self) -> None:
        with self.assertRaisesRegex(ValueError, "repair action"):
            HealthCheck(
                "task.definition",
                HealthCategory.TASK,
                HealthStatus.REPAIRABLE,
                "The scheduled task differs from the expected definition.",
            )

    def test_nonrepairable_check_cannot_expose_action(self) -> None:
        with self.assertRaisesRegex(ValueError, "Only repairable"):
            HealthCheck(
                "data.config",
                HealthCategory.CONFIGURATION,
                HealthStatus.ACTION_REQUIRED,
                "Configuration data is untrusted.",
                repair_action="data.replace",
            )

    def test_identifiers_are_strict(self) -> None:
        with self.assertRaisesRegex(ValueError, "check_id"):
            HealthCheck(
                "Task Definition",
                HealthCategory.TASK,
                HealthStatus.HEALTHY,
                "Valid.",
            )


class HealthReportTests(unittest.TestCase):
    def test_report_accepts_utc_z_suffix(self) -> None:
        report = HealthReport(
            "2026-08-09T12:00:00Z",
            (
                HealthCheck(
                    "data.config",
                    HealthCategory.CONFIGURATION,
                    HealthStatus.HEALTHY,
                    "Configuration is valid.",
                ),
            ),
        )

        self.assertEqual(report.as_dict()["capturedAt"], "2026-08-09T12:00:00Z")

    def test_report_uses_highest_priority_status_and_counts(self) -> None:
        report = HealthReport(
            "2026-07-26T00:10:00+08:00",
            (
                HealthCheck(
                    "data.config",
                    HealthCategory.CONFIGURATION,
                    HealthStatus.HEALTHY,
                    "Configuration is valid.",
                ),
                HealthCheck(
                    "task.definition",
                    HealthCategory.TASK,
                    HealthStatus.REPAIRABLE,
                    "The task can be restored.",
                    repair_action="task.repair",
                ),
                HealthCheck(
                    "notification.setting",
                    HealthCategory.NOTIFICATION,
                    HealthStatus.WARNING,
                    "Windows notifications may be disabled.",
                ),
            ),
        )

        payload = report.as_dict()

        self.assertEqual(report.status, HealthStatus.REPAIRABLE)
        self.assertEqual(payload["summary"]["healthy"], 1)
        self.assertEqual(payload["summary"]["warning"], 1)
        self.assertEqual(payload["summary"]["repairable"], 1)
        self.assertEqual(payload["summary"]["action-required"], 0)

    def test_action_required_overrides_repairable(self) -> None:
        report = HealthReport(
            "2026-07-26T00:10:00+08:00",
            (
                HealthCheck(
                    "task.definition",
                    HealthCategory.TASK,
                    HealthStatus.REPAIRABLE,
                    "The task can be restored.",
                    repair_action="task.repair",
                ),
                HealthCheck(
                    "data.state",
                    HealthCategory.DATA,
                    HealthStatus.ACTION_REQUIRED,
                    "State is untrusted.",
                ),
            ),
        )

        self.assertEqual(report.status, HealthStatus.ACTION_REQUIRED)

    def test_report_rejects_duplicate_checks_and_naive_time(self) -> None:
        check = HealthCheck(
            "data.config",
            HealthCategory.CONFIGURATION,
            HealthStatus.HEALTHY,
            "Configuration is valid.",
        )
        with self.assertRaisesRegex(ValueError, "unique"):
            HealthReport(
                "2026-07-26T00:10:00+08:00",
                (check, check),
            )
        with self.assertRaisesRegex(ValueError, "UTC offset"):
            HealthReport("2026-07-26T00:10:00", (check,))


if __name__ == "__main__":
    unittest.main()
