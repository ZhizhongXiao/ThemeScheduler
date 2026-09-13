from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"

from theme_scheduler.notification_contracts import (
    APP_USER_MODEL_ID,
)
from theme_scheduler.windows_identity import (
    WindowsIdentityError,
    configure_current_process_identity,
    read_current_process_identity,
)


class FakeIdentityApi:
    def __init__(
        self,
        *,
        readback: str = APP_USER_MODEL_ID,
        set_error: Exception | None = None,
    ) -> None:
        self.readback = readback
        self.set_error = set_error
        self.set_values: list[str] = []

    def set_current(self, app_user_model_id: str) -> None:
        self.set_values.append(app_user_model_id)
        if self.set_error is not None:
            raise self.set_error

    def get_current(self) -> str:
        return self.readback


class WindowsProcessIdentityTests(unittest.TestCase):
    def test_read_is_non_mutating_and_requires_frozen_identity(self) -> None:
        api = FakeIdentityApi(readback=APP_USER_MODEL_ID)

        self.assertEqual(read_current_process_identity(api), APP_USER_MODEL_ID)
        self.assertEqual(api.set_values, [])

    def test_frozen_identity_is_set_and_read_back(self) -> None:
        api = FakeIdentityApi()

        actual = configure_current_process_identity(api)

        self.assertEqual(actual, APP_USER_MODEL_ID)
        self.assertEqual(api.set_values, [APP_USER_MODEL_ID])

    def test_readback_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(WindowsIdentityError, "did not match"):
            configure_current_process_identity(
                FakeIdentityApi(readback="Foreign.Product")
            )

    def test_set_failure_is_not_hidden(self) -> None:
        with self.assertRaisesRegex(OSError, "blocked"):
            configure_current_process_identity(
                FakeIdentityApi(set_error=OSError("blocked"))
            )

    @unittest.skipUnless(
        os.name == "nt",
        "The real process identity API requires Windows.",
    )
    def test_real_windows_identity_round_trip_in_child_process(
        self,
    ) -> None:
        code = (
            "from theme_scheduler.windows_identity import "
            "configure_current_process_identity; "
            "print(configure_current_process_identity())"
        )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(SOURCE_ROOT)

        completed = subprocess.run(
            [sys.executable, "-c", code],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=30,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), APP_USER_MODEL_ID)


if __name__ == "__main__":
    unittest.main()
