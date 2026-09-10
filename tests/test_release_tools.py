from __future__ import annotations

import importlib.util
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGING_ROOT = PROJECT_ROOT / "packaging"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RELEASE_TOOLS = _load(
    "themescheduler_release_tools",
    PACKAGING_ROOT / "release_tools.py",
)
ICON = _load(
    "themescheduler_generate_icon",
    PACKAGING_ROOT / "generate_icon.py",
)


class ReleaseConfigurationTests(unittest.TestCase):
    def test_quality_report_uses_locked_static_lint_and_format_gates(self) -> None:
        commands: list[list[str]] = []

        def runner(command, **_kwargs):
            command = list(command)
            commands.append(command)
            if command[2:] == ["ruff", "--version"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="ruff 0.15.20\n",
                    stderr="",
                )
            if command[2:] == ["pyright", "--version"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="pyright 1.1.411\n",
                    stderr="",
                )
            if command[2:] == ["pyright", "--outputjson"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(
                        {
                            "summary": {
                                "filesAnalyzed": 111,
                                "errorCount": 0,
                                "warningCount": 0,
                                "informationCount": 0,
                            }
                        }
                    ),
                    stderr="",
                )
            if command[2:4] == ["ruff", "format"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="195 files already formatted\n",
                    stderr="",
                )
            if "--select" in command:
                finding = {
                    "code": "C901",
                    "filename": "src/theme_scheduler/example.py",
                    "location": {"row": 10, "column": 1},
                    "message": "example complexity",
                }
                return subprocess.CompletedProcess(
                    command,
                    1,
                    stdout=json.dumps([finding]),
                    stderr="",
                )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="[]",
                stderr="",
            )

        source = RELEASE_TOOLS.capture_source_manifest(PROJECT_ROOT)
        report = RELEASE_TOOLS.capture_quality_report(
            PROJECT_ROOT,
            Path(sys.executable),
            source_identity=source["sourceTreeSha256"],
            runner=runner,
        )

        self.assertEqual(report["kind"], "themescheduler.quality-report")
        self.assertEqual(report["schemaVersion"], 2)
        self.assertEqual(report["tools"]["ruff"]["version"], "0.15.20")
        self.assertEqual(report["tools"]["pyright"]["version"], "1.1.411")
        self.assertTrue(report["staticTyping"]["passed"])
        self.assertEqual(report["staticTyping"]["filesAnalyzed"], 111)
        self.assertTrue(report["lint"]["passed"])
        self.assertEqual(report["lint"]["findingCount"], 0)
        self.assertTrue(report["format"]["passed"])
        self.assertFalse(report["complexity"]["blocking"])
        self.assertEqual(report["complexity"]["findingCount"], 1)
        self.assertEqual(
            report["complexity"]["findingCount"],
            sum(report["complexity"]["byRule"].values()),
        )
        self.assertTrue(
            all(
                not Path(item["path"]).is_absolute()
                for item in report["complexity"]["findings"]
            )
        )
        quality_commands = [
            command
            for command in commands
            if command[2:4] in (["pyright", "--outputjson"], ["ruff", "check"])
            or command[2:4] == ["ruff", "format"]
        ]
        self.assertEqual(len(quality_commands), 4)
        self.assertTrue(
            all(
                "--no-cache" in command
                for command in quality_commands
                if command[2] == "ruff"
            )
        )

    def test_release_version_resources_and_icon_are_consistent(self) -> None:
        version = RELEASE_TOOLS.project_version(PROJECT_ROOT)
        RELEASE_TOOLS.validate_release_configuration(
            PROJECT_ROOT,
            version,
        )

        with self.assertRaisesRegex(ValueError, "pyproject"):
            RELEASE_TOOLS.validate_release_configuration(
                PROJECT_ROOT,
                "0.0.0",
            )

    def test_manifest_configuration_rejects_missing_include(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "MANIFEST.in").write_text(
                "include missing.json\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(FileNotFoundError, "missing file"):
                RELEASE_TOOLS.validate_manifest_configuration(root)

    def test_source_manifest_is_stable_and_excludes_generated_files(self) -> None:
        first = RELEASE_TOOLS.capture_source_manifest(PROJECT_ROOT)
        second = RELEASE_TOOLS.capture_source_manifest(PROJECT_ROOT)

        self.assertEqual(first, second)
        self.assertEqual(first["kind"], "themescheduler.source-manifest")
        self.assertRegex(first["sourceTreeSha256"], r"^[0-9a-f]{64}$")
        paths = {item["path"] for item in first["files"]}
        self.assertIn("src/theme_scheduler/cli/app.py", paths)
        self.assertIn("pyproject.toml", paths)
        self.assertNotIn("pyrightconfig.json", paths)
        self.assertIn("MANIFEST.in", paths)
        self.assertIn("docs/CODE_QUALITY.md", paths)
        self.assertIn("src/theme_scheduler/lifecycle/payload.py", paths)
        self.assertIn(
            "src/theme_scheduler/lifecycle/deployment/service.py",
            paths,
        )
        self.assertIn("src/theme_scheduler/automation/runner.py", paths)
        self.assertIn("src/theme_scheduler/errors.py", paths)
        self.assertNotIn("tests/coverage_baseline.json", paths)
        self.assertIn(
            "src/theme_scheduler/scheduler/specification.py",
            paths,
        )
        self.assertIn("src/theme_scheduler/workbench/api.py", paths)
        self.assertIn(
            "src/theme_scheduler/workbench/contracts.py",
            paths,
        )
        self.assertNotIn("src/theme_scheduler/install_contracts.py", paths)
        self.assertNotIn("src/theme_scheduler/deployment.py", paths)
        self.assertNotIn("src/theme_scheduler/auto_service.py", paths)
        self.assertNotIn("src/theme_scheduler/gui_api.py", paths)
        self.assertNotIn("src/theme_scheduler/scheduler.py", paths)
        self.assertIn("ui/html/workbench.html", paths)
        self.assertIn("ui/css/workbench.css", paths)
        self.assertIn("assets/ThemeScheduler.ico", paths)
        self.assertNotIn("artifacts", {path.split("/", 1)[0] for path in paths})
        self.assertFalse(any("__pycache__" in path for path in paths))
        self.assertFalse(any(".egg-info/" in path for path in paths))

    def test_generated_icon_matches_frozen_asset(self) -> None:
        frozen = (PROJECT_ROOT / "assets" / "ThemeScheduler.ico").read_bytes()

        self.assertEqual(ICON.icon_bytes(), frozen)
        reserved, kind, count = struct.unpack_from("<HHH", frozen, 0)
        self.assertEqual((reserved, kind, count), (0, 1, 7))

    def test_icon_writer_is_idempotent_and_refuses_different_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "ThemeScheduler.ico"
            ICON.write_icon(target)
            ICON.write_icon(target)
            target.write_bytes(b"different")
            with self.assertRaisesRegex(FileExistsError, "different icon"):
                ICON.write_icon(target)


class ReleaseEvidenceTests(unittest.TestCase):
    def _write_minimal_pe(
        self,
        path: Path,
        *,
        machine: int = 0x8664,
        magic: int = 0x20B,
        subsystem: int = 2,
    ) -> None:
        data = bytearray(512)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 0x3C, 0x80)
        data[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<H", data, 0x84, machine)
        struct.pack_into("<H", data, 0x98, magic)
        struct.pack_into("<H", data, 0x98 + 68, subsystem)
        path.write_bytes(data)

    def test_pe_inspection_requires_x64_gui(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / "valid.exe"
            self._write_minimal_pe(valid)

            result = RELEASE_TOOLS.inspect_pe(valid)
            self.assertEqual(result["machine"], "x64")
            self.assertEqual(result["format"], "PE32+")
            self.assertEqual(result["subsystem"], "windows-gui")

            console = root / "console.exe"
            self._write_minimal_pe(console, subsystem=3)
            with self.assertRaisesRegex(ValueError, "Windows GUI"):
                RELEASE_TOOLS.inspect_pe(console)

    def test_release_readme_discloses_unsigned_distribution(self) -> None:
        text = RELEASE_TOOLS.distribution_readme(
            version="0.1.1",
            setup_sha256="a" * 64,
        )

        self.assertIn("未使用 Authenticode", text)
        self.assertIn("SmartScreen", text)
        self.assertIn("A" * 64, text)
        self.assertIn("不要关闭 Defender", text)
        self.assertIn("保存并启用", text)
        self.assertIn("修复/重装", text)
        self.assertIn("恢复安装前外观", text)
        self.assertIn("保留配置及昼夜颜色", text)
        self.assertIn("%LOCALAPPDATA%\\ThemeScheduler", text)

    def test_write_json_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "record.json"
            RELEASE_TOOLS.write_json(target, {"value": 1})
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"value": 1},
            )
            with self.assertRaisesRegex(FileExistsError, "overwrite"):
                RELEASE_TOOLS.write_json(target, {"value": 2})

    def test_release_report_must_match_current_source(self) -> None:
        report_root = PROJECT_ROOT / "artifacts" / "test-reports"
        report_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            dir=report_root,
            prefix="release-report-test-",
        ) as directory:
            report = Path(directory) / "release.json"
            payload = {
                "kind": "themescheduler.test-report",
                "schemaVersion": 2,
                "capturedAt": "2026-07-26T18:00:00+08:00",
                "mode": "release",
                "group": None,
                "patterns": ["test_*.py"],
                "testsRun": 1,
                "failures": 0,
                "errors": 0,
                "skipped": 0,
                "durationSeconds": 0.1,
                "checks": [
                    {"checkId": check_id, "returnCode": 0}
                    for check_id in sorted(RELEASE_TOOLS.REQUIRED_RELEASE_CHECK_IDS)
                ],
                "success": True,
                "sourceIdentity": "a" * 64,
            }
            report.write_text(
                json.dumps(payload),
                encoding="utf-8",
            )

            loaded = RELEASE_TOOLS.validate_release_test_report(
                report,
                project_root=PROJECT_ROOT,
                source_identity="a" * 64,
            )
            self.assertEqual(loaded["testsRun"], 1)

            with self.assertRaisesRegex(ValueError, "different source"):
                RELEASE_TOOLS.validate_release_test_report(
                    report,
                    project_root=PROJECT_ROOT,
                    source_identity="b" * 64,
                )

            payload["checks"].pop()
            report.write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "auxiliary checks"):
                RELEASE_TOOLS.validate_release_test_report(
                    report,
                    project_root=PROJECT_ROOT,
                    source_identity="a" * 64,
                )


if __name__ == "__main__":
    unittest.main()
