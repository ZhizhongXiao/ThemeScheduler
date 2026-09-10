"""Create and verify the immutable cold archive for superseded release evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_ROOT = PROJECT_ROOT / "artifacts"
ARCHIVE_VERSION = "0.1.0"
COLD_ROOT = ARTIFACTS_ROOT / "cold-archive"
ARCHIVE_PATH = COLD_ROOT / f"ThemeScheduler-{ARCHIVE_VERSION}.zip"
INDEX_PATH = COLD_ROOT / f"ThemeScheduler-{ARCHIVE_VERSION}.json"
MANIFEST_NAME = "_cold-archive-manifest.json"
MANIFEST_KIND = "themescheduler.cold-artifact-archive"
MANIFEST_SCHEMA_VERSION = 1
SOURCE_PATHS = (
    "releases/0.1.0",
    "acceptance/stage10-final-release.json",
    "acceptance/stage10-live",
    "acceptance/stage10-local",
    "test-reports/20260727T000308-release.json",
)
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_REPARSE_POINT_FLAG = 0x400
_CHECKSUM_LINE = re.compile(r"^([0-9A-Fa-f]{64})\s+\*?(.+)$")
_RELEASE_ROOT = PurePosixPath("releases/0.1.0")


class ColdArchiveError(RuntimeError):
    """Raised when a cold archive cannot be created or trusted."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_reparse(path: Path) -> bool:
    stat_result = path.lstat()
    return path.is_symlink() or bool(
        getattr(stat_result, "st_file_attributes", 0) & _REPARSE_POINT_FLAG
    )


def _assert_inside_artifacts(path: Path, artifacts_root: Path) -> Path:
    root = artifacts_root.resolve()
    candidate = path.resolve(strict=False)
    if candidate == root:
        raise ColdArchiveError("The artifacts root cannot be archived or removed.")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ColdArchiveError(f"Path escaped artifacts root: {candidate}") from exc
    current = root
    for part in candidate.relative_to(root).parts:
        current = current / part
        if current.exists() and _is_reparse(current):
            raise ColdArchiveError(f"Reparse points are not accepted: {current}")
    return candidate


def _source_roots(artifacts_root: Path) -> tuple[Path, ...]:
    roots = tuple(
        _assert_inside_artifacts(artifacts_root / relative, artifacts_root)
        for relative in SOURCE_PATHS
    )
    missing = [path for path in roots if not path.exists()]
    if missing:
        raise ColdArchiveError(
            "Cold archive source is missing: "
            + ", ".join(str(path) for path in missing)
        )
    return roots


def _iter_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    if not root.is_dir():
        raise ColdArchiveError(f"Archive source is not a regular file/tree: {root}")
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if _is_reparse(path):
            raise ColdArchiveError(f"Reparse points are not accepted: {path}")
        if path.is_file():
            yield path
        elif not path.is_dir():
            raise ColdArchiveError(f"Unsupported archive source entry: {path}")


def build_manifest(artifacts_root: Path = ARTIFACTS_ROOT) -> dict[str, Any]:
    artifacts_root = artifacts_root.resolve()
    entries: list[dict[str, Any]] = []
    for source in _source_roots(artifacts_root):
        for path in _iter_files(source):
            relative = path.relative_to(artifacts_root).as_posix()
            entries.append(
                {
                    "path": relative,
                    "bytes": path.stat().st_size,
                    "sha256": _sha256_file(path),
                }
            )
    entries.sort(key=lambda entry: str(entry["path"]).casefold())
    if not entries:
        raise ColdArchiveError("Cold archive has no source files.")
    encoded_entries = json.dumps(
        entries,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "kind": MANIFEST_KIND,
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "version": ARCHIVE_VERSION,
        "sourceRoots": list(SOURCE_PATHS),
        "fileCount": len(entries),
        "bytes": sum(int(entry["bytes"]) for entry in entries),
        "contentSha256": _sha256_bytes(encoded_entries),
        "entries": entries,
    }


