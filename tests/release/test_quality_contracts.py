from __future__ import annotations

import ast
import importlib.util
import inspect
import tomllib
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"

from theme_scheduler.automation.runner import AutoRunner
from theme_scheduler.lifecycle.deployment import (
    FileDeploymentService,
)
from theme_scheduler.scheduled_auto import ScheduledAutoCoordinator
from theme_scheduler.scheduler import TaskSchedulerBackend
from theme_scheduler.system_integration import CurrentUserIntegrationService
from theme_scheduler.uninstall_service import IndependentUninstallService
from theme_scheduler.workbench import (
    ConfigSummary,
    GuiApi,
    OverviewResult,
    ProfileSummary,
    StateSummary,
    TaskSummary,
    WorkspaceValidationResult,
    shell,
)
from theme_scheduler.workbench.overview import GuiOverviewMixin


def source_lines(callable_object) -> int:
    lines, _ = inspect.getsourcelines(callable_object)
    return len(lines)


def internal_dependency_graph() -> dict[str, set[str]]:
    module_paths: dict[str, Path] = {}
    package_modules: set[str] = set()
    for path in (SOURCE_ROOT / "theme_scheduler").rglob("*.py"):
        relative = path.relative_to(SOURCE_ROOT).with_suffix("")
        parts = relative.parts
        if parts[-1] == "__init__":
            module = ".".join(parts[:-1])
            package_modules.add(module)
        else:
            module = ".".join(parts)
        module_paths[module] = path

    graph = {module: set() for module in module_paths}
    for module, path in module_paths.items():
        package = module if module in package_modules else module.rpartition(".")[0]
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            candidates: list[str] = []
            if isinstance(node, ast.Import):
                candidates.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    relative_name = "." * node.level + (node.module or "")
                    base = importlib.util.resolve_name(relative_name, package)
                else:
                    base = node.module or ""
                candidates.append(base)
                candidates.extend(
                    f"{base}.{alias.name}" for alias in node.names if base
                )
            for candidate in candidates:
                if candidate in module_paths and candidate != module:
                    graph[module].add(candidate)
    return graph


def dependency_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []
    cycles: list[list[str]] = []

    def visit(module: str) -> None:
        if module in visiting:
            start = path.index(module)
            cycles.append(path[start:] + [module])
            return
        if module in visited:
            return
        visiting.add(module)
        path.append(module)
        for dependency in sorted(graph[module]):
            visit(dependency)
        path.pop()
        visiting.remove(module)
        visited.add(module)

    for module in sorted(graph):
        visit(module)
    return cycles


