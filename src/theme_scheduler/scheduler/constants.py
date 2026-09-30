"""Frozen identities and values for the managed scheduled task."""

import re

TASK_SPEC_KIND = "themescheduler.task-spec"
TASK_SPEC_SCHEMA_VERSION = 2
TASK_BACKUP_KIND = "themescheduler.task-definition-backup"
TASK_BACKUP_SCHEMA_VERSION = 1
DEFAULT_TASK_PATH = r"\ThemeScheduler"
DAY_PREPARE_TRIGGER_ID = "DayPrepare"
DAY_TRIGGER_ID = "DayBoundary"
NIGHT_PREPARE_TRIGGER_ID = "NightPrepare"
NIGHT_TRIGGER_ID = "NightBoundary"
DEFERRED_PREPARE_TRIGGER_ID = "DeferredPrepare"
DEFERRED_TRIGGER_ID = "DeferredBoundary"
AUTO_RETRY_TRIGGER_ID = "AutoRetry"
AUTO_ARGUMENTS = "auto"
EXECUTION_TIME_LIMIT = "PT5M"
_TIME_PATTERN = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
_FIXED_TRIGGER_IDS = frozenset(
    {
        DAY_PREPARE_TRIGGER_ID,
        DAY_TRIGGER_ID,
        NIGHT_PREPARE_TRIGGER_ID,
        NIGHT_TRIGGER_ID,
    }
)
_DEFERRED_TRIGGER_IDS = frozenset({DEFERRED_PREPARE_TRIGGER_ID, DEFERRED_TRIGGER_ID})
_AUTO_RETRY_TRIGGER_IDS = frozenset({AUTO_RETRY_TRIGGER_ID})
