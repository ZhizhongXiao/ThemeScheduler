"""Failure-state helpers shared by the automatic runner."""

from __future__ import annotations

from ..auto_transaction import AutoTransaction, file_sha256
from ..core import AutoResultKind
from ..persistence import captured_at
from ..state import AppState
from .contracts import AutoRunnerBindings


class AutoFailureMixin(AutoRunnerBindings):
    def _save_failure_state(
        self, before: AppState, timestamp: str, *, partial: bool
    ) -> bool:
        try:
            self.state_store.save(
                AppState(
                    paused=before.paused,
                    active_profile=before.active_profile,
                    last_run_at=timestamp,
                    last_applied_profile=before.last_applied_profile,
                    last_result="partial" if partial else "failed",
                )
            )
            return True
        except Exception:
            return False

    def _restore_state_if_needed(
        self,
        before: AppState,
        before_hash: str,
        after_hash: str,
    ) -> bool:
        try:
            current_hash = file_sha256(self.layout.state)
            if current_hash == before_hash:
                return True
            if current_hash != after_hash:
                return False
            self.state_store.save(before)
            return file_sha256(self.layout.state) == before_hash
        except Exception:
            return False

    def _best_effort_failure_log(
        self, transaction: AutoTransaction, result: AutoResultKind
    ) -> None:
        try:
            self._append_event(
                timestamp=captured_at(),
                level=(
                    "ERROR"
                    if result is AutoResultKind.APPLY_FAILED_ROLLED_BACK
                    else "CRITICAL"
                ),
                event="auto.failed",
                result=(
                    "failed"
                    if result is AutoResultKind.APPLY_FAILED_ROLLED_BACK
                    else "partial"
                ),
                target=transaction.target_profile,
                transaction=transaction.transaction_id,
                error_code=transaction.error_code,
                message=transaction.message,
                verification="failed",
                rollback_attempted=(transaction.rollback_succeeded is not None),
                rollback_succeeded=transaction.rollback_succeeded,
            )
        except Exception:
            pass
