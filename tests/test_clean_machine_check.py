from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "tools" / "clean_machine_check.ps1"
CHECKLIST = PROJECT_ROOT / "docs" / "CLEAN_MACHINE_ACCEPTANCE.md"


class CleanMachineCheckTests(unittest.TestCase):
    def test_script_is_read_only_product_acceptance_tool(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        for phase in (
            "Release",
            "PreInstall",
            "PostInstall",
            "PostBoundary",
            "PreUpgrade",
            "PostUpgrade",
            "PostUninstall",
        ):
            with self.subTest(phase=phase):
                self.assertIn(f'"{phase}"', source)
        for forbidden in (
            "Start-Process",
            "Set-ItemProperty",
            "New-ItemProperty",
            "Remove-Item",
            "Register-ScheduledTask",
            "Unregister-ScheduledTask",
            "Set-ScheduledTask",
            "Disable-ScheduledTask",
            "Enable-ScheduledTask",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertIn("Refusing to overwrite existing evidence", source)
        self.assertIn("themescheduler.clean-machine-check", source)

    def test_checklist_keeps_static_and_manual_evidence_separate(self) -> None:
        text = CHECKLIST.read_text(encoding="utf-8")

        self.assertIn("0.1.1", text)
        self.assertIn("0.1.2", text)
        self.assertIn("没有 Python", text)
        self.assertIn("PostBoundary", text)
        self.assertIn("PostUninstall", text)
        self.assertIn("不能替代", text)
        self.assertIn("三按钮通知", text)
        self.assertIn("恢复安装前外观", text)

    def test_script_defaults_to_current_release_version(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn('[string]$ExpectedVersion = "0.1.3"', source)

    def test_script_requires_release_hash_instead_of_freezing_an_old_one(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("ExpectedSetupSha256 is required", source)
        self.assertNotIn(
            "c8d9f55c93908a17b38e1c9cd98b6b6efd23776ad4d9583b5fe16aaa5c00645c",
            source,
        )

    def test_powershell_source_parses_on_windows(self) -> None:
        if os.name != "nt":
            return
        escaped = str(SCRIPT).replace("'", "''")
        command = (
            "$tokens=$null;$errors=$null;"
            "[void][System.Management.Automation.Language.Parser]::ParseFile("
            f"'{escaped}',[ref]$tokens,[ref]$errors);"
            "if($errors.Count){$errors|%{$_.Message};exit 1}"
        )
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)


if __name__ == "__main__":
    unittest.main()
