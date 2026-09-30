"""Versioned single-task scheduler contracts and orchestration."""

from .constants import (
    AUTO_ARGUMENTS,
    AUTO_RETRY_TRIGGER_ID,
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
from .retry import (
    AutoRetryOutcome,
    AutoRetryStatus,
    ceil_to_whole_minute,
    ensure_future_auto_retry,
)
from .specification import build_task_spec, validate_desired_task
from .triggers import DailyTrigger, TaskTrigger, TimeTrigger

__all__ = [
    "AUTO_ARGUMENTS",
    "AUTO_RETRY_TRIGGER_ID",
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
    "AutoRetryOutcome",
    "AutoRetryStatus",
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
    "ceil_to_whole_minute",
    "compare_task_specs",
    "delete_task",
    "ensure_future_auto_retry",
    "inspect_task",
    "reconcile_task",
    "set_task_enabled",
    "validate_desired_task",
]
