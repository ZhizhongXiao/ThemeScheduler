"""Pure contracts and decisions for the stage-4 automatic switching core.

This module deliberately performs no Windows writes.  The formal stage-4
orchestrator will compose these decisions with the existing persistence and
Windows adapters.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum, IntEnum
from pathlib import Path
from typing import Protocol

from .config import AppConfig
from .state import AppState

PROFILE_DAY = "day"
PROFILE_NIGHT = "night"


class AutoExitCode(IntEnum):
    """Stable process exit codes for the future ``auto`` entrypoint."""

    SUCCESS = 0
    PAUSED = 10
    ALREADY_RUNNING = 11
    DATA_UNTRUSTED = 20
    APPLY_FAILED_ROLLED_BACK = 30
    PARTIAL_FAILURE = 40
    FATAL_FAILURE = 50


class AutoResultKind(str, Enum):
    """Machine-readable result names independent from translated messages."""

    APPLIED = "applied"
    NO_CHANGE = "no-change"
    PAUSED = "paused"
    ALREADY_RUNNING = "already-running"
    DATA_UNTRUSTED = "data-untrusted"
    APPLY_FAILED_ROLLED_BACK = "apply-failed-rolled-back"
    PARTIAL_FAILURE = "partial-failure"
    FATAL_FAILURE = "fatal-failure"


EXIT_CODE_BY_RESULT: dict[AutoResultKind, AutoExitCode] = {
    AutoResultKind.APPLIED: AutoExitCode.SUCCESS,
    AutoResultKind.NO_CHANGE: AutoExitCode.SUCCESS,
    AutoResultKind.PAUSED: AutoExitCode.PAUSED,
    AutoResultKind.ALREADY_RUNNING: AutoExitCode.ALREADY_RUNNING,
    AutoResultKind.DATA_UNTRUSTED: AutoExitCode.DATA_UNTRUSTED,
    AutoResultKind.APPLY_FAILED_ROLLED_BACK: (AutoExitCode.APPLY_FAILED_ROLLED_BACK),
    AutoResultKind.PARTIAL_FAILURE: AutoExitCode.PARTIAL_FAILURE,
    AutoResultKind.FATAL_FAILURE: AutoExitCode.FATAL_FAILURE,
}


class Clock(Protocol):
    """Injectable aware local clock used by automatic decisions."""

    def now(self) -> datetime: ...


class SystemClock:
    """Production clock; returns current local time with a UTC offset."""

    def now(self) -> datetime:
        return datetime.now().astimezone()


class ExecutionLock(Protocol):
    """Non-blocking, process-wide lease held for the complete auto run."""

    def acquire(self) -> bool: ...

    def release(self) -> None: ...


class AutoPlanKind(str, Enum):
    PAUSED = "paused"
    NO_CHANGE = "no-change"
    APPLY = "apply"


@dataclass(frozen=True)
class AutoRunPlan:
    """Side-effect-free plan produced only from trusted configuration/state."""

    kind: AutoPlanKind
    target_profile: str | None
    target_apps_theme: str | None
    learn_profile: str | None

    def __post_init__(self) -> None:
        if self.kind is AutoPlanKind.PAUSED:
            if any(
                value is not None
                for value in (
                    self.target_profile,
                    self.target_apps_theme,
                    self.learn_profile,
                )
            ):
                raise ValueError("A paused plan cannot contain write targets.")
            return
        if self.target_profile not in {PROFILE_DAY, PROFILE_NIGHT}:
            raise ValueError("An apply plan requires a day or night target.")
        if self.target_apps_theme not in {"light", "dark"}:
            raise ValueError("An apply plan requires a supported app theme.")
        if self.learn_profile not in {None, PROFILE_DAY, PROFILE_NIGHT}:
            raise ValueError("learnProfile must be day, night, or null.")
        if self.learn_profile == self.target_profile:
            raise ValueError("The target profile must never learn from itself.")
        if self.kind is AutoPlanKind.NO_CHANGE and self.learn_profile is not None:
            raise ValueError("A no-change plan cannot learn a profile.")


def _wall_time(value: str) -> time:
    hour, minute = (int(part) for part in value.split(":", 1))
    return time(hour, minute)


def target_profile_at(config: AppConfig, now: datetime) -> str:
    """Resolve the circular day interval using current aware local wall time.

    ``dayStart`` is inclusive and ``nightStart`` is exclusive.  The general
    circular calculation also behaves correctly when the configured day
    interval itself crosses midnight.
    """

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Automatic decisions require an aware local datetime.")
    current = now.timetz().replace(tzinfo=None)
    day_start = _wall_time(config.day_start)
    night_start = _wall_time(config.night_start)
    if day_start < night_start:
        is_day = day_start <= current < night_start
    else:
        is_day = current >= day_start or current < night_start
    return PROFILE_DAY if is_day else PROFILE_NIGHT


def learning_source(state: AppState, target_profile: str) -> str | None:
    """Return a trustworthy departing profile, or conservatively skip learning."""

    if target_profile not in {PROFILE_DAY, PROFILE_NIGHT}:
        raise ValueError("Target profile must be day or night.")
    if state.last_result != "success":
        return None
    if state.active_profile is None or state.active_profile == target_profile:
        return None
    return state.active_profile


def plan_auto_run(
    config: AppConfig,
    state: AppState,
    now: datetime,
    *,
    force_apply: bool = False,
) -> AutoRunPlan:
    """Build a plan without touching files, Windows, logs, or runtime folders."""

    if not isinstance(force_apply, bool):
        raise ValueError("forceApply must be boolean.")
    if state.paused:
        return AutoRunPlan(AutoPlanKind.PAUSED, None, None, None)
    target = target_profile_at(config, now)
    apps_theme = (
        config.day_apps_theme if target == PROFILE_DAY else config.night_apps_theme
    )
    if (
        not force_apply
        and state.last_result == "success"
        and state.active_profile == target
    ):
        return AutoRunPlan(
            AutoPlanKind.NO_CHANGE,
            target,
            apps_theme,
            None,
        )
    return AutoRunPlan(
        AutoPlanKind.APPLY,
        target,
        apps_theme,
        learning_source(state, target),
    )


def mutex_name_for_data_root(data_root: Path) -> str:
    """Derive a non-sensitive per-data-root name in the current session."""

    normalized = str(Path(data_root).resolve()).replace("/", "\\").casefold()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return f"Local\\ThemeScheduler.Auto.{digest}"
