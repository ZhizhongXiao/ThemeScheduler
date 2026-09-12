"use strict";

(function exposeApiContracts(global) {
  class ApiContractError extends TypeError {
    constructor(message) {
      super(`GUI API 响应不符合契约：${message}`);
      this.name = "ApiContractError";
    }
  }

  function record(value, path) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new ApiContractError(`${path} 应为对象`);
    }
    return value;
  }

  function field(value, name, type, path) {
    if (!(name in value)) {
      throw new ApiContractError(`${path}.${name} 缺失`);
    }
    if (type && typeof value[name] !== type) {
      throw new ApiContractError(`${path}.${name} 应为 ${type}`);
    }
  }

  function fields(value, names, path) {
    names.forEach(([name, type]) => field(value, name, type, path));
  }

  function validateOverview(data) {
    if (!["success", "partial"].includes(data.result)) return;
    fields(data, [
      ["targetProfile", "string"],
      ["initialSetupPending", "boolean"],
      ["liveWritesEnabled", "boolean"],
      ["currentAppearanceReadEnabled", "boolean"],
    ], "overview");

    const config = record(data.config, "overview.config");
    fields(config, [
      ["dayStart", "string"],
      ["nightStart", "string"],
      ["dayAppsTheme", "string"],
      ["nightAppsTheme", "string"],
      ["notifyErrors", "boolean"],
      ["notifyStatusChanges", "boolean"],
    ], "overview.config");

    record(data.state, "overview.state");
    const profiles = record(data.profiles, "overview.profiles");
    record(profiles.day, "overview.profiles.day");
    record(profiles.night, "overview.profiles.night");

    const task = record(data.task, "overview.task");
    fields(task, [
      ["available", "boolean"],
      ["valid", "boolean"],
    ], "overview.task");
  }

  function validateWorkspace(data) {
    field(data, "valid", "boolean", "validateWorkspace");
  }

  function validateHealth(data) {
    if (data.result === "error") return;
    field(data, "status", "string", "checkHealth");
    if (!Array.isArray(data.checks)) {
      throw new ApiContractError("checkHealth.checks 应为数组");
    }
  }

  function validateAppearance(data) {
    if (data.result !== "success") return;
    field(data, "appMode", "string", "readCurrentWindowsAppearance");
    record(data.color, "readCurrentWindowsAppearance.color");
  }

  const operation = (method, responseActions, validate = null) => Object.freeze({
    method,
    responseActions: Object.freeze(
      Array.isArray(responseActions) ? responseActions : [responseActions],
    ),
    validate,
  });

  const contracts = Object.freeze({
    overview: operation("get_overview", "overview", validateOverview),
    validateWorkspace: operation(
      "validate_workspace",
      "validate-workspace",
      validateWorkspace,
    ),
    saveWorkspace: operation("save_workspace", "save-workspace"),
    saveWorkspaceAndApply: operation(
      "save_workspace_and_apply",
      ["save-workspace-and-apply", "save-workspace"],
    ),
    setPaused: operation("set_paused", ["pause", "resume", "set-paused"]),
    checkTask: operation("check_task", "check-task"),
    checkHealth: operation("check_health", "check-health", validateHealth),
    readCurrentWindowsAppearance: operation(
      "read_current_windows_appearance",
      "read-current-windows-appearance",
      validateAppearance,
    ),
    repairNotificationIdentity: operation(
      "repair_notification_identity",
      "notification-identity-repair",
    ),
    repairTask: operation("repair_task", "repair-task"),
    resetPreferences: operation("reset_preferences", "reset-preferences"),
    restoreInstallAppearance: operation(
      "restore_install_appearance",
      "restore-install-appearance",
    ),
    launchUninstaller: operation("launch_uninstaller", "launch-uninstaller"),
    openTarget: operation("open_target", "open-target"),
  });

  function validate(action, response) {
    const contract = contracts[action];
    if (!contract) {
      throw new ApiContractError(`${action} 不是允许的工作台 API 动作`);
    }
    const data = record(response, action);
    fields(data, [
      ["action", "string"],
      ["result", "string"],
      ["message", "string"],
      ["dataChanged", "boolean"],
      ["windowsChanged", "boolean"],
      ["taskSchedulerChanged", "boolean"],
    ], action);

    if (!contract.responseActions.includes(data.action)) {
      throw new ApiContractError(
        `${action}.action 不匹配允许的后端动作`,
      );
    }
    contract.validate?.(data);
    return data;
  }

  global.ThemeSchedulerApiContracts = Object.freeze({
    ApiContractError,
    allowedActions: Object.freeze(Object.keys(contracts)),
    allowedMethods: Object.freeze(
      Object.values(contracts).map((contract) => contract.method),
    ),
    validate,
  });
})(window);
