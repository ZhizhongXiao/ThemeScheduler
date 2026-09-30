from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = PROJECT_ROOT / "ui"
HTML_ROOT = UI_ROOT / "html"
CSS_ROOT = UI_ROOT / "css"
JS_ROOT = UI_ROOT / "js"


class _FrontendParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.scripts: list[str] = []
        self.remote_urls: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(str(values["id"]))
        if tag == "script" and values.get("src"):
            self.scripts.append(str(values["src"]))
        for name in ("src", "href"):
            value = values.get(name)
            if value and value.startswith(("http://", "https://")):
                self.remote_urls.append(value)


class Stage12FrontendContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = (HTML_ROOT / "workbench.html").read_text(encoding="utf-8")
        self.app = (JS_ROOT / "workbench.js").read_text(encoding="utf-8")
        self.time_math = (JS_ROOT / "time_math.js").read_text(encoding="utf-8")
        self.time_dial = (JS_ROOT / "time_dial.js").read_text(encoding="utf-8")
        self.color_fields = (JS_ROOT / "color_fields.js").read_text(encoding="utf-8")
        self.api_contracts = (JS_ROOT / "api_contracts.js").read_text(encoding="utf-8")
        self.brand = (JS_ROOT / "brand.js").read_text(encoding="utf-8")
        self.tokens = (CSS_ROOT / "tokens.css").read_text(encoding="utf-8")
        self.styles = (CSS_ROOT / "workbench.css").read_text(encoding="utf-8")
        self.light_styles = (CSS_ROOT / "workbench-light.css").read_text(
            encoding="utf-8"
        )
        self.parser = _FrontendParser()
        self.parser.feed(self.html)

    def test_frontend_ids_are_unique_and_assets_are_local(self) -> None:
        self.assertEqual(
            len(self.parser.ids),
            len(set(self.parser.ids)),
        )
        self.assertEqual(self.parser.remote_urls, [])
        self.assertEqual(
            self.parser.scripts,
            [
                "../js/brand.js",
                "../js/api_contracts.js",
                "../js/time_math.js",
                "../js/time_dial.js",
                "../js/color_fields.js",
                "../js/workbench.js",
            ],
        )
        for reference in (
            "../css/tokens.css",
            "../css/workbench.css",
            "../css/workbench-light.css",
            *self.parser.scripts,
        ):
            self.assertTrue(
                (HTML_ROOT / reference).resolve().is_file(),
                reference,
            )
        embedded_font = UI_ROOT / "fonts" / "ThemeSchedulerSourceHanSans.otf"
        font_license = UI_ROOT / "fonts" / "OFL-1.1.txt"
        self.assertTrue(embedded_font.is_file())
        self.assertLess(embedded_font.stat().st_size, 500_000)
        self.assertIn(
            "SIL OPEN FONT LICENSE Version 1.1",
            font_license.read_text(encoding="utf-8"),
        )
        self.assertIn("@font-face", self.tokens)
        self.assertIn(
            'url("../fonts/ThemeSchedulerSourceHanSans.otf?v=20260910")',
            self.tokens,
        )
        self.assertIn('"ThemeScheduler Source Han Sans"', self.tokens)
        self.assertNotIn("@font-face", self.styles)

    def test_compact_navigation_and_split_plan_views_are_present(self) -> None:
        for name in ("plan", "maintenance", "debug"):
            self.assertIn(f'id="tab-{name}"', self.html)
            self.assertIn(f'id="page-{name}"', self.html)
        self.assertNotIn('id="tab-appearance"', self.html)
        self.assertNotIn('id="page-appearance"', self.html)
        self.assertIn('class="activity-rail"', self.html)
        self.assertIn('id="appearance-settings"', self.html)
        self.assertLess(
            self.html.index('id="page-plan"'),
            self.html.index('id="appearance-settings"'),
        )
        self.assertLess(
            self.html.index('id="appearance-settings"'),
            self.html.index('id="page-maintenance"'),
        )
        self.assertIn('id="interactive-time-dial"', self.html)
        self.assertIn('id="schedule-summary-dial"', self.html)
        self.assertIn('id="plan-overview-view"', self.html)
        self.assertIn('id="plan-settings-view"', self.html)
        self.assertIn('id="open-settings-button"', self.html)
        self.assertIn('id="back-to-overview-button"', self.html)
        self.assertIn('step="300"', self.html)

    def test_rail_brand_is_not_a_control_and_theme_icon_shows_current_mode(
        self,
    ) -> None:
        self.assertIn(
            'class="rail-brand" role="img" aria-label="ThemeScheduler 标志"',
            self.html,
        )
        self.assertIn("data-theme-scheduler-brand", self.html)
        self.assertIn('class="brand-night"', self.brand)
        self.assertEqual(self.html.count('<circle class="brand-face"'), 0)
        for page in ("setup.html", "uninstall.html"):
            page_html = (HTML_ROOT / page).read_text(encoding="utf-8")
            self.assertIn("data-theme-scheduler-brand", page_html)
            self.assertIn("../js/brand.js", page_html)
            self.assertNotIn("<circle class=", page_html)
        self.assertNotIn('class="rail-brand" aria-hidden="true">TS</div>', self.html)
        self.assertIn(
            '.ui-theme-toggle[data-effective-theme="light"] [data-theme-icon="light"]',
            self.styles,
        )
        self.assertIn(
            '.ui-theme-toggle[data-effective-theme="dark"] [data-theme-icon="dark"]',
            self.styles,
        )
        self.assertIn('class="overview-layout"', self.html)
        self.assertIn('class="settings-workbench"', self.html)
        self.assertIn('class="command-bar"', self.html)
        self.assertNotIn('id="boundary-toggle-button"', self.html)
        self.assertIn('id="boundary-tab-day"', self.html)
        self.assertIn('id="boundary-tab-night"', self.html)
        self.assertEqual(self.html.count('id="restore-install-button"'), 1)
        self.assertLess(
            self.html.index('id="restore-install-button"'),
            self.html.index('id="page-maintenance"'),
        )
        self.assertIn(
            'completedBoundary === "day" ? "night" : "day"',
            self.app,
        )

    def test_boundary_tabs_and_inverse_dial_support_focused_editing(self) -> None:
        self.assertEqual(self.html.count('data-boundary-tab="'), 2)
        self.assertEqual(self.html.count('data-boundary-panel="'), 2)
        self.assertIn('addEventListener("dialdragend"', self.app)
        self.assertIn("state.adjustedHands.add(event.detail.selectedHand)", self.app)
        self.assertIn("if (state.adjustedHands.size !== 2) return;", self.app)
        self.assertIn('data-dial-period="day"', self.html)
        self.assertIn(
            '.settings-dial-panel[data-dial-period="day"] .clock-face',
            self.styles,
        )
        self.assertIn(
            '.settings-dial-panel[data-dial-period="night"] .clock-face',
            self.styles,
        )

    def test_operation_gui_follows_windows_with_named_light_and_dark_palettes(
        self,
    ) -> None:
        self.assertIn(
            '<meta name="color-scheme" content="light dark">',
            self.html,
        )
        self.assertIn(
            'media="(prefers-color-scheme: light)"',
            self.html,
        )
        self.assertIn('"Source Han Sans SC"', self.tokens)
        self.assertIn('id="ui-theme-toggle"', self.html)
        self.assertIn("initializeUiThemeToggle()", self.app)
        self.assertIn('stylesheet.media = "all"', self.app)
        self.assertIn('stylesheet.media = "not all"', self.app)
        self.assertIn("stylesheet.media = LIGHT_THEME_MEDIA", self.app)
        self.assertNotIn("document.styleSheets", self.app)
        self.assertNotIn("cssRules", self.app)
        self.assertIn('data-theme-icon="light"', self.html)
        self.assertIn('data-theme-icon="dark"', self.html)
        self.assertIn("button.dataset.effectiveTheme = effectiveTheme", self.app)
        self.assertNotIn('icon.textContent = "↻"', self.app)
        self.assertNotIn("localStorage", self.app)
        # NameToolsSuite daylight foundation.
        for token in (
            "background: #f0f4ff",
            "--surface: #ffffff",
            "--surface-alt: #f8fafc",
            "--surface-report: rgba(255, 255, 255, 0.9)",
            "--surface-command: rgba(255, 255, 255, 0.94)",
            "--surface-section: #f8fafc",
            "--line: #cbd5e1",
            "--text: #0f172a",
            "--text-secondary: #334155",
            "--muted: #64748b",
            "--purple: #1e40af",
            "--purple-bright: #3b82f6",
            "h1, h2 { color: #1e40af; }",
            "background: linear-gradient(135deg, #3b82f6, #1e40af)",
            "background: #3b82f6",
            "box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05)",
        ):
            self.assertIn(token, self.light_styles)
        # Dracula night foundation.
        for token in (
            "background: #282a36",
            "--surface-alt: #44475a",
            "--surface-report: rgba(40, 42, 54, 0.88)",
            "--surface-command: rgba(40, 42, 54, 0.93)",
            "--surface-section: rgba(33, 34, 44, 0.5)",
            "--text: #f8f8f2",
            "--night-arc: #6272a4",
            "--purple: var(--ts-brand)",
            "--green: var(--ts-success)",
            "--red: var(--ts-danger)",
            "--yellow: var(--ts-warning)",
        ):
            self.assertIn(token, self.styles)
        for token in (
            "--ts-purple: #bd93f9",
            "--ts-green: #50fa7b",
            "--ts-red: #ff5555",
            "--ts-yellow: #f1fa8c",
        ):
            self.assertIn(token, self.tokens)

    def test_schedule_overview_has_non_layout_zoom_paths(self) -> None:
        self.assertIn('id="expand-schedule-button"', self.html)
        self.assertIn('id="schedule-zoom-popover"', self.html)
        self.assertIn('popover="auto"', self.html)
        self.assertIn('popovertarget="schedule-zoom-popover"', self.html)
        self.assertIn('id="schedule-summary-dial-expanded"', self.html)
        self.assertIn(
            '$("#schedule-summary-dial").addEventListener("dblclick", '
            "openScheduleZoom)",
            self.app,
        )
        self.assertIn(
            '$("#schedule-summary-dial-expanded").addEventListener(',
            self.app,
        )
        self.assertIn("state.expandedSummaryDial?.setTimes(", self.app)
        self.assertIn(".schedule-zoom-popover", self.styles)
        self.assertIn("position: sticky", self.styles)
        self.assertNotIn("window.open(", self.app)

    def test_stage13_surfaces_have_explicit_light_theme_coverage(self) -> None:
        light_theme = self.light_styles
        for selector in (
            ".activity-rail {",
            ".rail-brand {",
            ".tab-button.is-active {",
            ".nav-tooltip {",
            ".icon-button {",
            ".panel {",
            ".schedule-zoom-popover {",
            ".toast {",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, light_theme)
        self.assertIn(
            'input[type="time"], input[type="text"], input[type="number"], select,',
            light_theme,
        )
        # The approved workbench prototype is integrated later. If one of its
        # theme-sensitive surfaces appears, it must either consume a shared
        # surface token or receive an explicit light-theme rule in this block.
        for selector in (
            ".status-strip",
            ".workbench",
            ".inspector",
            ".appearance-rack",
            ".command-bar",
        ):
            surface_rule = re.search(
                rf"{re.escape(selector)}\s*\{{(?P<body>.*?)\}}",
                self.styles,
                flags=re.DOTALL,
            )
            if surface_rule is None:
                continue
            uses_surface_token = "background: var(--surface" in surface_rule.group(
                "body",
            )
            has_explicit_light_rule = f"{selector} {{" in light_theme
            with self.subTest(future_surface=selector):
                self.assertTrue(uses_surface_token or has_explicit_light_rule)

    def test_api_boundary_has_runtime_contract_validation(self) -> None:
        self.assertIn("class ApiContractError", self.api_contracts)
        self.assertIn("validateOverview", self.api_contracts)
        self.assertIn('["dayStart", "string"]', self.api_contracts)
        self.assertIn(
            "apiContracts.validate(action, await work())",
            self.app,
        )
        self.assertIn(
            'apiContracts.validate(\n      "validateWorkspace"',
            self.app,
        )

    def test_direct_id_selectors_in_app_exist_in_html(self) -> None:
        referenced = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', self.app))
        missing = referenced - set(self.parser.ids)
        self.assertEqual(missing, set())

    def test_time_components_do_not_access_backend_or_windows(self) -> None:
        combined = self.time_math + self.time_dial + self.color_fields
        for forbidden in (
            "pywebview",
            "fetch(",
            "XMLHttpRequest",
            "localStorage",
            "PowerShell",
            "registry",
        ):
            self.assertNotIn(forbidden, combined)

    def test_frozen_clock_semantics_are_explicit_in_source(self) -> None:
        self.assertIn("MINUTE_STEP = 5", self.time_math)
        self.assertIn("MINUTES_PER_DAY * 360", self.time_math)
        self.assertIn("applyHourDrag", self.time_math)
        self.assertIn("applyMinuteDrag", self.time_math)
        self.assertIn("index < 96", self.time_dial)
        self.assertIn("day-arc", self.time_dial)
        self.assertIn("night-arc", self.time_dial)
        self.assertIn("length = isDay ? 124 : 102", self.time_dial)

    def test_color_fields_use_hex_rgb_and_preview_without_picker(self) -> None:
        self.assertIn("fromHex", self.color_fields)
        self.assertIn("fromHexInput", self.color_fields)
        self.assertIn("fromChannels", self.color_fields)
        self.assertIn("0–255", self.color_fields)  # noqa: RUF001 - Preserve the displayed numeric range.
        self.assertIn("data-color-preview", self.html)
        self.assertNotIn("data-color-preview-value", self.html)
        self.assertNotIn("previewValue", self.color_fields)
        self.assertIn("color.hex.slice(1)", self.color_fields)
        self.assertNotIn('type="color"', self.html)
        self.assertNotIn("color-picker", self.html)
        self.assertEqual(self.html.count('class="hex-prefix"'), 2)
        self.assertIn('placeholder="RRGGBB"', self.html)

    def test_available_color_preview_is_not_covered_by_placeholder_pattern(
        self,
    ) -> None:
        available_rule = re.search(
            r"\.color-preview-panel\s*\{(?P<body>.*?)\}",
            self.styles,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(available_rule)
        assert available_rule is not None
        self.assertIn("background-image: none", available_rule.group("body"))
        unavailable_rule = re.search(
            r"\.color-preview-panel\.is-unavailable\s*\{(?P<body>.*?)\}",
            self.styles,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(unavailable_rule)
        assert unavailable_rule is not None
        self.assertIn(
            "repeating-linear-gradient",
            unavailable_rule.group("body"),
        )

    def test_existing_backend_actions_remain_in_app_coordinator(self) -> None:
        for action in (
            "validate_workspace",
            "save_workspace",
            "read_current_windows_appearance",
            "restore_install_appearance",
            "reset_preferences",
            "launch_uninstaller",
        ):
            self.assertIn(f"window.pywebview.api.{action}", self.app)
        self.assertIn("window.themeSchedulerOpenHealth", self.app)

    def test_workspace_has_two_explicit_save_actions_without_sync_button(
        self,
    ) -> None:
        self.assertIn('id="save-only-button"', self.html)
        self.assertIn('id="workspace-save-result"', self.html)
        self.assertIn('id="save-apply-button"', self.html)
        self.assertIn("保存计划", self.html)
        self.assertIn("保存并应用当前时段", self.html)
        self.assertNotIn('id="sync-button"', self.html)
        self.assertIn("save_workspace_and_apply", self.app)
        self.assertNotIn("sync_now", self.app)
        self.assertIn("renderWorkspaceResult(result)", self.app)

    def test_current_windows_appearance_import_is_draft_only(self) -> None:
        self.assertEqual(
            self.html.count('data-import-appearance="'),
            2,
        )
        self.assertEqual(
            self.html.count('data-import-status="'),
            2,
        )
        self.assertNotIn("只读取为", self.html)
        self.assertIn('result?.result !== "success"', self.app)
        self.assertIn("state.draftColors[profile] = color", self.app)
        self.assertIn('accentSource", "string"', self.api_contracts)
        self.assertIn('sourcesDiverged", "boolean"', self.api_contracts)
        self.assertIn("当前外观来源", self.app)
        self.assertIn("活动主题与实时状态", self.app)
        self.assertIn('id="appearance-report"', self.html)
        self.assertIn(
            "readCurrentWindowsAppearance.themeAppearance", self.api_contracts
        )
        self.assertIn("尚未保存", self.app)
        self.assertIn("安全预览不会读取真实 Windows 外观", self.app)
        self.assertIn("status.hidden = false", self.app)
        self.assertIn('aria-disabled="true"', self.html)
        self.assertIn("is-safety-blocked", self.html)
        self.assertIn("import-button", self.html)
        self.assertNotIn(
            "save_workspace(result",
            self.app,
        )

    def test_meridiem_uses_one_animated_binary_toggle(self) -> None:
        self.assertIn('id="meridiem-toggle"', self.html)
        self.assertIn('role="switch"', self.html)
        self.assertNotIn('id="toggle-half-day"', self.html)
        self.assertNotIn('id="minus-half-day"', self.html)
        self.assertNotIn('id="plus-half-day"', self.html)
        self.assertEqual(
            self.app.count("shiftHalfDay(1)"),
            1,
        )

    def test_application_modes_use_animated_binary_toggles(self) -> None:
        self.assertIn('data-mode-toggle="apps" data-profile="day"', self.html)
        self.assertIn('data-mode-toggle="apps" data-profile="night"', self.html)
        self.assertIn('data-mode-toggle="system" data-profile="day"', self.html)
        self.assertIn('data-mode-toggle="system" data-profile="night"', self.html)
        self.assertIn('id="day-theme"', self.html)
        self.assertIn('id="night-theme"', self.html)
        self.assertIn('id="day-system-theme"', self.html)
        self.assertIn('id="night-system-theme"', self.html)
        self.assertIn("state.draft[`${profile}AppsTheme`]", self.app)

    def test_boundary_transition_toast_is_centered_and_short(self) -> None:
        self.assertIn("centered: true", self.app)
        self.assertIn("duration: 1100", self.app)
        self.assertIn("已切换至", self.app)
        self.assertIn("void toast.offsetWidth", self.app)
        self.assertIn("duration: 260", self.app)

    def test_workspace_result_is_inside_settings_detail(self) -> None:
        detail = self.html.index('class="settings-detail-panel"')
        result = self.html.index('id="workspace-result"')
        command = self.html.index('class="settings-command-area"')
        self.assertLess(detail, result)
        self.assertLess(result, command)

    def test_debug_details_are_collapsed_and_summary_avoids_paths(self) -> None:
        self.assertIn("<span>任务计划定义</span>", self.html)
        self.assertIn("<span>状态与昼夜 profile</span>", self.html)
        self.assertIn('id="debug-evidence-title"', self.html)
        self.assertIn('id="debug-schedule-evidence-title"', self.html)
        self.assertIn('id="debug-appearance-evidence-title"', self.html)
        self.assertIn('id="recent-log-report"', self.html)
        self.assertIn('id="copy-diagnostic-button"', self.html)
        self.assertIn(
            'class="button button-secondary compact" id="copy-diagnostic-button"',
            self.html,
        )
        for report_id in (
            "recent-log-report",
            "task-report",
            "state-profile-report",
            "appearance-report",
            "overview-report",
        ):
            self.assertIn(f'data-copy-report="{report_id}"', self.html)
        self.assertNotIn('data-copy-report="health-report"', self.html)
        self.assertIn('button.textContent = "已复制"', self.app)
        self.assertIn('await copyText(report.textContent || "")', self.app)
        self.assertIn("event.preventDefault();", self.app)
        summary_source = self.app[
            self.app.index("function diagnosticSummary") : self.app.index(
                "async function copyText"
            )
        ]
        self.assertNotIn("dataRoot", summary_source)
        self.assertNotIn("executable", summary_source)
        self.assertIn("核心状态：读取失败", summary_source)  # noqa: RUF001 - Match exact localized copy.
        self.assertIn('data.message || "后端未返回可用状态"', summary_source)
        self.assertIn("上次自动运行", summary_source)
        self.assertIn("未结束自动事务", summary_source)
        self.assertIn("themeAppearance?.colorizationColor", summary_source)

    def test_maintenance_actions_are_grouped_and_footer_is_page_local(self) -> None:
        plan = self.html[
            self.html.index('id="page-plan"') : self.html.index('id="page-maintenance"')
        ]
        maintenance = self.html[
            self.html.index('id="page-maintenance"') : self.html.index(
                'id="page-debug"'
            )
        ]
        debug = self.html[self.html.index('id="page-debug"') :]
        self.assertIn('id="maintenance-plan-title"', maintenance)
        self.assertIn('id="maintenance-data-title"', maintenance)
        self.assertIn('id="data-root"', maintenance)
        self.assertIn("关闭窗口后程序不会驻留后台", maintenance)
        self.assertNotIn('id="data-root"', plan + debug)
        self.assertNotIn("关闭窗口后程序不会驻留后台", plan + debug)
        self.assertNotIn("span-two", maintenance)
        self.assertIn('id="check-task-button"', maintenance)

    def test_responsive_workbench_and_non_clipped_hover_emphasis_are_defined(
        self,
    ) -> None:
        self.assertIn("@media (max-width: 900px), (max-height: 760px)", self.styles)
        self.assertIn("grid-template-rows: auto auto auto", self.styles)
        self.assertIn(".maintenance-groups", self.styles)
        self.assertIn(".debug-evidence-grid", self.styles)
        self.assertIn(".settings-entry-card:hover", self.styles)
        self.assertIn("transform: none", self.styles)

    def test_task_check_presents_its_inspection_and_feedback(self) -> None:
        self.assertIn('"checkTask",', self.app)
        self.assertIn('"任务计划存在差异"', self.app)
        self.assertIn(
            'setText("#task-report", JSON.stringify(result.inspection || result, null, 2))',
            self.app,
        )
        self.assertIn('"自动事务待检查"', self.app)

    def test_mutating_identity_repair_is_conditional_maintenance_action(self) -> None:
        maintenance = self.html[
            self.html.index('id="page-maintenance"') : self.html.index(
                'id="page-debug"'
            )
        ]
        debug = self.html[self.html.index('id="page-debug"') :]
        self.assertIn('id="repair-identity-button"', maintenance)
        self.assertNotIn('id="repair-identity-button"', debug)
        self.assertIn(
            'check.repairAction === "notification.identity-repair"',
            self.app,
        )
        self.assertIn("identityRepairButton.hidden = !identityCheck", self.app)

    def test_deferred_interaction_contracts_have_stable_placeholders(self) -> None:
        self.assertIn('data-interaction-contract="UI-013-01"', self.html)
        self.assertIn('data-feedback-contract="UI-013-02"', self.html)

    def test_confirmation_uses_centered_webview_dialog(self) -> None:
        self.assertIn('id="confirmation-dialog"', self.html)
        self.assertIn("requestConfirmation(", self.app)
        self.assertIn("dialog.showModal()", self.app)
        self.assertNotIn('!window.confirm("', self.app)
        self.assertIn("inset: 0;", self.styles)
        self.assertIn("margin: auto;", self.styles)

    def test_toast_uses_weighted_centered_chinese_feedback(self) -> None:
        self.assertIn('class="toast-message"', self.html)
        self.assertIn("const FEEDBACK_LABELS", self.app)
        self.assertIn("const IMPORTANT_FEEDBACK_ACTIONS", self.app)
        self.assertIn("failed || IMPORTANT_FEEDBACK_ACTIONS.has(action)", self.app)
        self.assertIn("options.centered !== false", self.app)
        self.assertNotIn('class="toast-kind"', self.html)
        self.assertIn("isError ? 2800 : 1200", self.app)
        self.assertIn('toast.classList.remove("is-visible");', self.app)
        self.assertNotIn(
            'toast.classList.remove("is-visible", "is-centered")',
            self.app,
        )
        self.assertNotIn("诊断摘要已复制；未包含完整路径或原始 JSON。", self.app)  # noqa: RUF001 - Match exact localized copy.
        self.assertNotIn('lang="en"', self.html)

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_time_math_deterministic_examples_in_javascript(self) -> None:
        completed = subprocess.run(
            [
                shutil.which("node") or "node",
                str(PROJECT_ROOT / "tests" / "ui" / "time_math.test.js"),
            ],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("stage12 time math: ok", completed.stdout)

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_color_field_examples_in_javascript(self) -> None:
        completed = subprocess.run(
            [
                shutil.which("node") or "node",
                str(PROJECT_ROOT / "tests" / "ui" / "color_fields.test.js"),
            ],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("stage12 color fields: ok", completed.stdout)

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_api_contract_examples_in_javascript(self) -> None:
        completed = subprocess.run(
            [
                shutil.which("node") or "node",
                str(PROJECT_ROOT / "tests" / "ui" / "api_contracts.test.js"),
            ],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("GUI API contracts: ok", completed.stdout)


if __name__ == "__main__":
    unittest.main()
