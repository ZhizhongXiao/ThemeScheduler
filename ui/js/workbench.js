"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));
const timeMath = window.ThemeSchedulerTimeMath;
const timeDials = window.ThemeSchedulerTimeDials;
const colorModule = window.ThemeSchedulerColor;
const apiContracts = window.ThemeSchedulerApiContracts;

const state = {
  busy: false,
  overview: null,
  loadedConfig: null,
  draft: null,
  loadedColors: null,
  draftColors: null,
  dirty: false,
  activePage: "plan",
  planView: "overview",
  selectedBoundary: "day",
  selectedHand: "minute",
  adjustedHands: new Set(),
  currentAppearance: null,
  toastTimer: null,
  interactiveDial: null,
  summaryDial: null,
  expandedSummaryDial: null,
  colorFields: {},
  uiThemeOverride: null,
  systemThemeQuery: null,
  lightThemeStylesheet: null,
};

const LIGHT_THEME_MEDIA = "(prefers-color-scheme: light)";

function setText(selector, value) {
  const node = $(selector);
  if (node) node.textContent = value;
}

function systemUiTheme() {
  return state.systemThemeQuery?.matches ? "light" : "dark";
}

function renderUiThemeToggle() {
  const button = $("#ui-theme-toggle");
  const icon = button?.querySelector("[data-ui-theme-icon]");
  const label = button?.querySelector("[data-ui-theme-label]");
  const stylesheet = state.lightThemeStylesheet;
  if (!button || !icon || !label || !stylesheet) return;
  const systemTheme = systemUiTheme();
  if (state.uiThemeOverride === "light") {
    stylesheet.media = "all";
  } else if (state.uiThemeOverride === "dark") {
    stylesheet.media = "not all";
  } else {
    stylesheet.media = LIGHT_THEME_MEDIA;
  }
  const effectiveTheme = state.uiThemeOverride || systemTheme;
  const targetTheme = effectiveTheme === "light" ? "dark" : "light";
  const targetLabel = targetTheme === "light" ? "浅色" : "深色";
  const effectiveLabel = effectiveTheme === "light" ? "日间" : "夜间";
  button.dataset.effectiveTheme = effectiveTheme;
  if (state.uiThemeOverride) {
    const systemLabel = systemTheme === "light" ? "日间" : "夜间";
    label.textContent = `当前为${effectiveLabel}界面；切换为${systemLabel}界面`;
    button.setAttribute(
      "aria-label",
      `当前为${effectiveLabel}界面；切换为${systemLabel}界面并恢复跟随 Windows`,
    );
    button.title = (
      `当前临时使用${effectiveLabel}界面；`
      + `点击恢复跟随 Windows（${systemLabel}）`
    );
  } else {
    label.textContent = `当前为${effectiveLabel}界面；切换为${targetLabel}界面`;
    button.setAttribute(
      "aria-label",
      `当前为${effectiveLabel}界面；临时切换为${targetLabel}界面`,
    );
    button.title = (
      `当前跟随 Windows（${effectiveLabel}界面）；点击临时切换为${targetLabel}界面`
    );
  }
  button.setAttribute("aria-pressed", String(Boolean(state.uiThemeOverride)));
}

function initializeUiThemeToggle() {
  state.systemThemeQuery = window.matchMedia(LIGHT_THEME_MEDIA);
  state.lightThemeStylesheet = $("#workbench-light-theme");
  const button = $("#ui-theme-toggle");
  if (!button || !state.lightThemeStylesheet) {
    if (button) {
      button.disabled = true;
      button.title = "界面主题切换不可用";
    }
    return;
  }
  button.addEventListener("click", () => {
    if (state.uiThemeOverride) {
      state.uiThemeOverride = null;
    } else {
      state.uiThemeOverride = systemUiTheme() === "light" ? "dark" : "light";
    }
    renderUiThemeToggle();
  });
  state.systemThemeQuery.addEventListener("change", renderUiThemeToggle);
  renderUiThemeToggle();
}

function cloneConfig(config) {
  return {
    dayStart: config.dayStart,
    nightStart: config.nightStart,
    dayAppsTheme: config.dayAppsTheme,
    nightAppsTheme: config.nightAppsTheme,
    notifyErrors: config.notifyErrors,
    notifyStatusChanges: config.notifyStatusChanges,
  };
}

function cloneColor(color) {
  return color ? colorModule.cloneColor(color) : null;
}

function cloneColors(colors) {
  return {
    day: cloneColor(colors?.day),
    night: cloneColor(colors?.night),
  };
}

