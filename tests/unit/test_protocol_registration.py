from __future__ import annotations

import unittest
from pathlib import Path

from theme_scheduler.protocol_registration import (
    PROTOCOL_ARGUMENT,
    ProtocolRegistration,
    RegistryTreeBackup,
)

EXECUTABLE = Path(
    r"C:\Users\Example\AppData\Local\Programs"
    r"\ThemeScheduler\app\ThemeScheduler.exe"
)


class ProtocolRegistrationTests(unittest.TestCase):
    def test_freezes_scheme_icon_and_exact_safe_command(self) -> None:
        registration = ProtocolRegistration(EXECUTABLE)

        self.assertEqual(registration.scheme, "themescheduler-action")
        self.assertEqual(
            registration.command,
            f'"{EXECUTABLE}" {PROTOCOL_ARGUMENT} "%1"',
        )
        self.assertEqual(registration.icon, f'"{EXECUTABLE}",0')

    def test_rejects_wrong_executable_and_scheme(self) -> None:
        with self.assertRaises(ValueError):
            ProtocolRegistration(Path(r"C:\Tools\python.exe"))
        with self.assertRaises(ValueError):
            ProtocolRegistration(EXECUTABLE, scheme="https")

    def test_tree_backup_requires_root_sorted_keys_and_unique_values(
        self,
    ) -> None:
        backup = RegistryTreeBackup(
            ("", "shell", r"shell\open"),
            (
                ("", "", "root", 1),
                (r"shell\open", "", "open", 1),
            ),
        )
        self.assertEqual(backup.keys[0], "")
        with self.assertRaises(ValueError):
            RegistryTreeBackup(("shell", ""), ())
        with self.assertRaises(ValueError):
            RegistryTreeBackup(
                ("",),
                (("", "Name", "a", 1), ("", "name", "b", 1)),
            )


if __name__ == "__main__":
    unittest.main()
