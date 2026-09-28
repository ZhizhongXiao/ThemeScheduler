"""Notification payloads and backend boundary for scheduled switches."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .notification_contracts import (
    APP_USER_MODEL_ID,
    NOTIFICATION_TITLE,
    NotificationCategory,
    NotificationDelivery,
    NotificationRequest,
)
from .notification_protocol import (
    NotificationAction,
    build_action_uri,
)
from .switch_override import PendingSwitch


@dataclass(frozen=True)
class NotificationButton:
    content: str
    action: NotificationAction
    uri: str

    def as_dict(self) -> dict[str, str]:
        return {
            "content": self.content,
            "action": self.action.value,
            "activationType": "protocol",
            "arguments": self.uri,
        }


@dataclass(frozen=True)
class PrepareSwitchNotification:
    target_profile: str
    scheduled_at: str
    buttons: tuple[NotificationButton, ...]

    @classmethod
    def from_pending(cls, pending: PendingSwitch) -> PrepareSwitchNotification:
        if pending.action_token is None:
            raise ValueError("A prepared switch notification requires an action token.")
        labels = {
            NotificationAction.SKIP: "跳过本次",
            NotificationAction.DELAY: "延后 30 分钟",
            NotificationAction.CONFIRM: "确认",
        }
        actions = [
            NotificationAction.SKIP,
            NotificationAction.CONFIRM,
        ]
        if pending.can_delay:
            actions.insert(1, NotificationAction.DELAY)
        return cls(
            pending.target_profile,
            pending.scheduled_at.isoformat(timespec="seconds"),
            tuple(
                NotificationButton(
                    labels[action],
                    action,
                    build_action_uri(action, pending.action_token),
                )
                for action in actions
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        profile_name = "昼间" if self.target_profile == "day" else "夜间"
        local_time = self.scheduled_at[11:16]
        return {
            "appUserModelId": APP_USER_MODEL_ID,
            "title": NOTIFICATION_TITLE,
            "category": NotificationCategory.STATUS.value,
            "event": "schedule.prepare",
            "body": (f"将在 {local_time} 切换到{profile_name}配置。"),
            "scheduledAt": self.scheduled_at,
            "targetProfile": self.target_profile,
            "buttons": [button.as_dict() for button in self.buttons],
        }


def success_notification(profile: str) -> NotificationRequest:
    if profile not in {"day", "night"}:
        raise ValueError("Success notification profile must be day or night.")
    profile_name = "昼间" if profile == "day" else "夜间"
    return NotificationRequest(
        NotificationCategory.STATUS,
        "schedule.applied",
        f"已成功切换到{profile_name}配置。",
    )


def error_notification(result: str) -> NotificationRequest:
    if result not in {
        "data-untrusted",
        "apply-failed-rolled-back",
        "partial-failure",
        "fatal-failure",
    }:
        raise ValueError("Unsupported automatic error result.")
    return NotificationRequest(
        NotificationCategory.ERROR,
        "auto.failed",
        "Automatic theme switching needs attention. Open ThemeScheduler health checks for details.",
        error_code=result,
    )


class ScheduledNotificationBackend(Protocol):
    def send_prepare(
        self, request: PrepareSwitchNotification
    ) -> NotificationDelivery: ...

    def send_status(self, request: NotificationRequest) -> NotificationDelivery: ...

    def clear_prepare(self) -> NotificationDelivery: ...
