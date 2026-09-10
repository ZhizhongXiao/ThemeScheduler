"""Strict, Windows-backend-neutral Stage 9 notification contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

APP_USER_MODEL_ID = "ThemeScheduler.ThemeScheduler"
TOAST_ACTIVATOR_CLSID = "403DE4CE-F3F9-4335-B847-8B2297DC9B6F"
NOTIFICATION_PROTOCOL_SCHEME = "themescheduler-action"
NOTIFICATION_TITLE = "ThemeScheduler"
NOTIFICATION_KIND = "themescheduler.notification"
NOTIFICATION_SCHEMA_VERSION = 1

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")


class NotificationCategory(str, Enum):
    ERROR = "error"
    STATUS = "status"


class NotificationDeliveryResult(str, Enum):
    SENT = "sent"
    SUPPRESSED = "suppressed"
    FAILED = "failed"


def _bounded_text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{field} must contain 1-{maximum} characters.")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{field} cannot contain control characters.")
    return value


@dataclass(frozen=True)
class NotificationRequest:
    """A privacy-bounded local notification request."""

    category: NotificationCategory
    event: str
    body: str
    error_code: str | None = None
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.category, NotificationCategory):
            raise ValueError("category must be a NotificationCategory.")
        if (
            not isinstance(self.event, str)
            or len(self.event) > 64
            or not _TOKEN_PATTERN.fullmatch(self.event)
        ):
            raise ValueError("event must use bounded lowercase dotted tokens.")
        _bounded_text(self.body, "body", 240)
        for value, field, maximum in (
            (self.error_code, "errorCode", 64),
            (self.correlation_id, "correlationId", 96),
        ):
            if value is not None:
                _bounded_text(value, field, maximum)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": NOTIFICATION_KIND,
            "schemaVersion": NOTIFICATION_SCHEMA_VERSION,
            "appUserModelId": APP_USER_MODEL_ID,
            "title": NOTIFICATION_TITLE,
            "category": self.category.value,
            "event": self.event,
            "body": self.body,
            "errorCode": self.error_code,
            "correlationId": self.correlation_id,
        }


@dataclass(frozen=True)
class NotificationDelivery:
    """Record delivery without allowing it to redefine the core result."""

    result: NotificationDeliveryResult
    fallback_logged: bool
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.result, NotificationDeliveryResult):
            raise ValueError("result must be a NotificationDeliveryResult.")
        if not isinstance(self.fallback_logged, bool):
            raise ValueError("fallback_logged must be boolean.")
        _bounded_text(self.message, "message", 500)
        if (
            self.result is not NotificationDeliveryResult.FAILED
            and self.fallback_logged
        ):
            raise ValueError("Only failed delivery can require a log fallback.")

    def as_dict(self) -> dict[str, Any]:
        return {
            "result": self.result.value,
            "fallbackLogged": self.fallback_logged,
            "message": self.message,
        }
