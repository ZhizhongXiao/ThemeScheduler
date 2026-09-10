"""Project-local fixes for deterministic PyInstaller output."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

BaseModuleEntry = tuple[str, str, str]


def sorted_base_library_modules(
    modules: Iterable[BaseModuleEntry],
) -> list[BaseModuleEntry]:
    """Return a stable order for PyInstaller's base-library module TOC."""
    return sorted(modules, key=lambda entry: (entry[0].casefold(), entry[0]))


def enable_reproducible_base_library() -> None:
    """Make PyInstaller write ``base_library.zip`` in a stable order.

    PyInstaller 6.20 builds the base-library TOC from a dependency graph whose
    traversal order can vary between processes.  The archive members themselves
    are deterministic, but their order is not.  Sorting at the writer boundary
    keeps onedir, onefile, and the embedded Setup payload byte-reproducible.
    """
    from PyInstaller.building import build_main

    original = build_main.create_base_library_zip
    if getattr(original, "_themescheduler_deterministic", False):
        return

    def create_base_library_zip(
        filename: str,
        modules_toc: Iterable[BaseModuleEntry],
        code_cache: Any = None,
    ) -> None:
        original(
            filename,
            sorted_base_library_modules(modules_toc),
            code_cache,
        )

    create_base_library_zip._themescheduler_deterministic = True  # type: ignore[attr-defined]
    build_main.create_base_library_zip = create_base_library_zip
