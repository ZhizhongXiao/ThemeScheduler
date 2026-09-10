from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from theme_scheduler.cli.preview import main


class PreviewCliTests(unittest.TestCase):
    def test_workbench_is_explicitly_write_free(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("theme_scheduler.gui.launch_gui", return_value=11) as launch,
        ):
            data_root = Path(directory) / "data"
            result = main(
                [
                    "workbench",
                    "--data-root",
                    str(data_root),
                    "--allow-system-reads",
                ]
            )
            self.assertTrue((data_root / "config.json").is_file())
            self.assertTrue((data_root / "state.json").is_file())
            self.assertTrue((data_root / "profiles" / "day.json").is_file())
            self.assertTrue((data_root / "profiles" / "night.json").is_file())

        self.assertEqual(result, 11)
        launch.assert_called_once_with(
            data_root=data_root,
            installed=False,
            executable=None,
            allow_live_writes=False,
            allow_system_reads=True,
        )

    def test_setup_builds_the_requested_safe_runtime(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "theme_scheduler.setup_gui.launch_setup_preview",
                return_value=12,
            ) as launch,
        ):
            log_root = Path(directory) / "logs"
            result = main(
                [
                    "setup",
                    "--operation",
                    "upgrade",
                    "--result",
                    "partial",
                    "--log-root",
                    str(log_root),
                ]
            )

        self.assertEqual(result, 12)
        api = launch.call_args.args[0]
        self.assertTrue(api.runtime.preview_mode)
        self.assertEqual(api.runtime.operation, "upgrade")
        self.assertEqual(api.runtime.result, "partial")
        self.assertEqual(api._log_root, log_root.resolve())

    def test_uninstall_builds_the_requested_safe_runtime(self) -> None:
        with patch(
            "theme_scheduler.uninstall_gui.launch_uninstall_window",
            return_value=13,
        ) as launch:
            result = main(["uninstall", "--result", "partial"])

        self.assertEqual(result, 13)
        api = launch.call_args.args[0]
        self.assertTrue(api.runtime.preview_mode)
        self.assertEqual(api.runtime.result, "partial")
        self.assertEqual(launch.call_args.kwargs, {"preview": True})


if __name__ == "__main__":
    unittest.main()
