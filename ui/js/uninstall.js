"use strict";

const wizardRuntime = window.ThemeSchedulerWizardRuntime;
const { addFact, all: $$, byId: $ } = wizardRuntime;

const PAGE_ORDER = ["welcome", "options", "confirm", "progress", "result"];
const GROUP_ORDER = ["prepare", "integrate", "program", "data"];
const STAGE_GROUPS = {
  starting: "prepare",
  "staging-copy": "prepare",
  handoff: "prepare",
  "waiting-for-launcher": "prepare",
  "task-removed": "integrate",
  "appearance-handled": "integrate",
  "shortcuts-removed": "integrate",
  "registration-removed": "integrate",
  "program-root-removed": "program",
  "user-data-cleaned": "data",
  "self-cleanup-scheduled": "data",
  completed: "data",
  failed: "data",
};
const STAGE_LABELS = {
  starting: "正在启动卸载会话",
  "staging-copy": "正在复制并验证独立卸载器",
  handoff: "正在交接到临时卸载器",
  "waiting-for-launcher": "正在等待安装目录中的卸载窗口退出",
  "task-removed": "计划任务已移除并验证",
  "appearance-handled": "安装前外观恢复选择已处理",
  "shortcuts-removed": "快捷方式已移除并验证",
  "registration-removed": "控制面板登记与通知协议已移除",
  "program-root-removed": "程序文件已删除并验证",
  "user-data-cleaned": "用户数据已按选择处理",
  "self-cleanup-scheduled": "临时卸载目录已安排自动清理",
  completed: "卸载事务已经完成",
  failed: "卸载未完整完成，正在整理可重试结果",
};

const state = {
  page: "welcome",
  status: null,
  validatedOptions: null,
  operationId: null,
  succeeded: false,
};

const setPage = wizardRuntime.createNavigation({
  state,
  pageOrder: PAGE_ORDER,
  renderFooter,
});

function renderFooter() {
  const cancel = $("cancel-button");
  const back = $("back-button");
  const next = $("next-button");
  const note = $("footer-note");
  cancel.hidden = false;
  cancel.disabled = false;
  cancel.textContent = "取消";
  back.hidden = true;
  back.disabled = false;
  next.hidden = false;
  next.disabled = false;
  next.textContent = "下一步";
  note.textContent = state.status?.previewMode
    ? "安全预览不会修改 Windows"
    : "确认前不会修改系统";

  if (state.page === "welcome") {
    next.disabled = !state.status?.ok;
    if (state.status && !state.status.ok) {
      next.hidden = true;
      cancel.textContent = "关闭";
    }
  } else if (state.page === "options") {
    back.hidden = false;
  } else if (state.page === "confirm") {
    back.hidden = false;
    next.textContent = state.status?.previewMode ? "演示卸载" : "卸载";
    note.textContent = state.status?.previewMode
      ? "安全预览不会修改 Windows"
      : "点击“卸载”后开始清理";
  } else if (state.page === "progress") {
    cancel.disabled = true;
    cancel.textContent = "处理中";
    next.hidden = true;
    note.textContent = "卸载完成前不能关闭";
  } else if (state.page === "result") {
    cancel.hidden = true;
    next.textContent = "退出";
    note.textContent = state.succeeded
      ? "退出后将自动清理临时卸载目录"
      : "可保留结果后退出并重新运行卸载器";
  }
}

function readOptions() {
  return {
    appearance: $("restore-appearance").checked
      ? "restore-pre-install-app-mode-and-accent"
      : "keep-current-appearance",
    keepConfigAndProfiles: $("keep-config").checked,
    keepLogs: $("keep-logs").checked,
  };
}

function renderConfirmation() {
  const list = $("confirmation-list");
  const options = state.validatedOptions;
  list.replaceChildren();
  addFact(
    list,
    "Windows 外观",
    options.appearance === "restore-pre-install-app-mode-and-accent"
      ? "恢复安装前的应用模式和强调色；Windows 系统模式保持不变"
      : "保持卸载时的当前外观",
  );
  addFact(
    list,
    "配置与颜色",
    options.keepConfigAndProfiles
      ? "保留配置、状态和昼夜颜色"
      : "删除配置、状态和昼夜颜色",
  );
  addFact(
    list,
    "日志",
    options.keepLogs ? "保留历史日志" : "删除历史日志",
  );
  addFact(
    list,
    "始终删除",
    "程序、任务、快捷方式、控制面板登记、通知协议、运行缓存和 WebView2 缓存",
  );
}

async function validateOptions() {
  $("options-error").textContent = "";
  const response = await window.pywebview.api.validate_options(readOptions());
  if (!response.ok) {
    $("options-error").textContent = response.error;
    return false;
  }
  state.validatedOptions = response.options;
  return true;
}

function renderProgress(snapshot) {
  const stages = snapshot.stages || [];
  const currentGroup = STAGE_GROUPS[snapshot.stage] || null;
  const seen = new Set(stages.map((stage) => STAGE_GROUPS[stage]).filter(Boolean));
  const currentIndex = currentGroup ? GROUP_ORDER.indexOf(currentGroup) : -1;
  const reachedIndex = Math.max(
    0,
    ...Array.from(seen).map((group) => GROUP_ORDER.indexOf(group)),
  );
  $$("[data-progress-group]").forEach((node) => {
    const group = node.dataset.progressGroup;
    const index = GROUP_ORDER.indexOf(group);
    node.classList.toggle("is-active", group === currentGroup && snapshot.stage !== "failed");
    node.classList.toggle(
      "is-complete",
      seen.has(group) && (snapshot.stage === "failed" || index < currentIndex || snapshot.stage === "completed"),
    );
  });
  $("progress-bar").style.width = `${18 + reachedIndex * 27}%`;
  $("progress-detail").textContent = STAGE_LABELS[snapshot.stage] || "正在执行卸载事务";
  $("handoff-note").hidden = snapshot.stage !== "handoff";
}

