from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.task_bridge_benchmark import (
    capture_input_identity,
    evaluate_batch_read,
    measure,
    percentile,
    summarize,
)


class TaskBridgeBenchmarkTests(unittest.TestCase):
    def test_percentile_uses_linear_interpolation(self) -> None:
        self.assertEqual(percentile([1.0, 2.0, 3.0, 4.0], 50), 2.5)
        self.assertAlmostEqual(percentile([1.0, 2.0, 3.0, 4.0], 95), 3.85)
        with self.assertRaisesRegex(ValueError, "At least one"):
            percentile([], 50)

    def test_summary_is_milliseconds_and_keeps_samples(self) -> None:
        result = summarize([0.1, 0.2, 0.3])
        self.assertEqual(result["sampleCount"], 3)
        self.assertEqual(result["samplesMs"], [100.0, 200.0, 300.0])
        self.assertEqual(result["p50Ms"], 200.0)
        self.assertEqual(result["p95Ms"], 290.0)
        self.assertEqual(result["maxMs"], 300.0)

    def test_measure_excludes_warmups_and_returns_last_result(self) -> None:
        calls: list[int] = []

        def operation() -> int:
            calls.append(len(calls) + 1)
            return calls[-1]

        timings, result = measure(operation, warmups=2, samples=3)
        self.assertEqual(len(calls), 5)
        self.assertEqual(len(timings), 3)
        self.assertEqual(result, 5)

    def test_batch_read_requires_every_threshold(self) -> None:
        recommended = evaluate_batch_read(
            health_p50_ms=1000.1,
            task_launch_count=2,
            task_share_p50=50.0,
        )
        self.assertTrue(recommended["recommendBatchRead"])
        self.assertEqual(recommended["decision"], "implement-batched-read")

        for values in (
            (1000.0, 2, 50.0),
            (1000.1, 1, 50.0),
            (1000.1, 2, 49.9),
        ):
            with self.subTest(values=values):
                retained = evaluate_batch_read(
                    health_p50_ms=values[0],
                    task_launch_count=values[1],
                    task_share_p50=values[2],
                )
                self.assertFalse(retained["recommendBatchRead"])
                self.assertEqual(retained["decision"], "retain-single-operation-bridge")

    def test_report_kind_and_schema_are_stable(self) -> None:
        from tools import task_bridge_benchmark

        self.assertEqual(
            task_bridge_benchmark.KIND,
            "themescheduler.task-bridge-benchmark",
        )
        self.assertEqual(task_bridge_benchmark.SCHEMA_VERSION, 1)

    def test_input_identity_excludes_documentation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src/theme_scheduler").mkdir(parents=True)
            (root / "entrypoints").mkdir()
            (root / "tools").mkdir()
            (root / "docs").mkdir()
            (root / "src/theme_scheduler/example.py").write_text("value = 1\n")
            (root / "entrypoints/bridge.ps1").write_text("exit 0\n")
            (root / "tools/task_bridge_benchmark.py").write_text("tool\n")
            (root / "pyproject.toml").write_text("config\n")
            (root / "uv.lock").write_text("lock\n")
            documentation = root / "docs/TESTING.md"
            documentation.write_text("before\n")

            before = capture_input_identity(root)
            documentation.write_text("after\n")
            after = capture_input_identity(root)

            self.assertEqual(before, after)
            self.assertEqual(before["fileCount"], 5)


if __name__ == "__main__":
    unittest.main()
