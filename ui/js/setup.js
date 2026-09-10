"use strict";

const wizardRuntime = window.ThemeSchedulerWizardRuntime;
const { addFact, all: $$, byId: $ } = wizardRuntime;

const PAGE_ORDER = ["welcome", "options", "confirm", "progress", "result"];
const PROGRESS_GROUP_ORDER = ["prepare", "deploy", "integrate", "verify"];
const STAGE_GROUPS = {
  starting: "prepare",
  "waiting-for-applications": "prepare",
  "preparing-data": "prepare",
  "capturing-integration": "prepare",
  "deploying-files": "deploy",
  "applying-integration": "integrate",
  "verifying-installation": "verify",
  "committing-files": "verify",
  completed: "verify",
};
const STAGE_LABELS = {
  starting: "正在启动安装会话",
  "waiting-for-applications": "正在等待现有程序退出并取得安装锁",
  "preparing-data": "正在验证或初始化用户数据",
  "capturing-integration": "正在保存当前系统集成状态",
  "deploying-files": "正在部署并验证程序文件",
  "applying-integration": "正在创建快捷方式、登记和计划任务",
  "verifying-installation": "正在执行安装后读回验证",
  "committing-files": "正在提交安装事务",
  completed: "安装事务已经完成",
  "rolling-back-integration": "安装失败，正在恢复系统集成",
  "rolling-back-files": "正在恢复原程序文件",
  "rolling-back-data": "正在移除本次新建的数据",
  failed: "安装未完成，正在整理结果",
};

const state = {
  page: "welcome",
  status: null,
  validatedOptions: null,
  operationId: null,
  installSucceeded: false,
};

const setPage = wizardRuntime.createNavigation({
  state,
  pageOrder: PAGE_ORDER,
  renderFooter,
  focusPage: true,
});

function operationText(operation, retainedData = false) {
  if (operation === "install" && retainedData) {
    return [
      "恢复安装",
      "将重新部署程序并复用已保留的时间、模式、昼夜颜色、状态、日志和安装恢复点。",
    ];
  }
  return {
    install: ["首次安装", "将部署程序并建立当前用户集成；产品配置在安装后由主界面完成。"],
    reinstall: ["修复 / 重装", "将完整替换程序文件、修复系统集成，并原样保留可信用户数据。"],
    upgrade: ["升级", "将升级程序文件、更新系统集成，并原样保留可信用户数据。"],
  }[operation] || [operation, ""];
}

function renderFooter() {
  const cancel = $("cancel-button");
  const back = $("back-button");
  const next = $("next-button");
  const finishOnly = $("finish-only-button");
  const note = $("footer-note");
  cancel.hidden = false;
  cancel.disabled = false;
  cancel.textContent = "取消";
  back.hidden = true;
  back.disabled = false;
  next.hidden = false;
  next.disabled = false;
  next.textContent = "下一步";
  finishOnly.hidden = true;
  finishOnly.disabled = false;
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
    next.textContent = state.status?.previewMode ? "演示安装" : "安装";
    note.textContent = state.status?.previewMode
      ? "安全预览不会修改 Windows"
      : "点击“安装”后开始系统写入";
  } else if (state.page === "progress") {
    cancel.disabled = true;
    cancel.textContent = "处理中";
    next.hidden = true;
    note.textContent = "安装或回滚完成前不能关闭";
  } else if (state.page === "result") {
    cancel.hidden = true;
    next.textContent = state.installSucceeded
      ? "完成并打开 ThemeScheduler"
      : "关闭";
    finishOnly.hidden = !state.installSucceeded;
    note.textContent = "安装会话已经结束";
  }
}

function readOptions() {
  return {
    kind: "themescheduler.setup-options",
    schemaVersion: 2,
    desktopShortcut: $("desktop-shortcut").checked,
  };
}

