"""Validated workbench configuration mutations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..configuration_service import ConfigurationService
from ..control_service import ControlService
from ..initial_setup import (
    clear_initial_setup_marker,
    initial_setup_pending,
)
from .contracts import WorkbenchBindings, WorkspaceValidationResult


class GuiConfigurationMixin(WorkbenchBindings):
    def validate_config(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._api_lock:
            try:
                config = self._parse_config(payload)
                return {
                    "action": "validate-config",
                    "result": "success",
                    "valid": True,
                    "config": self._gui_config(config),
                    "dataChanged": False,
                    "windowsChanged": False,
                    "taskSchedulerChanged": False,
                    "message": "Configuration is valid.",
                }
            except Exception as exc:
                response = self._error("validate-config", exc)
                response["valid"] = False
                return response

    def validate_workspace(
        self,
        payload: Mapping[str, Any],
    ) -> WorkspaceValidationResult:
        with self._api_lock:
            try:
                config, colors = self._parse_workspace(payload)
                return WorkspaceValidationResult(
                    action="validate-workspace",
                    result="success",
                    valid=True,
                    config=self._gui_config(config),
                    colors={name: color.as_dict() for name, color in colors.items()},
                    dataChanged=False,
                    windowsChanged=False,
                    taskSchedulerChanged=False,
                    message="Configuration and colors are valid.",
                )
            except Exception as exc:
                response = self._error("validate-workspace", exc)
                response["valid"] = False
                return WorkspaceValidationResult(**response)

    def save_config(
        self,
        payload: Mapping[str, Any],
        confirmed: bool = False,
    ) -> dict[str, Any]:
        with self._api_lock:
            if not self._allow_live_writes:
                return {
                    "action": "save-config",
                    "result": "blocked",
                    "message": (
                        "Live task changes are disabled for this development launch."
                    ),
                    "dataChanged": False,
                    "windowsChanged": False,
                    "taskSchedulerChanged": False,
                }
            if confirmed is not True:
                return {
                    "action": "save-config",
                    "result": "blocked",
                    "message": "Configuration save requires explicit confirmation.",
                    "dataChanged": False,
                    "windowsChanged": False,
                    "taskSchedulerChanged": False,
                }
            try:
                target = self._parse_config(payload)
                user_id = self._scheduler.current_user_id()
                service = ConfigurationService(
                    self._layout,
                    self._lock_factory(),
                    self._scheduler,
                    executable=str(self._executable),
                    user_id=user_id,
                )
                return service.update(target).as_dict()
            except Exception as exc:
                return self._error("save-config", exc)

    def save_workspace(
        self,
        payload: Mapping[str, Any],
        confirmed: bool = False,
    ) -> dict[str, Any]:
        with self._api_lock:
            if not self._allow_live_writes:
                return {
                    "action": "save-workspace",
                    "result": "blocked",
                    "message": (
                        "Live profile, configuration, and task changes are "
                        "disabled for this development launch."
                    ),
                    "dataChanged": False,
                    "windowsChanged": False,
                    "taskSchedulerChanged": False,
                }
            if confirmed is not True:
                return {
                    "action": "save-workspace",
                    "result": "blocked",
                    "message": (
                        "Profile configuration save requires explicit confirmation."
                    ),
                    "dataChanged": False,
                    "windowsChanged": False,
                    "taskSchedulerChanged": False,
                }
            try:
                target, colors = self._parse_workspace(payload)
                setup_pending = initial_setup_pending(self._layout.initial_setup_marker)
                service = ConfigurationService(
                    self._layout,
                    self._lock_factory(),
                    self._scheduler,
                    executable=str(self._executable),
                    user_id=self._scheduler.current_user_id(),
                    audit_event="appearance.configuration.updated",
                )
                response = service.update_bundle(
                    target,
                    colors,
                ).as_dict()
                response["action"] = "save-workspace"
                response["initialSetupActivated"] = False
                if setup_pending and response.get("result") in {
                    "changed",
                    "no-change",
                }:
                    activation = (
                        ControlService(
                            self._layout,
                            self._lock_factory(),
                        )
                        .set_paused(False)
                        .as_dict()
                    )
                    response["initialSetupActivation"] = activation
                    if activation.get("result") in {
                        "changed",
                        "no-change",
                    }:
                        response["initialSetupActivated"] = True
                        try:
                            clear_initial_setup_marker(
                                self._layout.initial_setup_marker
                            )
                            response["result"] = "changed"
                            response["dataChanged"] = True
                            response["message"] = (
                                "Configuration, profiles, and Task Scheduler "
                                "are consistent. Initial setup is complete and "
                                "automatic switching is enabled; Windows was "
                                "not synchronized."
                            )
                        except Exception as exc:
                            response["initialSetupActivated"] = False
                            response["result"] = "partial-failure"
                            response["message"] = (
                                "Configuration was saved and pause state was "
                                "released, but the initial-setup marker remains; "
                                "automatic runs stay blocked until a later save "
                                f"clears it. {type(exc).__name__}: {exc}"
                            )
                    else:
                        response["result"] = "partial-failure"
                        response["message"] = (
                            "Configuration and profiles were saved, but "
                            "automatic switching remains paused because "
                            "initial setup activation did not complete. "
                            f"{activation.get('message', '')}"
                        ).strip()
                return response
            except Exception as exc:
                return self._error("save-workspace", exc)

    def set_paused(
        self,
        paused: bool,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        with self._api_lock:
            if not isinstance(paused, bool):
                return self._error(
                    "set-paused",
                    ValueError("Paused target must be boolean."),
                )
            if confirmed is not True:
                return {
                    "action": "pause" if paused else "resume",
                    "result": "blocked",
                    "message": "Pause-state change requires explicit confirmation.",
                    "dataChanged": False,
                    "windowsChanged": False,
                    "taskSchedulerChanged": False,
                }
            try:
                if paused is False and initial_setup_pending(
                    self._layout.initial_setup_marker
                ):
                    return {
                        "action": "resume",
                        "result": "blocked",
                        "message": (
                            "Complete and save the first-run configuration "
                            "before enabling automatic switching."
                        ),
                        "dataChanged": False,
                        "windowsChanged": False,
                        "taskSchedulerChanged": False,
                    }
                service = ControlService(self._layout, self._lock_factory())
                return service.set_paused(paused).as_dict()
            except Exception as exc:
                return self._error("pause" if paused else "resume", exc)
