"""Build the app, independent uninstaller, payload, and single-file Setup."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGING_ROOT = Path(__file__).resolve().parent
for import_root in (PROJECT_ROOT, PACKAGING_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from build_payload import assemble_payload  # noqa: E402
from release_tools import (  # noqa: E402
    capture_build_environment,
    capture_quality_report,
    capture_source_manifest,
    distribution_readme,
    file_sha256,
    inspect_pe,
    validate_release_configuration,
    validate_release_test_report,
    write_json,
)

SOURCE_DATE_EPOCH = "1767225600"


def _run(command: list[str], *, environment=None) -> None:
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Build command exited with {completed.returncode}: {command}"
        )


def _build_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONHASHSEED"] = "0"
    environment["SOURCE_DATE_EPOCH"] = SOURCE_DATE_EPOCH
    environment.setdefault(
        "UV_CACHE_DIR",
        str(PROJECT_ROOT / "artifacts" / ".uv-cache"),
    )
    return environment


def _write_text(path: Path, text: str) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite release file: {path}")
    path.write_text(text, encoding="utf-8", newline="\n")


def _project_descendant(
    path: Path,
    project_root: Path = PROJECT_ROOT,
) -> Path:
    root = Path(project_root).resolve(strict=True)
    candidate = Path(path).resolve(strict=False)
    if candidate == root or not candidate.is_relative_to(root):
        raise ValueError(f"Release path must be below the project root: {candidate}")
    return candidate


def _safe_rmtree(
    path: Path,
    project_root: Path = PROJECT_ROOT,
) -> None:
    candidate = _project_descendant(path, project_root)
    if candidate.exists():
        shutil.rmtree(candidate)


def build_release(
    *,
    output_root: Path,
    version: str,
    python: Path,
    test_report: Path,
) -> Path:
    validate_release_configuration(PROJECT_ROOT, version)
    source_manifest = capture_source_manifest(PROJECT_ROOT)
    release_test = validate_release_test_report(
        test_report,
        project_root=PROJECT_ROOT,
        source_identity=str(source_manifest["sourceTreeSha256"]),
    )
    quality_report = capture_quality_report(
        PROJECT_ROOT,
        python,
        source_identity=str(source_manifest["sourceTreeSha256"]),
    )
    build_environment = capture_build_environment(python)
    build_environment["reproducibility"] = {
        "pythonHashSeed": "0",
        "sourceDateEpoch": SOURCE_DATE_EPOCH,
    }
    environment = _build_environment()
    uv = shutil.which("uv")
    if uv is None:
        raise FileNotFoundError("uv executable is required for release builds.")
    _run([uv, "lock", "--check"], environment=environment)

    output_root = _project_descendant(output_root)
    if output_root.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing build root: {output_root}"
        )
    output_root.parent.mkdir(parents=True, exist_ok=True)
    work = output_root.parent / f".{output_root.name}.work"
    if work.exists():
        raise FileExistsError(f"Build work directory exists: {work}")
    work.mkdir()
    try:
        app_dist = work / "dist-app"
        app_build = work / "build-app"
        _run(
            [
                str(python),
                "-m",
                "PyInstaller",
                "--clean",
                "--noconfirm",
                "--workpath",
                str(app_build),
                "--distpath",
                str(app_dist),
                str(PROJECT_ROOT / "packaging" / "ThemeScheduler.spec"),
            ],
            environment=environment,
        )
        bundle = work / "bundle"
        _, payload_manifest_path, payload_manifest = assemble_payload(
            app_dist=app_dist / "ThemeScheduler",
            uninstaller=app_dist / "Uninstall.exe",
            output_root=bundle,
            version=version,
        )
        setup_dist = work / "dist-setup"
        environment["THEMESCHEDULER_SETUP_BUNDLE_ROOT"] = str(bundle)
        _run(
            [
                str(python),
                "-m",
                "PyInstaller",
                "--clean",
                "--noconfirm",
                "--workpath",
                str(work / "build-setup"),
                "--distpath",
                str(setup_dist),
                str(PROJECT_ROOT / "packaging" / "ThemeSchedulerSetup.spec"),
            ],
            environment=environment,
        )
        setup = setup_dist / "ThemeScheduler-Setup.exe"
        if not setup.is_file():
            raise FileNotFoundError("Setup build did not produce its EXE.")
        output_root.mkdir()
        distribution = output_root / "dist"
        distribution.mkdir()
        evidence = output_root / "evidence"
        evidence.mkdir()
        published_setup = distribution / setup.name
        shutil.copy2(setup, published_setup)
        shutil.copytree(bundle, evidence / "bundle-evidence")
        write_json(evidence / "source-manifest.json", source_manifest)
        write_json(evidence / "quality-report.json", quality_report)
        write_json(
            evidence / "build-environment.json",
            build_environment,
        )
        shutil.copy2(
            Path(test_report).resolve(strict=True),
            evidence / "release-test-report.json",
        )
        warnings = evidence / "pyinstaller-warnings"
        warning_sources = (
            app_build / "ThemeScheduler" / "warn-ThemeScheduler.txt",
            app_build / "Uninstall" / "warn-Uninstall.txt",
            work
            / "build-setup"
            / "ThemeSchedulerSetup"
            / "warn-ThemeSchedulerSetup.txt",
        )
        copied_warnings: list[Path] = []
        for warning_source in warning_sources:
            if warning_source.is_file():
                warnings.mkdir(exist_ok=True)
                target = warnings / warning_source.name
                shutil.copy2(warning_source, target)
                copied_warnings.append(target)

        setup_pe = inspect_pe(published_setup)
        main_pe = inspect_pe(bundle / "payload" / "app" / "ThemeScheduler.exe")
        uninstall_pe = inspect_pe(bundle / "payload" / "maintenance" / "Uninstall.exe")
        setup_sha256 = str(setup_pe["sha256"])
        readme = distribution_readme(
            version=version,
            setup_sha256=setup_sha256,
        )
        published_readme = distribution / "RELEASE-README.md"
        _write_text(published_readme, readme)

        release_record = {
            "kind": "themescheduler.release-record",
            "schemaVersion": 1,
            "productId": "ThemeScheduler",
            "version": version,
            "architecture": "windows-x64",
            "builtAt": build_environment["capturedAt"],
            "source": {
                "identity": source_manifest["sourceTreeSha256"],
                "manifestSha256": file_sha256(evidence / "source-manifest.json"),
                "fileCount": len(source_manifest["files"]),
            },
            "buildEnvironmentSha256": file_sha256(evidence / "build-environment.json"),
            "uvLockSha256": file_sha256(PROJECT_ROOT / "uv.lock"),
            "tests": {
                "reportSha256": file_sha256(evidence / "release-test-report.json"),
                "testsRun": release_test["testsRun"],
                "checks": len(release_test["checks"]),
                "success": True,
            },
            "quality": {
                "reportSha256": file_sha256(evidence / "quality-report.json"),
                "ruffVersion": quality_report["tools"]["ruff"]["version"],
                "pyrightVersion": quality_report["tools"]["pyright"]["version"],
                "lintPassed": quality_report["lint"]["passed"],
                "formatPassed": quality_report["format"]["passed"],
                "staticTypingPassed": quality_report["staticTyping"]["passed"],
                "complexityFindings": quality_report["complexity"]["findingCount"],
            },
            "payload": {
                "fileCount": len(payload_manifest.files),
                "documentSha256": payload_manifest.document_sha256,
                "manifestFileSha256": file_sha256(payload_manifest_path),
            },
            "executables": {
                "setup": setup_pe,
                "main": main_pe,
                "uninstaller": uninstall_pe,
            },
            "security": {
                "authenticode": "not-used",
                "upx": False,
                "obfuscation": False,
                "securityExclusions": False,
                "dynamicApplicationCodeDownload": False,
                "smartScreenAcceptance": "pending",
                "defenderAcceptance": "pending",
            },
            "sbom": {
                "included": False,
                "reason": "Optional for the first small-audience release.",
            },
            "pyinstallerWarnings": [
                {
                    "path": path.relative_to(output_root).as_posix(),
                    "sha256": file_sha256(path),
                }
                for path in copied_warnings
            ],
        }
        release_manifest = evidence / "release-manifest.json"
        write_json(release_manifest, release_record)

        release_layout = output_root / "release-layout.json"
        write_json(
            release_layout,
            {
                "distributionRoot": "dist",
                "evidenceRoot": "evidence",
                "kind": "themescheduler.release-layout",
                "schemaVersion": 1,
                "version": version,
            },
        )

        distribution_checksum_files = (
            published_setup,
            published_readme,
        )
        distribution_checksum_lines = [
            f"{file_sha256(path)}  {path.name}" for path in distribution_checksum_files
        ]
        distribution_checksums = distribution / "SHA256SUMS.txt"
        _write_text(
            distribution_checksums,
            "\n".join(distribution_checksum_lines) + "\n",
        )

        checksum_files = (
            published_setup,
            published_readme,
            distribution_checksums,
            release_layout,
            release_manifest,
            evidence / "source-manifest.json",
            evidence / "quality-report.json",
            evidence / "build-environment.json",
            evidence / "release-test-report.json",
            evidence / "bundle-evidence" / "payload-manifest.json",
        )
        checksum_lines = [
            f"{file_sha256(path)}  {path.relative_to(output_root).as_posix()}"
            for path in checksum_files
        ]
        _write_text(
            output_root / "SHA256SUMS.txt",
            "\n".join(checksum_lines) + "\n",
        )
        return published_setup
    except Exception:
        _safe_rmtree(work)
        raise
    finally:
        if work.exists():
            _safe_rmtree(work)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="build-theme-scheduler-release")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--python",
        type=Path,
        default=Path(sys.executable),
    )
    parser.add_argument("--test-report", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        setup = build_release(
            output_root=args.output_root,
            version=args.version,
            python=args.python,
            test_report=args.test_report,
        )
        print(setup)
        return 0
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
