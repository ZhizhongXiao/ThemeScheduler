from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.artifacts import (
    ArtifactPlanError,
    build_plan,
    execute_cleanup,
    snapshot_path,
    verify_plan,
)


class ArtifactCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.artifacts = self.root / "artifacts"
        self.release = self.artifacts / "releases" / "0.1.0"
        self.release.mkdir(parents=True)
        distribution = self.release / "dist"
        distribution.mkdir()
        payload = distribution / "ThemeScheduler-Setup.exe"
        payload.write_bytes(b"release")
        digest = hashlib.sha256(payload.read_bytes()).hexdigest()
        (self.release / "SHA256SUMS.txt").write_text(
            f"{digest}  dist/ThemeScheduler-Setup.exe\n",
            encoding="utf-8",
        )
        self.stage14_release = self.artifacts / "releases" / "0.1.1"
        self.stage14_release.mkdir()
        stage14_distribution = self.stage14_release / "dist"
        stage14_distribution.mkdir()
        stage14_payload = stage14_distribution / "ThemeScheduler-Setup.exe"
        stage14_payload.write_bytes(b"release-0.1.1")
        stage14_digest = hashlib.sha256(stage14_payload.read_bytes()).hexdigest()
        (self.stage14_release / "SHA256SUMS.txt").write_text(
            f"{stage14_digest}  dist/ThemeScheduler-Setup.exe\n",
            encoding="utf-8",
        )
        self.stage17_release = self.artifacts / "releases" / "0.1.2"
        self.stage17_release.mkdir()
        stage17_distribution = self.stage17_release / "dist"
        stage17_distribution.mkdir()
        stage17_payload = stage17_distribution / "ThemeScheduler-Setup.exe"
        stage17_payload.write_bytes(b"release-0.1.2")
        stage17_digest = hashlib.sha256(stage17_payload.read_bytes()).hexdigest()
        (self.stage17_release / "SHA256SUMS.txt").write_text(
            f"{stage17_digest}  dist/ThemeScheduler-Setup.exe\n",
            encoding="utf-8",
        )
        old_build = self.artifacts / "build" / "stage9-7"
        old_build.mkdir(parents=True)
        (old_build / "old.exe").write_bytes(b"old")
        live = self.artifacts / "acceptance" / "stage10-live"
        live.mkdir(parents=True)
        (live / "installation-report.json").write_text(
            "{}\n",
            encoding="utf-8",
        )
        (live / "ThemeScheduler-Setup-MOTW.exe").write_bytes(b"release")
        (self.artifacts / "acceptance" / "stage10-final-release.json").write_text(
            "{}\n",
            encoding="utf-8",
        )
        stage14_live = self.artifacts / "acceptance" / "stage14-live"
        stage14_live.mkdir()
        (stage14_live / "final-uninstall-rc8-report.json").write_text(
            "{}\n",
            encoding="utf-8",
        )
        (stage14_live / "ThemeScheduler-Setup-Zone3.exe").write_bytes(b"duplicate")
        old_acceptance = self.artifacts / "acceptance" / "stage8-4"
        old_acceptance.mkdir()
        (old_acceptance / "payload.bin").write_bytes(b"old-evidence")
        reports = self.artifacts / "test-reports"
        reports.mkdir()
        (reports / "20260727T000308-release.json").write_text(
            "{}\n",
            encoding="utf-8",
        )
        (reports / "20260729T230507-release.json").write_text(
            "{}\n",
            encoding="utf-8",
        )
        (reports / "old.json").write_text("{}\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _plan(self) -> dict[str, object]:
        return build_plan(self.root)

    def test_plan_keeps_release_and_compact_latest_evidence(self) -> None:
        actions = {
            entry["path"]: entry["action"]
            for entry in self._plan()["entries"]  # type: ignore[index]
        }
        self.assertEqual(
            actions["artifacts/releases/0.1.0"],
            "keep",
        )
        self.assertEqual(
            actions["artifacts/releases/0.1.1"],
            "keep",
        )
        self.assertEqual(
            actions["artifacts/releases/0.1.2"],
            "keep",
        )
        self.assertEqual(
            actions["artifacts/acceptance/stage10-live/installation-report.json"],
            "keep",
        )
        self.assertEqual(
            actions["artifacts/test-reports/20260727T000308-release.json"],
            "keep",
        )
        self.assertEqual(
            actions["artifacts/test-reports/20260729T230507-release.json"],
            "keep",
        )
        self.assertEqual(
            actions[
                "artifacts/acceptance/stage14-live/final-uninstall-rc8-report.json"
            ],
            "keep",
        )

    def test_plan_deletes_superseded_payloads_and_reports(self) -> None:
        actions = {
            entry["path"]: entry["action"]
            for entry in self._plan()["entries"]  # type: ignore[index]
        }
        self.assertEqual(actions["artifacts/build/stage9-7"], "delete")
        self.assertEqual(
            actions["artifacts/acceptance/stage8-4"],
            "delete",
        )
        self.assertEqual(
            actions["artifacts/acceptance/stage10-live/ThemeScheduler-Setup-MOTW.exe"],
            "delete",
        )
        self.assertEqual(
            actions["artifacts/test-reports/old.json"],
            "delete",
        )
        self.assertEqual(
            actions["artifacts/acceptance/stage14-live/ThemeScheduler-Setup-Zone3.exe"],
            "delete",
        )

    def test_unknown_top_level_entry_requires_review(self) -> None:
        unknown = self.artifacts / "unexpected"
        unknown.mkdir()
        (unknown / "data.bin").write_bytes(b"unknown")
        actions = {
            entry["path"]: entry["action"]
            for entry in self._plan()["entries"]  # type: ignore[index]
        }
        self.assertEqual(actions["artifacts/unexpected"], "review")

    def test_unknown_versioned_release_requires_review(self) -> None:
        unknown = self.artifacts / "releases" / "9.9.9"
        unknown.mkdir()
        (unknown / "unknown.bin").write_bytes(b"unknown")
        actions = {
            entry["path"]: entry["action"]
            for entry in self._plan()["entries"]  # type: ignore[index]
        }
        self.assertEqual(actions["artifacts/releases/9.9.9"], "review")

    def test_preview_does_not_delete_anything(self) -> None:
        plan = self._plan()
        result = execute_cleanup(
            plan,
            self.root,
            apply=False,
            confirmed=False,
        )
        self.assertEqual(result["mode"], "preview")
        self.assertTrue((self.artifacts / "build" / "stage9-7").exists())
        self.assertTrue(self.release.exists())
        self.assertTrue(self.stage14_release.exists())

    def test_apply_deletes_only_planned_entries(self) -> None:
        plan = self._plan()
        result = execute_cleanup(
            plan,
            self.root,
            apply=True,
            confirmed=True,
        )
        self.assertTrue(result["completed"])
        self.assertFalse((self.artifacts / "build" / "stage9-7").exists())
        self.assertFalse((self.artifacts / "acceptance" / "stage8-4").exists())
        self.assertTrue(self.release.exists())
        self.assertTrue(
            (
                self.artifacts
                / "acceptance"
                / "stage10-live"
                / "installation-report.json"
            ).exists()
        )

    def test_apply_requires_explicit_confirmation(self) -> None:
        with self.assertRaisesRegex(
            ArtifactPlanError,
            "confirm-artifact-cleanup",
        ):
            execute_cleanup(
                self._plan(),
                self.root,
                apply=True,
                confirmed=False,
            )

    def test_changed_candidate_invalidates_plan(self) -> None:
        plan = self._plan()
        changed = self.artifacts / "build" / "stage9-7" / "new.bin"
        changed.write_bytes(b"changed")
        with self.assertRaisesRegex(
            ArtifactPlanError,
            "changed since planning",
        ):
            verify_plan(plan, self.root)

    def test_artifact_root_cannot_be_snapshotted(self) -> None:
        with self.assertRaisesRegex(
            ArtifactPlanError,
            "never targets",
        ):
            snapshot_path(self.artifacts, self.root)


if __name__ == "__main__":
    unittest.main()
