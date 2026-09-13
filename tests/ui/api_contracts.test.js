"use strict";

const fs = require("node:fs");
const path = require("node:path");

global.window = global;
require("../../ui/js/api_contracts.js");

const contracts = global.ThemeSchedulerApiContracts;

function validOverview() {
  return {
    action: "overview",
    result: "success",
    message: "Status is current.",
    dataChanged: false,
    windowsChanged: false,
    taskSchedulerChanged: false,
    targetProfile: "day",
    initialSetupPending: false,
    liveWritesEnabled: true,
    currentAppearanceReadEnabled: true,
    config: {
      dayStart: "06:15",
      nightStart: "23:45",
      dayAppsTheme: "light",
      nightAppsTheme: "dark",
      daySystemTheme: "light",
      nightSystemTheme: "dark",
      dayStartTaskbarAccent: false,
      nightStartTaskbarAccent: true,
      dayTitleBordersAccent: false,
      nightTitleBordersAccent: true,
      notifyErrors: true,
      notifyStatusChanges: true,
    },
    state: {},
    profiles: { day: {}, night: {} },
    task: { available: true, valid: true },
  };
}

const accepted = contracts.validate("overview", validOverview());
if (accepted.targetProfile !== "day") {
  throw new Error("Valid overview was not returned.");
}

const broken = validOverview();
delete broken.config.dayStart;
let rejected = false;
try {
  contracts.validate("overview", broken);
} catch (error) {
  rejected = error.name === "ApiContractError"
    && error.message.includes("overview.config.dayStart");
}
if (!rejected) {
  throw new Error("Missing nested overview field was not rejected.");
}

const backendError = contracts.validate("overview", {
  action: "overview",
  result: "failed",
  message: "Configuration could not be read.",
  dataChanged: false,
  windowsChanged: false,
  taskSchedulerChanged: false,
});
if (backendError.result !== "failed") {
  throw new Error("Valid backend error envelope was not preserved.");
}

function expectContractError(label, callback, messagePart) {
  try {
    callback();
  } catch (error) {
    if (error.name === "ApiContractError" && error.message.includes(messagePart)) {
      return;
    }
    throw error;
  }
  throw new Error(`${label} was not rejected.`);
}

expectContractError(
  "Mismatched backend action",
  () => contracts.validate("validateWorkspace", {
    action: "check-health",
    result: "success",
    message: "Wrong response.",
    dataChanged: false,
    windowsChanged: false,
    taskSchedulerChanged: false,
    valid: true,
  }),
  "validateWorkspace.action 不匹配允许的后端动作",
);

expectContractError(
  "Non-boolean mutation flag",
  () => contracts.validate("overview", {
    ...validOverview(),
    dataChanged: "false",
  }),
  "overview.dataChanged 应为 boolean",
);

expectContractError(
  "Missing workspace validity",
  () => contracts.validate("validateWorkspace", {
    action: "validate-workspace",
    result: "success",
    message: "Missing validity.",
    dataChanged: false,
    windowsChanged: false,
    taskSchedulerChanged: false,
  }),
  "validateWorkspace.valid 缺失",
);

expectContractError(
  "Unknown frontend action",
  () => contracts.validate("arbitraryAction", validOverview()),
  "arbitraryAction 不是允许的工作台 API 动作",
);

for (const action of [
  "overview",
  "validateWorkspace",
  "saveWorkspace",
  "setPaused",
  "checkTask",
  "checkHealth",
  "readCurrentWindowsAppearance",
  "repairNotificationIdentity",
  "repairTask",
  "resetPreferences",
  "restoreInstallAppearance",
  "launchUninstaller",
  "openTarget",
]) {
  if (!contracts.allowedActions.includes(action)) {
    throw new Error(`Allowed action is missing: ${action}`);
  }
}

const workbenchSource = fs.readFileSync(
  path.join(__dirname, "../../ui/js/workbench.js"),
  "utf8",
);
const calledMethods = Array.from(
  new Set(
    Array.from(
      workbenchSource.matchAll(/window\.pywebview\.api\.([a-z_]+)/g),
      (match) => match[1],
    ),
  ),
).sort();
const allowedMethods = [...contracts.allowedMethods].sort();
if (JSON.stringify(calledMethods) !== JSON.stringify(allowedMethods)) {
  throw new Error(
    `Workbench API allowlist drifted: called=${calledMethods}; allowed=${allowedMethods}`,
  );
}

console.log("GUI API contracts: ok");