function renderConfirmation() {
  const list = $("confirmation-list");
  list.replaceChildren();
  const plan = state.status.plan;
  const options = state.validatedOptions;
  const [operationLabel] = operationText(plan.operation, plan.retainedData);
  addFact(list, "操作", `${operationLabel} · ${plan.targetVersion}`);
  addFact(list, "位置", state.status.installRoot);
  if (!plan.retainedData) {
    addFact(list, "首次设置", "自动切换先保持暂停；安装后在主界面设置并启用");
  } else {
    addFact(list, "用户数据", "原样保留现有时间、模式、昼夜颜色、暂停状态、日志和首次恢复点");
  }
  addFact(
    list,
    "快捷方式",
    options.desktopShortcut ? "开始菜单和桌面" : "仅开始菜单",
  );
  addFact(list, "Windows 外观", "安装及修复过程均不立即同步或改变当前外观");
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
  const rollbackStage = [...stages].reverse().find((stage) => stage.startsWith("rolling-back-"));
  const currentGroup = STAGE_GROUPS[snapshot.stage] || null;
  const seenGroups = new Set(stages.map((stage) => STAGE_GROUPS[stage]).filter(Boolean));
  const currentIndex = currentGroup ? PROGRESS_GROUP_ORDER.indexOf(currentGroup) : -1;
  const reachedIndex = Math.max(
    0,
    ...Array.from(seenGroups).map((group) => PROGRESS_GROUP_ORDER.indexOf(group)),
  );

  $$("[data-progress-group]").forEach((node) => {
    const group = node.dataset.progressGroup;
    const index = PROGRESS_GROUP_ORDER.indexOf(group);
    node.classList.toggle("is-active", !rollbackStage && group === currentGroup);
    node.classList.toggle(
      "is-complete",
      seenGroups.has(group) && (
        Boolean(rollbackStage) || index < currentIndex
      ),
    );
  });
  $("progress-bar").style.width = `${20 + reachedIndex * 26}%`;
  $("progress-detail").textContent = STAGE_LABELS[snapshot.stage] || "正在执行安装事务";
  const rollback = $("rollback-note");
  rollback.hidden = !rollbackStage;
  if (rollbackStage) {
    rollback.textContent = STAGE_LABELS[rollbackStage];
  }
}

function failedForwardStage(stages) {
  const rollbackIndex = stages.findIndex((stage) => stage.startsWith("rolling-back-"));
  const candidates = stages.slice(0, rollbackIndex < 0 ? stages.length : rollbackIndex);
  return [...candidates].reverse().find(
    (stage) => !["starting", "waiting-for-applications", "failed"].includes(stage),
  ) || "starting";
}

function renderResult(snapshot) {
  const layout = $("result-layout");
  const outcome = snapshot.outcome;
  layout.classList.remove("is-warning", "is-error");
  $("result-facts").replaceChildren();
  $("result-error").textContent = "";
  $("open-log-button").hidden = !snapshot.logAvailable;
  state.installSucceeded = false;

  if (!outcome) {
    layout.classList.add("is-error");
    $("result-icon").textContent = "!";
    $("result-title").textContent = "安装未完成";
    $("result-detail").textContent = "安装器遇到未处理的错误，没有取得可信的安装结果。";
    addFact($("result-facts"), "失败阶段", STAGE_LABELS[failedForwardStage(snapshot.stages)]);
    addFact($("result-facts"), "详细信息", snapshot.error || "未知错误");
    return;
  }

  const result = outcome.result;
  if (result === "success") {
    state.installSucceeded = outcome.verified === true;
    $("result-icon").textContent = "✓";
    $("result-title").textContent = outcome.operation === "reinstall"
      ? "修复完成"
      : (outcome.operation === "upgrade" ? "升级完成" : "安装完成");
    $("result-detail").textContent = outcome.operation === "install" && !outcome.retainedData
      ? "程序与当前用户集成均已验证。打开主界面完成首次设置后，自动切换才会启用。"
      : "程序文件、当前用户集成和计划任务均已验证；既有用户数据保持不变。";
  } else if (result === "success-with-warning") {
    state.installSucceeded = outcome.verified === true;
    layout.classList.add("is-warning");
    $("result-icon").textContent = "!";
    $("result-title").textContent = "安装完成，但有后续提醒";
    $("result-detail").textContent = outcome.message || "安装本身已经验证，可打开主界面继续处理。";
  } else {
    layout.classList.add("is-error");
    $("result-icon").textContent = "!";
    $("result-title").textContent = result === "partial" ? "安装失败，回滚不完整" : "安装未完成";
    $("result-detail").textContent = outcome.rollbackSucceeded === true
      ? "安装器已恢复本次操作前的程序和系统集成状态。"
      : "部分恢复无法验证，请保留日志并停止继续安装。";
    addFact($("result-facts"), "失败阶段", STAGE_LABELS[failedForwardStage(snapshot.stages)]);
    addFact(
      $("result-facts"),
      "回滚",
      outcome.rollbackAttempted
        ? (outcome.rollbackSucceeded ? "已完成并验证" : "未能完整验证")
        : "未开始系统修改，无需回滚",
    );
  }
  addFact($("result-facts"), "版本", outcome.version || "—");
  addFact($("result-facts"), "事务编号", outcome.transactionId || "—");
  if (snapshot.logError) {
    $("result-error").textContent = `安装结束，但日志保存失败：${snapshot.logError}`;
  }
}

