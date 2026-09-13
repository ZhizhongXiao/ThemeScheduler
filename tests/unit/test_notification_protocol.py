from __future__ import annotations

import unittest

from theme_scheduler.notification_protocol import (
    NOTIFICATION_HEALTH_URI,
    NotificationAction,
    NotificationProtocolError,
    build_action_uri,
    is_health_activation_uri,
    new_action_token,
    parse_action_uri,
)


class NotificationProtocolTests(unittest.TestCase):
    def test_health_activation_is_one_fixed_parameter_free_uri(self) -> None:
        self.assertEqual(
            NOTIFICATION_HEALTH_URI,
            "themescheduler-action://health/",
        )
        self.assertTrue(is_health_activation_uri(NOTIFICATION_HEALTH_URI))
        for uri in (
            "themescheduler-action://health",
            "themescheduler-action://health/?repair=true",
            "themescheduler-action://health/#report",
            "THEMESCHEDULER-ACTION://health/",
            "themescheduler-action://switch/",
        ):
            with self.subTest(uri=uri):
                self.assertFalse(is_health_activation_uri(uri))

    def test_all_actions_round_trip_in_canonical_uri(self) -> None:
        token = "0123456789abcdef0123456789abcdef"
        for action in NotificationAction:
            with self.subTest(action=action):
                uri = build_action_uri(action, token)
                self.assertEqual(parse_action_uri(uri), (action, token))
                self.assertNotIn("profile", uri)
                self.assertNotIn("scheduled", uri)

    def test_generated_token_has_128_bits_in_lowercase_hex(self) -> None:
        first = new_action_token()
        second = new_action_token()
        self.assertRegex(first, r"^[0-9a-f]{32}$")
        self.assertNotEqual(first, second)

    def test_parser_rejects_surplus_duplicate_and_noncanonical_data(self) -> None:
        token = "0123456789abcdef0123456789abcdef"
        invalid = (
            f"themescheduler-action://other?action=skip&token={token}",
            f"themescheduler-action://switch/path?action=skip&token={token}",
            f"themescheduler-action://switch?action=skip&token={token}",
            f"themescheduler-action://switch?action=skip&token={token}&x=1",
            (
                "themescheduler-action://switch?"
                f"action=skip&action=confirm&token={token}"
            ),
            f"themescheduler-action://switch?token={token}&action=skip",
            f"THEMESCHEDULER-ACTION://switch?action=skip&token={token}",
            f"themescheduler-action://switch?action=skip&token={token}#x",
            (f"themescheduler-action://switch:invalid?action=skip&token={token}"),
        )
        for uri in invalid:
            with (
                self.subTest(uri=uri),
                self.assertRaises(NotificationProtocolError),
            ):
                parse_action_uri(uri)

    def test_parser_rejects_unknown_action_and_invalid_token(self) -> None:
        with self.assertRaisesRegex(NotificationProtocolError, "unsupported"):
            parse_action_uri(
                "themescheduler-action://switch/?"
                "action=apply&token=0123456789abcdef0123456789abcdef"
            )
        with self.assertRaisesRegex(NotificationProtocolError, "lowercase"):
            parse_action_uri(
                "themescheduler-action://switch/?"
                "action=skip&token=0123456789ABCDEF0123456789ABCDEF"
            )


if __name__ == "__main__":
    unittest.main()
