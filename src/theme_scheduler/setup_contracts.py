"""Strict, side-effect-free contracts for the Stage 8.6 Setup program."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import AppConfig
from .errors import ContractError
from .lifecycle import (
    InstallationRecord,
    InstallationRecordStore,
    InstallContractError,
    InstallLayout,
    PayloadManifest,
)

SETUP_OPTIONS_SCHEMA_VERSION = 2
SETUP_PLAN_SCHEMA_VERSION = 2
SETUP_OPTIONS_KIND = "themescheduler.setup-options"
SETUP_PLAN_KIND = "themescheduler.setup-plan"
_VERSION_PATTERN = re.compile(
    r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?"
)


class SetupContractError(ContractError):
    """Raised before Setup is allowed to mutate files or Windows."""


def _exact(
    payload: Mapping[str, Any],
    expected: set[str],
    label: str,
) -> None:
    if set(payload) != expected:
        raise SetupContractError(f"{label} fields do not match schema.")


def _version_key(value: str) -> tuple[Any, ...]:
    if not isinstance(value, str) or not _VERSION_PATTERN.fullmatch(value):
        raise SetupContractError("Version must use semantic X.Y.Z syntax.")
    core, separator, prerelease = value.partition("-")
    core_key = tuple(int(part) for part in core.split("."))
    if not separator:
        return (*core_key, 1, ())
    identifiers: list[tuple[int, int | str]] = []
    for identifier in prerelease.split("."):
        if identifier.isdigit():
            identifiers.append((0, int(identifier)))
        else:
            identifiers.append((1, identifier.casefold()))
    return (*core_key, 0, tuple(identifiers))


@dataclass(frozen=True)
class SetupOptions:
    """Installation-only choices accepted by the GUI and nothing else."""

    desktop_shortcut: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.desktop_shortcut, bool):
            raise SetupContractError("desktopShortcut must be boolean.")

    @classmethod
    def defaults(cls) -> SetupOptions:
        return cls(False)

    @property
    def config(self) -> AppConfig:
        """Safe bootstrap defaults; the first GUI save replaces them."""

        return AppConfig.defaults()

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": SETUP_OPTIONS_KIND,
            "schemaVersion": SETUP_OPTIONS_SCHEMA_VERSION,
            "desktopShortcut": self.desktop_shortcut,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SetupOptions:
        _exact(
            payload,
            {
                "kind",
                "schemaVersion",
                "desktopShortcut",
            },
            "Setup options",
        )
        if payload.get("kind") != SETUP_OPTIONS_KIND:
            raise SetupContractError("JSON is not ThemeScheduler Setup options.")
        if payload.get("schemaVersion") != SETUP_OPTIONS_SCHEMA_VERSION:
            raise SetupContractError("Unsupported Setup options schemaVersion.")
        return cls(
            payload.get("desktopShortcut"),  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class SetupPlan:
    operation: str
    target_version: str
    installed_version: str | None
    install_root: str
    data_root: str
    retained_data: bool

    def __post_init__(self) -> None:
        if self.operation not in {"install", "upgrade", "reinstall"}:
            raise SetupContractError("Setup operation is invalid.")
        _version_key(self.target_version)
        if self.installed_version is not None:
            _version_key(self.installed_version)
        layout = InstallLayout(Path(self.install_root), Path(self.data_root))
        if str(layout.program_root) != self.install_root:
            raise SetupContractError("installRoot is not canonical.")
        if str(layout.data_root) != self.data_root:
            raise SetupContractError("dataRoot is not canonical.")
        if not isinstance(self.retained_data, bool):
            raise SetupContractError("retainedData must be boolean.")
        if self.operation == "install" and self.installed_version is not None:
            raise SetupContractError("Fresh install cannot have installedVersion.")
        if self.operation != "install" and self.installed_version is None:
            raise SetupContractError("Upgrade and reinstall require installedVersion.")

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": SETUP_PLAN_KIND,
            "schemaVersion": SETUP_PLAN_SCHEMA_VERSION,
            "operation": self.operation,
            "targetVersion": self.target_version,
            "installedVersion": self.installed_version,
            "installRoot": self.install_root,
            "dataRoot": self.data_root,
            "retainedData": self.retained_data,
        }


def create_setup_plan(
    layout: InstallLayout,
    manifest: PayloadManifest,
) -> SetupPlan:
    """Classify install/reinstall/upgrade without changing any state."""

    program_entries = (
        tuple(layout.program_root.iterdir()) if layout.program_root.is_dir() else ()
    )
    record: InstallationRecord | None = None
    if program_entries:
        try:
            record = InstallationRecordStore(layout.installation_record).load()
        except (InstallContractError, OSError, ValueError) as exc:
            raise SetupContractError(
                "Existing program files have no trusted installation record; "
                "automatic overlay or repair is refused."
            ) from exc
        if Path(record.install_root) != layout.program_root:
            raise SetupContractError(
                "Existing installation record belongs to another program root."
            )

    if record is None:
        operation = "install"
        installed_version = None
    else:
        installed_version = record.version
        target_key = _version_key(manifest.version)
        installed_key = _version_key(installed_version)
        if target_key < installed_key:
            raise SetupContractError(
                "Downgrade is not supported; uninstall the newer version first."
            )
        operation = "reinstall" if target_key == installed_key else "upgrade"

    retained_data = any(
        path.exists()
        for path in (
            layout.data_root / "config.json",
            layout.data_root / "state.json",
            layout.data_root / "profiles" / "day.json",
            layout.data_root / "profiles" / "night.json",
            layout.data_root / "backup" / "install.json",
            layout.data_root / "backup" / "install.theme",
        )
    )
    return SetupPlan(
        operation=operation,
        target_version=manifest.version,
        installed_version=installed_version,
        install_root=str(layout.program_root),
        data_root=str(layout.data_root),
        retained_data=retained_data,
    )