async function pollInstall() {
  const response = await window.pywebview.api.get_install_status(state.operationId);
  if (!response.ok) {
    renderResult({
      stages: [],
      outcome: null,
      error: response.error,
      logAvailable: false,
      logError: null,
    });
    setPage("result");
    return;
  }
  renderProgress(response);
  if (response.phase === "running") {
    window.setTimeout(pollInstall, 300);
    return;
  }
  renderResult(response);
  setPage("result");
}

async function beginInstall() {
  $("confirm-error").textContent = "";
  const response = await window.pywebview.api.start_install(state.validatedOptions);
  if (!response.ok) {
    $("confirm-error").textContent = response.error;
    return;
  }
  state.operationId = response.operationId;
  setPage("progress");
  renderProgress({
    stage: "starting",
    stages: ["starting"],
  });
  pollInstall();
}

async function closeSetupWindow() {
  const response = await window.pywebview.api.close_window();
  if (!response.ok) {
    const target = state.page === "result" ? $("result-error") : $("options-error");
    target.textContent = response.error;
  }
}

async function completeSetup(openApp) {
  $("result-error").textContent = "";
  const response = await window.pywebview.api.complete(openApp);
  if (!response.ok) $("result-error").textContent = response.error;
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
  $("preflight-title").textContent = "环境检查通过";
  $("preflight-detail").textContent = "内置载荷、安装记录和现有核心数据均可继续处理。";
  if (status.previewMode) {
    $("preflight-detail").textContent = "安全预览已就绪；后续安装进度为本地演示，不会修改 Windows。";
    document.querySelector(".wizard-rail-note").innerHTML = "安全预览<br>不会修改 Windows";
    document.querySelector(".confirm-notice").textContent = "这是安全预览。点击“演示安装”只会演示进度与结果页面。";
  }
  const [label, note] = operationText(
    status.plan.operation,
    status.plan.retainedData,
  );
  $("welcome-operation").textContent = label;
  $("welcome-version").textContent = status.plan.targetVersion;
  $("welcome-facts").hidden = false;
  $("operation-badge").textContent = label;
  $("operation-note").textContent = note;
  $("install-root").value = status.installRoot;
  $("desktop-shortcut").checked = status.defaults.desktopShortcut;
  $("fresh-note").hidden = status.plan.retainedData;
  $("retained-note").hidden = !status.plan.retainedData;
  $("completion-note-text").textContent = status.plan.operation === "install"
    ? "安装完成后可直接打开 ThemeScheduler；本次不会立即改变 Windows 外观。"
    : "完成后可打开 ThemeScheduler 检查状态；既有配置和当前 Windows 外观均保持不变。";
  renderFooter();
}

$("setup-form").addEventListener("submit", (event) => event.preventDefault());
$("cancel-button").addEventListener("click", closeSetupWindow);
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
    await beginInstall();
  } else if (state.page === "result") {
    if (state.installSucceeded) {
      await completeSetup(true);
    } else {
      await closeSetupWindow();
    }
  }
});
$("finish-only-button").addEventListener("click", async () => {
  await completeSetup(false);
});
$("open-log-button").addEventListener("click", async () => {
  $("result-error").textContent = "";
  const response = await window.pywebview.api.open_setup_log();
  if (!response.ok) $("result-error").textContent = response.error;
});

window.addEventListener("pywebviewready", initialize);
