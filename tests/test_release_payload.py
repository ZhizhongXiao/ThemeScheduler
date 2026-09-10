from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]

from theme_scheduler.lifecycle import (
    PayloadManifest,
)

SPEC = importlib.util.spec_from_file_location(
    "themescheduler_build_payload",
    PROJECT_ROOT / "packaging" / "build_payload.py",
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Cannot load packaging/build_payload.py")
BUILD_PAYLOAD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD_PAYLOAD)

RELEASE_SPEC = importlib.util.spec_from_file_location(
    "themescheduler_build_release",
    PROJECT_ROOT / "packaging" / "build_release.py",
)
if RELEASE_SPEC is None or RELEASE_SPEC.loader is None:
    raise RuntimeError("Cannot load packaging/build_release.py")
BUILD_RELEASE = importlib.util.module_from_spec(RELEASE_SPEC)
RELEASE_SPEC.loader.exec_module(BUILD_RELEASE)


class ReleasePayloadTests(unittest.TestCase):
    def test_windows_payload_commit_retries_transient_access_denied(self) -> None:
        calls: list[tuple[Path, Path]] = []

        def transient_replace(source: Path, destination: Path) -> None:
            calls.append((source, destination))
            if len(calls) < 3:
                raise PermissionError(5, "simulated scanner lock")

        with (
            patch.object(BUILD_PAYLOAD.os, "name", "nt"),
            patch.object(BUILD_PAYLOAD.os, "replace", side_effect=transient_replace),
            patch.object(BUILD_PAYLOAD.time, "sleep") as sleep,
        ):
            BUILD_PAYLOAD._replace_staging_directory(
                Path("staging"),
                Path("output"),
            )

        self.assertEqual(len(calls), 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.25, 0.5])

    def test_payload_commit_does_not_retry_non_permission_errors(self) -> None:
        with (
            patch.object(BUILD_PAYLOAD.os, "name", "nt"),
            patch.object(
                BUILD_PAYLOAD.os,
                "replace",
                side_effect=FileNotFoundError("simulated missing source"),
            ) as replace,
            self.assertRaisesRegex(FileNotFoundError, "missing source"),
        ):
            BUILD_PAYLOAD._replace_staging_directory(
                Path("staging"),
                Path("output"),
            )

        replace.assert_called_once()

    def test_release_cleanup_is_confined_to_the_declared_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            child = root / "artifacts" / ".candidate.work"
            child.mkdir(parents=True)
            (child / "temporary.txt").write_text(
                "temporary",
                encoding="utf-8",
            )

            BUILD_RELEASE._safe_rmtree(child, root)

            self.assertFalse(child.exists())
            with self.assertRaisesRegex(ValueError, "below the project root"):
                BUILD_RELEASE._safe_rmtree(root, root)
            with self.assertRaisesRegex(ValueError, "below the project root"):
                BUILD_RELEASE._safe_rmtree(root.parent, root)

    def test_uninstaller_bundle_contains_its_own_minimal_bridges(self) -> None:
        spec = (PROJECT_ROOT / "packaging" / "ThemeScheduler.spec").read_text(
            encoding="utf-8"
        )

        self.assertIn("uninstall_datas", spec)
        self.assertIn("theme_manager_bridge.ps1", spec)
        self.assertIn("task_scheduler_bridge.ps1", spec)
        self.assertIn("notification_bridge.ps1", spec)
        self.assertIn("shortcut_bridge.ps1", spec)
        self.assertIn("uninstall_cleanup.ps1", spec)
        uninstall_section = spec.split(
            "uninstall_analysis = Analysis(",
            1,
        )[1]
        self.assertIn("datas=uninstall_datas", uninstall_section)
        self.assertIn("version_info_uninstall.txt", spec)
        self.assertIn("ThemeScheduler.ico", spec)

        setup_spec = (
            PROJECT_ROOT / "packaging" / "ThemeSchedulerSetup.spec"
        ).read_text(encoding="utf-8")
        self.assertIn("version_info_setup.txt", setup_spec)
        self.assertIn("ThemeScheduler.ico", setup_spec)

    def test_release_output_separates_distribution_and_evidence(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "build_release.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('distribution = output_root / "dist"', source)
        self.assertIn('evidence = output_root / "evidence"', source)
        self.assertIn(
            'shutil.copytree(bundle, evidence / "bundle-evidence")',
            source,
        )
        self.assertIn(
            'distribution / "SHA256SUMS.txt"',
            source,
        )
        self.assertIn(
            'release_manifest = evidence / "release-manifest.json"',
            source,
        )
        self.assertIn(
            'write_json(evidence / "quality-report.json", quality_report)',
            source,
        )
        self.assertIn(
            'evidence / "quality-report.json"',
            source,
        )
        self.assertIn(
            'release_layout = output_root / "release-layout.json"',
            source,
        )
        self.assertIn('"distributionRoot": "dist"', source)
        self.assertIn('"evidenceRoot": "evidence"', source)
        self.assertNotIn(
            'output_root / "bundle-evidence"',
            source,
        )

    def test_assembly_creates_exact_verified_tree_and_sidecar_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "dist" / "ThemeScheduler"
            (app / "_internal").mkdir(parents=True)
            (app / "ThemeScheduler.exe").write_bytes(b"main")
            (app / "_internal" / "python.dll").write_bytes(b"runtime")
            uninstall = root / "dist" / "Uninstall.exe"
            uninstall.write_bytes(b"uninstall")
            output = root / "release"

            payload, manifest_path, manifest = BUILD_PAYLOAD.assemble_payload(
                app_dist=app,
                uninstaller=uninstall,
                output_root=output,
                version="1.2.3",
            )

            loaded = PayloadManifest.from_dict(
                __import__("json").loads(manifest_path.read_text(encoding="utf-8"))
            )
            self.assertEqual(loaded, manifest)
            loaded.verify_tree(payload)
            self.assertTrue((payload / "app" / "ThemeScheduler.exe").is_file())
            self.assertTrue((payload / "maintenance" / "Uninstall.exe").is_file())
            self.assertFalse((payload / "app" / "Uninstall.exe").exists())
            if sys.platform == "win32":
                import subprocess

                inherited = subprocess.run(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-Command",
                        (
                            "$acl=Get-Acl -LiteralPath "
                            f"'{str(output).replace("'", "''")}';"
                            "$acl | Select-Object AreAccessRulesProtected,Sddl "
                            "| ConvertTo-Json -Compress;"
                            "if ($acl.AreAccessRulesProtected) { exit 1 }"
                        ),
                    ],
                    check=False,
                    capture_output=True,
                )
                self.assertEqual(
                    inherited.returncode,
                    0,
                    msg=(
                        f"stdout={inherited.stdout.decode(errors='replace')!r}; "
                        f"stderr={inherited.stderr.decode(errors='replace')!r}"
                    ),
                )

    def test_assembly_refuses_existing_output_and_missing_entrypoints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "ThemeScheduler"
            app.mkdir()
            uninstall = root / "Uninstall.exe"
            uninstall.write_bytes(b"uninstall")

            with self.assertRaisesRegex(ValueError, "ThemeScheduler.exe"):
                BUILD_PAYLOAD.assemble_payload(
                    app_dist=app,
                    uninstaller=uninstall,
                    output_root=root / "release",
                    version="1.2.3",
                )

            (app / "ThemeScheduler.exe").write_bytes(b"main")
            output = root / "release"
            output.mkdir()
            with self.assertRaisesRegex(FileExistsError, "overwrite"):
                BUILD_PAYLOAD.assemble_payload(
                    app_dist=app,
                    uninstaller=uninstall,
                    output_root=output,
                    version="1.2.3",
                )


if __name__ == "__main__":
    unittest.main()
