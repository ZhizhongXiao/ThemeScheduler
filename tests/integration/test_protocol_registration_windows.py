from __future__ import annotations

import unittest
from pathlib import Path

from tests.integration.test_system_integration_windows import FakeWinReg
from theme_scheduler.protocol_registration import (
    ProtocolRegistration,
    RegistryTreeBackup,
)
from theme_scheduler.protocol_registration_windows import (
    PROTOCOL_KEY,
    WindowsNotificationProtocolBackend,
)

EXECUTABLE = Path(
    r"C:\Users\Example\AppData\Local\Programs"
    r"\ThemeScheduler\app\ThemeScheduler.exe"
)


class WindowsProtocolRegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = FakeWinReg()
        self.backend = WindowsNotificationProtocolBackend(self.registry)
        self.registration = ProtocolRegistration(EXECUTABLE)

    def test_write_read_capture_delete_and_restore_exact_tree(self) -> None:
        prior = RegistryTreeBackup(
            ("", "foreign"),
            (
                ("", "", "prior", self.registry.REG_SZ),
                (
                    "foreign",
                    "Binary",
                    b"\x01\x02",
                    self.registry.REG_BINARY,
                ),
            ),
        )
        self.backend.restore(prior)
        self.assertEqual(self.backend.capture(), prior)

        self.backend.write(self.registration)

        self.assertEqual(self.backend.read(), self.registration)
        self.assertEqual(
            set(self.backend.capture().keys),
            {
                "",
                "DefaultIcon",
                "shell",
                r"shell\open",
                r"shell\open\command",
            },
        )
        self.backend.restore(prior)
        self.assertEqual(self.backend.capture(), prior)
        self.backend.restore(None)
        self.assertIsNone(self.backend.capture())
        self.assertNotIn(PROTOCOL_KEY, self.registry.keys)

    def test_extra_value_or_wrong_command_is_drift(self) -> None:
        self.backend.write(self.registration)
        self.registry.keys[PROTOCOL_KEY]["Unexpected"] = (
            "value",
            self.registry.REG_SZ,
        )
        self.assertIsNone(self.backend.read())

        self.backend.write(self.registration)
        command_key = PROTOCOL_KEY + r"\shell\open\command"
        self.registry.keys[command_key][""] = (
            '"C:\\Tools\\other.exe" "%1"',
            self.registry.REG_SZ,
        )
        self.assertIsNone(self.backend.read())


if __name__ == "__main__":
    unittest.main()
