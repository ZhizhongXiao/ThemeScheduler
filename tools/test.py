"""Layered, quiet-by-default test entry point.

Examples:
    python tools/test.py quick setup
    python tools/test.py affected setup
    python tools/test.py integration
    python tools/test.py release
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import unittest
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = PROJECT_ROOT / "tests"
TEST_CATEGORIES = ("unit", "integration", "release")
TEST_CATEGORY_ROOTS = tuple(TEST_ROOT / name for name in TEST_CATEGORIES)
REPORT_ROOT = PROJECT_ROOT / "artifacts" / "test-reports"
QUALITY_ROOT = PROJECT_ROOT / "artifacts" / "quality"
COVERAGE_JSON = QUALITY_ROOT / "coverage.json"
COVERAGE_RUN_METADATA = QUALITY_ROOT / "coverage-run.json"
PACKAGING_ROOT = PROJECT_ROOT / "packaging"
if str(PACKAGING_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGING_ROOT))

from release_tools import (  # noqa: E402
    TEST_REPORT_SCHEMA_VERSION,
    capture_source_manifest,
    release_quality_commands,
)

GROUPS: dict[str, dict[str, tuple[str, ...]]] = {
    "setup": {
        "quick": ("test_setup*.py",),
        "affected": (
            "test_setup*.py",
            "test_deployment.py",
            "test_lifecycle_contracts.py",
            "test_system_integration*.py",
            "test_scheduler*.py",
            "test_data_contracts.py",
            "test_gui.py",
            "test_auto*.py",
            "test_uninstall_contracts.py",
        ),
    },
    "deployment": {
        "quick": ("test_deployment.py",),
        "affected": (
            "test_deployment.py",
            "test_lifecycle_contracts.py",
            "test_setup*.py",
            "test_release_payload.py",
        ),
    },
    "uninstall": {
        "quick": ("test_uninstall*.py",),
        "affected": (
            "test_uninstall*.py",
            "test_lifecycle_contracts.py",
            "test_system_integration*.py",
            "test_maintenance*.py",
        ),
    },
    "gui": {
        "quick": (
            "test_stage12_gui.py",
            "test_gui.py",
        ),
        "affected": (
            "test_stage12_gui.py",
            "test_gui.py",
            "test_setup_gui.py",
            "test_uninstall_gui.py",
            "test_configuration_service.py",
            "test_control*.py",
            "test_maintenance*.py",
            "test_scheduler*.py",
        ),
    },
    "scheduler": {
        "quick": ("test_scheduler*.py",),
        "affected": (
            "test_scheduler*.py",
            "test_configuration_service.py",
            "test_system_integration*.py",
            "test_setup*.py",
        ),
    },
    "auto": {
        "quick": ("test_auto*.py",),
        "affected": (
            "test_auto*.py",
            "test_core.py",
            "test_control*.py",
            "test_accent_profile.py",
            "test_persistence.py",
        ),
    },
    "theme": {
        "quick": ("test_appearance.py", "test_accent*.py"),
        "affected": (
            "test_appearance.py",
            "test_accent*.py",
            "test_auto*.py",
            "test_maintenance*.py",
        ),
    },
    "data": {
        "quick": (
            "test_data_contracts.py",
            "test_persistence.py",
        ),
        "affected": (
            "test_data_contracts.py",
            "test_persistence.py",
            "test_configuration_service.py",
            "test_auto*.py",
            "test_setup*.py",
        ),
    },
    "stage9": {
        "quick": (
            "test_notification_contracts.py",
            "test_notification_dedup.py",
            "test_notification_protocol.py",
            "test_notification_action_cli.py",
            "test_notification_action_service.py",
            "test_notification_identity_service.py",
            "test_notifications_windows.py",
            "test_protocol_registration*.py",
            "test_scheduled_notifications.py",
            "test_scheduled_auto.py",
            "test_switch_override.py",
            "test_health_contracts.py",
            "test_health_service.py",
            "test_windows_identity.py",
        ),
        "affected": (
            "test_notification_contracts.py",
            "test_notification_dedup.py",
            "test_notification_protocol.py",
            "test_notification_action_cli.py",
            "test_notification_action_service.py",
            "test_notification_identity_service.py",
            "test_notifications_windows.py",
            "test_protocol_registration*.py",
            "test_scheduled_notifications.py",
            "test_scheduled_auto.py",
            "test_switch_override.py",
            "test_health_contracts.py",
            "test_health_service.py",
            "test_auto*.py",
            "test_control*.py",
            "test_configuration_service.py",
            "test_gui.py",
            "test_scheduler*.py",
            "test_system_integration*.py",
            "test_persistence.py",
        ),
    },
    "stage10": {
        "quick": (
            "test_release_payload.py",
            "test_release_tools.py",
        ),
        "affected": (
            "test_release_payload.py",
            "test_release_tools.py",
            "test_lifecycle_contracts.py",
            "test_deployment.py",
            "test_setup*.py",
            "test_uninstall*.py",
            "test_system_integration*.py",
        ),
    },
    "stage11": {
        "quick": (
            "test_artifacts.py",
            "test_cold_archive.py",
            "test_test_runner.py",
        ),
        "affected": (
            "test_artifacts.py",
            "test_cold_archive.py",
            "test_test_runner.py",
            "test_release_payload.py",
            "test_release_tools.py",
        ),
    },
    "stage12": {
        "quick": (
            "test_stage12_gui.py",
            "test_gui.py",
            "test_accent_profile.py",
        ),
        "affected": (
            "test_stage12_gui.py",
            "test_gui.py",
            "test_configuration_service.py",
            "test_control*.py",
            "test_maintenance*.py",
            "test_scheduler*.py",
        ),
    },
    "stage13": {
        "quick": (
            "test_stage12_gui.py",
            "test_setup_gui.py",
            "test_uninstall_gui.py",
            "test_gui.py",
        ),
        "affected": (
            "test_stage12_gui.py",
            "test_gui.py",
            "test_setup*.py",
            "test_deployment.py",
            "test_lifecycle_contracts.py",
            "test_system_integration*.py",
            "test_scheduler*.py",
            "test_uninstall*.py",
            "test_auto*.py",
        ),
    },
    "stage14": {
        "quick": (
            "test_release_payload.py",
            "test_release_tools.py",
            "test_stage12_gui.py",
            "test_setup_gui.py",
            "test_uninstall_gui.py",
        ),
        "affected": (
            "test_release_payload.py",
            "test_release_tools.py",
            "test_stage12_gui.py",
            "test_gui.py",
            "test_setup*.py",
            "test_deployment.py",
            "test_lifecycle_contracts.py",
            "test_system_integration*.py",
            "test_scheduler*.py",
            "test_uninstall*.py",
            "test_auto*.py",
        ),
    },
    "stage15": {
        "quick": (
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
        "affected": (
            "test_lifecycle_package.py",
            "test_source_packages.py",
            "test_errors.py",
            "test_quality_contracts.py",
            "test_clean_machine_check.py",
            "test_lifecycle_contracts.py",
            "test_deployment.py",
            "test_auto*.py",
            "test_gui.py",
            "test_setup*.py",
            "test_system_integration*.py",
            "test_uninstall*.py",
            "test_scheduler*.py",
            "test_release_payload.py",
            "test_release_tools.py",
        ),
    },
}


def resolve_patterns(mode: str, group: str | None) -> tuple[str, ...]:
    if mode in {"integration", "full", "release", "coverage"}:
        if group is not None:
            raise ValueError(f"{mode} does not accept a group.")
        return ("test_*.py",)
    if mode not in {"quick", "affected"}:
        raise ValueError(f"Unknown test mode: {mode}")
    if group not in GROUPS:
        raise ValueError(
            "Unknown test group; choose one of: " + ", ".join(sorted(GROUPS))
        )
    assert group is not None
    return GROUPS[group][mode]


def _flatten(suite: unittest.TestSuite) -> Iterable[unittest.TestCase]:
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


def build_suite(patterns: Sequence[str]) -> unittest.TestSuite:
    loader = unittest.TestLoader()
    selected: list[unittest.TestCase] = []
    seen: set[str] = set()
    for pattern in patterns:
        for category_root in TEST_CATEGORY_ROOTS:
            discovered = loader.discover(
                str(category_root),
                pattern=pattern,
                top_level_dir=str(PROJECT_ROOT),
            )
            for test in _flatten(discovered):
                identity = test.id()
                if identity in seen:
                    continue
                seen.add(identity)
                selected.append(test)
    return unittest.TestSuite(selected)


def _run_check(check_id: str, command: Sequence[str]) -> dict[str, object]:
    started = time.monotonic()
    completed = subprocess.run(
        list(command),
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    duration = round(time.monotonic() - started, 3)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        if detail:
            print(detail, file=sys.stderr)
    return {
        "checkId": check_id,
        "command": list(command),
        "returnCode": completed.returncode,
        "durationSeconds": duration,
    }


def _write_report(payload: dict[str, object]) -> Path:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    label = str(payload["mode"])
    group = payload.get("group")
    if group:
        label += f"-{group}"
    path = REPORT_ROOT / f"{timestamp}-{label}.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _in_virtual_environment() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def coverage_commands(*, verbose: bool) -> tuple[tuple[str, ...], ...]:
    integration = [
        sys.executable,
        "-m",
        "coverage",
        "run",
        str(Path(__file__).resolve()),
        "integration",
        "--no-report",
    ]
    if verbose:
        integration.append("--verbose")
    return (
        (sys.executable, "-m", "coverage", "erase"),
        tuple(integration),
        (
            sys.executable,
            "-m",
            "coverage",
            "json",
            "-o",
            str(COVERAGE_JSON),
        ),
        (sys.executable, "-m", "coverage", "report"),
    )


def run_coverage(*, verbose: bool) -> int:
    if not _in_virtual_environment():
        print(
            "error: coverage mode requires the project virtual environment; "
            "run it through uv.",
            file=sys.stderr,
        )
        return 2

    QUALITY_ROOT.mkdir(parents=True, exist_ok=True)
    COVERAGE_JSON.unlink(missing_ok=True)
    COVERAGE_RUN_METADATA.unlink(missing_ok=True)
    commands = coverage_commands(verbose=verbose)

    erase = subprocess.run(commands[0], cwd=PROJECT_ROOT, check=False)
    if erase.returncode != 0:
        return erase.returncode

    tests = subprocess.run(commands[1], cwd=PROJECT_ROOT, check=False)
    json_result = subprocess.run(commands[2], cwd=PROJECT_ROOT, check=False)
    report = subprocess.run(commands[3], cwd=PROJECT_ROOT, check=False)
    success = (
        tests.returncode == 0 and json_result.returncode == 0 and report.returncode == 0
    )
    COVERAGE_RUN_METADATA.write_text(
        json.dumps(
            {
                "kind": "themescheduler.coverage-run",
                "schemaVersion": 1,
                "capturedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
                "sourceIdentity": capture_source_manifest(PROJECT_ROOT)[
                    "sourceTreeSha256"
                ],
                "testsReturnCode": tests.returncode,
                "jsonReturnCode": json_result.returncode,
                "reportReturnCode": report.returncode,
                "success": success,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if tests.returncode != 0:
        return tests.returncode
    if json_result.returncode != 0:
        return json_result.returncode
    return report.returncode


def run(
    mode: str,
    group: str | None,
    *,
    verbose: bool,
    write_report: bool,
) -> int:
    patterns = resolve_patterns(mode, group)
    suite = build_suite(patterns)
    started = time.monotonic()
    result = unittest.TextTestRunner(
        stream=sys.stdout,
        verbosity=2 if verbose else 0,
    ).run(suite)
    duration = round(time.monotonic() - started, 3)
    checks: list[dict[str, object]] = []
    success = result.wasSuccessful()
    if mode == "release" and success:
        checks.append(
            _run_check(
                "compileall",
                (
                    sys.executable,
                    "-m",
                    "compileall",
                    "-q",
                    "src",
                    "entrypoints",
                    "packaging",
                    "tools",
                ),
            )
        )
        uv = shutil.which("uv")
        if uv is None:
            checks.append(
                {
                    "command": ["uv", "lock", "--check"],
                    "returnCode": 127,
                    "durationSeconds": 0,
                    "message": "uv executable was not found.",
                    "checkId": "uv-lock",
                }
            )
        else:
            checks.append(_run_check("uv-lock", (uv, "lock", "--check")))
        for check_id, command in release_quality_commands(Path(sys.executable)):
            checks.append(_run_check(check_id, command))
        success = all(check["returnCode"] == 0 for check in checks)

    payload: dict[str, object] = {
        "kind": "themescheduler.test-report",
        "schemaVersion": TEST_REPORT_SCHEMA_VERSION,
        "capturedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": mode,
        "group": group,
        "patterns": list(patterns),
        "testsRun": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "durationSeconds": duration,
        "checks": checks,
        "success": success,
        "sourceIdentity": (
            capture_source_manifest(PROJECT_ROOT)["sourceTreeSha256"]
            if mode == "release"
            else None
        ),
    }
    report_path = _write_report(payload) if write_report else None
    print(
        f"[{mode}{f':{group}' if group else ''}] "
        f"tests={result.testsRun} duration={duration:.3f}s "
        f"result={'OK' if success else 'FAILED'}"
    )
    if report_path is not None:
        print(f"report={report_path.relative_to(PROJECT_ROOT)}")
    return 0 if success else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="themescheduler-test")
    parser.add_argument(
        "mode",
        choices=(
            "quick",
            "affected",
            "integration",
            "full",
            "release",
            "coverage",
        ),
    )
    parser.add_argument(
        "group",
        nargs="?",
        choices=tuple(sorted(GROUPS)),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every test name; default output only shows failures.",
    )
    report = parser.add_mutually_exclusive_group()
    report.add_argument(
        "--report",
        action="store_true",
        help="Write a compact JSON report for quick or affected runs.",
    )
    report.add_argument(
        "--no-report",
        action="store_true",
        help="Do not write the default integration or release report.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.mode == "coverage":
            if args.group is not None:
                raise ValueError("coverage does not accept a group.")
            if args.report or args.no_report:
                raise ValueError(
                    "coverage owns its JSON output and does not accept test-report flags."
                )
            return run_coverage(verbose=args.verbose)
        return run(
            args.mode,
            args.group,
            verbose=args.verbose,
            write_report=(
                args.report
                or (
                    args.mode in {"integration", "full", "release"}
                    and not args.no_report
                )
            ),
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
