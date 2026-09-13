from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from tools.cold_archive import (
    SOURCE_PATHS,
    ColdArchiveError,
    create_archive,
    verify_archive,
)


class ColdArchiveTests(unittest.TestCase):
    def _project(self, root: Path) -> Path:
        project = root / "project"
        artifacts = project / "artifacts"
        for index, relative in enumerate(SOURCE_PATHS):
            target = artifacts / relative
            if relative == "releases/0.1.0":
                payload = target / "dist" / "sample.bin"
                payload.parent.mkdir(parents=True, exist_ok=True)
                payload.write_bytes(b"accepted-release")
                checksum = hashlib.sha256(payload.read_bytes()).hexdigest().upper()
                (target / "SHA256SUMS.txt").write_text(
                    f"{checksum}  dist/sample.bin\n",
                    encoding="utf-8",
                )
                continue
            if target.suffix:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f"file-{index}", encoding="utf-8")
            else:
                target.mkdir(parents=True, exist_ok=True)
                (target / f"sample-{index}.json").write_text(
                    f'{{"index": {index}}}\n',
                    encoding="utf-8",
                )
        return project

    def test_preview_does_not_write_or_remove_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = self._project(Path(temporary))
            result = create_archive(
                project_root=project,
                apply=False,
                confirmed=False,
            )

            self.assertEqual(result["mode"], "preview")
            self.assertFalse((project / "artifacts" / "cold-archive").exists())
            for relative in SOURCE_PATHS:
                self.assertTrue((project / "artifacts" / relative).exists())

    def test_archive_is_verified_before_sources_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = self._project(Path(temporary))
            result = create_archive(
                project_root=project,
                apply=True,
                confirmed=True,
            )
            archive = project / "artifacts" / "cold-archive"

            self.assertTrue(result["valid"])
            self.assertTrue(result["sourcesRemoved"])
            self.assertEqual(
                verify_archive(
                    archive / "ThemeScheduler-0.1.0.zip",
                    archive / "ThemeScheduler-0.1.0.json",
                )["files"],
                len(SOURCE_PATHS) + 1,
            )
            for relative in SOURCE_PATHS:
                self.assertFalse((project / "artifacts" / relative).exists())

    def test_tampered_archive_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = self._project(Path(temporary))
            create_archive(
                project_root=project,
                apply=True,
                confirmed=True,
            )
            archive = project / "artifacts" / "cold-archive"
            archive_path = archive / "ThemeScheduler-0.1.0.zip"
            with archive_path.open("ab") as handle:
                handle.write(b"tampered")

            with self.assertRaisesRegex(ColdArchiveError, "SHA-256"):
                verify_archive(
                    archive_path,
                    archive / "ThemeScheduler-0.1.0.json",
                )


if __name__ == "__main__":
    unittest.main()
