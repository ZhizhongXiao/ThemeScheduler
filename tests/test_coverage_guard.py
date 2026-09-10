from __future__ import annotations

import unittest

from tools.coverage_guard import (
    BASELINE_KIND,
    BASELINE_SCHEMA_VERSION,
    compare_snapshots,
    snapshot_from_report,
    validate_successful_run,
)


def _summary(
    *,
    statements: int,
    covered_lines: int,
    branches: int,
    covered_branches: int,
) -> dict[str, int]:
    return {
        "num_statements": statements,
        "covered_lines": covered_lines,
        "num_branches": branches,
        "covered_branches": covered_branches,
    }


def _snapshot(
    files: dict[str, dict[str, int | float]],
    *,
    line_rate: float = 80.0,
    branch_rate: float = 70.0,
) -> dict[str, object]:
    return {
        "kind": BASELINE_KIND,
        "schemaVersion": BASELINE_SCHEMA_VERSION,
        "totals": {"lineRate": line_rate, "branchRate": branch_rate},
        "files": files,
    }


class CoverageGuardTests(unittest.TestCase):
    def test_report_snapshot_normalizes_paths_and_separates_rates(self) -> None:
        report = {
            "meta": {"branch_coverage": True, "version": "test"},
            "files": {
                "src\\theme_scheduler\\core.py": {
                    "summary": _summary(
                        statements=10,
                        covered_lines=8,
                        branches=4,
                        covered_branches=2,
                    )
                }
            },
            "totals": _summary(
                statements=10,
                covered_lines=8,
                branches=4,
                covered_branches=2,
            ),
        }

        snapshot = snapshot_from_report(report)

        self.assertEqual(snapshot["totals"]["lineRate"], 80.0)
        self.assertEqual(snapshot["totals"]["branchRate"], 50.0)
        self.assertIn("src/theme_scheduler/core.py", snapshot["files"])

    def test_compare_reports_total_module_and_new_zero_coverage_regressions(
        self,
    ) -> None:
        baseline = _snapshot(
            {
                "src/theme_scheduler/core.py": {
                    "statements": 10,
                    "coveredLines": 8,
                    "lineRate": 80.0,
                    "branches": 4,
                    "coveredBranches": 3,
                    "branchRate": 75.0,
                }
            }
        )
        current = _snapshot(
            {
                "src/theme_scheduler/core.py": {
                    "statements": 10,
                    "coveredLines": 7,
                    "lineRate": 70.0,
                    "branches": 4,
                    "coveredBranches": 2,
                    "branchRate": 50.0,
                },
                "src/theme_scheduler/new_module.py": {
                    "statements": 5,
                    "coveredLines": 0,
                    "lineRate": 0.0,
                    "branches": 0,
                    "coveredBranches": 0,
                    "branchRate": 100.0,
                },
            },
            line_rate=70.0,
            branch_rate=50.0,
        )

        failures = compare_snapshots(baseline, current)

        self.assertTrue(any("total lineRate regressed" in item for item in failures))
        self.assertTrue(any("core.py lineRate regressed" in item for item in failures))
        self.assertTrue(any("new production module" in item for item in failures))

    def test_success_metadata_must_match_current_source(self) -> None:
        metadata = {
            "kind": "themescheduler.coverage-run",
            "schemaVersion": 1,
            "success": True,
            "sourceIdentity": "expected",
        }
        validate_successful_run(metadata, current_source_identity="expected")
        with self.assertRaisesRegex(ValueError, "stale"):
            validate_successful_run(metadata, current_source_identity="changed")
        with self.assertRaisesRegex(ValueError, "did not complete"):
            validate_successful_run(
                {**metadata, "success": False},
                current_source_identity="expected",
            )

    def test_compare_allows_intentionally_removed_production_module(self) -> None:
        removed = {
            "src/theme_scheduler/retired.py": {
                "statements": 10,
                "coveredLines": 8,
                "lineRate": 80.0,
                "branches": 4,
                "coveredBranches": 3,
                "branchRate": 75.0,
            }
        }

        failures = compare_snapshots(_snapshot(removed), _snapshot({}))

        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
