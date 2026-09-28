"""Strict, side-effect-free protocol contract for notification actions."""

from __future__ import annotations

import re
import secrets
from enum import Enum
from urllib.parse import parse_qsl, urlsplit

from .errors import ContractError
from .notification_contracts import NOTIFICATION_PROTOCOL_SCHEME

NOTIFICATION_ACTION_HOST = "switch"
NOTIFICATION_HEALTH_HOST = "health"
NOTIFICATION_HEALTH_URI = (
    f"{NOTIFICATION_PROTOCOL_SCHEME}://{NOTIFICATION_HEALTH_HOST}/"
)
_TOKEN_PATTERN = re.compile(r"[0-9a-f]{32}")


class NotificationProtocolError(ContractError):
    """Raised when a notification action URI is not canonical and trusted."""


class NotificationAction(str, Enum):  # noqa: UP042 - Preserve str(Enum) output pending a dedicated migration.
    SKIP = "skip"
    DELAY = "delay"
    CONFIRM = "confirm"


def new_action_token() -> str:
    """Create a 128-bit one-time token suitable for a notification URI."""

    return secrets.token_hex(16)


def is_health_activation_uri(uri: object) -> bool:
    """Accept only the parameter-free health activation endpoint."""

    return uri == NOTIFICATION_HEALTH_URI


def _validate_token(token: object) -> str:
    if not isinstance(token, str) or not _TOKEN_PATTERN.fullmatch(token):
        raise NotificationProtocolError(
            "Notification action token must be 32 lowercase hexadecimal characters."
        )
    return token


def build_action_uri(action: NotificationAction, token: str) -> str:
    """Build the sole accepted canonical protocol URI form."""

    if not isinstance(action, NotificationAction):
        raise NotificationProtocolError(
            "Notification action must be a supported action."
        )
    validated_token = _validate_token(token)
    return (
        f"{NOTIFICATION_PROTOCOL_SCHEME}://{NOTIFICATION_ACTION_HOST}/"
        f"?action={action.value}&token={validated_token}"
    )


def parse_action_uri(uri: object) -> tuple[NotificationAction, str]:
    """Parse an action URI without accepting aliases or surplus data."""

    if not isinstance(uri, str) or not uri or len(uri) > 256:
        raise NotificationProtocolError(
            "Notification action URI must contain 1-256 characters."
        )
    if any(ord(character) < 33 or ord(character) > 126 for character in uri):
        raise NotificationProtocolError(
            "Notification action URI must contain printable ASCII without spaces."
        )
    try:
        parts = urlsplit(uri)
        port = parts.port
    except ValueError as exc:
        raise NotificationProtocolError(
            "Notification action URI endpoint is invalid."
        ) from exc
    if (
        parts.scheme != NOTIFICATION_PROTOCOL_SCHEME
        or parts.netloc != NOTIFICATION_ACTION_HOST
        or parts.path != "/"
        or parts.fragment
        or parts.username is not None
        or parts.password is not None
        or port is not None
    ):
        raise NotificationProtocolError(
            "Notification action URI endpoint is not supported."
        )
    try:
        pairs = parse_qsl(
            parts.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=2,
        )
    except ValueError as exc:
        raise NotificationProtocolError(
            "Notification action query is invalid."
        ) from exc
    if len(pairs) != 2 or {name for name, _value in pairs} != {
        "action",
        "token",
    }:
        raise NotificationProtocolError(
            "Notification action query fields do not match the contract."
        )
    values = dict(pairs)
    try:
        action = NotificationAction(values["action"])
    except ValueError as exc:
        raise NotificationProtocolError("Notification action is unsupported.") from exc
    token = _validate_token(values["token"])
    if uri != build_action_uri(action, token):
        raise NotificationProtocolError("Notification action URI is not canonical.")
    return action, token
