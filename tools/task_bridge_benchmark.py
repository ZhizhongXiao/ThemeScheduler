"""Read-only Task Scheduler bridge and health-check performance benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from theme_scheduler.automation import WindowsAutoBackend
from theme_scheduler.health_service import HealthService
from theme_scheduler.lifecycle import InstallLayout
from theme_scheduler.notification_contracts import APP_USER_MODEL_ID
from theme_scheduler.notifications_windows import WindowsNotificationBackend
from theme_scheduler.protocol_registration_windows import (
    WindowsNotificationProtocolBackend,
)
from theme_scheduler.scheduler import DEFAULT_TASK_PATH, TaskSpec
from theme_scheduler.scheduler_windows import WindowsTaskSchedulerBackend
from theme_scheduler.storage import UserDataLayout
from theme_scheduler.system_integration import ShortcutPlan
from theme_scheduler.system_integration_windows import (
    WindowsKnownFolderReader,
    WindowsShortcutBackend,
)
from theme_scheduler.windows_subprocess import no_window_options

PROJECT_ROOT = Path(__file__).resolve().parents[1]
KIND = "themescheduler.task-bridge-benchmark"
SCHEMA_VERSION = 1
DEFAULT_OUTPUT = Path("artifacts/quality/task-bridge-benchmark.json")


def capture_input_identity(project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    roots = (
        project_root / "src" / "theme_scheduler",
        project_root / "entrypoints",
    )
    paths = [
        path
        for root in roots
        for path in root.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".py", ".ps1"}
    ]
    paths.extend(
        (
            project_root / "tools" / "task_bridge_benchmark.py",
            project_root / "pyproject.toml",
            project_root / "uv.lock",
        )
    )
    entries: list[dict[str, Any]] = []
    identity = hashlib.sha256()
    for path in sorted(set(paths)):
        content = path.read_bytes()
        relative = path.relative_to(project_root).as_posix()
        digest = hashlib.sha256(content).hexdigest()
        entries.append({"path": relative, "size": len(content), "sha256": digest})
        identity.update(f"{relative}\0{len(content)}\0{digest}\n".encode())
    return {
        "sha256": identity.hexdigest(),
        "fileCount": len(entries),
        "files": entries,
    }


def percentile(samples: Sequence[float], percentage: float) -> float:
    if not samples:
        raise ValueError("At least one timing sample is required.")
    if not 0 <= percentage <= 100:
        raise ValueError("Percentile must be between 0 and 100.")
    ordered = sorted(samples)
    position = (len(ordered) - 1) * percentage / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def summarize(samples: Sequence[float]) -> dict[str, Any]:
    if not samples:
        raise ValueError("At least one timing sample is required.")
    milliseconds = [sample * 1000 for sample in samples]
    return {
        "sampleCount": len(milliseconds),
        "samplesMs": [round(value, 3) for value in milliseconds],
        "meanMs": round(sum(milliseconds) / len(milliseconds), 3),
        "p50Ms": round(percentile(milliseconds, 50), 3),
        "p95Ms": round(percentile(milliseconds, 95), 3),
        "maxMs": round(max(milliseconds), 3),
    }


def measure[T](
    operation: Callable[[], T],
    *,
    warmups: int,
    samples: int,
) -> tuple[list[float], T]:
    if warmups < 0 or samples < 1:
        raise ValueError("Warmups must be non-negative and samples must be positive.")
    result: T
    for _ in range(warmups):
        result = operation()
    timings: list[float] = []
    for _ in range(samples):
        started = time.perf_counter()
        result = operation()
        timings.append(time.perf_counter() - started)
    return timings, result


def evaluate_batch_read(
    *,
    health_p50_ms: float,
    task_launch_count: int,
    task_share_p50: float,
) -> dict[str, Any]:
    health_slow = health_p50_ms > 1000
    repeated_launches = task_launch_count >= 2
    task_dominates = task_share_p50 >= 50
    recommended = health_slow and repeated_launches and task_dominates
    return {
        "thresholds": {
            "healthP50MsGreaterThan": 1000,
            "minimumTaskBridgeLaunches": 2,
            "taskBridgeSharePercentAtLeast": 50,
        },
        "observed": {
            "healthP50Ms": round(health_p50_ms, 3),
            "taskBridgeLaunchesPerHealthCheck": task_launch_count,
            "taskBridgeShareP50Percent": round(task_share_p50, 3),
        },
        "recommendBatchRead": recommended,
        "decision": (
            "implement-batched-read"
            if recommended
            else "retain-single-operation-bridge"
        ),
    }


class _RecordingScheduler:
    def __init__(self, backend: WindowsTaskSchedulerBackend) -> None:
        self.backend = backend
        self.read_durations: list[float] = []

    def read(self, task_path: str) -> TaskSpec | None:
        started = time.perf_counter()
        try:
            return self.backend.read(task_path)
        finally:
            self.read_durations.append(time.perf_counter() - started)


def _run_powershell(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8-sig",
        errors="replace",
        timeout=30,
        **no_window_options(),
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"PowerShell probe failed: {detail or completed.returncode}")
    return completed


def _powershell_command(*arguments: str) -> list[str]:
    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    executable = (
        Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )
    if not executable.is_file():
        raise FileNotFoundError(f"Windows PowerShell is missing: {executable}")
    return [str(executable), "-NoProfile", "-NonInteractive", *arguments]


def _health_once(
    install_layout: InstallLayout,
    layout: UserDataLayout,
    scheduler: WindowsTaskSchedulerBackend,
) -> tuple[str, list[float]]:
    task_durations: list[float] = []
    started = time.perf_counter()
    user_id = scheduler.current_user_id()
    task_durations.append(time.perf_counter() - started)
    recording_scheduler = _RecordingScheduler(scheduler)
    known_folders = WindowsKnownFolderReader()
    shortcut_plan = ShortcutPlan.create(
        install_layout,
        programs_folder=known_folders.programs(),
        desktop_folder=known_folders.desktop(),
        desktop_enabled=False,
    )
    service = HealthService(
        layout,
        install_layout,
        recording_scheduler,
        WindowsShortcutBackend(shortcut_plan.managed_paths),
        shortcut_plan,
        WindowsNotificationProtocolBackend(),
        user_id=user_id,
        windows_probe=WindowsAutoBackend(layout).probe,
        notification_probe=WindowsNotificationBackend().probe,
        process_identity_reader=lambda: APP_USER_MODEL_ID,
        deep_permissions=True,
    )
    report = service.inspect()
    task_durations.extend(recording_scheduler.read_durations)
    return report.status.value, task_durations


def run_benchmark(
    *,
    warmups: int,
    samples: int,
    burst_size: int,
    task_path: str,
) -> dict[str, Any]:
    if os.name != "nt":
        raise OSError("The task bridge benchmark requires Windows.")
    if burst_size < 2:
        raise ValueError("Burst size must be at least two reads.")
    install_layout = InstallLayout.default()
    layout = UserDataLayout(install_layout.data_root)
    if not install_layout.executable.is_file():
        raise FileNotFoundError(
            f"Installed ThemeScheduler executable is missing: {install_layout.executable}"
        )
    scheduler = WindowsTaskSchedulerBackend()
    if scheduler.read(task_path) is None:
        raise RuntimeError(f"The existing product task is missing: {task_path}")

    noop = _powershell_command("-Command", "exit 0")
    startup_timings, _ = measure(
        lambda: _run_powershell(noop),
        warmups=warmups,
        samples=samples,
    )
    read_timings, _ = measure(
        lambda: scheduler.read(task_path),
        warmups=warmups,
        samples=samples,
    )

    def burst_read() -> None:
        for _ in range(burst_size):
            scheduler.read(task_path)

    burst_timings, _ = measure(
        burst_read,
        warmups=warmups,
        samples=samples,
    )

    health_timings: list[float] = []
    health_task_timings: list[list[float]] = []
    health_statuses: list[str] = []
    for iteration in range(warmups + samples):
        started = time.perf_counter()
        status, task_timings = _health_once(install_layout, layout, scheduler)
        elapsed = time.perf_counter() - started
        if iteration >= warmups:
            health_timings.append(elapsed)
            health_task_timings.append(task_timings)
            health_statuses.append(status)

    startup_p50 = percentile(startup_timings, 50)
    script_com_estimates = [
        max(duration - startup_p50, 0.0) for duration in read_timings
    ]
    task_shares = [
        100 * sum(task) / total
        for task, total in zip(health_task_timings, health_timings, strict=True)
    ]
    launch_counts = [len(sample) for sample in health_task_timings]
    if len(set(launch_counts)) != 1:
        raise RuntimeError("Task bridge launch count changed between health samples.")

    health_summary = summarize(health_timings)
    task_share_p50 = percentile(task_shares, 50)
    decision = evaluate_batch_read(
        health_p50_ms=float(health_summary["p50Ms"]),
        task_launch_count=launch_counts[0],
        task_share_p50=task_share_p50,
    )
    version_command = _powershell_command(
        "-Command", "$PSVersionTable.PSVersion.ToString()"
    )
    powershell_version = _run_powershell(version_command).stdout.strip()
    windows = sys.getwindowsversion()
    return {
        "kind": KIND,
        "schemaVersion": SCHEMA_VERSION,
        "capturedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "benchmarkInputs": capture_input_identity(),
        "environment": {
            "platform": platform.platform(),
            "windowsVersion": platform.version(),
            "windowsBuild": windows.build,
            "architecture": platform.machine(),
            "pythonVersion": platform.python_version(),
            "powershellVersion": powershell_version,
            "installedExecutable": str(install_layout.executable),
            "taskPath": task_path,
        },
        "method": {
            "warmups": warmups,
            "samples": samples,
            "burstSize": burst_size,
            "taskOperations": "Probe and Read only; no task mutation",
            "healthPath": "production HealthService with transient permission probes",
            "processIdentity": "installed AUMID injected into the development harness",
        },
        "metrics": {
            "powershellStartup": summarize(startup_timings),
            "singleTaskRead": summarize(read_timings),
            "bridgeScriptComAndParsingEstimate": summarize(script_com_estimates),
            "continuousTaskReads": {
                **summarize(burst_timings),
                "perRead": summarize(
                    [duration / burst_size for duration in burst_timings]
                ),
            },
            "fullHealthCheck": {
                **health_summary,
                "statuses": health_statuses,
                "taskBridgeLaunchesPerSample": launch_counts[0],
                "taskBridgeDurations": [
                    summarize(sample) for sample in health_task_timings
                ],
                "taskBridgeSharePercent": {
                    "samples": [round(value, 3) for value in task_shares],
                    "p50": round(percentile(task_shares, 50), 3),
                    "p95": round(percentile(task_shares, 95), 3),
                    "max": round(max(task_shares), 3),
                },
            },
        },
        "decision": decision,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="themescheduler-task-bridge-benchmark")
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--burst-size", type=int, default=3)
    parser.add_argument("--task-path", default=DEFAULT_TASK_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run_benchmark(
            warmups=args.warmups,
            samples=args.samples,
            burst_size=args.burst_size,
            task_path=args.task_path,
        )
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temporary, output)
        print(json.dumps(report["decision"], ensure_ascii=False, indent=2))
        print(f"report={output}")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
