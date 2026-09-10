"""Typed dependencies shared by automatic-run implementation mixins."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..core import AutoResultKind
from ..state import StateStore
from ..storage import UserDataLayout
from .backend import AutoWindowsBackend
from .outcome import AutoRunOutcome


class AutoRunnerBindings:
    layout: UserDataLayout
    windows: AutoWindowsBackend
    state_store: StateStore
    cleanup_callback: Callable[[Path], None]
    _active_transaction_id: str | None

    def _outcome(
        self,
        result: AutoResultKind,
        message: str,
        *,
        target: str | None = None,
        transaction: Path | None = None,
        learned: str | None = None,
        windows_changed: bool = False,
        state_changed: bool = False,
        recovered: bool = False,
    ) -> AutoRunOutcome:
        raise NotImplementedError

    def _append_event(
        self,
        *,
        timestamp: str,
        level: str,
        event: str,
        result: str,
        target: str | None = None,
        transaction: str | None = None,
        error_code: str | None = None,
        message: str | None = None,
        verification: str | None = None,
        rollback_attempted: bool = False,
        rollback_succeeded: bool | None = None,
    ) -> None:
        raise NotImplementedError
