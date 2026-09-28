"""Plan, verify, and explicitly clean generated project artifacts.

The default command is non-destructive:

    python tools/artifacts.py plan
    python tools/artifacts.py verify
    python tools/artifacts.py clean

Real deletion additionally requires both flags:

    python tools/artifacts.py clean --apply --confirm-artifact-cleanup
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_ROOT = PROJECT_ROOT / "artifacts"
MAINTENANCE_ROOT = ARTIFACTS_ROOT / "maintenance"
DEFAULT_PLAN = MAINTENANCE_ROOT / "stage11-retention-plan.json"
DEFAULT_REPORT = MAINTENANCE_ROOT / "stage11-cleanup-report.json"
PLAN_KIND = "themescheduler.artifact-cleanup-plan"
PLAN_SCHEMA_VERSION = 1
POLICY_ID = "D-12-aggressive-rolling-v1"
FINAL_RELEASES = {
    "artifacts/releases/0.1.0": "Complete published 0.1.0 release.",
    "artifacts/releases/0.1.1": "Complete accepted 0.1.1 release.",
    "artifacts/releases/0.1.2": "Complete accepted 0.1.2 release.",
    "artifacts/releases/0.1.3": "Complete accepted 0.1.3 release.",
}
FINAL_RELEASE_REPORTS = {
    "artifacts/test-reports/20260727T000308-release.json",
    "artifacts/test-reports/20260729T230507-release.json",
}
COLD_ARCHIVE_FILES = {
    "artifacts/cold-archive/ThemeScheduler-0.1.0.zip",
    "artifacts/cold-archive/ThemeScheduler-0.1.0.json",
}
STAGE14_COMPACT_REPORTS = {
    "final-uninstall-rc8-report.json",
    "gui-boundary-report.json",
    "reinstall-report.json",
}
REPARSE_POINT_FLAG = 0x400
CHECKSUM_LINE = re.compile(r"^([0-9A-Fa-f]{64})\s+\*?(.+)$")


class ArtifactPlanError(RuntimeError):
    """Raised when an artifact plan is unsafe, invalid, or stale."""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _relative(path: Path, project_root: Path) -> str:
    return path.relative_to(project_root).as_posix()


def _is_reparse(path: Path) -> bool:
    stat = path.lstat()
    return path.is_symlink() or bool(
        getattr(stat, "st_file_attributes", 0) & REPARSE_POINT_FLAG
    )


def _assert_artifact_path(path: Path, project_root: Path) -> Path:
    artifacts_root = (project_root / "artifacts").resolve()
    candidate = path.absolute()
    if candidate == artifacts_root or candidate == project_root.resolve():
        raise ArtifactPlanError("Artifact root and project root are never targets.")
    try:
        candidate.relative_to(artifacts_root)
    except ValueError as exc:
        raise ArtifactPlanError(
            f"Path is outside the artifact root: {candidate}"
        ) from exc

    current = artifacts_root
    relative = candidate.relative_to(artifacts_root)
    for part in relative.parts:
        current = current / part
        if current.exists() and _is_reparse(current):
            raise ArtifactPlanError(f"Reparse points are not accepted: {current}")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(artifacts_root)
    except ValueError as exc:
        raise ArtifactPlanError(
            f"Resolved path escapes the artifact root: {resolved}"
        ) from exc
    return resolved


def _tree_records(path: Path) -> Iterable[tuple[str, str, int, int]]:
    if path.is_file():
        stat = path.stat()
        yield ("file", ".", stat.st_size, stat.st_mtime_ns)
        return

    yield ("directory", ".", 0, path.stat().st_mtime_ns)
    for child in sorted(path.rglob("*"), key=lambda item: item.as_posix()):
        if _is_reparse(child):
            raise ArtifactPlanError(f"Reparse points are not accepted: {child}")
        relative = child.relative_to(path).as_posix()
        stat = child.stat()
        if child.is_dir():
            yield ("directory", relative, 0, stat.st_mtime_ns)
        elif child.is_file():
            yield ("file", relative, stat.st_size, stat.st_mtime_ns)
        else:
            raise ArtifactPlanError(f"Unsupported artifact entry type: {child}")


def snapshot_path(path: Path, project_root: Path) -> dict[str, Any]:
    resolved = _assert_artifact_path(path, project_root)
    if not resolved.exists():
        raise ArtifactPlanError(f"Artifact path does not exist: {resolved}")
    records = list(_tree_records(resolved))
    file_count = sum(record[0] == "file" for record in records)
    directory_count = sum(record[0] == "directory" for record in records)
    byte_count = sum(record[2] for record in records)
    encoded = json.dumps(
        records,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "path": _relative(resolved, project_root),
        "kind": "file" if resolved.is_file() else "directory",
        "fileCount": file_count,
        "directoryCount": directory_count,
        "bytes": byte_count,
        "treeSha256": hashlib.sha256(encoded).hexdigest(),
    }


def _entry(
    path: Path,
    project_root: Path,
    *,
    action: str,
    category: str,
    reason: str,
) -> dict[str, Any]:
    try:
        snapshot = snapshot_path(path, project_root)
    except ArtifactPlanError as exc:
        return {
            "path": _relative(path.absolute(), project_root),
            "action": "review",
            "category": "unsafe-or-unreadable",
            "reason": f"{type(exc).__name__}: {exc}",
        }
    return {
        **snapshot,
        "action": action,
        "category": category,
        "reason": reason,
    }


def _children(path: Path) -> list[Path]:
    if not path.exists():
        return []
    return sorted(path.iterdir(), key=lambda item: item.name.casefold())


def build_plan(project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    project_root = project_root.resolve()
    artifacts = project_root / "artifacts"
    if not artifacts.is_dir():
        raise ArtifactPlanError(f"Artifact root does not exist: {artifacts}")

    entries: list[dict[str, Any]] = []
    known_top_level = {
        ".uv-cache",
        "acceptance",
        "build",
        "cache",
        "cold-archive",
        "diagnostics",
        "maintenance",
        "releases",
        "stage10-icon-preview.png",
        "test-reports",
    }

    build_root = artifacts / "build"
    entries.extend(
        _entry(
            child,
            project_root,
            action="delete",
            category="superseded-build",
            reason=("Intermediate build superseded by a protected versioned release."),
        )
        for child in _children(build_root)
    )

    releases_root = artifacts / "releases"
    for child in _children(releases_root):
        relative = _relative(child.absolute(), project_root)
        if relative in FINAL_RELEASES:
            entries.append(
                _entry(
                    child,
                    project_root,
                    action="keep",
                    category="published-release",
                    reason=FINAL_RELEASES[relative],
                )
            )
        else:
            entries.append(
                _entry(
                    child,
                    project_root,
                    action="review",
                    category="unknown-release",
                    reason=(
                        "Unknown versioned release is never deleted automatically."
                    ),
                )
            )

    cold_root = artifacts / "cold-archive"
    for child in _children(cold_root):
        relative = _relative(child.absolute(), project_root)
        if relative in COLD_ARCHIVE_FILES:
            entries.append(
                _entry(
                    child,
                    project_root,
                    action="keep",
                    category="published-release-cold-archive",
                    reason="Verified immutable archive for superseded release 0.1.0.",
                )
            )
        else:
            entries.append(
                _entry(
                    child,
                    project_root,
                    action="review",
                    category="unknown-cold-archive",
                    reason="Unknown cold archive is never deleted automatically.",
                )
            )

    acceptance_root = artifacts / "acceptance"
    for child in _children(acceptance_root):
        if child.name == "stage10-final-release.json":
            entries.append(
                _entry(
                    child,
                    project_root,
                    action="keep",
                    category="latest-release-evidence",
                    reason="Compact final release and lifecycle summary.",
                )
            )
        elif child.name == "stage10-local":
            entries.append(
                _entry(
                    child,
                    project_root,
                    action="keep",
                    category="latest-release-evidence",
                    reason="Compact reproducibility summary for 0.1.0.",
                )
            )
        elif child.name == "stage10-live":
            for live_child in _children(child):
                if live_child.is_file() and live_child.suffix.lower() == ".json":
                    entries.append(
                        _entry(
                            live_child,
                            project_root,
                            action="keep",
                            category="latest-release-evidence",
                            reason="Compact Stage-10 live acceptance report.",
                        )
                    )
                elif live_child.name == "ThemeScheduler-Setup-MOTW.exe":
                    entries.append(
                        _entry(
                            live_child,
                            project_root,
                            action="delete",
                            category="duplicate-payload",
                            reason=(
                                "Byte-identical MOTW acceptance copy; "
                                "SmartScreen result is retained in reports."
                            ),
                        )
                    )
                else:
                    entries.append(
                        _entry(
                            live_child,
                            project_root,
                            action="review",
                            category="unknown-current-evidence",
                            reason="Unexpected Stage-10 live evidence.",
                        )
                    )
        elif child.name == "stage14-live":
            for live_child in _children(child):
                if live_child.is_file() and live_child.name in STAGE14_COMPACT_REPORTS:
                    entries.append(
                        _entry(
                            live_child,
                            project_root,
                            action="keep",
                            category="latest-release-evidence",
                            reason=("Compact accepted Stage-14 lifecycle evidence."),
                        )
                    )
                else:
                    entries.append(
                        _entry(
                            live_child,
                            project_root,
                            action="delete",
                            category="superseded-or-duplicate-acceptance",
                            reason=(
                                "Large, failed, or reproducible evidence "
                                "superseded by the accepted Stage-14 reports."
                            ),
                        )
                    )
        else:
            entries.append(
                _entry(
                    child,
                    project_root,
                    action="delete",
                    category="old-acceptance-evidence",
                    reason=(
                        "Stage 0-9 raw evidence is superseded by current "
                        "documents and Stage-10 lifecycle acceptance."
                    ),
                )
            )

    reports_root = artifacts / "test-reports"
    for child in _children(reports_root):
        relative = _relative(child.absolute(), project_root)
        if relative in FINAL_RELEASE_REPORTS:
            entries.append(
                _entry(
                    child,
                    project_root,
                    action="keep",
                    category="latest-release-evidence",
                    reason="Release test report bound to a protected release.",
                )
            )
        elif child.is_file():
            entries.append(
                _entry(
                    child,
                    project_root,
                    action="delete",
                    category="superseded-test-report",
                    reason="Superseded pre-release test report.",
                )
            )
        else:
            entries.append(
                _entry(
                    child,
                    project_root,
                    action="review",
                    category="unknown-test-report-entry",
                    reason="Unexpected entry under test-reports.",
                )
            )

    direct_delete = {
        ".uv-cache": "Rebuildable artifact-local uv cache.",
        "cache": "Rebuildable stage caches.",
        "diagnostics": "Old raw diagnostics summarized in current documents.",
        "stage10-icon-preview.png": "Superseded generated icon preview.",
    }
    for name, reason in direct_delete.items():
        path = artifacts / name
        if path.exists():
            entries.append(
                _entry(
                    path,
                    project_root,
                    action="delete",
                    category="rebuildable-or-summarized",
                    reason=reason,
                )
            )

    maintenance = artifacts / "maintenance"
    if maintenance.exists():
        entries.append(
            _entry(
                maintenance,
                project_root,
                action="keep",
                category="stage11-maintenance",
                reason="Current Stage-11 plan and cleanup reports.",
            )
        )

    entries.extend(
        _entry(
            child,
            project_root,
            action="review",
            category="unknown-top-level",
            reason="Unknown artifact entry is never deleted automatically.",
        )
        for child in _children(artifacts)
        if child.name not in known_top_level
    )

    entries.sort(key=lambda item: str(item["path"]).casefold())
    _assert_non_overlapping_delete_entries(entries)
    totals: dict[str, dict[str, int]] = {}
    for action in ("keep", "delete", "review"):
        selected = [entry for entry in entries if entry["action"] == action]
        totals[action] = {
            "entries": len(selected),
            "files": sum(int(entry.get("fileCount", 0)) for entry in selected),
            "directories": sum(
                int(entry.get("directoryCount", 0)) for entry in selected
            ),
            "bytes": sum(int(entry.get("bytes", 0)) for entry in selected),
        }

    return {
        "kind": PLAN_KIND,
        "schemaVersion": PLAN_SCHEMA_VERSION,
        "policy": POLICY_ID,
        "generatedAt": _now(),
        "projectRoot": str(project_root),
        "artifactsRoot": str(artifacts.resolve()),
        "protectedPaths": [
            path
            for path in (*FINAL_RELEASES, *COLD_ARCHIVE_FILES)
            if (project_root / path).exists()
        ],
        "totals": totals,
        "entries": entries,
    }


def _assert_non_overlapping_delete_entries(
    entries: Iterable[Mapping[str, Any]],
) -> None:
    paths = sorted(
        (
            Path(str(entry["path"]))
            for entry in entries
            if entry.get("action") == "delete"
        ),
        key=lambda path: len(path.parts),
    )
    for index, parent in enumerate(paths):
        for child in paths[index + 1 :]:
            if child.is_relative_to(parent):
                raise ArtifactPlanError(f"Delete entries overlap: {parent} and {child}")


def _write_json(path: Path, payload: Mapping[str, Any], *, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists():
        raise FileExistsError(f"Refusing to overwrite temporary file: {temporary}")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_plan(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ArtifactPlanError("Artifact plan must be a JSON object.")
    if payload.get("kind") != PLAN_KIND:
        raise ArtifactPlanError("Artifact plan kind is invalid.")
    if payload.get("schemaVersion") != PLAN_SCHEMA_VERSION:
        raise ArtifactPlanError("Artifact plan schema version is invalid.")
    if payload.get("policy") != POLICY_ID:
        raise ArtifactPlanError("Artifact plan policy is invalid.")
    if not isinstance(payload.get("entries"), list):
        raise ArtifactPlanError("Artifact plan entries are invalid.")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_release_checksums(project_root: Path = PROJECT_ROOT) -> int:
    checked = 0
    releases_checked = 0
    for relative_release in FINAL_RELEASES:
        release = project_root.resolve() / relative_release
        if not release.exists():
            continue
        releases_checked += 1
        checksum_file = release / "SHA256SUMS.txt"
        if not checksum_file.is_file():
            raise ArtifactPlanError(
                f"Protected release checksum file is missing: {checksum_file}"
            )
        release_checked = 0
        for raw_line in checksum_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = CHECKSUM_LINE.fullmatch(line)
            if match is None:
                raise ArtifactPlanError(f"Invalid checksum line: {raw_line}")
            expected, relative_text = match.groups()
            target = (release / relative_text).resolve()
            try:
                target.relative_to(release.resolve())
            except ValueError as exc:
                raise ArtifactPlanError(
                    f"Release checksum path escapes release root: {target}"
                ) from exc
            if not target.is_file():
                raise ArtifactPlanError(f"Protected release file is missing: {target}")
            actual = _sha256(target)
            if actual.casefold() != expected.casefold():
                raise ArtifactPlanError(
                    f"Protected release checksum mismatch: {target}"
                )
            checked += 1
            release_checked += 1
        if release_checked == 0:
            raise ArtifactPlanError(f"Protected release has no checksums: {release}")
    cold_archive = (
        project_root.resolve()
        / "artifacts"
        / "cold-archive"
        / "ThemeScheduler-0.1.0.zip"
    )
    cold_index = cold_archive.with_suffix(".json")
    if cold_archive.exists() or cold_index.exists():
        if not cold_archive.is_file() or not cold_index.is_file():
            raise ArtifactPlanError("Protected cold archive pair is incomplete.")
        try:
            index = json.loads(cold_index.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ArtifactPlanError("Protected cold archive index is invalid.") from exc
        if not isinstance(index, dict) or index.get("archiveSha256") != _sha256(
            cold_archive
        ):
            raise ArtifactPlanError("Protected cold archive checksum mismatch.")
        checked += 1
    if releases_checked == 0:
        raise ArtifactPlanError("No protected release exists.")
    if checked == 0:
        raise ArtifactPlanError("Protected release has no checksums.")
    return checked


def _validate_plan_scope(
    plan: Mapping[str, Any],
    project_root: Path,
) -> list[Mapping[str, Any]]:
    project_root = project_root.resolve()
    if Path(str(plan.get("projectRoot", ""))).resolve() != project_root:
        raise ArtifactPlanError("Artifact plan project root does not match.")
    if (
        Path(str(plan.get("artifactsRoot", ""))).resolve()
        != (project_root / "artifacts").resolve()
    ):
        raise ArtifactPlanError("Artifact plan artifact root does not match.")

    entries = plan.get("entries")
    if not isinstance(entries, list):
        raise ArtifactPlanError("Artifact plan entries are invalid.")
    protected = [
        (project_root / str(path)).resolve() for path in plan.get("protectedPaths", [])
    ]
    for relative_release in FINAL_RELEASES:
        release = (project_root / relative_release).resolve()
        if release.exists() and release not in protected:
            raise ArtifactPlanError(
                f"Final release is not protected by the plan: {release}"
            )
    for relative_archive in COLD_ARCHIVE_FILES:
        archive = (project_root / relative_archive).resolve()
        if archive.exists() and archive not in protected:
            raise ArtifactPlanError(
                f"Cold archive is not protected by the plan: {archive}"
            )

    for raw_entry in entries:
        if not isinstance(raw_entry, Mapping):
            raise ArtifactPlanError("Artifact plan entry must be an object.")
        if raw_entry.get("action") not in {"keep", "delete", "review"}:
            raise ArtifactPlanError("Artifact plan action is invalid.")
        target = _assert_artifact_path(
            project_root / str(raw_entry.get("path", "")),
            project_root,
        )
        if raw_entry.get("action") == "delete":
            for protected_path in protected:
                if target == protected_path or protected_path.is_relative_to(target):
                    raise ArtifactPlanError(
                        f"Delete entry contains protected path: {target}"
                    )
    _assert_non_overlapping_delete_entries(entries)
    return entries


def verify_plan(
    plan: Mapping[str, Any],
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    entries = _validate_plan_scope(plan, project_root)
    checksum_count = verify_release_checksums(project_root)
    verified_delete_entries = 0
    verified_delete_bytes = 0
    for entry in entries:
        if entry.get("action") != "delete":
            continue
        target = project_root.resolve() / str(entry["path"])
        current = snapshot_path(target, project_root)
        for field in (
            "path",
            "kind",
            "fileCount",
            "directoryCount",
            "bytes",
            "treeSha256",
        ):
            if current.get(field) != entry.get(field):
                raise ArtifactPlanError(
                    f"Delete candidate changed since planning: "
                    f"{entry['path']} ({field})"
                )
        verified_delete_entries += 1
        verified_delete_bytes += int(entry.get("bytes", 0))
    return {
        "valid": True,
        "policy": POLICY_ID,
        "protectedReleaseChecksums": checksum_count,
        "deleteEntries": verified_delete_entries,
        "deleteBytes": verified_delete_bytes,
    }


def execute_cleanup(
    plan: Mapping[str, Any],
    project_root: Path = PROJECT_ROOT,
    *,
    apply: bool,
    confirmed: bool,
) -> dict[str, Any]:
    verification = verify_plan(plan, project_root)
    delete_entries = [
        entry for entry in plan["entries"] if entry.get("action") == "delete"
    ]
    result: dict[str, Any] = {
        "kind": "themescheduler.artifact-cleanup-result",
        "schemaVersion": 1,
        "policy": POLICY_ID,
        "createdAt": _now(),
        "mode": "apply" if apply else "preview",
        "verified": True,
        "protectedReleaseChecksums": verification["protectedReleaseChecksums"],
        "plannedDeleteEntries": len(delete_entries),
        "plannedDeleteBytes": verification["deleteBytes"],
        "deletedEntries": 0,
        "deletedBytes": 0,
    }
    if not apply:
        return result
    if not confirmed:
        raise ArtifactPlanError("Real cleanup requires --confirm-artifact-cleanup.")

    deleted_bytes = 0
    for entry in delete_entries:
        target = _assert_artifact_path(
            project_root.resolve() / str(entry["path"]),
            project_root,
        )
        deleted_bytes += int(entry.get("bytes", 0))
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    result["deletedEntries"] = len(delete_entries)
    result["deletedBytes"] = deleted_bytes
    result["completed"] = True
    return result


def _display(payload: Mapping[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="themescheduler-artifacts")
    commands = parser.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan")
    plan.add_argument("--output", type=Path, default=DEFAULT_PLAN)
    plan.add_argument("--force", action="store_true")

    verify = commands.add_parser("verify")
    verify.add_argument("--plan", type=Path, default=DEFAULT_PLAN)

    clean = commands.add_parser("clean")
    clean.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    clean.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    clean.add_argument("--apply", action="store_true")
    clean.add_argument(
        "--confirm-artifact-cleanup",
        action="store_true",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "plan":
            args.output.parent.mkdir(parents=True, exist_ok=True)
            payload = build_plan(PROJECT_ROOT)
            _write_json(args.output, payload, force=args.force)
            _display(
                {
                    "result": "planned",
                    "plan": str(args.output.resolve()),
                    "policy": payload["policy"],
                    "totals": payload["totals"],
                    "windowsChanged": False,
                    "filesDeleted": False,
                }
            )
            return 0
        if args.command == "verify":
            payload = load_plan(args.plan)
            result = verify_plan(payload, PROJECT_ROOT)
            _display(
                {
                    "result": "valid",
                    "plan": str(args.plan.resolve()),
                    **result,
                    "windowsChanged": False,
                    "filesDeleted": False,
                }
            )
            return 0

        payload = load_plan(args.plan)
        result = execute_cleanup(
            payload,
            PROJECT_ROOT,
            apply=args.apply,
            confirmed=args.confirm_artifact_cleanup,
        )
        if args.apply:
            _write_json(args.report, result, force=False)
        _display(
            {
                "result": "completed" if args.apply else "preview",
                "plan": str(args.plan.resolve()),
                **result,
                "report": (str(args.report.resolve()) if args.apply else None),
                "windowsChanged": False,
                "filesDeleted": bool(args.apply),
            }
        )
        return 0
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
