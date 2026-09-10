from __future__ import annotations

import ast
import unittest
from importlib import import_module
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
PACKAGE_ROOT = SOURCE_ROOT / "theme_scheduler"

from theme_scheduler.errors import (
    ContractError,
    DataError,
    ThemeSchedulerError,
    ThemeSchedulerRuntimeError,
)


class SharedErrorHierarchyTests(unittest.TestCase):
    def test_intermediate_errors_keep_builtin_catch_compatibility(self) -> None:
        self.assertTrue(issubclass(ContractError, ValueError))
        self.assertTrue(issubclass(DataError, ValueError))
        self.assertTrue(issubclass(ThemeSchedulerRuntimeError, RuntimeError))

    def test_every_defined_product_error_has_shared_root(self) -> None:
        discovered: list[tuple[str, str]] = []
        for path in PACKAGE_ROOT.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            module_tail = path.relative_to(SOURCE_ROOT).with_suffix("")
            module_name = ".".join(module_tail.parts)
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and node.name.endswith("Error"):
                    discovered.append((module_name, node.name))

        self.assertGreater(len(discovered), 30)
        for module_name, class_name in discovered:
            with self.subTest(
                module=module_name,
                class_name=class_name,
            ):
                error_type = getattr(
                    import_module(module_name),
                    class_name,
                )
                self.assertTrue(issubclass(error_type, ThemeSchedulerError))


if __name__ == "__main__":
    unittest.main()
