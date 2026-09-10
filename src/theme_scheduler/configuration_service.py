"""Transactional configuration and Task Scheduler update orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any

from .accent_profile import (
    AccentProfile,
    AccentProfileStore,
    RgbColor,
)
from .config import AppConfig, ConfigStore
from .control_service import ensure_no_pending_auto_transaction
from .core import Clock, ExecutionLock, SystemClock
from .log_policy import EventLogWriter, LogEvent
from .scheduler import (
    SchedulerMutationError,
    TaskSchedulerBackend,
    build_task_spec,
    reconcile_task,
)
from .storage import UserDataLayout
from .switch_override import PendingSwitch, PendingSwitchStore


class ConfigurationResultKind(str, Enum):
    CHANGED = "changed"
    NO_CHANGE = "no-change"
    ALREADY_RUNNING = "already-running"
    DATA_UNTRUSTED = "data-untrusted"
    PARTIAL_FAILURE = "partial-failure"
    FATAL_FAILURE = "fatal-failure"


@dataclass(frozen=True)
class ConfigurationOutcome:
    result: ConfigurationResultKind
    config_changed: bool | None
    task_changed: bool | None
    task_verified: bool
    rollback_attempted: bool
    rollback_succeeded: bool | None
    log_written: bool
    message: str
    task_accessed: bool = False
    pending_switch_changed: bool = False
    profiles_changed: Mapping[str, bool] | None = None
    profiles_verified: bool = False
    profile_rollback_attempted: bool = False
    profile_rollback_succeeded: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": "save-config",
            "result": self.result.value,
            "configChanged": self.config_changed,
            "taskSchedulerAccessed": self.task_accessed,
            "taskSchedulerChanged": self.task_changed,
            "taskVerified": self.task_verified,
            "pendingSwitchChanged": self.pending_switch_changed,
            "profilesChanged": (
                dict(self.profiles_changed)
                if self.profiles_changed is not None
                else None
            ),
            "profilesVerified": self.profiles_verified,
            "profileRollbackAttempted": (self.profile_rollback_attempted),
            "profileRollbackSucceeded": (self.profile_rollback_succeeded),
            "rollbackAttempted": self.rollback_attempted,
            "rollbackSucceeded": self.rollback_succeeded,
            "logWritten": self.log_written,
            "dataChanged": (
                True
                if (
                    self.config_changed is True
                    or self.log_written
                    or (
                        self.profiles_changed is not None
                        and any(self.profiles_changed.values())
                    )
                )
                else self.config_changed
            ),
            "windowsChanged": False,
            "message": self.message,
        }


class ConfigurationService:
    """Commit configuration and its task definition as one guarded operation."""

    def __init__(
        self,
        layout: UserDataLayout,
        execution_lock: ExecutionLock,
        task_backend: TaskSchedulerBackend,
        *,
        executable: str,
        user_id: str,
        config_store: ConfigStore | None = None,
        event_log: EventLogWriter | None = None,
        clock: Clock | None = None,
        pending_store: PendingSwitchStore | None = None,
        profile_stores: Mapping[str, AccentProfileStore] | None = None,
        audit_event: str = "config.updated",
        audit_trigger: str = "manual",
    ) -> None:
        self.layout = layout
        self.execution_lock = execution_lock
        self.task_backend = task_backend
        self.executable = executable
        self.user_id = user_id
        self.config_store = config_store or ConfigStore(layout.config)
        self.event_log = event_log or EventLogWriter(layout.event_log)
        self.clock = clock or SystemClock()
        self.pending_store = pending_store or PendingSwitchStore(layout.pending_switch)
        self.profile_stores = dict(
            profile_stores
            if profile_stores is not None
            else {
                name: AccentProfileStore(
                    layout.profile_path(name),
                    name,
                )
                for name in ("day", "night")
            }
        )
        if set(self.profile_stores) != {"day", "night"}:
            raise ValueError("Profile stores must contain exactly day and night.")
        self.audit_event = audit_event
        self.audit_trigger = audit_trigger

    @staticmethod
    def _timestamp(instant: datetime) -> str:
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("Configuration clock must include a UTC offset.")
        return instant.isoformat(timespec="seconds")

    def update(self, target: AppConfig) -> ConfigurationOutcome:
        if not isinstance(target, AppConfig):
            raise TypeError("Configuration target must be AppConfig.")
        return self._run_locked(lambda: self._update_locked(target))

    def update_bundle(
        self,
        target: AppConfig,
        profile_colors: Mapping[str, RgbColor],
    ) -> ConfigurationOutcome:
        if not isinstance(target, AppConfig):
            raise TypeError("Configuration target must be AppConfig.")
        if not isinstance(profile_colors, Mapping):
            raise TypeError("Profile colors must be a mapping.")
        if set(profile_colors) != {"day", "night"}:
            raise ValueError("Profile colors must contain exactly day and night.")
        colors = dict(profile_colors)
        if not all(isinstance(color, RgbColor) for color in colors.values()):
            raise TypeError("Profile colors must be RgbColor values.")
        return self._run_locked(lambda: self._update_bundle_locked(target, colors))

    def _run_locked(
        self,
        operation: Callable[[], ConfigurationOutcome],
    ) -> ConfigurationOutcome:
        try:
            acquired = self.execution_lock.acquire()
        except Exception as exc:
            return ConfigurationOutcome(
                ConfigurationResultKind.FATAL_FAILURE,
                None,
                None,
                False,
                False,
                None,
                False,
                f"Execution lock failed: {type(exc).__name__}: {exc}",
            )
        if not acquired:
            return ConfigurationOutcome(
                ConfigurationResultKind.ALREADY_RUNNING,
                False,
                None,
                False,
                False,
                None,
                False,
                "Another automatic or control operation owns the execution lock.",
            )

        try:
            outcome = operation()
        except Exception as exc:
            outcome = ConfigurationOutcome(
                ConfigurationResultKind.FATAL_FAILURE,
                None,
                None,
                False,
                False,
                None,
                False,
                f"Unexpected configuration failure: {type(exc).__name__}: {exc}",
            )

        try:
            self.execution_lock.release()
        except Exception as exc:
            if outcome.result in {
                ConfigurationResultKind.CHANGED,
                ConfigurationResultKind.NO_CHANGE,
            }:
                outcome = replace(
                    outcome,
                    result=ConfigurationResultKind.PARTIAL_FAILURE,
                    message=(
                        f"{outcome.message} Execution lock release failed: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                )
        return outcome

    def _update_bundle_locked(
        self,
        target: AppConfig,
        profile_colors: Mapping[str, RgbColor],
    ) -> ConfigurationOutcome:
        try:
            ensure_no_pending_auto_transaction(self.layout.runtime)
            before_profiles = {
                name: self.profile_stores[name].load() for name in ("day", "night")
            }
            timestamp = self._timestamp(self.clock.now())
            target_profiles = {}
            for name in ("day", "night"):
                before_profile = before_profiles[name]
                colorization_color = profile_colors[name].replace_colorization_rgb(
                    before_profile.colorization_color
                )
                target_profiles[name] = (
                    before_profile
                    if (
                        colorization_color == before_profile.colorization_color
                        and not before_profile.auto_colorization
                    )
                    else replace(
                        before_profile,
                        captured_at=timestamp,
                        auto_colorization=False,
                        colorization_color=colorization_color,
                    )
                )
        except Exception as exc:
            return ConfigurationOutcome(
                ConfigurationResultKind.DATA_UNTRUSTED,
                False,
                None,
                False,
                False,
                None,
                False,
                (
                    "Profile configuration update is blocked: "
                    f"{type(exc).__name__}: {exc}"
                ),
                profiles_changed=None,
                profiles_verified=False,
            )

        changed = {
            name: target_profiles[name] != before_profiles[name]
            for name in ("day", "night")
        }
        try:
            for name in ("day", "night"):
                if changed[name]:
                    self.profile_stores[name].replace_if_valid(target_profiles[name])
                elif self.profile_stores[name].load() != before_profiles[name]:
                    raise OSError(f"{name} profile changed during bundle preparation.")
        except Exception as exc:
            restored = self._restore_profiles(before_profiles)
            return ConfigurationOutcome(
                (
                    ConfigurationResultKind.FATAL_FAILURE
                    if restored
                    else ConfigurationResultKind.PARTIAL_FAILURE
                ),
                False,
                None,
                False,
                True,
                restored,
                False,
                (
                    "Profile write failed: "
                    f"{type(exc).__name__}: {exc}. "
                    + (
                        "Both profiles were restored."
                        if restored
                        else "Profile rollback could not be verified."
                    )
                ),
                profiles_changed={
                    name: False if restored else changed[name]
                    for name in ("day", "night")
                },
                profiles_verified=False,
                profile_rollback_attempted=True,
                profile_rollback_succeeded=restored,
            )

        config_outcome = self._update_locked(
            target,
            additional_data_changed=any(changed.values()),
        )
        if config_outcome.task_verified:
            return replace(
                config_outcome,
                message=(
                    "Configuration, profiles, and Task Scheduler are consistent."
                    if config_outcome.result
                    is not ConfigurationResultKind.PARTIAL_FAILURE
                    else config_outcome.message
                ),
                profiles_changed=changed,
                profiles_verified=True,
            )

        profiles_restored = self._restore_profiles(before_profiles)
        existing_rollback = config_outcome.rollback_succeeded
        rollback_succeeded = profiles_restored and existing_rollback is not False
        return replace(
            config_outcome,
            result=(
                ConfigurationResultKind.FATAL_FAILURE
                if rollback_succeeded
                else ConfigurationResultKind.PARTIAL_FAILURE
            ),
            rollback_attempted=True,
            rollback_succeeded=rollback_succeeded,
            message=(
                f"{config_outcome.message} "
                + (
                    "Both profiles were restored."
                    if profiles_restored
                    else "Profile rollback could not be verified."
                )
            ),
            profiles_changed={
                name: False if profiles_restored else changed[name]
                for name in ("day", "night")
            },
            profiles_verified=False,
            profile_rollback_attempted=True,
            profile_rollback_succeeded=profiles_restored,
        )

    def _update_locked(
        self,
        target: AppConfig,
        *,
        additional_data_changed: bool = False,
    ) -> ConfigurationOutcome:
        try:
            ensure_no_pending_auto_transaction(self.layout.runtime)
            before = self.config_store.load()
            pending: PendingSwitch | None = (
                self.pending_store.load() if self.pending_store.exists else None
            )
            desired_task = build_task_spec(
                target,
                executable=self.executable,
                user_id=self.user_id,
            )
        except Exception as exc:
            return ConfigurationOutcome(
                ConfigurationResultKind.DATA_UNTRUSTED,
                False,
                None,
                False,
                False,
                None,
                False,
                f"Configuration update is blocked: {type(exc).__name__}: {exc}",
            )

        config_changed = target != before
        if config_changed:
            try:
                self.config_store.save(target)
            except Exception as exc:
                restored = self._restore_config(before)
                return ConfigurationOutcome(
                    (
                        ConfigurationResultKind.FATAL_FAILURE
                        if restored
                        else ConfigurationResultKind.PARTIAL_FAILURE
                    ),
                    None if not restored else False,
                    None,
                    False,
                    True,
                    restored,
                    False,
                    (
                        f"Configuration save failed: {type(exc).__name__}: {exc}. "
                        + (
                            "The previous configuration was restored."
                            if restored
                            else "The resulting configuration could not be verified."
                        )
                    ),
                )

        try:
            task_outcome = reconcile_task(self.task_backend, desired_task)
        except SchedulerMutationError as exc:
            config_restored = self._restore_config(before) if config_changed else True
            rollback_succeeded = config_restored and exc.rollback_succeeded
            return ConfigurationOutcome(
                (
                    ConfigurationResultKind.FATAL_FAILURE
                    if rollback_succeeded
                    else ConfigurationResultKind.PARTIAL_FAILURE
                ),
                False if config_restored else None,
                None,
                False,
                True,
                rollback_succeeded,
                False,
                (
                    f"Task update failed: {exc}. "
                    + (
                        "Configuration and task were restored."
                        if rollback_succeeded
                        else "Rollback was incomplete; run task repair before continuing."
                    )
                ),
                task_accessed=True,
            )
        except Exception as exc:
            config_restored = self._restore_config(before) if config_changed else True
            return ConfigurationOutcome(
                ConfigurationResultKind.PARTIAL_FAILURE,
                False if config_restored else None,
                None,
                False,
                True,
                False,
                False,
                (
                    f"Task update failed unexpectedly: {type(exc).__name__}: {exc}. "
                    + (
                        "The previous configuration was restored, but task state "
                        "must be checked."
                        if config_restored
                        else "Configuration and task state must be checked."
                    )
                ),
                task_accessed=True,
            )

        pending_changed = False
        if pending is not None:
            try:
                self.pending_store.clear(pending)
                pending_changed = True
            except Exception as exc:
                return ConfigurationOutcome(
                    ConfigurationResultKind.PARTIAL_FAILURE,
                    config_changed,
                    task_outcome.changed,
                    task_outcome.verified,
                    False,
                    None,
                    False,
                    (
                        "Configuration and fixed Task Scheduler triggers were "
                        "committed, but stale pending-switch state could not be "
                        f"removed: {type(exc).__name__}: {exc}"
                    ),
                    task_accessed=True,
                )

        result = (
            ConfigurationResultKind.CHANGED
            if (
                config_changed
                or task_outcome.changed
                or pending_changed
                or additional_data_changed
            )
            else ConfigurationResultKind.NO_CHANGE
        )
        try:
            self.event_log.append(
                LogEvent(
                    occurred_at=self._timestamp(self.clock.now()),
                    level="INFO",
                    event=self.audit_event,
                    result=(
                        "success"
                        if result is ConfigurationResultKind.CHANGED
                        else "skipped"
                    ),
                    trigger=self.audit_trigger,
                    message=("Configuration and Task Scheduler are consistent."),
                )
            )
        except Exception as exc:
            return ConfigurationOutcome(
                ConfigurationResultKind.PARTIAL_FAILURE,
                config_changed,
                task_outcome.changed,
                task_outcome.verified,
                False,
                None,
                False,
                (
                    "Configuration and task were committed, but logging failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
                task_accessed=True,
                pending_switch_changed=pending_changed,
            )

        return ConfigurationOutcome(
            result,
            config_changed,
            task_outcome.changed,
            task_outcome.verified,
            False,
            None,
            True,
            "Configuration and Task Scheduler are consistent.",
            task_accessed=True,
            pending_switch_changed=pending_changed,
        )

    def _restore_config(self, before: AppConfig) -> bool:
        try:
            actual = self.config_store.load()
            if actual == before:
                return True
            self.config_store.save(before)
            return self.config_store.load() == before
        except Exception:
            return False

    def _restore_profiles(
        self,
        before: Mapping[str, AccentProfile],
    ) -> bool:
        restored = True
        for name in ("night", "day"):
            try:
                store = self.profile_stores[name]
                try:
                    current = store.load()
                except Exception:
                    current = None
                if current != before[name]:
                    store.restore_trusted(before[name])
                restored = restored and store.load() == before[name]
            except Exception:
                restored = False
        return restored
