"""Installed program and user-data layout contract."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ._validation import (
    PRODUCT_ID,
    InstallContractError,
    _is_relative_to,
    _same_path,
    _validate_transaction_id,
)


@dataclass(frozen=True)
class InstallLayout:
    program_root: Path
    data_root: Path

    def __post_init__(self) -> None:
        raw_program_root = Path(self.program_root)
        raw_data_root = Path(self.data_root)
        if not raw_program_root.is_absolute() or not raw_data_root.is_absolute():
            raise InstallContractError("Install roots must be absolute.")
        program_root = raw_program_root.resolve(strict=False)
        data_root = raw_data_root.resolve(strict=False)
        if (
            program_root.name != PRODUCT_ID
            or program_root.parent.name.casefold() != "programs"
        ):
            raise InstallContractError(
                "Program root must be a Programs/ThemeScheduler directory."
            )
        if data_root.name != PRODUCT_ID:
            raise InstallContractError("Data root must be a ThemeScheduler directory.")
        if _same_path(program_root, data_root):
            raise InstallContractError("Program and data roots must be separate.")
        if _is_relative_to(program_root, data_root) or _is_relative_to(
            data_root, program_root
        ):
            raise InstallContractError(
                "Program and data roots cannot contain each other."
            )
        object.__setattr__(self, "program_root", program_root)
        object.__setattr__(self, "data_root", data_root)

    @classmethod
    def default(cls) -> InstallLayout:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise OSError("LOCALAPPDATA is unavailable.")
        base = Path(local_app_data).resolve(strict=False)
        return cls(
            base / "Programs" / PRODUCT_ID,
            base / PRODUCT_ID,
        )

    @property
    def app(self) -> Path:
        return self.program_root / "app"

    @property
    def executable(self) -> Path:
        return self.app / "ThemeScheduler.exe"

    @property
    def maintenance(self) -> Path:
        return self.program_root / "maintenance"

    @property
    def uninstaller(self) -> Path:
        return self.maintenance / "Uninstall.exe"

    @property
    def metadata(self) -> Path:
        return self.program_root / "metadata"

    @property
    def installation_record(self) -> Path:
        return self.metadata / "installation.json"

    @property
    def payload_manifest(self) -> Path:
        return self.metadata / "payload-manifest.json"

    def staging(self, transaction_id: str) -> Path:
        _validate_transaction_id(transaction_id)
        return self.program_root / f".staging-{transaction_id}"

    def rollback(self, transaction_id: str) -> Path:
        _validate_transaction_id(transaction_id)
        return self.program_root / f".rollback-{transaction_id}"

    def lifecycle_journal(self, transaction_id: str) -> Path:
        _validate_transaction_id(transaction_id)
        return self.program_root / f".{transaction_id}.json"

    def deployment_journal(self, transaction_id: str) -> Path:
        _validate_transaction_id(transaction_id)
        return self.program_root / f".{transaction_id}.deployment.json"

    def validate_owned_target(
        self, candidate: Path, *, allow_root: bool = False
    ) -> Path:
        target = Path(candidate).resolve(strict=False)
        if not _is_relative_to(target, self.program_root):
            raise InstallContractError("Target is outside the program root.")
        if _same_path(target, self.program_root) and not allow_root:
            raise InstallContractError(
                "Program root requires explicit allow_root confirmation."
            )
        return target
