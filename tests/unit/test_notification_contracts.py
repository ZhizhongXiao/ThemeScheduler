from __future__ import annotations

import unittest

from theme_scheduler.notification_contracts import (
    APP_USER_MODEL_ID,
    NOTIFICATION_TITLE,
    NotificationCategory,
    NotificationDelivery,
    NotificationDeliveryResult,
    NotificationRequest,
)


class NotificationRequestTests(unittest.TestCase):
    def test_request_uses_fixed_identity_and_privacy_bounded_fields(
        self,
    ) -> None:
        request = NotificationRequest(
            NotificationCategory.ERROR,
            "auto.data-untrusted",
            "Configuration data could not be verified.",
            error_code="data.untrusted",
            correlation_id="accent-20260726T010203-12345678",
        )

        payload = request.as_dict()

        self.assertEqual(payload["appUserModelId"], APP_USER_MODEL_ID)
        self.assertEqual(payload["title"], NOTIFICATION_TITLE)
        self.assertEqual(payload["category"], "error")
        self.assertNotIn("path", payload)

    def test_request_rejects_unknown_tokens_and_control_characters(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "event"):
            NotificationRequest(
                NotificationCategory.ERROR,
                "Auto Failure",
                "Failure.",
            )
        with self.assertRaisesRegex(ValueError, "control"):
            NotificationRequest(
                NotificationCategory.STATUS,
                "control.paused",
                "Paused.\nUnexpected second line.",
            )


class NotificationDeliveryTests(unittest.TestCase):
    def test_failed_delivery_can_record_log_fallback(self) -> None:
        outcome = NotificationDelivery(
            NotificationDeliveryResult.FAILED,
            True,
            "Notification failed and the fallback event was logged.",
        )

        self.assertTrue(outcome.as_dict()["fallbackLogged"])

    def test_success_cannot_claim_log_fallback(self) -> None:
        with self.assertRaisesRegex(ValueError, "Only failed"):
            NotificationDelivery(
                NotificationDeliveryResult.SENT,
                True,
                "Invalid.",
            )


if __name__ == "__main__":
    unittest.main()
