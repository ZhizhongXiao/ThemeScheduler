"""Scheduler contract and mutation failures."""

from __future__ import annotations

from ..errors import ContractError, ThemeSchedulerRuntimeError


class SchedulerContractError(ContractError):
    """Raised when a task definition is unsafe or outside the v1 contract."""


class SchedulerMutationError(ThemeSchedulerRuntimeError):
    """Raised when a task mutation or its readback verification fails."""

    def __init__(
        self,
        message: str,
        *,
        rollback_attempted: bool,
        rollback_succeeded: bool,
    ) -> None:
        super().__init__(message)
        self.rollback_attempted = rollback_attempted
        self.rollback_succeeded = rollback_succeeded
