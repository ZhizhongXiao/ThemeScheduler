from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class DocumentationContractTests(unittest.TestCase):
    def test_webview2_docs_describe_detection_and_stop_only_behavior(self) -> None:
        paths = (
            PROJECT_ROOT / "packaging" / "README.md",
            PROJECT_ROOT / "docs" / "ARCHITECTURE.md",
            PROJECT_ROOT / "docs" / "DESIGN.md",
            PROJECT_ROOT / "docs" / "INSTALLATION.md",
            PROJECT_ROOT / "docs" / "RELEASE.md",
            PROJECT_ROOT / "docs" / "USER_INSTALLATION.md",
            PROJECT_ROOT / "docs" / "AI_Rules.md",
            PROJECT_ROOT / "packaging" / "README.md",
        )
        combined = "\n".join(path.read_text(encoding="utf-8-sig") for path in paths)

        self.assertNotIn("Bootstrapper", combined)
        self.assertNotIn("Standalone Installer", combined)
        self.assertIn("原生错误提示", combined)
        self.assertIn("停止启动", combined)
        self.assertNotIn("缺失联网", combined)
        self.assertNotIn("缺失离线", combined)

    def test_scheduler_docs_describe_queue_policy(self) -> None:
        paths = (
            PROJECT_ROOT / "docs" / "SCHEDULER.md",
            PROJECT_ROOT / "docs" / "DESIGN.md",
            PROJECT_ROOT / "docs" / "ARCHITECTURE.md",
        )
        combined = "\n".join(path.read_text(encoding="utf-8-sig") for path in paths)

        self.assertIn("Queue", combined)
        self.assertNotIn("IgnoreNew", combined)

    def test_skipped_occurrence_persists_and_candidate_docs_preserve_history(
        self,
    ) -> None:
        scheduler = (PROJECT_ROOT / "docs" / "SCHEDULER.md").read_text(
            encoding="utf-8-sig"
        )
        notifications = (
            PROJECT_ROOT / "docs" / "NOTIFICATIONS_AND_HEALTH.md"
        ).read_text(encoding="utf-8-sig")
        candidate = (
            PROJECT_ROOT / "docs" / "CLEAN_MACHINE_ACCEPTANCE_1.0.1.md"
        ).read_text(encoding="utf-8-sig")
        historical = (PROJECT_ROOT / "docs" / "CLEAN_MACHINE_ACCEPTANCE.md").read_text(
            encoding="utf-8-sig"
        )
        release = (PROJECT_ROOT / "docs" / "RELEASE.md").read_text(encoding="utf-8-sig")

        self.assertIn("SKIPPED", scheduler)
        self.assertIn("nextFixedAt", scheduler)
        self.assertIn("关闭状态通知", scheduler)
        self.assertIn("SKIPPED", notifications)
        self.assertIn("1.0.1", candidate)
        self.assertIn("1.0.0", candidate)
        self.assertIn("手动启动一次", candidate)
        self.assertNotIn("C:\\TS142", candidate)
        self.assertNotIn("157 文件", candidate)
        self.assertIn("0.1.2", historical)
        self.assertIn("coverage_guard.py check", release)


if __name__ == "__main__":
    unittest.main()
