"""Per-module coverage baseline and no-regression guard."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGING_ROOT = PROJECT_ROOT / "packaging"
if str(PACKAGING_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGING_ROOT))

from release_tools import capture_source_manifest  # noqa: E402

COVERAGE_JSON = PROJECT_ROOT / "artifacts" / "quality" / "coverage.json"
COVERAGE_RUN_METADATA = PROJECT_ROOT / "artifacts" / "quality" / "coverage-run.json"
BASELINE_PATH = PROJECT_ROOT / "tests" / "coverage_baseline.json"
BASELINE_KIND = "themescheduler.coverage-baseline"
BASELINE_SCHEMA_VERSION = 1
RATE_TOLERANCE = 0.05


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def _integer(summary: Mapping[str, Any], key: str) -> int:
    value = summary.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Coverage summary field {key} is invalid.")
    return value


def _rate(covered: int, total: int) -> float:
    return 100.0 if total == 0 else round(covered * 100.0 / total, 4)


def _summary(summary: Mapping[str, Any]) -> dict[str, int | float]:
    statements = _integer(summary, "num_statements")
    covered_lines = _integer(summary, "covered_lines")
    branches = _integer(summary, "num_branches")
    covered_branches = _integer(summary, "covered_branches")
    if covered_lines > statements or covered_branches > branches:
        raise ValueError("Coverage summary counts are inconsistent.")
    return {
        "statements": statements,
        "coveredLines": covered_lines,
        "lineRate": _rate(covered_lines, statements),
        "branches": branches,
        "coveredBranches": covered_branches,
        "branchRate": _rate(covered_branches, branches),
    }


def snapshot_from_report(report: Mapping[str, Any]) -> dict[str, Any]:
    meta = report.get("meta")
    files = report.get("files")
    totals = report.get("totals")
    if not isinstance(meta, dict) or not isinstance(files, dict):
        raise ValueError("Coverage report metadata or files are invalid.")
    if not isinstance(totals, dict):
        raise ValueError("Coverage report totals are invalid.")
    if meta.get("branch_coverage") is not True:
        raise ValueError("Coverage report must include branch coverage.")

    normalized: dict[str, dict[str, int | float]] = {}
    for raw_path, raw_file in sorted(files.items()):
        if not isinstance(raw_path, str) or not isinstance(raw_file, dict):
            raise ValueError("Coverage file entry is invalid.")
        summary = raw_file.get("summary")
        if not isinstance(summary, dict):
            raise ValueError(f"Coverage summary is missing for {raw_path}.")
        path = raw_path.replace("\\", "/")
        normalized[path] = _summary(summary)

    return {
        "kind": BASELINE_KIND,
        "schemaVersion": BASELINE_SCHEMA_VERSION,
        "coverageVersion": meta.get("version"),
        "totals": _summary(totals),
        "files": normalized,
    }


def validate_successful_run(
    metadata: Mapping[str, Any],
    *,
    current_source_identity: str,
) -> None:
    if metadata.get("kind") != "themescheduler.coverage-run":
        raise ValueError("Coverage run metadata kind is invalid.")
    if metadata.get("schemaVersion") != 1:
        raise ValueError("Coverage run metadata schema is unsupported.")
    if metadata.get("success") is not True:
        raise ValueError("Coverage run did not complete with a passing test suite.")
    if metadata.get("sourceIdentity") != current_source_identity:
        raise ValueError(
            "Coverage report is stale relative to the current source tree."
        )


def compare_snapshots(
    baseline: Mapping[str, Any],
    current: Mapping[str, Any],
) -> list[str]:
    if baseline.get("kind") != BASELINE_KIND:
        raise ValueError("Coverage baseline kind is invalid.")
    if baseline.get("schemaVersion") != BASELINE_SCHEMA_VERSION:
        raise ValueError("Coverage baseline schema is unsupported.")
    baseline_files = baseline.get("files")
    current_files = current.get("files")
    baseline_totals = baseline.get("totals")
    current_totals = current.get("totals")
    if (
        not isinstance(baseline_files, dict)
        or not isinstance(current_files, dict)
        or not isinstance(baseline_totals, dict)
        or not isinstance(current_totals, dict)
    ):
        raise ValueError("Coverage baseline or current snapshot is incomplete.")

    failures: list[str] = []
    for metric in ("lineRate", "branchRate"):
        baseline_rate = float(baseline_totals[metric])
        current_rate = float(current_totals[metric])
        if current_rate + RATE_TOLERANCE < baseline_rate:
            failures.append(
                f"total {metric} regressed: {baseline_rate:.1f}% -> {current_rate:.1f}%"
            )

    for path, baseline_summary in baseline_files.items():
        current_summary = current_files.get(path)
        if current_summary is None:
            continue
        for metric in ("lineRate", "branchRate"):
            old_rate = float(baseline_summary[metric])
            new_rate = float(current_summary[metric])
            if new_rate + RATE_TOLERANCE < old_rate:
                failures.append(
                    f"{path} {metric} regressed: {old_rate:.1f}% -> {new_rate:.1f}%"
                )

    for path, summary in current_files.items():
        if path in baseline_files or path.endswith("/__init__.py"):
            continue
        if int(summary["statements"]) > 0 and int(summary["coveredLines"]) == 0:
            failures.append(f"new production module has 0% line coverage: {path}")
    return failures


def _validated_current_snapshot() -> dict[str, Any]:
    metadata = _load_object(COVERAGE_RUN_METADATA)
    current_identity = str(capture_source_manifest(PROJECT_ROOT)["sourceTreeSha256"])
    validate_successful_run(metadata, current_source_identity=current_identity)
    return snapshot_from_report(_load_object(COVERAGE_JSON))


def _freeze(confirm: bool) -> int:
    if not confirm:
        print(
            "error: baseline write requires --confirm-baseline-write.",
            file=sys.stderr,
        )
        return 3
    snapshot = _validated_current_snapshot()
    try:
        with BASELINE_PATH.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(snapshot, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
    except FileExistsError:
        print(f"error: refusing to overwrite {BASELINE_PATH}", file=sys.stderr)
        return 2
    print(f"coverage baseline={BASELINE_PATH.relative_to(PROJECT_ROOT)}")
    return 0


def _check() -> int:
    current = _validated_current_snapshot()
    baseline = _load_object(BASELINE_PATH)
    failures = compare_snapshots(baseline, current)
    if failures:
        for failure in failures:
            print(f"error: {failure}", file=sys.stderr)
        return 1
    print("coverage guard=OK")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="themescheduler-coverage-guard")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--confirm-baseline-write", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "freeze":
            return _freeze(args.confirm_baseline_write)
        return _check()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
