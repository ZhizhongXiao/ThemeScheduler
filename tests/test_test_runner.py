from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools import test as test_runner
from tools.test import (
    GROUPS,
    build_suite,
    coverage_commands,
    release_quality_commands,
    resolve_patterns,
)


class LayeredTestRunnerTests(unittest.TestCase):
    def test_integration_full_and_release_select_complete_suite(self) -> None:
        self.assertEqual(
            resolve_patterns("integration", None),
            ("test_*.py",),
        )
        self.assertEqual(resolve_patterns("full", None), ("test_*.py",))
        self.assertEqual(resolve_patterns("release", None), ("test_*.py",))
        self.assertEqual(resolve_patterns("coverage", None), ("test_*.py",))

    def test_group_modes_are_explicit_and_unknown_groups_fail(self) -> None:
        self.assertEqual(
            resolve_patterns("quick", "deployment"),
            ("test_deployment.py",),
        )
        self.assertIn(
            "test_deployment.py",
            resolve_patterns("affected", "setup"),
        )
        self.assertEqual(
            resolve_patterns("quick", "stage10"),
            ("test_release_payload.py", "test_release_tools.py"),
        )
        self.assertEqual(
            resolve_patterns("quick", "stage11"),
            (
                "test_artifacts.py",
                "test_cold_archive.py",
                "test_test_runner.py",
            ),
        )
        self.assertEqual(
            resolve_patterns("quick", "stage14"),
            (
                "test_release_payload.py",
                "test_release_tools.py",
                "test_stage12_gui.py",
                "test_setup_gui.py",
                "test_uninstall_gui.py",
            ),
        )
        self.assertEqual(
            resolve_patterns("quick", "stage15"),
            (
                "test_lifecycle_package.py",
                "test_source_packages.py",
                "test_errors.py",
                "test_quality_contracts.py",
                "test_clean_machine_check.py",
                "test_lifecycle_contracts.py",
                "test_deployment.py",
                "test_auto_service.py",
                "test_gui.py",
            ),
        )
        with self.assertRaisesRegex(ValueError, "Unknown test group"):
            resolve_patterns("quick", "missing")
        with self.assertRaisesRegex(ValueError, "does not accept"):
            resolve_patterns("full", "setup")
        with self.assertRaisesRegex(ValueError, "does not accept"):
            resolve_patterns("coverage", "setup")

    def test_overlapping_patterns_are_deduplicated(self) -> None:
        suite = build_suite(("test_setup*.py", "test_setup_gui.py"))
        identities = [test.id() for test in suite]
        self.assertEqual(len(identities), len(set(identities)))
        self.assertGreater(len(identities), 0)

    def test_every_group_defines_quick_and_affected_patterns(self) -> None:
        for group, modes in GROUPS.items():
            with self.subTest(group=group):
                self.assertTrue(modes["quick"])
                self.assertTrue(modes["affected"])

    def test_release_quality_commands_cover_pyright_and_both_ruff_gates(
        self,
    ) -> None:
        commands = dict(release_quality_commands(Path(sys.executable)))

        self.assertEqual(set(commands), {"pyright", "ruff-lint", "ruff-format"})
        self.assertEqual(commands["pyright"][2:], ("pyright", "--outputjson"))
        self.assertIn("check", commands["ruff-lint"])
        self.assertIn("--output-format=json", commands["ruff-lint"])
        self.assertIn("format", commands["ruff-format"])
        self.assertIn("--check", commands["ruff-format"])
        self.assertIn("--no-cache", commands["ruff-lint"])
        self.assertIn("--no-cache", commands["ruff-format"])

    def test_coverage_commands_wrap_integration_without_release_gates(self) -> None:
        commands = coverage_commands(verbose=True)

        self.assertEqual(len(commands), 4)
        self.assertEqual(commands[0][2:], ("coverage", "erase"))
        self.assertEqual(commands[1][2:4], ("coverage", "run"))
        self.assertIn("integration", commands[1])
        self.assertIn("--no-report", commands[1])
        self.assertIn("--verbose", commands[1])
        self.assertEqual(commands[2][2:4], ("coverage", "json"))
        self.assertEqual(commands[3][2:], ("coverage", "report"))
        flattened = " ".join(part for command in commands for part in command)
        self.assertNotIn("pyright", flattened)
        self.assertNotIn("ruff", flattened)

    def test_coverage_mode_requires_virtual_environment(self) -> None:
        with (
            patch.object(test_runner, "_in_virtual_environment", return_value=False),
            patch.object(test_runner.subprocess, "run") as run_process,
            redirect_stderr(StringIO()),
        ):
            self.assertEqual(test_runner.run_coverage(verbose=False), 2)
        run_process.assert_not_called()

    def test_coverage_mode_erases_stale_json_and_propagates_test_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            quality_root = Path(temporary) / "quality"
            coverage_json = quality_root / "coverage.json"
            coverage_metadata = quality_root / "coverage-run.json"
            quality_root.mkdir()
            coverage_json.write_text("stale", encoding="utf-8")
            outcomes = [
                SimpleNamespace(returncode=0),
                SimpleNamespace(returncode=1),
                SimpleNamespace(returncode=0),
                SimpleNamespace(returncode=0),
            ]
            with (
                patch.object(test_runner, "_in_virtual_environment", return_value=True),
                patch.object(test_runner, "QUALITY_ROOT", quality_root),
                patch.object(test_runner, "COVERAGE_JSON", coverage_json),
                patch.object(
                    test_runner,
                    "COVERAGE_RUN_METADATA",
                    coverage_metadata,
                ),
                patch.object(
                    test_runner.subprocess,
                    "run",
                    side_effect=outcomes,
                ) as run_process,
            ):
                self.assertEqual(test_runner.run_coverage(verbose=False), 1)

            self.assertFalse(coverage_json.exists())
            self.assertEqual(run_process.call_count, 4)
            metadata = test_runner.json.loads(
                coverage_metadata.read_text(encoding="utf-8")
            )
            self.assertFalse(metadata["success"])
            self.assertEqual(metadata["testsReturnCode"], 1)


if __name__ == "__main__":
    unittest.main()
