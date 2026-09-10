"""Installation record and installed-app registration contracts."""

from __future__ import annotations

# Installation contracts revalidate objects decoded from JSON, and their
# validators are intentionally package-private rather than public API.
# pyright: strict, reportPrivateUsage=false, reportUnnecessaryIsInstance=false
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..persistence import atomic_write_json, load_json_object
from ._validation import (
    INSTALL_CONTRACT_SCHEMA_VERSION,
    INSTALLATION_RECORD_KIND,
    PRODUCT_ID,
    InstallContractError,
    JsonObject,
    _absolute_path,
    _exact,
    _require_schema_version,
    _same_path,
    _sha256,
    _timestamp,
    _validate_transaction_id,
    _version,
    json_document_sha256,
)
from .layout import InstallLayout
from .payload import PayloadManifest

type RegistryValue = str | int


@dataclass(frozen=True)
class InstallationRecord:
    version: str
    installed_at: str
    install_root: str
    data_root: str
    executable_path: str
    uninstall_path: str
    payload_manifest_sha256: str
    last_operation: str
    last_transaction_id: str

    def __post_init__(self) -> None:
        _version(self.version)
        _timestamp(self.installed_at, "installedAt")
        if self.last_operation not in {"install", "upgrade", "reinstall"}:
            raise InstallContractError("lastOperation is invalid.")
        _validate_transaction_id(self.last_transaction_id)
        _sha256(
            self.payload_manifest_sha256,
            "payloadManifestSha256",
        )
        layout = InstallLayout(
            _absolute_path(self.install_root, "installRoot"),
            _absolute_path(self.data_root, "dataRoot"),
        )
        executable = _absolute_path(self.executable_path, "executablePath")
        uninstaller = _absolute_path(self.uninstall_path, "uninstallPath")
        if not _same_path(executable, layout.executable):
            raise InstallContractError(
                "executablePath does not match the frozen install layout."
            )
        if not _same_path(uninstaller, layout.uninstaller):
            raise InstallContractError(
                "uninstallPath does not match the frozen install layout."
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": INSTALLATION_RECORD_KIND,
            "schemaVersion": INSTALL_CONTRACT_SCHEMA_VERSION,
            "productId": PRODUCT_ID,
            "version": self.version,
            "installedAt": self.installed_at,
            "installRoot": self.install_root,
            "dataRoot": self.data_root,
            "executablePath": self.executable_path,
            "uninstallPath": self.uninstall_path,
            "payloadManifestSha256": self.payload_manifest_sha256,
            "lastOperation": self.last_operation,
            "lastTransactionId": self.last_transaction_id,
        }

    @classmethod
    def create(
        cls,
        layout: InstallLayout,
        manifest: PayloadManifest,
        *,
        installed_at: str,
        operation: str,
        transaction_id: str,
    ) -> InstallationRecord:
        return cls(
            version=manifest.version,
            installed_at=installed_at,
            install_root=str(layout.program_root),
            data_root=str(layout.data_root),
            executable_path=str(layout.executable),
            uninstall_path=str(layout.uninstaller),
            payload_manifest_sha256=manifest.document_sha256,
            last_operation=operation,
            last_transaction_id=transaction_id,
        )

    @classmethod
    def from_dict(cls, payload: JsonObject) -> InstallationRecord:
        _exact(
            payload,
            {
                "kind",
                "schemaVersion",
                "productId",
                "version",
                "installedAt",
                "installRoot",
                "dataRoot",
                "executablePath",
                "uninstallPath",
                "payloadManifestSha256",
                "lastOperation",
                "lastTransactionId",
            },
            "installation record",
        )
        if payload.get("kind") != INSTALLATION_RECORD_KIND:
            raise InstallContractError(
                "JSON is not a ThemeScheduler installation record."
            )
        _require_schema_version(payload, "installation record")
        if payload.get("productId") != PRODUCT_ID:
            raise InstallContractError("Installation record productId is invalid.")
        return cls(
            version=payload["version"],
            installed_at=payload["installedAt"],
            install_root=payload["installRoot"],
            data_root=payload["dataRoot"],
            executable_path=payload["executablePath"],
            uninstall_path=payload["uninstallPath"],
            payload_manifest_sha256=payload["payloadManifestSha256"],
            last_operation=payload["lastOperation"],
            last_transaction_id=payload["lastTransactionId"],
        )


class InstallationRecordStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def create(self, record: InstallationRecord) -> InstallationRecord:
        atomic_write_json(self.path, record.as_dict())
        return self.load()

    def load(self) -> InstallationRecord:
        return InstallationRecord.from_dict(load_json_object(self.path))

    def replace(
        self,
        record: InstallationRecord,
        *,
        expected_current_sha256: str,
    ) -> InstallationRecord:
        _sha256(expected_current_sha256, "expectedCurrentSha256")
        current_payload = load_json_object(self.path)
        if json_document_sha256(current_payload) != expected_current_sha256:
            raise InstallContractError(
                "Installation record changed before replacement."
            )
        atomic_write_json(self.path, record.as_dict(), force=True)
        written = self.load()
        if written != record:
            raise InstallContractError("Installation record readback mismatch.")
        return written


