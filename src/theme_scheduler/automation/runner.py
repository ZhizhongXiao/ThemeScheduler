"""Single-run automatic switching orchestration."""

from __future__ import annotations

# Constructor checks remain runtime guards for callers outside typed code.
# pyright: strict, reportUnnecessaryIsInstance=false
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from ..accent_profile import AccentProfile, AccentProfileStore
from ..accent_theme import (
    LiveThemeApplyError,
    theme_visual_state_from_dict,
)
from ..appearance import ThemeMode
from ..auto_transaction import (
    AUTO_TRANSACTION_FILE_NAME,
    AutoTransaction,
    AutoTransactionStore,
    file_sha256,
    json_document_sha256,
)
from ..config import ConfigStore
from ..core import (
    AutoExitCode,
    AutoPlanKind,
    AutoResultKind,
    AutoRunPlan,
    Clock,
    ExecutionLock,
    RunIntent,
    SystemClock,
    plan_auto_run,
)
from ..initial_setup import initial_setup_pending
from ..log_policy import EventLogWriter, LogEvent
from ..persistence import captured_at
from ..runtime_retention import (
    execute_runtime_cleanup,
    plan_runtime_cleanup,
)
from ..state import AppState, StateStore
from ..storage import UserDataLayout
from .backend import AutoWindowsBackend
from .failure import AutoFailureMixin
from .outcome import AutoRunOutcome
from .recovery import AutoRecoveryMixin

type CleanupCallback = Callable[[Path], None]


@dataclass(frozen=True)
class _TrustedRun:
    now: datetime
    timestamp: str
    plan: AutoRunPlan
    state: AppState


@dataclass(frozen=True)
class _ApplyRun:
    trusted: _TrustedRun
    target_profile: AccentProfile
    learning_store: AccentProfileStore | None
    state_before_hash: str
    state_after: AppState
    transaction_directory: Path
    transaction_store: AutoTransactionStore
    transaction: AutoTransaction
    learned: str | None = None