function workspaceIdentity(config, colors) {
  return JSON.stringify({
    config: cloneConfig(config),
    colors: cloneColors(colors),
  });
}

function showToast(message, isError = false, options = {}) {
  const toast = $("#toast");
  const centered = options.centered === true;
  const duration = options.duration ?? 4200;
  toast.classList.remove("is-visible");
  toast.textContent = message;
  toast.classList.toggle("is-error", isError);
  toast.classList.toggle("is-centered", centered);
  void toast.offsetWidth;
  toast.classList.add("is-visible");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => {
    toast.classList.remove("is-visible", "is-centered");
  }, duration);
}

function setBusy(busy) {
  state.busy = busy;
  $$("button").forEach((button) => {
    button.disabled = busy;
  });
}

function activatePage(name, options = {}) {
  const requested = ["plan", "maintenance", "debug"].includes(name)
    ? name
    : "plan";
  state.activePage = requested;
  $$("[data-page]").forEach((button) => {
    const selected = button.dataset.page === requested;
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
  });
  $$("[data-page-panel]").forEach((panel) => {
    const selected = panel.dataset.pagePanel === requested;
    panel.hidden = !selected;
    panel.classList.toggle("is-active", selected);
  });
  if (requested === "plan") {
    activatePlanView(options.planView || state.planView);
  } else {
    document.body.dataset.planViewMode = requested;
  }
  if (options.focus) $(`[data-page="${requested}"]`)?.focus();
}

function activatePlanView(name, options = {}) {
  const requested = name === "settings" ? "settings" : "overview";
  state.planView = requested;
  document.body.dataset.planViewMode = requested;
  $$("[data-plan-view]").forEach((view) => {
    const selected = view.dataset.planView === requested;
    view.hidden = !selected;
    view.classList.toggle("is-active", selected);
  });
  const settings = requested === "settings";
  setText("#workspace-title", settings ? "设置昼夜计划" : "计划概览");
  setText(
    "#workspace-subtitle",
    settings
      ? "选择昼间或夜间，使用共享表盘调整对应时点。"
      : "一个昼夜计划，按时准备对应的 Windows 外观。",
  );
  if (options.focus) {
    $(settings ? `#boundary-tab-${state.selectedBoundary}` : "#open-settings-button")?.focus();
  }
}

function configPayload() {
  return state.draft ? cloneConfig(state.draft) : {
    dayStart: $("#day-start").value,
    nightStart: $("#night-start").value,
    dayAppsTheme: $("#day-theme").value,
    nightAppsTheme: $("#night-theme").value,
    notifyErrors: $("#notify-errors").checked,
    notifyStatusChanges: $("#notify-status").checked,
  };
}

function workspacePayload() {
  const config = configPayload();
  return {
    ...config,
    dayColor: cloneColor(state.draftColors?.day),
    nightColor: cloneColor(state.draftColors?.night),
  };
}

function currentBoundaryKey() {
  return state.selectedBoundary === "day" ? "dayStart" : "nightStart";
}

function markDraftState() {
  state.dirty = Boolean(
    state.draft
    && state.loadedConfig
    && workspaceIdentity(state.draft, state.draftColors)
      !== workspaceIdentity(state.loadedConfig, state.loadedColors)
  );
  const node = $("#draft-state");
  node.textContent = state.dirty ? "有尚未保存的修改" : "与已保存配置一致";
  node.classList.toggle("is-dirty", state.dirty);
}

