from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]

from datetime import datetime, timedelta, timezone

from theme_scheduler.notification_contracts import (
    NotificationCategory,
    NotificationDeliveryResult,
    NotificationRequest,
)
from theme_scheduler.notifications_windows import (
    WindowsNotificationBackend,
    WindowsNotificationError,
)
from theme_scheduler.scheduled_notifications import (
    PrepareSwitchNotification,
)
from theme_scheduler.switch_override import PendingSwitch

BRIDGE = PROJECT_ROOT / "entrypoints" / "notification_bridge.ps1"
UTC8 = timezone(timedelta(hours=8))
TOKEN = "0123456789abcdef0123456789abcdef"


def completed(action: str, **fields) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps({"ok": True, "action": action, **fields}),
        stderr="",
    )


class WindowsNotificationBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = WindowsNotificationBackend(BRIDGE)

    @patch("theme_scheduler.notifications_windows.subprocess.run")
    def test_probe_is_read_only_and_hidden(self, run) -> None:
        run.return_value = completed("Probe", available=True, notificationSent=False)

        self.backend.probe()

        self.assertNotIn("-RequestPath", run.call_args.args[0])
        self.assertEqual(
            run.call_args.kwargs["creationflags"],
            subprocess.CREATE_NO_WINDOW,
        )

    @patch("theme_scheduler.notifications_windows.subprocess.run")
    def test_prepare_sends_only_bounded_bridge_fields(self, run) -> None:
        captured = {}

        def respond(command, **_kwargs):
            request_path = Path(command[command.index("-RequestPath") + 1])
            captured.update(json.loads(request_path.read_text(encoding="utf-8-sig")))
            return completed("Send", sent=True, notificationSent=True)

        run.side_effect = respond
        scheduled = datetime(2026, 7, 26, 20, 0, tzinfo=UTC8)
        pending = PendingSwitch.create(
            target_profile="night",
            scheduled_at=scheduled,
            next_fixed_at=scheduled + timedelta(hours=10),
            now=scheduled - timedelta(minutes=5),
        ).arm(now=scheduled - timedelta(minutes=5), token=TOKEN)

        delivery = self.backend.send_prepare(
            PrepareSwitchNotification.from_pending(pending)
        )

        self.assertIs(delivery.result, NotificationDeliveryResult.SENT)
        self.assertEqual(
            set(captured),
            {"appUserModelId", "title", "body", "buttons"},
        )
        self.assertEqual(len(captured["buttons"]), 3)
        self.assertNotIn("targetProfile", captured)
        self.assertNotIn("scheduledAt", captured)

    @patch("theme_scheduler.notifications_windows.subprocess.run")
    def test_status_has_no_buttons_and_failure_is_a_delivery_result(self, run) -> None:
        requests = []

        def respond(command, **_kwargs):
            request_path = Path(command[command.index("-RequestPath") + 1])
            requests.append(json.loads(request_path.read_text(encoding="utf-8-sig")))
            return completed("Send", sent=True, notificationSent=True)

        run.side_effect = respond
        request = NotificationRequest(
            NotificationCategory.STATUS,
            "schedule.applied",
            "Applied.",
        )
        sent = self.backend.send_status(request)
        self.assertIs(sent.result, NotificationDeliveryResult.SENT)
        self.assertEqual(requests[0]["buttons"], [])
        self.assertNotIn("launch", requests[0])

        run.side_effect = subprocess.TimeoutExpired([], 15)
        failed = self.backend.send_status(request)
        self.assertIs(failed.result, NotificationDeliveryResult.FAILED)
        self.assertFalse(failed.fallback_logged)

    @patch("theme_scheduler.notifications_windows.subprocess.run")
    def test_error_toast_launches_only_fixed_health_endpoint(self, run) -> None:
        captured = {}

        def respond(command, **_kwargs):
            request_path = Path(command[command.index("-RequestPath") + 1])
            captured.update(json.loads(request_path.read_text(encoding="utf-8-sig")))
            return completed("Send", sent=True, notificationSent=True)

        run.side_effect = respond
        request = NotificationRequest(
            NotificationCategory.ERROR,
            "auto.failed",
            "Needs attention.",
        )

        delivery = self.backend.send_status(request)

        self.assertIs(delivery.result, NotificationDeliveryResult.SENT)
        self.assertEqual(
            captured["launch"],
            "themescheduler-action://health/",
        )
        self.assertEqual(captured["buttons"], [])

    @patch("theme_scheduler.notifications_windows.subprocess.run")
    def test_probe_rejects_malformed_bridge_result(self, run) -> None:
        run.return_value = completed("Probe", available=False, notificationSent=False)
        with self.assertRaises(WindowsNotificationError):
            self.backend.probe()

    @patch("theme_scheduler.notifications_windows.subprocess.run")
    def test_delivery_failure_detail_is_bounded_to_one_line(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=42,
            stdout="",
            stderr="first line\r\nsecond line\twith detail",
        )
        request = NotificationRequest(
            NotificationCategory.ERROR,
            "auto.failed",
            "Needs attention.",
        )

        delivery = self.backend.send_status(request)

        self.assertIs(delivery.result, NotificationDeliveryResult.FAILED)
        self.assertIn(
            "first line second line with detail",
            delivery.message,
        )
        self.assertFalse(any(ord(character) < 32 for character in delivery.message))

    def test_bridge_script_has_fixed_identity_protocol_and_no_com_activator(
        self,
    ) -> None:
        script = BRIDGE.read_text(encoding="utf-8")
        self.assertIn("ThemeScheduler.ThemeScheduler", script)
        self.assertIn("themescheduler-action", script)
        self.assertIn("themescheduler-action://health/", script)
        self.assertIn('activationType="protocol" launch="', script)
        self.assertIn('activationType="protocol"', script)
        self.assertIn("CreateToastNotifier", script)
        self.assertNotIn("CoRegisterClassObject", script)


if __name__ == "__main__":
    unittest.main()
