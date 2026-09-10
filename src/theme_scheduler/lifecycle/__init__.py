"""Stable public lifecycle contracts grouped by responsibility."""

from ._validation import (
    INSTALL_CONTRACT_SCHEMA_VERSION,
    INSTALL_OPERATIONS,
    INSTALLATION_RECORD_KIND,
    LIFECYCLE_STATUSES,
    LIFECYCLE_TRANSACTION_KIND,
    PAYLOAD_MANIFEST_KIND,
    PRODUCT_ID,
    TERMINAL_LIFECYCLE_STATUSES,
    UNINSTALL_REGISTRY_KEY,
    InstallContractError,
    file_sha256,
    json_document_sha256,
)
from .installation import (
    InstallationRecord,
    InstallationRecordStore,
    InstalledAppRegistration,
)
from .layout import InstallLayout
from .payload import PayloadFile, PayloadManifest
from .transaction import LifecycleTransaction, LifecycleTransactionStore

__all__ = [
    "INSTALLATION_RECORD_KIND",
    "INSTALL_CONTRACT_SCHEMA_VERSION",
    "INSTALL_OPERATIONS",
    "LIFECYCLE_STATUSES",
    "LIFECYCLE_TRANSACTION_KIND",
    "PAYLOAD_MANIFEST_KIND",
    "PRODUCT_ID",
    "TERMINAL_LIFECYCLE_STATUSES",
    "UNINSTALL_REGISTRY_KEY",
    "InstallContractError",
    "InstallLayout",
    "InstallationRecord",
    "InstallationRecordStore",
    "InstalledAppRegistration",
    "LifecycleTransaction",
    "LifecycleTransactionStore",
    "PayloadFile",
    "PayloadManifest",
    "file_sha256",
    "json_document_sha256",
]
