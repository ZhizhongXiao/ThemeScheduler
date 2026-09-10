"""Pure stage-6 pause and resume contracts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum, IntEnum
from typing import Any

from .state import AppState


class ControlExitCode(IntEnum):
    SUCCESS = 0
    ALREADY_RUNNING = 11
    DATA_UNTRUSTED = 20
    PARTIAL_FAILURE = 40
    FATAL_FAILURE = 50


class ControlResultKind(str, Enum):
    CHANGED = "changed"
    NO_CHANGE = "no-change"
    ALREADY_RUNNING = "already-running"
    DATA_UNTRUSTED = "data-untrusted"
    PARTIAL_FAILURE = "partial-failure"
    FATAL_FAILURE = "fatal-failure"


EXIT_CODE_BY_CONTROL_RESULT = {
    ControlResultKind.CHANGED: ControlExitCode.SUCCESS,
    ControlResultKind.NO_CHANGE: ControlExitCode.SUCCESS,
    ControlResultKind.ALREADY_RUNNING: ControlExitCode.ALREADY_RUNNING,
    ControlResultKind.DATA_UNTRUSTED: ControlExitCode.DATA_UNTRUSTED,
    ControlResultKind.PARTIAL_FAILURE: ControlExitCode.PARTIAL_FAILURE,
    ControlResultKind.FATAL_FAILURE: ControlExitCode.FATAL_FAILURE,
}


@dataclass(frozen=True)
class PauseTransition:
    before: AppState
    after: AppState
    changed: bool


def plan_pause_transition(state: AppState, paused: bool) -> PauseTransition:
    """Return a state-only transition while preserving every history field."""

    if not isinstance(paused, bool):
        raise ValueError("paused target must be boolean.")
    if state.paused == paused:
        return PauseTransition(state, state, False)
    return PauseTransition(state, replace(state, paused=paused), True)


@dataclass(frozen=True)
class ControlOutcome:
    action: str
    result: ControlResultKind
    paused_before: bool | None
    paused_after: bool | None
    state_changed: bool | None
    log_written: bool
    message: str

    def __post_init__(self) -> None:
        if self.action not in {"pause", "resume"}:
            raise ValueError("Control action must be pause or resume.")

    @property
    def exit_code(self) -> ControlExitCode:
        return EXIT_CODE_BY_CONTROL_RESULT[self.result]

    def as_dict(self) -> dict[str, Any]:
        data_changed: bool | None
        if self.log_written or self.state_changed is True:
            data_changed = True
        elif self.state_changed is False:
            data_changed = False
        else:
            data_changed = None
        return {
            "action": self.action,
            "result": self.result.value,
            "exitCode": int(self.exit_code),
            "pausedBefore": self.paused_before,
            "pausedAfter": self.paused_after,
            "stateChanged": self.state_changed,
            "logWritten": self.log_written,
            "dataChanged": data_changed,
            "windowsChanged": False,
            "taskSchedulerAccessed": False,
            "taskSchedulerChanged": False,
            "message": self.message,
        }
