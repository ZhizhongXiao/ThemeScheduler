from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from theme_scheduler.notification_protocol import (
    NotificationAction,
    parse_action_uri,
)
from theme_scheduler.scheduled_notifications import (
    PrepareSwitchNotification,
    error_notification,
    success_notification,
)
from theme_scheduler.switch_override import PendingSwitch

UTC8 = timezone(timedelta(hours=8))
TOKEN = "0123456789abcdef0123456789abcdef"


class ScheduledNotificationTests(unittest.TestCase):
    def _pending(self, *, next_minutes: int = 720) -> PendingSwitch:
        scheduled = datetime(2026, 7, 26, 20, 0, tzinfo=UTC8)
        return PendingSwitch.create(
            target_profile="night",
            scheduled_at=scheduled,
            next_fixed_at=scheduled + timedelta(minutes=next_minutes),
            now=scheduled - timedelta(minutes=5),
        ).arm(now=scheduled - timedelta(minutes=5), token=TOKEN)

    def test_prepare_notification_contains_three_safe_protocol_buttons(
        self,
    ) -> None:
        request = PrepareSwitchNotification.from_pending(self._pending())
        payload = request.as_dict()

        self.assertEqual(
            [item["content"] for item in payload["buttons"]],
            ["跳过本次", "延后 30 分钟", "确认"],
        )
        parsed = [parse_action_uri(item["arguments"]) for item in payload["buttons"]]
        self.assertEqual(
            [action for action, _token in parsed],
            [
                NotificationAction.SKIP,
                NotificationAction.DELAY,
                NotificationAction.CONFIRM,
            ],
        )
        self.assertTrue(all(token == TOKEN for _action, token in parsed))

    def test_delay_button_is_omitted_when_next_boundary_is_too_close(
        self,
    ) -> None:
        request = PrepareSwitchNotification.from_pending(self._pending(next_minutes=30))

        self.assertEqual(
            [button.action for button in request.buttons],
            [NotificationAction.SKIP, NotificationAction.CONFIRM],
        )

    def test_success_notification_is_noninteractive_and_profile_bounded(
        self,
    ) -> None:
        payload = success_notification("day").as_dict()
        self.assertEqual(payload["event"], "schedule.applied")
        self.assertIn("昼间", payload["body"])
        with self.assertRaises(ValueError):
            success_notification("other")

    def test_error_notification_is_generic_and_bounded(self) -> None:
        payload = error_notification("partial-failure").as_dict()

        self.assertEqual(payload["event"], "auto.failed")
        self.assertEqual(payload["errorCode"], "partial-failure")
        self.assertNotIn("\\", payload["body"])


if __name__ == "__main__":
    unittest.main()