function renderResult(snapshot) {
  const layout = $("result-layout");
  const outcome = snapshot.outcome;
  layout.classList.remove("is-warning", "is-error");
  $("result-facts").replaceChildren();
  $("result-error").textContent = "";
  state.succeeded = false;

  if (!outcome) {
    layout.classList.add("is-error");
    $("result-icon").textContent = "!";
    $("result-title").textContent = "卸载未完成";
    $("result-detail").textContent = snapshot.error || "未取得可信的卸载结果。";
    return;
  }
  if (outcome.result === "completed" && outcome.verified === true) {
    state.succeeded = true;
    $("result-icon").textContent = "✓";
    $("result-title").textContent = "卸载完成";
    $("result-detail").textContent = "程序与系统集成已经移除并验证；临时卸载目录将在本窗口退出后自动清理。";
  } else {
    layout.classList.add("is-error");
    $("result-icon").textContent = "!";
    $("result-title").textContent = "卸载未完整完成";
    $("result-detail").textContent = outcome.message || "部分步骤未能完成；已完成步骤不会被伪装成完整成功。";
  }
  addFact(
    $("result-facts"),
    "安装前外观",
    outcome.appearanceRestored ? "已恢复并验证" : "按选择保持当前外观",
  );
  addFact(
    $("result-facts"),
    "程序与集成",
    outcome.programRootRemoved && outcome.registrationRemoved
      ? "已删除并验证"
      : "仍有项目需要重试",
  );
  addFact(
    $("result-facts"),
    "用户数据",
    outcome.dataCleaned ? "已按选择处理" : "尚未完成处理",
  );
  if ((outcome.residualPaths || []).length) {
    addFact(
      $("result-facts"),
      "残留",
      `${outcome.residualPaths.length} 项；请保留临时卸载目录后重试`,
    );
  }
}

async function pollUninstall() {
  const response = await window.pywebview.api.get_uninstall_status(state.operationId);
  if (!response.ok) {
    renderResult({ outcome: null, error: response.error });
    setPage("result");
    return;
  }
  renderProgress(response);
  if (response.phase === "running") {
    window.setTimeout(pollUninstall, 250);
    return;
  }
  if (response.outcome?.result === "handoff") {
    window.setTimeout(closeWindow, 350);
    return;
  }
  renderResult(response);
  setPage("result");
}

async function beginUninstall() {
  $("confirm-error").textContent = "";
  const response = await window.pywebview.api.start_uninstall(
    state.validatedOptions,
  );
  if (!response.ok) {
    $("confirm-error").textContent = response.error;
    return;
  }
  state.operationId = response.operationId;
  setPage("progress");
  renderProgress({ stage: "starting", stages: ["starting"] });
  pollUninstall();
}

async function closeWindow() {
  const response = await window.pywebview.api.close_window();
  if (!response.ok) {
    const target = state.page === "result"
      ? $("result-error")
      : $("options-error");
    target.textContent = response.error;
  }
}

async function requestExit() {
  const response = await window.pywebview.api.request_exit();
  if (!response.ok) {
    $("result-error").textContent = response.error;
  }
}

async function initialize() {
  renderFooter();
  const status = await window.pywebview.api.get_status();
  state.status = status;
  const panel = $("preflight-panel");
  if (!status.ok) {
    panel.classList.add("is-error");
    $("preflight-symbol").textContent = "!";
    $("preflight-title").textContent = "无法安全继续";
    $("preflight-detail").textContent = status.error;
    renderFooter();
    return;
  }
  panel.classList.add("is-ok");
  $("preflight-symbol").textContent = "✓";
  $("preflight-title").textContent = "卸载环境检查通过";
  $("preflight-detail").textContent = status.previewMode
    ? "安全预览已就绪；后续进度不会修改 Windows 或文件。"
    : "独立卸载器、固定目标和当前用户范围均已确认。";
  $("welcome-facts").hidden = false;
  $("restore-appearance").checked = (
    status.options.appearance === "restore-pre-install-app-mode-and-accent"
  );
  $("keep-config").checked = status.options.keepConfigAndProfiles;
  $("keep-logs").checked = status.options.keepLogs;
  state.validatedOptions = status.options;
  if (status.previewMode) {
    document.querySelector(".wizard-rail-note").innerHTML = "安全预览<br>不会修改 Windows";
    document.querySelector(".confirm-notice").textContent = "这是安全预览。点击“演示卸载”只展示进度和结果。";
  }
  renderFooter();
  if (status.autoStart) {
    setPage("progress");
    await beginUninstall();
  }
}

$("uninstall-form").addEventListener("submit", (event) => event.preventDefault());
$("cancel-button").addEventListener("click", closeWindow);
$("back-button").addEventListener("click", () => {
  if (state.page === "options") setPage("welcome");
  if (state.page === "confirm") setPage("options");
});
$("next-button").addEventListener("click", async () => {
  if (state.page === "welcome") {
    setPage("options");
  } else if (state.page === "options") {
    if (await validateOptions()) {
      renderConfirmation();
      setPage("confirm");
    }
  } else if (state.page === "confirm") {
    await beginUninstall();
  } else if (state.page === "result") {
    await requestExit();
  }
});

window.addEventListener("pywebviewready", initialize);
