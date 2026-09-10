"""Assemble and hash one immutable Stage-8 release payload."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from theme_scheduler.lifecycle import PayloadManifest  # noqa: E402

WINDOWS_REPLACE_ATTEMPTS = 6
WINDOWS_REPLACE_INITIAL_DELAY_SECONDS = 0.25


def _reset_windows_acl_inheritance(root: Path) -> None:
    """Make release files executable by the account receiving the artifact.

    Some isolated Windows build accounts create temporary directories with a
    protected owner-only ACL.  Moving such a directory would preserve that ACL
    and make an otherwise valid EXE return Access Denied for the developer.
    Release artifacts therefore inherit the destination parent's normal ACL.
    """

    if os.name != "nt":
        return
    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    icacls = Path(system_root) / "System32" / "icacls.exe"
    if not icacls.is_file():
        raise OSError(f"Windows ACL tool is missing: {icacls}")
    for operation in ("/inheritance:e", "/reset"):
        completed = subprocess.run(
            [
                str(icacls),
                str(root),
                operation,
                "/T",
                "/C",
                "/Q",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise OSError(
                "Cannot reset release ACL inheritance "
                f"with {operation}: "
                f"{detail or f'exit code {completed.returncode}'}"
            )


def _replace_staging_directory(staging: Path, output_root: Path) -> None:
    """Commit a prepared payload despite short-lived Windows file scanners."""

    attempts = WINDOWS_REPLACE_ATTEMPTS if os.name == "nt" else 1
    delay = WINDOWS_REPLACE_INITIAL_DELAY_SECONDS
    for attempt in range(1, attempts + 1):
        try:
            os.replace(staging, output_root)
            return
        except PermissionError as exc:
            if attempt == attempts:
                raise PermissionError(
                    f"Cannot commit release payload after {attempts} attempts: {exc}"
                ) from exc
            time.sleep(delay)
            delay *= 2


def assemble_payload(
    *,
    app_dist: Path,
    uninstaller: Path,
    output_root: Path,
    version: str,
) -> tuple[Path, Path, PayloadManifest]:
    """Copy build products into a new bundle and capture its exact manifest."""

    app_dist = Path(app_dist).resolve(strict=True)
    uninstaller = Path(uninstaller).resolve(strict=True)
    output_root = Path(output_root).resolve(strict=False)
    if not app_dist.is_dir():
        raise ValueError("app_dist must be a PyInstaller onedir directory.")
    if not (app_dist / "ThemeScheduler.exe").is_file():
        raise ValueError("app_dist does not contain ThemeScheduler.exe.")
    if not uninstaller.is_file() or uninstaller.name != "Uninstall.exe":
        raise ValueError("uninstaller must identify Uninstall.exe.")
    if output_root.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing output root: {output_root}"
        )

    output_root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{output_root.name}.",
            suffix=".tmp",
            dir=output_root.parent,
        )
    )
    try:
        payload_root = staging / "payload"
        shutil.copytree(app_dist, payload_root / "app")
        (payload_root / "maintenance").mkdir(parents=True)
        shutil.copy2(
            uninstaller,
            payload_root / "maintenance" / "Uninstall.exe",
        )
        manifest = PayloadManifest.capture(payload_root, version=version)
        manifest_path = staging / "payload-manifest.json"
        manifest_path.write_text(
            json.dumps(
                manifest.as_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        _reset_windows_acl_inheritance(staging)
        _replace_staging_directory(staging, output_root)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return (
        output_root / "payload",
        output_root / "payload-manifest.json",
        manifest,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="build-theme-scheduler-payload")
    parser.add_argument("--app-dist", type=Path, required=True)
    parser.add_argument("--uninstaller", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--version", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload, manifest_path, manifest = assemble_payload(
            app_dist=args.app_dist,
            uninstaller=args.uninstaller,
            output_root=args.output_root,
            version=args.version,
        )
        print(
            json.dumps(
                {
                    "payload": str(payload),
                    "manifest": str(manifest_path),
                    "manifestSha256": manifest.document_sha256,
                    "fileCount": len(manifest.files),
                    "verified": True,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except (FileExistsError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
