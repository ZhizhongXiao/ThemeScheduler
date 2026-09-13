from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from theme_scheduler.cli.data import main as data_cli_main


class DataCliTests(unittest.TestCase):
    def test_initialize_without_confirmation_creates_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "data"
            with redirect_stderr(StringIO()):
                exit_code = data_cli_main(["initialize", "--data-root", str(root)])
            self.assertEqual(exit_code, 3)
            self.assertFalse(root.exists())

    def test_initialize_validate_and_log_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "data"
            with redirect_stdout(StringIO()):
                initialize_code = data_cli_main(
                    [
                        "initialize",
                        "--data-root",
                        str(root),
                        "--confirm-data-write",
                    ]
                )
            self.assertEqual(initialize_code, 0)

            output = StringIO()
            with redirect_stdout(output):
                validate_code = data_cli_main(["validate", "--data-root", str(root)])
            self.assertEqual(validate_code, 0)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["valid"])
            self.assertFalse(payload["windowsChanged"])
            self.assertEqual(payload["profiles"]["day"]["status"], "absent")
            self.assertEqual(payload["installBackup"]["status"], "absent")

            with redirect_stdout(StringIO()):
                log_code = data_cli_main(
                    [
                        "log-smoke",
                        "--data-root",
                        str(root),
                        "--confirm-data-write",
                    ]
                )
            self.assertEqual(log_code, 0)
            self.assertTrue((root / "logs" / "events.jsonl").is_file())

    def test_repeat_initialize_and_corrupt_config_do_not_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "data"
            with redirect_stdout(StringIO()):
                self.assertEqual(
                    data_cli_main(
                        [
                            "initialize",
                            "--data-root",
                            str(root),
                            "--confirm-data-write",
                        ]
                    ),
                    0,
                )
            with redirect_stderr(StringIO()):
                self.assertEqual(
                    data_cli_main(
                        [
                            "initialize",
                            "--data-root",
                            str(root),
                            "--confirm-data-write",
                        ]
                    ),
                    2,
                )
            config_path = root / "config.json"
            config_path.write_text('{"kind":', encoding="utf-8")
            with redirect_stderr(StringIO()):
                self.assertEqual(
                    data_cli_main(["validate", "--data-root", str(root)]), 2
                )
            self.assertEqual(config_path.read_text(encoding="utf-8"), '{"kind":')

    def test_log_smoke_without_confirmation_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "data"
            with redirect_stderr(StringIO()):
                exit_code = data_cli_main(["log-smoke", "--data-root", str(root)])
            self.assertEqual(exit_code, 3)
            self.assertFalse(root.exists())

    def test_initialize_failure_cleans_documents_created_by_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "data"
            with (
                patch(
                    "theme_scheduler.cli.data.StateStore.initialize",
                    side_effect=OSError("simulated state failure"),
                ),
                redirect_stderr(StringIO()),
            ):
                exit_code = data_cli_main(
                    [
                        "initialize",
                        "--data-root",
                        str(root),
                        "--confirm-data-write",
                    ]
                )

            self.assertEqual(exit_code, 2)
            self.assertFalse((root / "config.json").exists())
            self.assertFalse((root / "state.json").exists())


if __name__ == "__main__":
    unittest.main()
