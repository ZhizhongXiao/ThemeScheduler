"""Versioned payload file and manifest contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._validation import (
    INSTALL_CONTRACT_SCHEMA_VERSION,
    PAYLOAD_MANIFEST_KIND,
    PRODUCT_ID,
    InstallContractError,
    JsonObject,
    _exact,
    _is_reparse_point,
    _require_schema_version,
    _sha256,
    _validate_relative_payload_path,
    _version,
    file_sha256,
    json_document_sha256,
)


@dataclass(frozen=True, order=True)
class PayloadFile:
    path: str
    size: int
    sha256: str

    def __post_init__(self) -> None:
        _validate_relative_payload_path(self.path)
        if (
            isinstance(self.size, bool)
            or not isinstance(self.size, int)
            or self.size < 0
        ):
            raise InstallContractError(
                "payload file size must be a non-negative integer."
            )
        _sha256(self.sha256, "payload file sha256")

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "size": self.size, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, payload: JsonObject) -> PayloadFile:
        _exact(payload, {"path", "size", "sha256"}, "payload file")
        return cls(
            path=payload["path"],
            size=payload["size"],
            sha256=payload["sha256"],
        )


@dataclass(frozen=True)
class PayloadManifest:
    version: str
    files: tuple[PayloadFile, ...]

    def __post_init__(self) -> None:
        _version(self.version)
        if not isinstance(self.files, tuple) or not self.files:
            raise InstallContractError("payload manifest files must be non-empty.")
        if any(not isinstance(item, PayloadFile) for item in self.files):
            raise InstallContractError(
                "payload manifest files must contain PayloadFile values."
            )
        if (
            tuple(sorted(self.files, key=lambda item: item.path.casefold()))
            != self.files
        ):
            raise InstallContractError(
                "payload manifest files must use canonical case-insensitive order."
            )
        folded = [item.path.casefold() for item in self.files]
        if len(set(folded)) != len(folded):
            raise InstallContractError(
                "payload manifest contains duplicate Windows paths."
            )
        paths = {item.path for item in self.files}
        for required in (
            "app/ThemeScheduler.exe",
            "maintenance/Uninstall.exe",
        ):
            if required not in paths:
                raise InstallContractError(
                    f"payload manifest is missing required file: {required}"
                )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": PAYLOAD_MANIFEST_KIND,
            "schemaVersion": INSTALL_CONTRACT_SCHEMA_VERSION,
            "productId": PRODUCT_ID,
            "version": self.version,
            "files": [item.as_dict() for item in self.files],
        }

    @property
    def document_sha256(self) -> str:
        return json_document_sha256(self.as_dict())

    @classmethod
    def from_dict(cls, payload: JsonObject) -> PayloadManifest:
        _exact(
            payload,
            {"kind", "schemaVersion", "productId", "version", "files"},
            "payload manifest",
        )
        if payload.get("kind") != PAYLOAD_MANIFEST_KIND:
            raise InstallContractError("JSON is not a ThemeScheduler payload manifest.")
        _require_schema_version(payload, "payload manifest")
        if payload.get("productId") != PRODUCT_ID:
            raise InstallContractError("Payload manifest productId is invalid.")
        files = payload.get("files")
        if not isinstance(files, list):
            raise InstallContractError("payload manifest files must be an array.")
        if any(not isinstance(item, Mapping) for item in files):
            raise InstallContractError("payload manifest file entries must be objects.")
        return cls(
            version=payload["version"],
            files=tuple(PayloadFile.from_dict(item) for item in files),
        )

    @classmethod
    def capture(cls, root: Path, *, version: str) -> PayloadManifest:
        raw_root = Path(root)
        if _is_reparse_point(raw_root):
            raise InstallContractError(
                "Payload root must be a regular non-reparse directory."
            )
        root = raw_root.resolve(strict=True)
        if not root.is_dir() or _is_reparse_point(root):
            raise InstallContractError(
                "Payload root must be a regular non-reparse directory."
            )
        items: list[PayloadFile] = []
        for candidate in root.rglob("*"):
            if _is_reparse_point(candidate):
                raise InstallContractError(
                    f"Payload cannot contain a reparse point: {candidate}"
                )
            if candidate.is_dir():
                continue
            if not candidate.is_file():
                raise InstallContractError(
                    f"Payload contains a non-regular file: {candidate}"
                )
            relative = candidate.relative_to(root).as_posix()
            items.append(
                PayloadFile(
                    path=relative,
                    size=candidate.stat().st_size,
                    sha256=file_sha256(candidate),
                )
            )
        return cls(
            version=version,
            files=tuple(sorted(items, key=lambda item: item.path.casefold())),
        )

    def verify_tree(self, root: Path) -> None:
        captured = PayloadManifest.capture(root, version=self.version)
        if captured != self:
            expected = {item.path: item for item in self.files}
            actual = {item.path: item for item in captured.files}
            if set(expected) != set(actual):
                raise InstallContractError(
                    "Payload tree file set does not match the manifest."
                )
            for path, expected_file in expected.items():
                actual_file = actual[path]
                if actual_file.size != expected_file.size:
                    raise InstallContractError(f"Payload file size mismatch: {path}")
                if actual_file.sha256 != expected_file.sha256:
                    raise InstallContractError(f"Payload file SHA-256 mismatch: {path}")
            raise InstallContractError("Payload tree does not match the manifest.")
