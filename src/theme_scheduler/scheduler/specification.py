"""Build and validate the single managed ThemeScheduler task."""

from __future__ import annotations

import ntpath
from datetime import datetime, timedelta

from ..config import AppConfig
from ..switch_override import PendingSwitch
from ._validation import _absolute_windows_path
from .constants import (
    _AUTO_RETRY_TRIGGER_IDS,
    _DEFERRED_TRIGGER_IDS,
    _FIXED_TRIGGER_IDS,
    AUTO_ARGUMENTS,
    AUTO_RETRY_TRIGGER_ID,
    DAY_PREPARE_TRIGGER_ID,
    DAY_TRIGGER_ID,
    DEFAULT_TASK_PATH,
    DEFERRED_PREPARE_TRIGGER_ID,
    DEFERRED_TRIGGER_ID,
    NIGHT_PREPARE_TRIGGER_ID,
    NIGHT_TRIGGER_ID,
)
from .errors import SchedulerContractError
from .models import TaskAction, TaskPrincipal, TaskSettings, TaskSpec
from .triggers import DailyTrigger, TaskTrigger, TimeTrigger


def build_task_spec(
    config: AppConfig,
    *,
    executable: str,
    user_id: str,
    task_path: str = DEFAULT_TASK_PATH,
    pending_switch: PendingSwitch | None = None,
    auto_retry_at: datetime | None = None,
) -> TaskSpec:
    """Build fixed triggers plus valid deferred and retry one-time triggers."""

    normalized_executable = _absolute_windows_path(executable, "task.action.executable")
    triggers: list[TaskTrigger] = [
        DailyTrigger(
            DAY_PREPARE_TRIGGER_ID,
            _subtract_local_minutes(config.day_start, 5),
        ),
        DailyTrigger(DAY_TRIGGER_ID, config.day_start),
        DailyTrigger(
            NIGHT_PREPARE_TRIGGER_ID,
            _subtract_local_minutes(config.night_start, 5),
        ),
        DailyTrigger(NIGHT_TRIGGER_ID, config.night_start),
    ]
    if (
        pending_switch is not None
        and pending_switch.defer_count > 0
        and pending_switch.decision.value != "skipped"
    ):
        triggers.extend(
            (
                TimeTrigger(
                    DEFERRED_PREPARE_TRIGGER_ID,
                    pending_switch.prepare_at.isoformat(timespec="seconds"),
                ),
                TimeTrigger(
                    DEFERRED_TRIGGER_ID,
                    pending_switch.scheduled_at.isoformat(timespec="seconds"),
                ),
            )
        )
    if auto_retry_at is not None:
        triggers.append(
            TimeTrigger(
                AUTO_RETRY_TRIGGER_ID,
                auto_retry_at.isoformat(timespec="seconds"),
            )
        )
    task = TaskSpec(
        task_path=task_path,
        enabled=True,
        triggers=tuple(triggers),
        action=TaskAction(
            normalized_executable,
            AUTO_ARGUMENTS,
            ntpath.dirname(normalized_executable),
        ),
        principal=TaskPrincipal(user_id),
        settings=TaskSettings(),
    )
    validate_desired_task(task)
    return task


def _subtract_local_minutes(value: str, minutes: int) -> str:
    parsed = datetime.strptime(value, "%H:%M")
    return (parsed - timedelta(minutes=minutes)).strftime("%H:%M")