def _manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _write_zip(
    target: Path,
    manifest: Mapping[str, Any],
    artifacts_root: Path,
) -> None:
    with zipfile.ZipFile(
        target,
        mode="x",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for entry in manifest["entries"]:
            relative = str(entry["path"])
            data = (artifacts_root / relative).read_bytes()
            info = zipfile.ZipInfo(relative, date_time=_FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
        manifest_info = zipfile.ZipInfo(MANIFEST_NAME, date_time=_FIXED_ZIP_TIME)
        manifest_info.compress_type = zipfile.ZIP_DEFLATED
        manifest_info.external_attr = 0o100644 << 16
        archive.writestr(
            manifest_info,
            _manifest_bytes(manifest),
            compress_type=zipfile.ZIP_DEFLATED,
        )


def _load_manifest(data: bytes) -> dict[str, Any]:
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ColdArchiveError(
            "Cold archive manifest is not valid UTF-8 JSON."
        ) from exc
    if not isinstance(manifest, dict):
        raise ColdArchiveError("Cold archive manifest must be an object.")
    if manifest.get("kind") != MANIFEST_KIND:
        raise ColdArchiveError("Cold archive manifest kind is invalid.")
    if manifest.get("schemaVersion") != MANIFEST_SCHEMA_VERSION:
        raise ColdArchiveError("Cold archive manifest schema is invalid.")
    if manifest.get("version") != ARCHIVE_VERSION:
        raise ColdArchiveError("Cold archive version is invalid.")
    if not isinstance(manifest.get("entries"), list):
        raise ColdArchiveError("Cold archive entries are invalid.")
    return manifest


def _verify_embedded_release_checksums(archive: zipfile.ZipFile) -> int:
    checksum_member = str(_RELEASE_ROOT / "SHA256SUMS.txt")
    try:
        lines = archive.read(checksum_member).decode("utf-8").splitlines()
    except KeyError as exc:
        raise ColdArchiveError("Archived release checksum file is missing.") from exc
    except UnicodeDecodeError as exc:
        raise ColdArchiveError("Archived release checksums are not UTF-8.") from exc
    checked = 0
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        match = _CHECKSUM_LINE.fullmatch(line)
        if match is None:
            raise ColdArchiveError(f"Invalid archived checksum line: {raw_line}")
        expected, relative_text = match.groups()
        relative = PurePosixPath(relative_text.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ColdArchiveError("Archived release checksum path escaped its root.")
        member = str(_RELEASE_ROOT / relative)
        try:
            data = archive.read(member)
        except KeyError as exc:
            raise ColdArchiveError(
                f"Archived release file is missing: {member}"
            ) from exc
        if _sha256_bytes(data).casefold() != expected.casefold():
            raise ColdArchiveError(f"Archived release checksum mismatch: {member}")
        checked += 1
    if checked == 0:
        raise ColdArchiveError("Archived release checksum file is empty.")
    return checked


def verify_archive(
    archive_path: Path = ARCHIVE_PATH,
    index_path: Path = INDEX_PATH,
) -> dict[str, Any]:
    if not archive_path.is_file() or not index_path.is_file():
        raise ColdArchiveError("Cold archive or its external index is missing.")
    with index_path.open("r", encoding="utf-8") as handle:
        index = json.load(handle)
    if not isinstance(index, dict):
        raise ColdArchiveError("Cold archive index must be an object.")
    expected_archive_hash = index.get("archiveSha256")
    if expected_archive_hash != _sha256_file(archive_path):
        raise ColdArchiveError("Cold archive SHA-256 does not match its index.")

    with zipfile.ZipFile(archive_path, mode="r") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ColdArchiveError("Cold archive contains duplicate member names.")
        if MANIFEST_NAME not in names:
            raise ColdArchiveError("Cold archive manifest is missing.")
        manifest = _load_manifest(archive.read(MANIFEST_NAME))
        expected_names = {str(entry["path"]) for entry in manifest["entries"]} | {
            MANIFEST_NAME
        }
        if set(names) != expected_names:
            raise ColdArchiveError("Cold archive members do not match its manifest.")
        for entry in manifest["entries"]:
            data = archive.read(str(entry["path"]))
            if len(data) != int(entry["bytes"]):
                raise ColdArchiveError(f"Cold archive size mismatch: {entry['path']}")
            if _sha256_bytes(data) != entry["sha256"]:
                raise ColdArchiveError(
                    f"Cold archive checksum mismatch: {entry['path']}"
                )
        release_checksums = _verify_embedded_release_checksums(archive)

    manifest_without_archive_hash = {
        key: value for key, value in index.items() if key != "archiveSha256"
    }
    if manifest_without_archive_hash != manifest:
        raise ColdArchiveError("External cold archive index does not match the ZIP.")
    return {
        "valid": True,
        "version": ARCHIVE_VERSION,
        "archive": str(archive_path.resolve()),
        "archiveSha256": expected_archive_hash,
        "files": manifest["fileCount"],
        "sourceBytes": manifest["bytes"],
        "archiveBytes": archive_path.stat().st_size,
        "releaseChecksums": release_checksums,
    }


def _write_index(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    if path.exists() or temporary.exists():
        raise ColdArchiveError(f"Refusing to overwrite cold archive index: {path}")
    with temporary.open("xb") as handle:
        handle.write(_manifest_bytes(payload))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _remove_sources(artifacts_root: Path) -> None:
    for source in _source_roots(artifacts_root):
        if source.is_dir():
            shutil.rmtree(source)
        else:
            source.unlink()


def create_archive(
    *,
    project_root: Path = PROJECT_ROOT,
    apply: bool,
    confirmed: bool,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    artifacts_root = project_root / "artifacts"
    archive_path = artifacts_root / "cold-archive" / ARCHIVE_PATH.name
    index_path = artifacts_root / "cold-archive" / INDEX_PATH.name
    manifest = build_manifest(artifacts_root)
    preview = {
        "kind": "themescheduler.cold-artifact-archive-plan",
        "schemaVersion": 1,
        "version": ARCHIVE_VERSION,
        "mode": "apply" if apply else "preview",
        "sourceRoots": list(SOURCE_PATHS),
        "files": manifest["fileCount"],
        "sourceBytes": manifest["bytes"],
        "archive": str(archive_path.resolve(strict=False)),
        "index": str(index_path.resolve(strict=False)),
    }
    if not apply:
        return preview
    if not confirmed:
        raise ColdArchiveError("Real archiving requires --confirm-cold-archive.")
    if archive_path.exists() or index_path.exists():
        raise ColdArchiveError("Cold archive target already exists.")

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive_path.with_name(f".{archive_path.name}.tmp")
    if temporary.exists():
        raise ColdArchiveError(f"Cold archive temporary file exists: {temporary}")
    try:
        _write_zip(temporary, manifest, artifacts_root)
        os.replace(temporary, archive_path)
        index = {**manifest, "archiveSha256": _sha256_file(archive_path)}
        _write_index(index_path, index)
        verification = verify_archive(archive_path, index_path)
        if build_manifest(artifacts_root) != manifest:
            raise ColdArchiveError("Cold archive sources changed during creation.")
        _remove_sources(artifacts_root)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        **preview,
        **verification,
        "sourcesRemoved": True,
    }


def _display(payload: Mapping[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="themescheduler-cold-archive")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan")
    commands.add_parser("verify")
    archive = commands.add_parser("archive")
    archive.add_argument("--apply", action="store_true")
    archive.add_argument("--confirm-cold-archive", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "plan":
            result = create_archive(apply=False, confirmed=False)
        elif args.command == "verify":
            result = verify_archive()
        else:
            result = create_archive(
                apply=args.apply,
                confirmed=args.confirm_cold_archive,
            )
    except (ColdArchiveError, OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    _display(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
