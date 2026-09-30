"""Test support dedicated to managed-theme V2 transactions."""

from __future__ import annotations

from pathlib import Path


class ScriptedThemeApplyV2Backend:
    """Configurable IThemeManager2 fake with an explicit operation trace."""

    def __init__(
        self,
        active: Path,
        target: Path,
        *,
        normalize_to_custom: bool = False,
    ) -> None:
        self.active = active
        self.original = active
        self.target = target
        self.normalize_to_custom = normalize_to_custom
        self.index = 6
        self.custom_index = 0
        self.target_index = 13
        self.apply_count = 0
        self.bridge_before: int | None = None
        self.current_indices_override: tuple[int, int] | None = None
        self.apply_active_paths: list[Path] = []
        self.restore_original_on_set = True
        self.set_active_path: Path | None = None
        self.set_result_override: int | None = None
        self.failures: dict[str, list[Exception | None]] = {}
        self.calls: list[tuple[str, object | None]] = []
        self.bridge_budgets: list[float | None] = []
        self.apply_budgets: list[float | None] = []

    def fail_next(self, operation: str, error: Exception) -> None:
        self.failures.setdefault(operation, []).append(error)

    def allow_next(self, operation: str) -> None:
        self.failures.setdefault(operation, []).append(None)

    def _raise_if_scripted(self, operation: str) -> None:
        queued = self.failures.get(operation)
        if not queued:
            return
        error = queued.pop(0)
        if error is not None:
            raise error

    def current_theme_path(self) -> Path:
        self.calls.append(("current_theme_path", None))
        self._raise_if_scripted("current_theme_path")
        return self.active

    def current_v2_index(self, *, timeout_seconds: float | None = None) -> int:
        self.calls.append(("current_v2_index", None))
        self.bridge_budgets.append(timeout_seconds)
        self._raise_if_scripted("current_v2_index")
        return self.index

    def current_v2_indices(
        self,
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[int, int]:
        self.calls.append(("current_v2_indices", None))
        self.bridge_budgets.append(timeout_seconds)
        self._raise_if_scripted("current_v2_indices")
        return self.current_indices_override or (self.index, self.custom_index)

    def apply_theme_v2(
        self,
        path: Path,
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[int, int]:
        self.calls.append(("apply_theme_v2", path))
        self.bridge_budgets.append(timeout_seconds)
        self.apply_budgets.append(timeout_seconds)
        self._raise_if_scripted("apply_theme_v2")
        self.apply_count += 1
        before = self.index if self.bridge_before is None else self.bridge_before
        self.index = (
            self.custom_index if self.normalize_to_custom else self.target_index
        )
        self.active = (
            self.apply_active_paths.pop(0)
            if self.apply_active_paths
            else self.target
            if self.apply_count == 1
            else path
        )
        return before, self.target_index

    def set_v2_index(
        self,
        index: int,
        *,
        timeout_seconds: float | None = None,
    ) -> int:
        self.calls.append(("set_v2_index", index))
        self.bridge_budgets.append(timeout_seconds)
        self._raise_if_scripted("set_v2_index")
        self.index = index
        if self.restore_original_on_set:
            self.active = self.set_active_path or self.original
        return (
            self.set_result_override if self.set_result_override is not None else index
        )
