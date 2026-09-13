from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = PROJECT_ROOT / "packaging" / "reproducible_pyinstaller.py"
SPEC = importlib.util.spec_from_file_location(
    "themescheduler_reproducible_pyinstaller",
    MODULE_PATH,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Cannot load packaging/reproducible_pyinstaller.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReproduciblePyInstallerTests(unittest.TestCase):
    def test_base_library_modules_have_stable_case_aware_order(self) -> None:
        entries = [
            ("re._parser", "parser.py", "PYMODULE"),
            ("locale", "locale.py", "PYMODULE"),
            ("re.__init__", "re.py", "PYMODULE"),
        ]

        actual = MODULE.sorted_base_library_modules(reversed(entries))

        self.assertEqual(
            [entry[0] for entry in actual],
            ["locale", "re.__init__", "re._parser"],
        )

    def test_both_specs_enable_deterministic_base_library_before_analysis(
        self,
    ) -> None:
        for filename in ("ThemeScheduler.spec", "ThemeSchedulerSetup.spec"):
            text = (PROJECT_ROOT / "packaging" / filename).read_text(encoding="utf-8")
            self.assertIn("enable_reproducible_base_library()", text)
            self.assertLess(
                text.index("enable_reproducible_base_library()"),
                text.index("Analysis("),
            )


if __name__ == "__main__":
    unittest.main()
