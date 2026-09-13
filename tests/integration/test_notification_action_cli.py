from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from theme_scheduler.cli.notification_action import (
    run_installed_notification_action,
)

HEALTH_URI = "themescheduler-action://health/"


class InstalledNotificationActionTests(unittest.TestCase):
    def test_health_activation_signals_existing_gui_without_scheduler(self) -> None:
        layout = Mock()
        layout.root = Path(r"C:\Users\Test\AppData\Local\ThemeScheduler")
        with (
            patch(
                "theme_scheduler.cli.notification_action.UserDataLayout.default",
                return_value=layout,
            ),
            patch(
                "theme_scheduler.cli.notification_action._installed_executable",
                return_value=Path(
                    r"C:\Users\Test\AppData\Local\Programs"
                    r"\ThemeScheduler\app\ThemeScheduler.exe"
                ),
            ),
            patch(
                "theme_scheduler.cli.notification_action.gui_activation_event_name",
                return_value=r"Local\ThemeScheduler.GuiActivation.test",
            ),
            patch(
                "theme_scheduler.cli.notification_action.signal_existing_gui",
                return_value=True,
            ) as signal,
            patch(
                "theme_scheduler.cli.notification_action.WindowsTaskSchedulerBackend"
            ) as scheduler,
        ):
            result = run_installed_notification_action(HEALTH_URI)

        self.assertEqual(result, 0)
        signal.assert_called_once_with(r"Local\ThemeScheduler.GuiActivation.test")
        scheduler.assert_not_called()

    def test_health_activation_opens_read_only_health_section_if_closed(
        self,
    ) -> None:
        layout = Mock()
        layout.root = Path(r"C:\Users\Test\AppData\Local\ThemeScheduler")
        launch = Mock(return_value=0)
        with (
            patch(
                "theme_scheduler.cli.notification_action.UserDataLayout.default",
                return_value=layout,
            ),
            patch(
                "theme_scheduler.cli.notification_action._installed_executable",
                return_value=Path(
                    r"C:\Users\Test\AppData\Local\Programs"
                    r"\ThemeScheduler\app\ThemeScheduler.exe"
                ),
            ),
            patch(
                "theme_scheduler.cli.notification_action.signal_existing_gui",
                return_value=False,
            ),
            patch("theme_scheduler.gui.launch_gui", launch),
        ):
            result = run_installed_notification_action(HEALTH_URI)

        self.assertEqual(result, 0)
        launch.assert_called_once_with(
            data_root=None,
            installed=True,
            initial_section="health",
        )


if __name__ == "__main__":
    unittest.main()