class QualityContractTests(unittest.TestCase):
    def test_manifest_explicit_includes_exist(self) -> None:
        manifest = (PROJECT_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        includes = [
            line.removeprefix("include ").strip()
            for line in manifest.splitlines()
            if line.startswith("include ")
        ]

        self.assertNotIn("pyrightconfig.json", includes)
        self.assertTrue(includes)
        for relative in includes:
            with self.subTest(relative=relative):
                self.assertTrue((PROJECT_ROOT / relative).is_file())

    def test_pyright_has_one_full_source_configuration(self) -> None:
        self.assertFalse((PROJECT_ROOT / "pyrightconfig.json").exists())
        with (PROJECT_ROOT / "pyproject.toml").open("rb") as stream:
            config = tomllib.load(stream)["tool"]["pyright"]
        self.assertEqual(config["include"], ["src/theme_scheduler"])
        self.assertEqual(
            config["strict"],
            [
                "src/theme_scheduler/scheduler/models.py",
                "src/theme_scheduler/lifecycle/installation.py",
                "src/theme_scheduler/automation/runner.py",
                "src/theme_scheduler/automation/recovery.py",
                "src/theme_scheduler/automation/contracts.py",
                "src/theme_scheduler/automation/outcome.py",
                "src/theme_scheduler/accent_theme.py",
            ],
        )

    def test_test_modules_do_not_inject_the_source_root(self) -> None:
        bootstrap = "sys.path.insert" + "(0, str(SOURCE_ROOT))"
        offenders = [
            path.relative_to(PROJECT_ROOT).as_posix()
            for path in (PROJECT_ROOT / "tests").rglob("test_*.py")
            if bootstrap in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [])

    def test_test_modules_are_grouped_by_scope(self) -> None:
        test_root = PROJECT_ROOT / "tests"
        self.assertEqual(list(test_root.glob("test_*.py")), [])
        for category in ("unit", "integration", "release"):
            with self.subTest(category=category):
                directory = test_root / category
                self.assertTrue((directory / "__init__.py").is_file())
                self.assertTrue(any(directory.glob("test_*.py")))

    def test_internal_module_dependencies_are_acyclic(self) -> None:
        self.assertEqual(dependency_cycles(internal_dependency_graph()), [])

    def test_scheduler_backend_is_the_only_workbench_protocol(self) -> None:
        self.assertTrue(callable(TaskSchedulerBackend.current_user_id))
        self.assertFalse(hasattr(shell, "SchedulerFacade"))

    def test_former_hotspot_entry_points_are_short_orchestrators(self) -> None:
        self.assertLessEqual(source_lines(FileDeploymentService.deploy), 60)
        self.assertLessEqual(source_lines(AutoRunner.run_locked), 40)
        self.assertLessEqual(source_lines(ScheduledAutoCoordinator.run_locked), 40)
        self.assertLessEqual(source_lines(CurrentUserIntegrationService.apply), 40)
        self.assertLessEqual(source_lines(IndependentUninstallService.run), 40)

    def test_overview_builders_have_focused_size(self) -> None:
        for name in (
            "_build_profiles_summary",
            "_build_backup_summary",
            "_build_task_summary",
            "get_overview",
        ):
            with self.subTest(name=name):
                self.assertLessEqual(
                    source_lines(getattr(GuiOverviewMixin, name)),
                    40,
                )

    def test_workbench_wire_contracts_expose_stable_keys(self) -> None:
        self.assertIn("profiles", OverviewResult.__annotations__)
        self.assertIn("task", OverviewResult.__annotations__)
        self.assertIn("valid", ProfileSummary.__annotations__)
        self.assertIn("available", TaskSummary.__annotations__)
        self.assertEqual(
            set(ConfigSummary.__annotations__),
            {
                "dayStart",
                "nightStart",
                "dayAppsTheme",
                "nightAppsTheme",
                "daySystemTheme",
                "nightSystemTheme",
                "dayStartTaskbarAccent",
                "nightStartTaskbarAccent",
                "dayTitleBordersAccent",
                "nightTitleBordersAccent",
                "notifyErrors",
                "notifyStatusChanges",
            },
        )
        self.assertIn("paused", StateSummary.__annotations__)
        self.assertIn("valid", WorkspaceValidationResult.__annotations__)
        signature = inspect.signature(GuiApi.__init__)
        self.assertEqual(
            signature.parameters["lock_factory"].annotation,
            "LockFactory",
        )

    def test_python_entrypoints_are_frozen_build_boundaries_only(self) -> None:
        entrypoint_root = PROJECT_ROOT / "entrypoints"
        self.assertEqual(
            {path.name for path in entrypoint_root.glob("*.py")},
            {
                "setup_main.py",
                "themescheduler_main.py",
                "uninstall_main.py",
            },
        )
        for path in entrypoint_root.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotIn("sys.path", source)
                self.assertNotIn("SOURCE_ROOT", source)
                self.assertNotIn("noqa: E402", source)

        project = tomllib.loads(
            (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(
            project["project"]["scripts"],
            {"themescheduler-preview": "theme_scheduler.cli.preview:main"},
        )


if __name__ == "__main__":
    unittest.main()