def validate_desired_task(task: TaskSpec) -> None:
    """Reject any definition outside the stage-9 production contract."""

    if task.task_path.count("\\") != 1:
        raise SchedulerContractError(
            "The taskPath must identify one task in the root folder."
        )
    ids = {trigger.trigger_id for trigger in task.triggers}
    valid_topologies = {
        frozenset(_FIXED_TRIGGER_IDS),
        frozenset(_FIXED_TRIGGER_IDS | _AUTO_RETRY_TRIGGER_IDS),
        frozenset(_FIXED_TRIGGER_IDS | _DEFERRED_TRIGGER_IDS),
        frozenset(_FIXED_TRIGGER_IDS | _DEFERRED_TRIGGER_IDS | _AUTO_RETRY_TRIGGER_IDS),
    }
    if frozenset(ids) not in valid_topologies:
        raise SchedulerContractError(
            "Task trigger identities do not match the managed contract."
        )
    trigger_by_id = {trigger.trigger_id: trigger for trigger in task.triggers}
    if AUTO_RETRY_TRIGGER_ID in ids and not isinstance(
        trigger_by_id[AUTO_RETRY_TRIGGER_ID], TimeTrigger
    ):
        raise SchedulerContractError("AutoRetry must be a one-time trigger.")

    daily = {
        trigger.trigger_id: trigger
        for trigger in task.triggers
        if isinstance(trigger, DailyTrigger)
    }
    if set(daily) != _FIXED_TRIGGER_IDS:
        raise SchedulerContractError("All four fixed triggers must be daily triggers.")
    if any(
        trigger.trigger_type != "Daily"
        or trigger.days_interval != 1
        or not trigger.enabled
        for trigger in daily.values()
    ):
        raise SchedulerContractError(
            "All fixed triggers must be enabled and run daily."
        )
    if daily[DAY_TRIGGER_ID].local_time == daily[NIGHT_TRIGGER_ID].local_time:
        raise SchedulerContractError("Task boundary times must differ.")
    if daily[DAY_PREPARE_TRIGGER_ID].local_time != _subtract_local_minutes(
        daily[DAY_TRIGGER_ID].local_time, 5
    ) or daily[NIGHT_PREPARE_TRIGGER_ID].local_time != _subtract_local_minutes(
        daily[NIGHT_TRIGGER_ID].local_time, 5
    ):
        raise SchedulerContractError(
            "Each fixed prepare trigger must precede its boundary by five minutes."
        )

    deferred = {
        trigger.trigger_id: trigger
        for trigger in task.triggers
        if isinstance(trigger, TimeTrigger)
        and trigger.trigger_id in _DEFERRED_TRIGGER_IDS
    }
    if deferred:
        if set(deferred) != _DEFERRED_TRIGGER_IDS:
            raise SchedulerContractError(
                "Deferred triggers must form one complete pair."
            )
        if any(
            trigger.trigger_type != "Time" or not trigger.enabled
            for trigger in deferred.values()
        ):
            raise SchedulerContractError(
                "Deferred triggers must be enabled one-time triggers."
            )
        prepare_at = datetime.fromisoformat(
            deferred[DEFERRED_PREPARE_TRIGGER_ID].start_at
        )
        boundary_at = datetime.fromisoformat(deferred[DEFERRED_TRIGGER_ID].start_at)
        if prepare_at + timedelta(minutes=5) != boundary_at:
            raise SchedulerContractError(
                "Deferred prepare must precede deferred execution by five minutes."
            )
    retry = {
        trigger.trigger_id: trigger
        for trigger in task.triggers
        if isinstance(trigger, TimeTrigger)
        and trigger.trigger_id in _AUTO_RETRY_TRIGGER_IDS
    }
    if retry and (
        len(retry) != 1
        or retry[AUTO_RETRY_TRIGGER_ID].trigger_type != "Time"
        or not retry[AUTO_RETRY_TRIGGER_ID].enabled
    ):
        raise SchedulerContractError("AutoRetry must be one enabled one-time trigger.")
    if task.action.action_type != "Exec" or task.action.action_count != 1:
        raise SchedulerContractError(
            "The production task must contain exactly one Exec action."
        )
    executable = _absolute_windows_path(
        task.action.executable, "task.action.executable"
    )
    working_directory = _absolute_windows_path(
        task.action.working_directory,
        "task.action.workingDirectory",
    )
    if ntpath.normcase(ntpath.dirname(executable)) != ntpath.normcase(
        working_directory
    ):
        raise SchedulerContractError(
            "Task working directory must be the executable directory."
        )
    if task.action.arguments != AUTO_ARGUMENTS:
        raise SchedulerContractError(
            "The production task action must contain only the auto argument."
        )
    if (
        task.principal.logon_type != "InteractiveToken"
        or task.principal.run_level != "LeastPrivilege"
    ):
        raise SchedulerContractError(
            "The task must use the interactive user at least privilege."
        )
    if task.settings != TaskSettings():
        raise SchedulerContractError(
            "Task settings do not match the frozen stage-5 contract."
        )
