"""Stable automatic-run result model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core import (
    EXIT_CODE_BY_RESULT,
    AutoExitCode,
    AutoResultKind,
)


@dataclass(frozen=True)
class AutoRunOutcome:
    result: AutoResultKind
    target_profile: str | None
    transaction_directory: Path | None
    learned_profile: str | None
    windows_changed: bool
    state_changed: bool
    recovered: bool
    message: str

    @property
    def exit_code(self) -> AutoExitCode:
        return EXIT_CODE_BY_RESULT[self.result]

    def as_dict(self) -> dict[str, Any]:
        return {
            "result": self.result.value,
            "exitCode": int(self.exit_code),
            "targetProfile": self.target_profile,
            "transactionDirectory": (
                str(self.transaction_directory.resolve())
                if self.transaction_directory is not None
                else None
            ),
            "learnedProfile": self.learned_profile,
            "windowsChanged": self.windows_changed,
            "stateChanged": self.state_changed,
            "recovered": self.recovered,
            "message": self.message,
        }