function renderTimeDraft() {
  if (!state.draft || !state.interactiveDial || !state.summaryDial) return;
  const activeTime = state.draft[currentBoundaryKey()];
  state.interactiveDial.setTime(activeTime, { selectedHand: state.selectedHand });
  state.summaryDial.setTimes(state.draft.dayStart, state.draft.nightStart);
  state.expandedSummaryDial?.setTimes(
    state.draft.dayStart,
    state.draft.nightStart,
  );

  const parsed = timeMath.parseTime(activeTime);
  setText("#dial-hour-value", String(parsed.hour).padStart(2, "0"));
  setText("#dial-minute-value", String(parsed.minute).padStart(2, "0"));
  $("#dial-hour-value").classList.toggle("is-selected", state.selectedHand === "hour");
  $("#dial-minute-value").classList.toggle("is-selected", state.selectedHand === "minute");

  const afternoon = parsed.hour >= 12;
  const meridiemToggle = $("#meridiem-toggle");
  meridiemToggle.classList.toggle("is-second", afternoon);
  meridiemToggle.setAttribute("aria-checked", String(afternoon));
  meridiemToggle.setAttribute("aria-label", `当前为${afternoon ? "下午" : "上午"}；点击切换至${afternoon ? "上午" : "下午"}`);
  const editingDay = state.selectedBoundary === "day";
  const dialPanel = $(".settings-dial-panel");
  dialPanel.dataset.dialPeriod = state.selectedBoundary;
  const contextIcon = $("#dial-context-icon");
  contextIcon.classList.toggle("day-shape", editingDay);
  contextIcon.classList.toggle("night-shape", !editingDay);
  setText(
    "#dial-context-title",
    `${editingDay ? "昼间" : "夜间"}开始 · ${activeTime}`,
  );
  const dialHost = $("#interactive-time-dial");
  dialHost.dataset.boundary = state.selectedBoundary;
  dialHost.classList.toggle("is-day-boundary", editingDay);
  dialHost.classList.toggle("is-night-boundary", !editingDay);

  $$('[data-boundary-tab]').forEach((tab) => {
    const selected = tab.dataset.boundaryTab === state.selectedBoundary;
    tab.classList.toggle("is-active", selected);
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  $$('[data-boundary-panel]').forEach((panel) => {
    const selected = panel.dataset.boundaryPanel === state.selectedBoundary;
    panel.hidden = !selected;
    panel.classList.toggle("is-active", selected);
  });

  $("#day-start").value = state.draft.dayStart;
  $("#night-start").value = state.draft.nightStart;
  setText("#day-summary-time", `${state.draft.dayStart} 开始`);
  setText("#night-summary-time", `${state.draft.nightStart} 开始`);
  setText("#day-tab-time", state.draft.dayStart);
  setText("#night-tab-time", state.draft.nightStart);
  setText("#day-summary-time-expanded", `${state.draft.dayStart} 开始`);
  setText("#night-summary-time-expanded", `${state.draft.nightStart} 开始`);
  renderDialProgress();
  markDraftState();
}

function renderDialProgress() {
  const target = state.selectedBoundary === "day" ? "夜间" : "昼间";
  const hourDone = state.adjustedHands.has("hour");
  const minuteDone = state.adjustedHands.has("minute");
  const message = hourDone && minuteDone
    ? `时针和分针均已调整，正在转到${target}设置。`
    : hourDone
      ? "时针已调整；再拖动一次分针后自动切换。"
      : minuteDone
        ? "分针已调整；再拖动一次时针后自动切换。"
        : `分别拖动时针和分针后，将自动转到${target}设置。`;
  setText("#dial-progress-text", message);
}

function renderDraft() {
  if (!state.draft) return;
  renderTimeDraft();
  $("#day-theme").value = state.draft.dayAppsTheme;
  $("#night-theme").value = state.draft.nightAppsTheme;
  for (const name of ["day", "night"]) {
    const dark = state.draft[`${name}AppsTheme`] === "dark";
    const toggle = $(`[data-theme-toggle="${name}"]`);
    toggle.classList.toggle("is-second", dark);
    toggle.setAttribute("aria-checked", String(dark));
    toggle.setAttribute("aria-label", `${name === "day" ? "昼间" : "夜间"}应用模式为${dark ? "深色" : "浅色"}；点击切换至${dark ? "浅色" : "深色"}`);
  }
  $("#notify-errors").checked = state.draft.notifyErrors;
  $("#notify-status").checked = state.draft.notifyStatusChanges;
}

function renderColorDraft(profiles) {
  for (const name of ["day", "night"]) {
    const color = state.draftColors?.[name];
    if (color) {
      state.colorFields[name].setColor(color);
    } else {
      state.colorFields[name].setUnavailable(
        profiles?.[name]?.message || "颜色记录不可用",
      );
    }
  }
  renderColorMatchNote();
}

function renderColorMatchNote() {
  const note = $("#color-match-note");
  const same = Boolean(
    state.draftColors?.day
    && state.draftColors?.night
    && colorModule.sameColor(state.draftColors.day, state.draftColors.night)
  );
  note.hidden = !same;
}

function renderWorkspaceResult(save) {
  const panel = $("#workspace-result");
  panel.hidden = false;
  const saveResult = save?.result;
  setText(
    "#workspace-save-result",
    ["changed", "no-change"].includes(saveResult)
      ? (saveResult === "changed" ? "已保存并读回验证" : "无需修改，读回一致")
      : (save?.message || "未保存"),
  );
}

function renderRecentLog(summary) {
  if (!summary?.available) {
    setText("#recent-log-report", `日志摘要不可用\n${summary?.message || "未知原因"}`);
    return;
  }
  const events = summary.events || [];
  setText(
    "#recent-log-report",
    events.length
      ? events.map((event) => (
        `${event.occurredAt}  [${event.level}]  ${event.event}\n`
        + `${event.result}${event.targetProfile ? ` · ${event.targetProfile}` : ""}`
        + `${event.message ? ` · ${event.message}` : ""}`
      )).join("\n\n")
      : "尚无结构化事件。",
  );
}

function diagnosticSummary() {
  const data = state.overview;
  if (!data) return "ThemeScheduler：状态尚未读取。";
  return [
    "ThemeScheduler 诊断摘要",
    `读取时间：${data.now}`,
    `核心状态：${data.result}`,
    `当前目标：${data.targetProfile}`,
    `自动切换：${data.state?.paused ? "已暂停" : "运行中"}`,
    `任务定义：${data.task?.valid ? "正常" : "需检查"}`,
    `昼间 profile：${data.profiles?.day?.valid ? "有效" : "无效"}`,
    `夜间 profile：${data.profiles?.night?.valid ? "有效" : "无效"}`,
  ].join("\n");
}

function renderSwatch(selector, color) {
  const swatch = $(selector);
  if (!swatch) return;
  swatch.style.backgroundColor = color?.hex || "transparent";
  swatch.classList.toggle("is-unavailable", !color?.hex);
}

function renderPlanProfiles(data) {
  for (const name of ["day", "night"]) {
    const profile = data.profiles?.[name];
    const mode = data.config?.[`${name}AppsTheme`] === "light" ? "浅色" : "深色";
    const color = profile?.valid ? profile.color : null;
    setText(`#overview-${name}-mode`, mode);
    setText(`#overview-${name}-color`, color?.hex || "颜色不可用");
    renderSwatch(`#overview-${name}-swatch`, color);
  }
}

function renderCurrentAppearance(result) {
  const available = result?.result === "success" && result.color?.hex;
  state.currentAppearance = available ? result : null;
  setText("#current-appearance-state", available ? "已读取" : "不可用");
  setText(
    "#current-appearance-mode",
    available ? (result.appMode === "light" ? "浅色应用" : "深色应用") : "暂不可读取",
  );
  setText("#current-appearance-color", available ? result.color.hex : "—");
  setText(
    "#current-appearance-note",
    available ? "实际应用模式与强调色" : "当前启动方式未启用系统外观读取",
  );
  renderSwatch("#current-appearance-swatch", available ? result.color : null);
}

async function refreshCurrentAppearance(enabled) {
  if (!enabled) {
    renderCurrentAppearance(null);
    return;
  }
  try {
    const result = apiContracts.validate(
      "readCurrentWindowsAppearance",
      await window.pywebview.api.read_current_windows_appearance(),
    );
    renderCurrentAppearance(result);
  } catch (_error) {
    renderCurrentAppearance(null);
  }
}

async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const helper = document.createElement("textarea");
  helper.value = text;
  helper.setAttribute("readonly", "");
  helper.style.position = "fixed";
  helper.style.opacity = "0";
  document.body.appendChild(helper);
  helper.select();
  const copied = document.execCommand("copy");
  helper.remove();
  if (!copied) throw new Error("当前 WebView 不允许写入剪贴板。");
}

function renderOverview(data, options = {}) {
  state.overview = data;
  const usable = ["success", "partial"].includes(data.result);
  const healthy = data.result === "success";
  $("#health-pill").className = `status-pill ${healthy ? "is-ok" : "is-warn"}`;
  setText("#health-text", healthy ? "核心状态可用" : "部分状态不可用");
  setText("#overview-report", JSON.stringify(data, null, 2));
  setText("#task-report", JSON.stringify(data.task, null, 2));
  setText(
    "#state-profile-report",
    JSON.stringify(
      {
        state: data.state,
        profiles: data.profiles,
      },
      null,
      2,
    ),
  );
  renderRecentLog(data.recentLog);
  if (!usable) {
    showToast(data.message || "状态读取失败", true);
    return;
  }

  const targetName = data.targetProfile === "day" ? "昼间" : "夜间";
  setText("#target-profile", targetName);
  setText("#target-symbol", data.targetProfile === "day" ? "昼" : "夜");
  setText("#current-time", new Date(data.now).toLocaleString("zh-CN"));
  setText("#pause-state", data.state.paused ? "已暂停" : "运行中");
  const automationToggle = $("#automation-toggle-button");
  automationToggle.setAttribute("aria-checked", String(!data.state.paused));
  automationToggle.classList.toggle("is-paused", data.state.paused);
  const initialSetupPending = data.initialSetupPending === true;
  state.initialSetupPending = initialSetupPending;
  $("#initial-setup-banner").hidden = !initialSetupPending;
  automationToggle.disabled = state.busy || initialSetupPending;
  $("#save-only-button").textContent = initialSetupPending
    ? "保存并启用"
    : "保存";
  setText("#last-result", `上次结果：${data.state.lastResult}`);
  if (data.task.available === false) {
    setText("#task-state", "不可用");
    setText("#task-detail", "核心配置仍可操作");
  } else {
    setText("#task-state", data.task.valid ? "正常" : "需修复");
    setText("#task-detail", data.task.valid ? "定义与配置一致" : `${data.task.differences.length} 项差异`);
  }

  const validProfiles = ["day", "night"].filter((name) => data.profiles[name].valid).length;
  setText("#profile-state", `${validProfiles} / 2`);
  setText("#profile-detail", validProfiles === 2 ? "昼夜记录均有效" : "颜色记录不完整");
  renderPlanProfiles(data);

  const installBackup = data.installBackup;
  if (installBackup?.valid) {
    const mode = installBackup.appMode === "Light" ? "浅色" : "深色";
    setText(
      "#install-backup-state",
      `安装前外观恢复点：已验证 · 应用${mode} · ${installBackup.colorizationColor}`,
    );
  } else {
    setText(
      "#install-backup-state",
      `安装前外观恢复点：${installBackup?.message || "不可用"}`,
    );
  }

  if (!state.dirty || options.resetDraft) {
    state.loadedConfig = cloneConfig(data.config);
    state.draft = cloneConfig(data.config);
    state.loadedColors = cloneColors({
      day: data.profiles.day.valid ? data.profiles.day.color : null,
      night: data.profiles.night.valid ? data.profiles.night.color : null,
    });
    state.draftColors = cloneColors(state.loadedColors);
    state.dirty = false;
    renderColorDraft(data.profiles);
  }
  renderDraft();

  setText("#data-root", `数据目录：${data.dataRoot}`);
  const safetyNote = $("#safety-note");
  safetyNote.textContent = data.liveWritesEnabled
    ? "实机操作已启用；保存和修复会在确认后更新计划或系统集成。"
    : "当前为安全预览，任务与主题写入已关闭。";
  safetyNote.classList.toggle("is-live", data.liveWritesEnabled);
  $$("[data-import-appearance]").forEach((button) => {
    const enabled = Boolean(data.currentAppearanceReadEnabled);
    button.dataset.readEnabled = String(enabled);
    button.setAttribute("aria-disabled", String(!enabled));
    button.classList.toggle("is-safety-blocked", !enabled);
    button.disabled = state.busy;
    button.title = enabled
      ? "读取当前应用模式和强调色到该时段草稿"
      : "隔离安全预览不会读取真实 Windows 外观";
    const profile = button.dataset.importAppearance;
    const status = $(`[data-import-status="${profile}"]`);
    if (status && !status.classList.contains("is-imported")) {
      status.hidden = true;
      status.textContent = "";
    }
  });
}

async function callApi(action, work, successMessage) {
  if (state.busy) return null;
  setBusy(true);
  try {
    const result = apiContracts.validate(action, await work());
    const failed = !["success", "changed", "no-change", "applied", "restored"].includes(result.result);
    showToast(result.message || successMessage || result.result, failed);
    return result;
  } catch (error) {
    showToast(String(error), true);
    return null;
  } finally {
    setBusy(false);
  }
}

async function refresh(options = {}) {
  const result = await callApi(
    "overview",
    () => window.pywebview.api.get_overview(),
  );
  if (result) {
    renderOverview(result, options);
    await refreshCurrentAppearance(result.currentAppearanceReadEnabled);
  }
}

const healthStatusLabels = {
  healthy: "正常",
  warning: "警告",
  repairable: "需要修复",
  "action-required": "需要处理",
};

async function runHealthInspection() {
  const result = await callApi(
    "checkHealth",
    () => window.pywebview.api.check_health(),
  );
  if (!result?.checks) return;
  const text = result.checks
    .map((check) => {
      const label = healthStatusLabels[check.status] || check.status;
      return `${label.padEnd(8)} ${check.id}\n${check.message}`;
    })
    .join("\n\n");
  setText("#health-report", text);
  setText(
    "#maintenance-health-summary",
    `只读健康检查：${healthStatusLabels[result.status] || result.status}`,
  );
}

window.themeSchedulerOpenHealth = async () => {
  activatePage("maintenance");
  const maintenance = $("#maintenance");
  maintenance.scrollIntoView({ behavior: "smooth", block: "start" });
  await runHealthInspection();
};

function updateDraftValue(key, value) {
  if (!state.draft) return;
  state.draft[key] = value;
  renderDraft();
}

function selectBoundary(name) {
  if (!["day", "night"].includes(name)) return;
  if (state.selectedBoundary !== name) state.adjustedHands.clear();
  const changed = state.selectedBoundary !== name;
  state.selectedBoundary = name;
  renderTimeDraft();
  if (changed) {
    $("#interactive-time-dial").animate(
      [{ opacity: 1 }, { opacity: 0.35 }, { opacity: 1 }],
      { duration: 260, easing: "ease-in-out" },
    );
  }
}

function validateFiveMinuteDraft() {
  for (const [label, key] of [["昼间", "dayStart"], ["夜间", "nightStart"]]) {
    const parsed = timeMath.parseTime(state.draft[key]);
    if (parsed.minute % 5 !== 0) {
      return `${label}开始时间必须使用 5 分钟步进。`;
    }
  }
  if (state.draft.dayStart === state.draft.nightStart) {
    return "昼间和夜间的开始时间不能相同。";
  }
  return "";
}

for (const name of ["day", "night"]) {
  state.colorFields[name] = new colorModule.ColorFields(
    $(`[data-color-editor="${name}"]`),
    {
      onChange: (color) => {
        if (!state.draftColors) return;
        state.draftColors[name] = cloneColor(color);
        renderColorMatchNote();
        markDraftState();
      },
    },
  );
}

initializeUiThemeToggle();

state.summaryDial = new timeDials.ScheduleSummaryDial($("#schedule-summary-dial"));
state.expandedSummaryDial = new timeDials.ScheduleSummaryDial(
  $("#schedule-summary-dial-expanded"),
);
state.interactiveDial = new timeDials.InteractiveTimeDial(
  $("#interactive-time-dial"),
  {
    value: "06:15",
    onChange: (value, detail) => {
      state.selectedHand = detail.selectedHand;
      updateDraftValue(currentBoundaryKey(), value);
    },
  },
);

function openScheduleZoom() {
  const popover = $("#schedule-zoom-popover");
  if (!popover || popover.matches(":popover-open")) return;
  popover.showPopover();
  $("#close-schedule-zoom-button")?.focus();
}

function closeScheduleZoom() {
  const popover = $("#schedule-zoom-popover");
  if (!popover || !popover.matches(":popover-open")) return;
  popover.hidePopover();
  $("#expand-schedule-button")?.focus();
}

$("#schedule-summary-dial").addEventListener("dblclick", openScheduleZoom);
$("#schedule-summary-dial-expanded").addEventListener(
  "dblclick",
  closeScheduleZoom,
);
$("#schedule-zoom-popover").addEventListener("toggle", (event) => {
  if (event.newState === "open") {
    $("#close-schedule-zoom-button")?.focus();
    return;
  }
  $("#expand-schedule-button")?.focus();
});

$("#interactive-time-dial").addEventListener("dialhandchange", (event) => {
  state.selectedHand = event.detail.selectedHand;
  renderTimeDraft();
});

$("#interactive-time-dial").addEventListener("dialdragend", (event) => {
  if (!event.detail.changed) return;
  state.adjustedHands.add(event.detail.selectedHand);
  renderDialProgress();
  if (state.adjustedHands.size !== 2) return;
  const completedBoundary = state.selectedBoundary;
  window.setTimeout(() => {
    if (state.selectedBoundary !== completedBoundary) return;
    const next = completedBoundary === "day" ? "night" : "day";
    selectBoundary(next);
    $(`#boundary-tab-${next}`)?.focus();
    showToast(`已切换至${next === "day" ? "昼间" : "夜间"}`, false, {
      centered: true,
      duration: 1500,
    });
  }, 180);
});

$$("[data-page]").forEach((button) => {
  button.addEventListener("click", () => activatePage(
    button.dataset.page,
    button.dataset.page === "plan" ? { planView: "overview" } : {},
  ));
  button.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const tabs = $$("[data-page]");
    const index = tabs.indexOf(button);
    const nextIndex = event.key === "Home"
      ? 0
      : event.key === "End"
        ? tabs.length - 1
        : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    activatePage(tabs[nextIndex].dataset.page, { focus: true });
  });
});

