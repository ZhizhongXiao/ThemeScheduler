"""Explicit application of the appearance belonging to the current period."""

from __future__ import annotations

from .automation import AutoRunner, AutoRunOutcome, AutoWindowsBackend
from .core import Clock, ExecutionLock, RunIntent
from .storage import UserDataLayout


class ManualAppearanceService:
    """A product use case distinct from scheduled automatic execution.

    The service reuses the verified transactional runner while selecting the
    manual-current intent.  That intent ignores pause for this single explicit
    operation, never learns the departing profile, and does not change pause.
    """

    def __init__(
        self,
        layout: UserDataLayout,
        execution_lock: ExecutionLock,
        windows: AutoWindowsBackend,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._runner = AutoRunner(
            layout,
            execution_lock,
            windows,
            clock=clock,
            intent=RunIntent.MANUAL_CURRENT,
        )

    def apply_current(self) -> AutoRunOutcome:
        return self._runner.run()