class AutoRunner(AutoFailureMixin, AutoRecoveryMixin):
    """Single-run coordinator; callers provide the lock and Windows adapter."""

    def __init__(
        self,
        layout: UserDataLayout,
        execution_lock: ExecutionLock,
        windows: AutoWindowsBackend,
        *,
        clock: Clock | None = None,
        config_store: ConfigStore | None = None,
        state_store: StateStore | None = None,
        event_log: EventLogWriter | None = None,
        cleanup_callback: CleanupCallback | None = None,
        intent: RunIntent = RunIntent.AUTOMATIC,
    ) -> None:
        if not isinstance(intent, RunIntent):
            raise ValueError("intent must be a RunIntent.")
        self.layout = layout
        self.execution_lock = execution_lock
        self.windows = windows
        self.clock = clock or SystemClock()
        self.config_store = config_store or ConfigStore(layout.config)
        self.state_store = state_store or StateStore(layout.state)
        self.event_log = event_log or EventLogWriter(layout.event_log)
        self.cleanup_callback = cleanup_callback or self._cleanup_runtime
        self.intent = intent
        self._active_transaction_id: str | None = None

    @staticmethod
    def _iso(instant: datetime) -> str:
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("Automatic run clock must include a UTC offset.")
        return instant.isoformat(timespec="seconds")

    def _cleanup_runtime(self, protected: Path) -> None:
        plan = plan_runtime_cleanup(
            self.layout.runtime,
            protected=(protected,),
        )
        execute_runtime_cleanup(self.layout.runtime, plan)

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
        return AutoRunOutcome(
            result,
            target,
            transaction,
            learned,
            windows_changed,
            state_changed,
            recovered,
            message,
        )

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
        self.event_log.append(
            LogEvent(
                occurred_at=timestamp,
                level=level,
                event=event,
                result=result,
                trigger=(
                    "manual" if self.intent is RunIntent.MANUAL_CURRENT else "auto"
                ),
                target_profile=target,
                transaction_id=(transaction or self._active_transaction_id),
                error_code=error_code,
                message=message,
                verification=verification,
                rollback_attempted=rollback_attempted,
                rollback_succeeded=rollback_succeeded,
            )
        )

    def run(self) -> AutoRunOutcome:
        try:
            acquired = self.execution_lock.acquire()
        except Exception as exc:
            return self._outcome(
                AutoResultKind.FATAL_FAILURE,
                f"Execution lock failed: {type(exc).__name__}: {exc}",
            )
        if not acquired:
            return self._outcome(
                AutoResultKind.ALREADY_RUNNING,
                "Another automatic run already owns the execution lock.",
            )

        outcome: AutoRunOutcome
        try:
            outcome = self.run_locked()
        except Exception as exc:
            outcome = self._outcome(
                AutoResultKind.FATAL_FAILURE,
                f"Unexpected automatic-core failure: {type(exc).__name__}: {exc}",
            )
        try:
            self.execution_lock.release()
        except Exception as exc:
            if outcome.exit_code == AutoExitCode.SUCCESS:
                return self._outcome(
                    AutoResultKind.PARTIAL_FAILURE,
                    f"Run completed but lock release failed: {type(exc).__name__}: {exc}",
                    target=outcome.target_profile,
                    transaction=outcome.transaction_directory,
                    learned=outcome.learned_profile,
                    windows_changed=outcome.windows_changed,
                    state_changed=outcome.state_changed,
                    recovered=outcome.recovered,
                )
        return outcome

    def _load_trusted_run(
        self,
        now: datetime,
        timestamp: str,
    ) -> _TrustedRun | AutoRunOutcome:
        try:
            if initial_setup_pending(self.layout.initial_setup_marker):
                try:
                    self._append_event(
                        timestamp=timestamp,
                        level="INFO",
                        event="auto.initial-setup-pending",
                        result="skipped",
                        message=(
                            "Automatic switching is blocked until first-run "
                            "configuration is saved."
                        ),
                    )
                except Exception as exc:
                    return self._outcome(
                        AutoResultKind.PARTIAL_FAILURE,
                        (
                            "Initial setup remained safely blocked, but "
                            f"logging failed: {exc}"
                        ),
                    )
                return self._outcome(
                    AutoResultKind.PAUSED,
                    "Initial setup is incomplete; Windows was not changed.",
                )
            config = self.config_store.load()
            state = self.state_store.load()
            plan = plan_auto_run(
                config,
                state,
                now,
                intent=self.intent,
            )
            return _TrustedRun(now, timestamp, plan, state)
        except Exception as exc:
            message = f"Automatic data is untrusted: {type(exc).__name__}: {exc}"
            try:
                self._append_event(
                    timestamp=timestamp,
                    level="ERROR",
                    event="auto.data-untrusted",
                    result="failed",
                    error_code="data.untrusted",
                    message=message,
                )
            except Exception:
                pass
            return self._outcome(AutoResultKind.DATA_UNTRUSTED, message)

    def _inactive_plan_outcome(
        self,
        trusted: _TrustedRun,
    ) -> AutoRunOutcome | None:
        plan = trusted.plan
        if plan.kind is AutoPlanKind.PAUSED:
            try:
                self._append_event(
                    timestamp=trusted.timestamp,
                    level="INFO",
                    event="auto.paused",
                    result="skipped",
                    message="Automatic switching is paused.",
                )
            except Exception as exc:
                return self._outcome(
                    AutoResultKind.PARTIAL_FAILURE,
                    f"Paused safely, but logging failed: {exc}",
                )
            return self._outcome(
                AutoResultKind.PAUSED,
                (
                    "Immediate synchronization is blocked while automatic "
                    "switching is paused; Windows was not changed."
                    if self.intent is RunIntent.MANUAL_CURRENT
                    else ("Automatic switching is paused; Windows was not changed.")
                ),
            )
        if plan.kind is not AutoPlanKind.NO_CHANGE:
            return None
        assert plan.target_profile is not None
        try:
            self._append_event(
                timestamp=trusted.timestamp,
                level="INFO",
                event="auto.no-change",
                result="skipped",
                target=plan.target_profile,
                message=("The trusted active profile already matches the target."),
            )
        except Exception as exc:
            return self._outcome(
                AutoResultKind.PARTIAL_FAILURE,
                f"No Windows change was needed, but logging failed: {exc}",
                target=plan.target_profile,
            )
        return self._outcome(
            AutoResultKind.NO_CHANGE,
            "The trusted active profile already matches the current target.",
            target=plan.target_profile,
        )

    def _load_apply_profiles(
        self,
        trusted: _TrustedRun,
    ) -> tuple[AccentProfile, AccentProfileStore | None] | AutoRunOutcome:
        plan = trusted.plan
        assert plan.target_profile is not None
        target_store = AccentProfileStore(
            self.layout.profile_path(plan.target_profile),
            plan.target_profile,
        )
        try:
            target_profile = target_store.load()
            learning_store = None
            if plan.learn_profile is not None:
                learning_store = AccentProfileStore(
                    self.layout.profile_path(plan.learn_profile),
                    plan.learn_profile,
                )
                learning_store.load()
            return target_profile, learning_store
        except Exception as exc:
            message = (
                f"Required accent profile is untrusted: {type(exc).__name__}: {exc}"
            )
            try:
                self._append_event(
                    timestamp=trusted.timestamp,
                    level="ERROR",
                    event="auto.data-untrusted",
                    result="failed",
                    target=plan.target_profile,
                    error_code="profile.untrusted",
                    message=message,
                )
            except Exception:
                pass
            return self._outcome(
                AutoResultKind.DATA_UNTRUSTED,
                message,
                target=plan.target_profile,
            )

    def _probe_apply_capability(
        self,
        trusted: _TrustedRun,
    ) -> AutoRunOutcome | None:
        target = trusted.plan.target_profile
        assert target is not None
        try:
            self.windows.probe()
            return None
        except Exception as exc:
            message = (
                f"Automatic Windows capability probe failed: "
                f"{type(exc).__name__}: {exc}"
            )
            try:
                self._append_event(
                    timestamp=trusted.timestamp,
                    level="ERROR",
                    event="auto.capability-failed",
                    result="failed",
                    target=target,
                    error_code="capability.unavailable",
                    message=message,
                )
            except Exception:
                pass
            return self._outcome(
                AutoResultKind.FATAL_FAILURE,
                message,
                target=target,
            )

    def _create_apply_run(
        self,
        trusted: _TrustedRun,
        target_profile: AccentProfile,
        learning_store: AccentProfileStore | None,
    ) -> _ApplyRun:
        plan = trusted.plan
        assert plan.target_profile is not None
        assert plan.target_apps_theme is not None
        self.layout.ensure_directories()
        state_before_hash = file_sha256(self.layout.state)
        state_after = AppState(
            paused=trusted.state.paused,
            active_profile=plan.target_profile,
            last_run_at=trusted.timestamp,
            last_applied_profile=plan.target_profile,
            last_result="success",
        )
        directory = self.layout.new_transaction_directory(trusted.now)
        directory.mkdir(parents=False, exist_ok=False)
        store = AutoTransactionStore(directory / AUTO_TRANSACTION_FILE_NAME)
        transaction = store.create(
            AutoTransaction(
                transaction_id=directory.name,
                status="planned",
                started_at=trusted.timestamp,
                updated_at=trusted.timestamp,
                target_profile=plan.target_profile,
                target_apps_theme=plan.target_apps_theme,
                learn_profile=plan.learn_profile,
                state_before_sha256=state_before_hash,
                state_after=state_after,
                state_after_sha256=json_document_sha256(state_after.as_dict()),
            )
        )
        self._active_transaction_id = transaction.transaction_id
        return _ApplyRun(
            trusted,
            target_profile,
            learning_store,
            state_before_hash,
            state_after,
            directory,
            store,
            transaction,
        )

    def _learn_departing_profile(
        self,
        context: _ApplyRun,
    ) -> _ApplyRun | AutoRunOutcome:
        profile_name = context.trusted.plan.learn_profile
        if profile_name is None:
            return context
        assert context.learning_store is not None
        try:
            learned_profile = self.windows.capture_profile(
                profile_name,
                context.trusted.timestamp,
            )
            context.learning_store.replace_if_valid(learned_profile)
            return replace(context, learned=profile_name)
        except Exception as exc:
            failed = replace(
                context.transaction,
                status="failed",
                updated_at=captured_at(),
                error_code="learning.failed",
                message=(
                    f"Departing profile learning failed: {type(exc).__name__}: {exc}"
                )[:500],
            )
            context.transaction_store.save(failed)
            return self._outcome(
                AutoResultKind.APPLY_FAILED_ROLLED_BACK,
                failed.message or "Departing profile learning failed.",
                target=context.trusted.plan.target_profile,
                transaction=context.transaction_directory,
            )

    def _apply_target_profile(
        self,
        context: _ApplyRun,
    ) -> _ApplyRun | AutoRunOutcome:
        plan = context.trusted.plan
        assert plan.target_apps_theme is not None
        try:
            apply_outcome = self.windows.apply_profile(
                context.target_profile,
                ThemeMode(plan.target_apps_theme),
                context.transaction_directory,
            )
            actual = theme_visual_state_from_dict(apply_outcome.actual)
            transaction = context.transaction_store.save(
                replace(
                    context.transaction,
                    status="windows-verified",
                    updated_at=captured_at(),
                    windows_target=actual,
                )
            )
            return replace(context, transaction=transaction)
        except LiveThemeApplyError as exc:
            return self._live_apply_failure(context, exc)
        except Exception as exc:
            return self._unexpected_apply_failure(context, exc)

    def _live_apply_failure(
        self,
        context: _ApplyRun,
        failure: LiveThemeApplyError,
    ) -> AutoRunOutcome:
        result = (
            AutoResultKind.APPLY_FAILED_ROLLED_BACK
            if failure.rollback_succeeded
            else AutoResultKind.PARTIAL_FAILURE
        )
        failed = replace(
            context.transaction,
            status=("failed" if failure.rollback_succeeded else "partial"),
            updated_at=captured_at(),
            error_code="theme.apply-failed",
            message=str(failure)[:500],
            rollback_succeeded=failure.rollback_succeeded,
        )
        context.transaction_store.save(failed)
        self._save_failure_state(
            context.trusted.state,
            context.trusted.timestamp,
            partial=not failure.rollback_succeeded,
        )
        self._best_effort_failure_log(failed, result)
        return self._outcome(
            result,
            str(failure),
            target=context.trusted.plan.target_profile,
            transaction=context.transaction_directory,
            learned=context.learned,
            windows_changed=not failure.rollback_succeeded,
            state_changed=True,
        )

    def _unexpected_apply_failure(
        self,
        context: _ApplyRun,
        failure: Exception,
    ) -> AutoRunOutcome:
        partial = replace(
            context.transaction,
            status="partial",
            updated_at=captured_at(),
            error_code="theme.unexpected",
            message=f"{type(failure).__name__}: {failure}"[:500],
        )
        context.transaction_store.save(partial)
        self._save_failure_state(
            context.trusted.state,
            context.trusted.timestamp,
            partial=True,
        )
        self._best_effort_failure_log(partial, AutoResultKind.PARTIAL_FAILURE)
        return self._outcome(
            AutoResultKind.PARTIAL_FAILURE,
            partial.message or "Unexpected theme failure.",
            target=context.trusted.plan.target_profile,
            transaction=context.transaction_directory,
            learned=context.learned,
            windows_changed=True,
            state_changed=True,
        )

    def _commit_run_state(
        self,
        context: _ApplyRun,
    ) -> _ApplyRun | AutoRunOutcome:
        try:
            self.state_store.save(context.state_after)
        except Exception as exc:
            rollback_succeeded = self.windows.rollback(context.transaction_directory)
            state_restored = self._restore_state_if_needed(
                context.trusted.state,
                context.state_before_hash,
                context.transaction.state_after_sha256,
            )
            safe = rollback_succeeded and state_restored
            failed = replace(
                context.transaction,
                status="failed" if safe else "partial",
                updated_at=captured_at(),
                error_code="state.commit-failed",
                message=(f"State commit failed: {type(exc).__name__}: {exc}")[:500],
                rollback_succeeded=safe,
            )
            context.transaction_store.save(failed)
            result = (
                AutoResultKind.APPLY_FAILED_ROLLED_BACK
                if safe
                else AutoResultKind.PARTIAL_FAILURE
            )
            self._best_effort_failure_log(failed, result)
            return self._outcome(
                result,
                failed.message or "State commit failed.",
                target=context.trusted.plan.target_profile,
                transaction=context.transaction_directory,
                learned=context.learned,
                windows_changed=not safe,
                state_changed=not state_restored,
            )
        transaction = context.transaction_store.save(
            replace(
                context.transaction,
                status="state-committed",
                updated_at=captured_at(),
            )
        )
        return replace(context, transaction=transaction)

    def _complete_apply_run(
        self,
        context: _ApplyRun,
    ) -> AutoRunOutcome:
        cleanup_failure = self._cleanup_committed_run(context)
        if cleanup_failure is not None:
            return cleanup_failure
        log_failure = self._log_applied_event(context)
        if log_failure is not None:
            return log_failure
        context.transaction_store.save(
            replace(
                context.transaction,
                status="completed",
                updated_at=captured_at(),
            )
        )
        return self._outcome(
            AutoResultKind.APPLIED,
            (
                "Current-period profile applied, verified, committed, and logged."
                if self.intent is RunIntent.MANUAL_CURRENT
                else "Automatic profile applied, verified, committed, and logged."
            ),
            target=context.trusted.plan.target_profile,
            transaction=context.transaction_directory,
            learned=context.learned,
            windows_changed=True,
            state_changed=True,
        )

    def _cleanup_committed_run(
        self,
        context: _ApplyRun,
    ) -> AutoRunOutcome | None:
        try:
            self.cleanup_callback(context.transaction_directory)
            return None
        except Exception as exc:
            partial = context.transaction_store.save(
                replace(
                    context.transaction,
                    status="partial",
                    updated_at=captured_at(),
                    error_code="cleanup.failed",
                    message=(f"Runtime cleanup failed: {type(exc).__name__}: {exc}")[
                        :500
                    ],
                )
            )
            return self._outcome(
                AutoResultKind.PARTIAL_FAILURE,
                partial.message or "Runtime cleanup failed.",
                target=context.trusted.plan.target_profile,
                transaction=context.transaction_directory,
                learned=context.learned,
                windows_changed=True,
                state_changed=True,
            )

    def _log_applied_event(
        self,
        context: _ApplyRun,
    ) -> AutoRunOutcome | None:
        try:
            self._append_event(
                timestamp=context.trusted.timestamp,
                level="INFO",
                event="auto.applied",
                result="success",
                target=context.trusted.plan.target_profile,
                message="Automatic profile applied and verified.",
                verification="passed",
            )
            return None
        except Exception as exc:
            partial = context.transaction_store.save(
                replace(
                    context.transaction,
                    status="partial",
                    updated_at=captured_at(),
                    error_code="log.write-failed",
                    message=(f"Event log write failed: {type(exc).__name__}: {exc}")[
                        :500
                    ],
                )
            )
            return self._outcome(
                AutoResultKind.PARTIAL_FAILURE,
                partial.message or "Event log write failed.",
                target=context.trusted.plan.target_profile,
                transaction=context.transaction_directory,
                learned=context.learned,
                windows_changed=True,
                state_changed=True,
            )

    def run_locked(self) -> AutoRunOutcome:
        """Execute the core while the caller already owns the shared lock."""

        self._active_transaction_id = None
        recovery = self._recover_pending()
        if recovery is not None:
            return recovery
        now = self.clock.now()
        timestamp = self._iso(now)
        trusted = self._load_trusted_run(now, timestamp)
        if isinstance(trusted, AutoRunOutcome):
            return trusted
        inactive = self._inactive_plan_outcome(trusted)
        if inactive is not None:
            return inactive
        profiles = self._load_apply_profiles(trusted)
        if isinstance(profiles, AutoRunOutcome):
            return profiles
        capability_failure = self._probe_apply_capability(trusted)
        if capability_failure is not None:
            return capability_failure
        context = self._create_apply_run(trusted, *profiles)
        learned = self._learn_departing_profile(context)
        if isinstance(learned, AutoRunOutcome):
            return learned
        applied = self._apply_target_profile(learned)
        if isinstance(applied, AutoRunOutcome):
            return applied
        committed = self._commit_run_state(applied)
        if isinstance(committed, AutoRunOutcome):
            return committed
        return self._complete_apply_run(committed)