$$('[data-boundary-tab]').forEach((button) => {
  button.addEventListener("click", () => selectBoundary(button.dataset.boundaryTab));
  button.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = ["ArrowRight", "End"].includes(event.key) ? "night" : "day";
    selectBoundary(next);
    $(`#boundary-tab-${next}`)?.focus();
  });
});

$("#open-settings-button").addEventListener("click", () => {
  activatePlanView("settings", { focus: true });
});
$("#initial-setup-button").addEventListener("click", () => {
  activatePlanView("settings", { focus: true });
});
$("#back-to-overview-button").addEventListener("click", () => {
  if (state.dirty && !window.confirm("放弃尚未保存的设置并返回概览？")) return;
  if (state.dirty) {
    state.draft = cloneConfig(state.loadedConfig);
    state.draftColors = cloneColors(state.loadedColors);
    state.dirty = false;
    state.adjustedHands.clear();
    renderColorDraft(state.overview?.profiles);
    renderDraft();
  }
  activatePlanView("overview", { focus: true });
});

$("#dial-hour-value").addEventListener("click", () => {
  state.selectedHand = "hour";
  state.interactiveDial.selectHand("hour");
  renderTimeDraft();
});
$("#dial-minute-value").addEventListener("click", () => {
  state.selectedHand = "minute";
  state.interactiveDial.selectHand("minute");
  renderTimeDraft();
});

