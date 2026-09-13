from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import Mock, patch

from theme_scheduler.cli.app import main as app_main
from theme_scheduler.cli.auto import run_installed_auto
from theme_scheduler.core import AutoExitCode


class FormalAppEntryTests(unittest.TestCase):
    def test_no_arguments_opens_installed_gui(self) -> None:
        gui = Mock(return_value=0)

        self.assertEqual(app_main([], gui_handler=gui), 0)
        gui.assert_called_once_with(
            data_root=None,
            installed=True,
            initial_section=None,
        )

    def test_maintenance_focuses_same_installed_gui(self) -> None:
        gui = Mock(return_value=0)

        self.assertEqual(
            app_main(["maintenance"], gui_handler=gui),
            0,
        )
        gui.assert_called_once_with(
            data_root=None,
            installed=True,
            initial_section="maintenance",
        )

    def test_auto_uses_only_auto_handler(self) -> None:
        auto = Mock(return_value=7)
        gui = Mock(return_value=0)

        self.assertEqual(
            app_main(["auto"], auto_handler=auto, gui_handler=gui),
            7,
        )
        auto.assert_called_once_with()
        gui.assert_not_called()

    def test_notification_action_routes_exact_uri_without_gui(self) -> None:
        handler = Mock(return_value=0)
        gui = Mock(return_value=0)
        uri = (
            "themescheduler-action://switch/?"
            "action=confirm&token=0123456789abcdef0123456789abcdef"
        )

        result = app_main(
            ["notification-action", uri],
            notification_action_handler=handler,
            gui_handler=gui,
        )

        self.assertEqual(result, 0)
        handler.assert_called_once_with(uri)
        gui.assert_not_called()

    def test_unknown_and_extra_arguments_fail_without_dispatch(self) -> None:
        auto = Mock(return_value=0)
        gui = Mock(return_value=0)
        error = io.StringIO()

        with redirect_stderr(error):
            unknown = app_main(
                ["unknown"],
                auto_handler=auto,
                gui_handler=gui,
            )
            extra = app_main(
                ["auto", "unexpected"],
                auto_handler=auto,
                gui_handler=gui,
            )
            explicit_gui = app_main(["gui"], gui_handler=gui)

        self.assertEqual((unknown, extra, explicit_gui), (2, 2, 2))
        self.assertIn("no arguments", error.getvalue())
        auto.assert_not_called()
        gui.assert_not_called()

    def test_installed_auto_uses_default_layout_and_actual_clock(self) -> None:
        outcome = Mock(exit_code=AutoExitCode.SUCCESS)
        coordinator = Mock()
        coordinator.run.return_value = outcome
        layout = Mock()

        with (
            patch(
                "theme_scheduler.cli.auto.UserDataLayout.default",
                return_value=layout,
            ),
            patch(
                "theme_scheduler.cli.auto.create_live_scheduled_coordinator",
                return_value=(coordinator, "mutex"),
            ) as create,
        ):
            result = run_installed_auto()

        self.assertEqual(result, int(AutoExitCode.SUCCESS))
        create.assert_called_once_with(layout)
        coordinator.run.assert_called_once_with()

    def test_installed_auto_returns_error_without_raising(self) -> None:
        error = io.StringIO()
        with (
            patch(
                "theme_scheduler.cli.auto.UserDataLayout.default",
                side_effect=OSError("missing local app data"),
            ),
            redirect_stderr(error),
        ):
            result = run_installed_auto()

        self.assertEqual(result, 2)
        self.assertIn("missing local app data", error.getvalue())

    def test_windowed_auto_error_does_not_require_stderr(self) -> None:
        with (
            patch(
                "theme_scheduler.cli.auto.UserDataLayout.default",
                side_effect=OSError("missing local app data"),
            ),
            patch("theme_scheduler.cli.auto.sys.stderr", None),
        ):
            result = run_installed_auto()

        self.assertEqual(result, 2)

    def test_windowed_usage_error_does_not_require_stderr(self) -> None:
        with patch("theme_scheduler.cli.app.sys.stderr", None):
            result = app_main(["unknown"])

        self.assertEqual(result, 2)


if __name__ == "__main__":
    unittest.main()
