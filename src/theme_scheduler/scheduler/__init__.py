"""Versioned single-task scheduler contracts and orchestration."""

from .constants import (
    AUTO_ARGUMENTS,
    DAY_PREPARE_TRIGGER_ID,
    DAY_TRIGGER_ID,
    DEFAULT_TASK_PATH,
    DEFERRED_PREPARE_TRIGGER_ID,
    DEFERRED_TRIGGER_ID,
    NIGHT_PREPARE_TRIGGER_ID,
    NIGHT_TRIGGER_ID,
    TASK_BACKUP_KIND,
    TASK_BACKUP_SCHEMA_VERSION,
    TASK_SPEC_KIND,
    TASK_SPEC_SCHEMA_VERSION,
)
from .errors import SchedulerContractError, SchedulerMutationError
from .inspection import (
    TaskDifference,
    TaskInspection,
    compare_task_specs,
    inspect_task,
)
from .models import TaskAction, TaskPrincipal, TaskSettings, TaskSpec
from .mutation import (
    TaskDefinitionBackup,
    TaskMutationOutcome,
    TaskSchedulerBackend,
    delete_task,
    reconcile_task,
    set_task_enabled,
)
from .specification import build_task_spec, validate_desired_task
from .triggers import DailyTrigger, TaskTrigger, TimeTrigger

__all__ = [
    "AUTO_ARGUMENTS",
    "DAY_PREPARE_TRIGGER_ID",
    "DAY_TRIGGER_ID",
    "DEFAULT_TASK_PATH",
    "DEFERRED_PREPARE_TRIGGER_ID",
    "DEFERRED_TRIGGER_ID",
    "NIGHT_PREPARE_TRIGGER_ID",
    "NIGHT_TRIGGER_ID",
    "TASK_BACKUP_KIND",
    "TASK_BACKUP_SCHEMA_VERSION",
    "TASK_SPEC_KIND",
    "TASK_SPEC_SCHEMA_VERSION",
    "DailyTrigger",
    "SchedulerContractError",
    "SchedulerMutationError",
    "TaskAction",
    "TaskDefinitionBackup",
    "TaskDifference",
    "TaskInspection",
    "TaskMutationOutcome",
    "TaskPrincipal",
    "TaskSchedulerBackend",
    "TaskSettings",
    "TaskSpec",
    "TaskTrigger",
    "TimeTrigger",
    "build_task_spec",
    "compare_task_specs",
    "delete_task",
    "inspect_task",
    "reconcile_task",
    "set_task_enabled",
    "validate_desired_task",
]