$("#meridiem-toggle").addEventListener("click", () => {
  state.interactiveDial.shiftHalfDay(1);
  updateDraftValue(currentBoundaryKey(), state.interactiveDial.getTime());
});

["day", "night"].forEach((name) => {
  $(`#${name}-start`).addEventListener("change", (event) => {
    setText("#form-error", "");
    try {
      const parsed = timeMath.parseTime(event.target.value);
      if (parsed.minute % 5 !== 0) {
        throw new TypeError("时间必须使用 5 分钟步进");
      }
      updateDraftValue(`${name}Start`, event.target.value);
      selectBoundary(name);
    } catch (error) {
      setText("#form-error", String(error.message || error));
      renderTimeDraft();
    }
  });
  $(`[data-theme-toggle="${name}"]`).addEventListener("click", () => {
    const key = `${name}AppsTheme`;
    const value = state.draft[key] === "dark" ? "light" : "dark";
    $(`#${name}-theme`).value = value;
    updateDraftValue(key, value);
  });
});

$("#notify-errors").addEventListener("change", (event) => {
  updateDraftValue("notifyErrors", event.target.checked);
});
$("#notify-status").addEventListener("change", (event) => {
  updateDraftValue("notifyStatusChanges", event.target.checked);
});

async function submitWorkspace() {
  setText("#form-error", "");
  const localError = validateFiveMinuteDraft();
  if (localError) {
    setText("#form-error", localError);
    return null;
  }
  if (!state.draftColors?.day || !state.draftColors?.night) {
    setText("#form-error", "昼夜颜色记录必须完整且有效。");
    activatePage("plan", { planView: "settings" });
    return null;
  }
  if (!state.colorFields.day.isValid() || !state.colorFields.night.isValid()) {
    setText("#form-error", "请先修正昼间或夜间颜色输入，再保存。");
    activatePage("plan", { planView: "settings" });
    return null;
  }
  const payload = workspacePayload();
  let validation;
  try {
    validation = apiContracts.validate(
      "validateWorkspace",
      await window.pywebview.api.validate_workspace(payload),
    );
  } catch (error) {
    setText("#form-error", String(error.message || error));
    return null;
  }
  if (!validation.valid) {
    setText("#form-error", validation.message);
    return null;
  }
  const result = await callApi(
    "saveWorkspace",
    () => window.pywebview.api.save_workspace(payload, true),
    state.initialSetupPending
      ? "计划已保存并启用；Windows 外观保持不变"
      : "计划、昼夜颜色与任务定义已更新",
  );
  if (result) renderWorkspaceResult(result);
  const saveCompleted = ["changed", "no-change"].includes(result?.result);
  if (saveCompleted) {
    state.dirty = false;
    await refresh({ resetDraft: true });
    activatePlanView("overview");
  }
  return result;
}

