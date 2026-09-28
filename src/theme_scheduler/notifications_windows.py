"""Inbox WinRT notification adapter isolated behind PowerShell."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .errors import ThemeSchedulerRuntimeError
from .notification_contracts import (
    APP_USER_MODEL_ID,
    PREPARE_TOAST_GROUP,
    PREPARE_TOAST_TAG,
    NotificationCategory,
    NotificationDelivery,
    NotificationDeliveryResult,
    NotificationRequest,
)
from .notification_protocol import NOTIFICATION_HEALTH_URI
from .resources import resource_path
from .scheduled_notifications import PrepareSwitchNotification
from .windows_subprocess import no_window_options


class WindowsNotificationError(ThemeSchedulerRuntimeError):
    """Raised when the notification bridge is missing or malformed."""


def _single_line(value: object, *, maximum: int = 500) -> str:
    normalized = " ".join(str(value).split())
    return normalized[:maximum]


class WindowsNotificationBackend:
    TIMEOUT_SECONDS = 15

    def __init__(self, bridge_path: Path | None = None) -> None:
        self._bridge_path = Path(bridge_path) if bridge_path else None

    def _resolve_bridge(self) -> Path:
        path = (
            self._bridge_path.resolve()
            if self._bridge_path is not None
            else resource_path("entrypoints", "notification_bridge.ps1")
        )
        if not path.is_file():
            raise WindowsNotificationError(f"Notification bridge is missing: {path}")
        return path

    @staticmethod
    def _resolve_powershell() -> Path:
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        path = (
            Path(system_root)
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        if not path.is_file():
            raise WindowsNotificationError(f"Windows PowerShell is missing: {path}")
        return path

    def _run(
        self, action: str, request: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        if os.name != "nt":
            raise OSError("Windows notifications require Windows.")
        command = [
            str(self._resolve_powershell()),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self._resolve_bridge()),
            "-Action",
            action,
        ]
        try:
            with tempfile.TemporaryDirectory(
                prefix="themescheduler-notification-"
            ) as raw:
                if request is not None:
                    request_path = Path(raw) / "request.json"
                    request_path.write_text(
                        json.dumps(
                            request,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        encoding="utf-8-sig",
                        newline="\n",
                    )
                    command.extend(("-RequestPath", str(request_path)))
                completed = subprocess.run(
                    command,
                    input=None,
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8-sig",
                    errors="replace",
                    timeout=self.TIMEOUT_SECONDS,
                    **no_window_options(),
                )
        except subprocess.TimeoutExpired as exc:
            raise WindowsNotificationError(
                f"Notification bridge timed out after {self.TIMEOUT_SECONDS} seconds."
            ) from exc
        if completed.returncode != 0:
            detail = _single_line(
                completed.stderr or completed.stdout or "no output",
                maximum=320,
            )
            raise WindowsNotificationError(
                f"Notification bridge exited with {completed.returncode}: {detail}"
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise WindowsNotificationError(
                "Notification bridge returned invalid JSON."
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("ok") is not True
            or payload.get("action") != action
        ):
            raise WindowsNotificationError(
                f"Notification bridge returned an unexpected result: {payload!r}"
            )
        return payload

    def probe(self) -> None:
        payload = self._run("Probe")
        if (
            payload.get("available") is not True
            or payload.get("notificationSent") is not False
        ):
            raise WindowsNotificationError(
                f"Notification probe returned invalid fields: {payload!r}"
            )

    @staticmethod
    def _bridge_request(
        *,
        title: str,
        body: str,
        buttons: list[dict[str, str]],
        launch: str | None = None,
        tag: str | None = None,
        group: str | None = None,
    ) -> dict[str, Any]:
        request = {
            "appUserModelId": APP_USER_MODEL_ID,
            "title": title,
            "body": body,
            "buttons": [
                {
                    "content": button["content"],
                    "activationType": button["activationType"],
                    "arguments": button["arguments"],
                }
                for button in buttons
            ],
        }
        if launch is not None:
            request["launch"] = launch
        if tag is not None:
            request["tag"] = tag
        if group is not None:
            request["group"] = group
        return request

    def _deliver(self, request: Mapping[str, Any]) -> NotificationDelivery:
        try:
            payload = self._run("Send", request)
            if (
                payload.get("sent") is not True
                or payload.get("notificationSent") is not True
            ):
                raise WindowsNotificationError(
                    f"Notification send returned invalid fields: {payload!r}"
                )
            return NotificationDelivery(
                NotificationDeliveryResult.SENT,
                False,
                "Notification was sent through inbox WinRT.",
            )
        except Exception as exc:
            detail = _single_line(
                f"Notification delivery failed: {type(exc).__name__}: {exc}"
            )
            return NotificationDelivery(
                NotificationDeliveryResult.FAILED,
                False,
                detail,
            )

    def send_prepare(self, request: PrepareSwitchNotification) -> NotificationDelivery:
        payload = request.as_dict()
        return self._deliver(
            self._bridge_request(
                title=str(payload["title"]),
                body=str(payload["body"]),
                buttons=payload["buttons"],
                tag=PREPARE_TOAST_TAG,
                group=PREPARE_TOAST_GROUP,
            )
        )

    def clear_prepare(self) -> NotificationDelivery:
        try:
            payload = self._run("RemovePrepare")
            if (
                payload.get("removed") is not True
                or payload.get("notificationSent") is not False
            ):
                raise WindowsNotificationError(
                    f"Notification removal returned invalid fields: {payload!r}"
                )
            return NotificationDelivery(
                NotificationDeliveryResult.SENT,
                False,
                "The switch reminder was removed from notification history.",
            )
        except Exception as exc:
            detail = _single_line(
                f"Notification removal failed: {type(exc).__name__}: {exc}"
            )
            return NotificationDelivery(
                NotificationDeliveryResult.FAILED,
                False,
                detail,
            )

    def send_status(self, request: NotificationRequest) -> NotificationDelivery:
        payload = request.as_dict()
        return self._deliver(
            self._bridge_request(
                title=str(payload["title"]),
                body=str(payload["body"]),
                buttons=[],
                launch=(
                    NOTIFICATION_HEALTH_URI
                    if request.category is NotificationCategory.ERROR
                    else None
                ),
            )
        )