def _quote_windows_argument(value: str) -> str:
    if '"' in value:
        raise InstallContractError("Windows command arguments cannot contain quotes.")
    return f'"{value}"'


@dataclass(frozen=True)
class InstalledAppRegistration:
    display_version: str
    publisher: str
    display_icon: str
    install_location: str
    modify_path: str
    uninstall_string: str
    estimated_size_kib: int

    def __post_init__(self) -> None:
        _version(self.display_version, "displayVersion")
        if (
            not isinstance(self.publisher, str)
            or not self.publisher
            or len(self.publisher) > 200
        ):
            raise InstallContractError("publisher must be non-empty and bounded.")
        if (
            isinstance(self.estimated_size_kib, bool)
            or not isinstance(self.estimated_size_kib, int)
            or self.estimated_size_kib < 1
        ):
            raise InstallContractError("estimatedSizeKiB must be a positive integer.")
        install_root = _absolute_path(
            self.install_location,
            "installLocation",
        )
        if (
            install_root.name != PRODUCT_ID
            or install_root.parent.name.casefold() != "programs"
        ):
            raise InstallContractError(
                "installLocation does not match the frozen program root."
            )
        if not isinstance(self.display_icon, str) or "," not in self.display_icon:
            raise InstallContractError(
                "displayIcon must contain an absolute path and icon index."
            )
        icon_path, icon_index = self.display_icon.rsplit(",", 1)
        expected_executable = install_root / "app" / "ThemeScheduler.exe"
        if not _same_path(
            _absolute_path(icon_path, "displayIcon"),
            expected_executable,
        ):
            raise InstallContractError(
                "displayIcon does not match the installed executable."
            )
        if icon_index != "0":
            raise InstallContractError("displayIcon index must be 0.")
        for field, value in (
            ("modifyPath", self.modify_path),
            ("uninstallString", self.uninstall_string),
        ):
            if not isinstance(value, str) or not value or len(value) > 32767:
                raise InstallContractError(f"{field} must be non-empty and bounded.")
        expected_modify = (
            f"{_quote_windows_argument(str(expected_executable))} maintenance"
        )
        expected_uninstall = _quote_windows_argument(
            str(install_root / "maintenance" / "Uninstall.exe")
        )
        if self.modify_path != expected_modify:
            raise InstallContractError(
                "modifyPath does not match the maintenance entry."
            )
        if self.uninstall_string != expected_uninstall:
            raise InstallContractError(
                "uninstallString does not match the independent uninstaller."
            )

    @classmethod
    def create(
        cls,
        layout: InstallLayout,
        *,
        version: str,
        publisher: str,
        estimated_size_kib: int,
    ) -> InstalledAppRegistration:
        executable = str(layout.executable)
        uninstaller = str(layout.uninstaller)
        return cls(
            display_version=version,
            publisher=publisher,
            display_icon=f"{executable},0",
            install_location=str(layout.program_root),
            modify_path=f"{_quote_windows_argument(executable)} maintenance",
            uninstall_string=_quote_windows_argument(uninstaller),
            estimated_size_kib=estimated_size_kib,
        )

    def as_registry_values(self) -> dict[str, RegistryValue]:
        return {
            "DisplayName": PRODUCT_ID,
            "DisplayVersion": self.display_version,
            "Publisher": self.publisher,
            "DisplayIcon": self.display_icon,
            "InstallLocation": self.install_location,
            "ModifyPath": self.modify_path,
            "UninstallString": self.uninstall_string,
            "EstimatedSize": self.estimated_size_kib,
            "NoModify": 0,
            "NoRepair": 1,
        }

    @classmethod
    def from_registry_values(cls, values: JsonObject) -> InstalledAppRegistration:
        _exact(
            values,
            {
                "DisplayName",
                "DisplayVersion",
                "Publisher",
                "DisplayIcon",
                "InstallLocation",
                "ModifyPath",
                "UninstallString",
                "EstimatedSize",
                "NoModify",
                "NoRepair",
            },
            "installed-app registration",
        )
        if values.get("DisplayName") != PRODUCT_ID:
            raise InstallContractError("DisplayName is invalid.")
        no_modify = values.get("NoModify")
        no_repair = values.get("NoRepair")
        if (
            isinstance(no_modify, bool)
            or isinstance(no_repair, bool)
            or no_modify != 0
            or no_repair != 1
        ):
            raise InstallContractError("Installed-app maintenance flags are invalid.")
        return cls(
            display_version=values["DisplayVersion"],
            publisher=values["Publisher"],
            display_icon=values["DisplayIcon"],
            install_location=values["InstallLocation"],
            modify_path=values["ModifyPath"],
            uninstall_string=values["UninstallString"],
            estimated_size_kib=values["EstimatedSize"],
        )