$("#config-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await submitWorkspace();
});

$("#refresh-button").addEventListener("click", async () => {
  if (state.dirty && !window.confirm("放弃尚未保存的页面修改并重新读取？")) return;
  state.dirty = false;
  await refresh({ resetDraft: true });
});
$("#automation-toggle-button").addEventListener("click", async () => {
  const pause = !Boolean(state.overview?.state?.paused);
  const result = await callApi(
    "setPaused",
    () => window.pywebview.api.set_paused(pause, true),
  );
  if (result) await refresh();
});
$("#check-task-button").addEventListener("click", async () => {
  const result = await callApi(
    "checkTask",
    () => window.pywebview.api.check_task(),
  );
  if (result) await refresh();
});
$("#check-health-button").addEventListener("click", runHealthInspection);
$("#copy-diagnostic-button").addEventListener("click", async () => {
  const button = $("#copy-diagnostic-button");
  try {
    await copyText(diagnosticSummary());
    button.textContent = "已复制";
    setTimeout(() => {
      button.textContent = "复制摘要";
    }, 1800);
    showToast("诊断摘要已复制；未包含完整路径或原始 JSON。");
  } catch (error) {
    showToast(String(error.message || error), true);
  }
});
$$("[data-import-appearance]").forEach((button) => {
  button.addEventListener("click", async () => {
    if (button.dataset.readEnabled !== "true") {
      showToast("安全预览不会读取真实 Windows 外观；安装态才可使用。");
      return;
    }
    const profile = button.dataset.importAppearance;
    if (!["day", "night"].includes(profile)) return;
    const result = await callApi(
      "readCurrentWindowsAppearance",
      () => window.pywebview.api.read_current_windows_appearance(),
      "已读取当前 Windows 外观",
    );
    if (result?.result !== "success") return;
    const color = cloneColor(result.color);
    state.draftColors[profile] = color;
    state.colorFields[profile].setColor(color);
    state.draft[`${profile}AppsTheme`] = result.appMode;
    renderDraft();
    renderColorMatchNote();
    markDraftState();
    const label = profile === "day" ? "昼间" : "夜间";
    const mode = result.appMode === "light" ? "浅色" : "深色";
    const status = $(`[data-import-status="${profile}"]`);
    status.textContent = `已导入：${label} · ${mode} · ${color.hex} · 尚未保存`;
    status.hidden = false;
    status.classList.add("is-imported");
  });
});
$("#repair-identity-button").addEventListener("click", async () => {
  if (!window.confirm("仅修复 ThemeScheduler 拥有的通知快捷方式身份和 URI 协议？")) return;
  const result = await callApi(
    "repairNotificationIdentity",
    () => window.pywebview.api.repair_notification_identity(true),
  );
  if (result) await refresh();
});
$("#repair-task-button").addEventListener("click", async () => {
  if (!window.confirm("按当前配置修复 ThemeScheduler 任务计划？")) return;
  const result = await callApi(
    "repairTask",
    () => window.pywebview.api.repair_task(true),
  );
  if (result) await refresh();
});
$("#reset-preferences-button").addEventListener("click", async () => {
  if (!window.confirm("恢复默认时间和通知设置，并同步修复任务计划？昼夜应用模式选择会保留。")) return;
  const result = await callApi(
    "resetPreferences",
    () => window.pywebview.api.reset_preferences(true),
  );
  if (result) {
    state.dirty = false;
    await refresh({ resetDraft: true });
  }
});
$("#restore-install-button").addEventListener("click", async () => {
  if (!window.confirm("暂停自动切换并恢复安装前的应用模式和强调色？Windows 系统模式不会改变。")) return;
  const result = await callApi(
    "restoreInstallAppearance",
    () => window.pywebview.api.restore_install_appearance(true),
  );
  if (result) await refresh();
});
$("#uninstall-button").addEventListener("click", async () => {
  if (!window.confirm("启动独立卸载器？实际清理仍需在卸载器中确认。")) return;
  await callApi(
    "launchUninstaller",
    () => window.pywebview.api.launch_uninstaller(true),
  );
});
$$("[data-target]").forEach((button) => {
  button.addEventListener("click", () => {
    callApi(
      "openTarget",
      () => window.pywebview.api.open_target(button.dataset.target),
    );
  });
});

window.addEventListener("pywebviewready", async () => {
  const hash = window.location.hash.slice(1);
  if (hash === "maintenance") activatePage("maintenance");
  if (hash === "health") activatePage("maintenance");
  await refresh({ resetDraft: true });
  if (hash === "health") await window.themeSchedulerOpenHealth();
});
