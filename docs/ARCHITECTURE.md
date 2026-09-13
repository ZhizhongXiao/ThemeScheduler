# ThemeScheduler 架构文档

版本：架构基线 v0.1  
适用范围：Windows 11 x64、当前用户安装、单用户个性化设置

## 1. 文档目的

本文描述 ThemeScheduler 的系统边界、组件职责、部署形态、数据边界和关键运行链路。具体业务规则见 [DESIGN.md](DESIGN.md)，当前维护事项见 [BACKLOG.md](BACKLOG.md)。

## 2. 系统目标

ThemeScheduler 是一个低干扰、低依赖、可安装、可配置、可恢复和可完整卸载的 Windows 11 个人自动化工具。它在固定时间切换 Windows 模式、应用模式、强调色及两个强调色显示位置。用户仍可在两个计划时点之间通过 Windows 设置临时调整个性化选项；下一次边界按已保存计划覆盖，不自动学习临时调整。

架构必须满足以下约束：

- 不部署常驻托盘进程，不实时监听注册表。
- 复用 Windows 任务计划程序，不安装第三方调度器。
- 客户端无需预装 Python。
- 程序文件与用户数据分离。
- 默认仅修改当前用户范围的设置。
- 不为执行主题切换而唤醒设备。
- 安装、升级、修复和卸载行为可解释、可验证、可回滚。

## 3. 非目标与系统边界

第一版不负责：

- 三个或更多时段以及日出、日落动态调度；
- 判断个性化设置由用户还是其他程序修改；
- 强制控制不支持 Windows 系统主题的第三方应用或网页；
- 直接从任意 RGB 值生成完整 Windows 强调色调色板；
- 云同步、多用户统一管理或企业策略管理；
- 绕过、关闭 Microsoft Defender 或 SmartScreen；
- 自动修改不稳定、未公开的 Windows 夜间模式内部接口。

Windows 设置仍是用户手动选择主题和强调色的正式入口。ThemeScheduler 只保存和恢复 Windows 已生成的设置结果。

## 4. 系统上下文

```text
用户
 ├─ 通过安装/配置 GUI 管理计划与程序
 └─ 通过 Windows 设置临时调整主题和强调色

Windows 任务计划程序
 └─ 在昼夜预提醒点、边界和可选延后点启动 ThemeScheduler.exe auto
          │
          ▼
ThemeScheduler 核心
 ├─ 读取配置、状态和强调色快照
 ├─ 判断当前逻辑时段
 ├─ 读写 HKCU 个性化设置
 ├─ 保存状态并写入日志
 └─ 在异常时尝试发送通知
```

调度器只决定“何时启动”，核心程序始终根据启动时的实际时间决定“应用什么”，从而覆盖休眠、关机和重复补运行场景。

## 5. 部署架构

### 5.1 分发与安装

用户获得一个单文件 GUI 安装器：

```text
ThemeScheduler-Setup.exe
```

当前正式发布按版本展开保存，分发内容与工程证据分离：

```text
artifacts/releases/<版本>/
├─ dist/                     # Setup、用户说明和分发校验和
├─ evidence/                 # 构建、测试、清单和展开载荷证据
├─ release-layout.json
└─ SHA256SUMS.txt            # 绑定整个正式发布
```

`artifacts/build/` 只承载可重建的候选和中间产物，不再保存正式发布。候选通过
发布门槛后才整体冻结到 `artifacts/releases/<版本>/`。被后续版本取代的正式
发布可以在逐文件哈希清单验证后迁入 `artifacts/cold-archive/`；冷归档 ZIP
内部保持原 `releases/<版本>/` 相对结构，当前版本始终保持展开。

安装后的主程序采用 PyInstaller `onedir`，独立卸载器使用不依赖主程序 `_internal` 的自包含无控制台形态，默认安装到：

```text
%LOCALAPPDATA%\Programs\ThemeScheduler\
├─ app\
│  ├─ ThemeScheduler.exe
│  └─ _internal\
│     ├─ Python 运行组件及依赖
│     ├─ GUI 本地前端资源
│     └─ Windows 桥接资源
├─ maintenance\
│  └─ Uninstall.exe
└─ metadata\
   ├─ installation.json
   └─ payload-manifest.json
```

`ThemeScheduler.exe` 是统一入口：无参数启动按需 GUI，`auto` 保持任务计划契约，`maintenance` 打开轻量维护区。GUI 依赖延迟加载，`auto` 不初始化 pywebview。使用 `onedir` 可以避免计划任务每次运行时解压到临时目录，并使启动路径、依赖位置和升级边界更稳定。单文件形态用于安装器分发和必须独立于主 `onedir` 损坏面的卸载器，不用于自动任务或日常 GUI。

GUI 使用 pywebview 的 Edge Chromium 后端和本地前端资源，不允许 MSHTML 回退或远程页面。安装器在启动 Web GUI 前使用原生 Windows 能力检测 WebView2 Runtime；缺失时，经用户确认和 Microsoft Authenticode 签名验证后运行随包提供的 Evergreen Bootstrapper，离线发布可附带 Standalone Installer。目标设备不需要 Python、pip、uv 或虚拟环境。

### 5.2 用户数据

用户数据独立存放：

```text
%LOCALAPPDATA%\ThemeScheduler\
├─ config.json
├─ state.json
├─ profiles\
│  ├─ day.json
│  └─ night.json
├─ runtime\
│  ├─ 当前强调色事务文件
│  └─ pending-switch.json
├─ backup\
│  ├─ install.json
│  └─ install.theme
├─ logs\
└─ WebView2\
   └─ pywebview 用户数据
```

`profiles/` 保存昼夜最小语义强调色；`runtime/` 保存动态生成的管理主题、当次完整主题备份、事务清单和受保护的一次性切换决定；`backup/` 保存首次安装恢复点，普通运行和升级不得覆盖；`logs/` 保存轮换日志。程序升级默认保留用户数据；卸载时由用户选择是否保留配置、昼夜颜色记录和日志。

## 6. 组件划分

当前源代码结构：

```text
ThemeScheduler/
├─ src/theme_scheduler/
│  ├─ __init__.py
│  ├─ core.py
│  ├─ persistence.py
│  ├─ config.py
│  ├─ state.py
│  ├─ backup.py
│  ├─ log_policy.py
│  ├─ runtime_retention.py
│  ├─ execution_lock.py
│  ├─ auto_transaction.py
│  ├─ appearance.py
│  ├─ accent_profile.py
│  ├─ accent_service.py
│  ├─ accent_theme.py
│  ├─ manual_appearance_service.py
│  ├─ storage.py
│  ├─ initial_setup.py
│  ├─ explorer_recovery.py
│  ├─ errors.py
│  ├─ cli/
│  │  ├─ __init__.py
│  │  ├─ app.py
│  │  ├─ auto.py
│  │  ├─ control.py
│  │  ├─ data.py
│  │  ├─ gui.py
│  │  ├─ maintenance.py
│  │  ├─ notification_action.py
│  │  ├─ preview.py
│  │  ├─ scheduler.py
│  │  ├─ system_integration.py
│  │  └─ uninstall.py
│  ├─ scheduler/
│  │  ├─ __init__.py
│  │  ├─ constants.py
│  │  ├─ errors.py
│  │  ├─ _validation.py
│  │  ├─ triggers.py
│  │  ├─ models.py
│  │  ├─ specification.py
│  │  ├─ inspection.py
│  │  └─ mutation.py
│  ├─ scheduler_windows.py
│  ├─ windows_subprocess.py
│  ├─ configuration_service.py
│  ├─ maintenance_service.py
│  ├─ control.py
│  ├─ control_service.py
│  ├─ notification_contracts.py
│  ├─ notification_dedup.py
│  ├─ notification_protocol.py
│  ├─ switch_override.py
│  ├─ scheduled_notifications.py
│  ├─ scheduled_auto.py
│  ├─ notification_action_service.py
│  ├─ gui_activation.py
│  ├─ notifications_windows.py
│  ├─ protocol_registration.py
│  ├─ protocol_registration_windows.py
│  ├─ notification_identity_service.py
│  ├─ windows_identity.py
│  ├─ health_contracts.py
│  ├─ health_service.py
│  ├─ diagnostics.py
│  ├─ resources.py
│  ├─ webview_runtime.py
│  ├─ lifecycle/
│  │  ├─ __init__.py
│  │  ├─ _validation.py
│  │  ├─ layout.py
│  │  ├─ payload.py
│  │  ├─ installation.py
│  │  ├─ transaction.py
│  │  └─ deployment/
│  │     ├─ __init__.py
│  │     ├─ _shared.py
│  │     ├─ journal.py
│  │     ├─ filesystem.py
│  │     └─ service.py
│  ├─ system_integration.py
│  ├─ system_integration_windows.py
│  ├─ system_integration_backup.py
│  ├─ setup_contracts.py
│  ├─ setup_data.py
│  ├─ setup_service.py
│  ├─ setup_windows.py
│  ├─ setup_gui_api.py
│  ├─ setup_gui.py
│  ├─ setup_preview.py
│  ├─ uninstall_contracts.py
│  ├─ uninstall_service.py
│  ├─ uninstall_windows.py
│  ├─ uninstall_gui_api.py
│  ├─ uninstall_gui.py
│  ├─ uninstall_preview.py
│  ├─ automation/
│  │  ├─ __init__.py
│  │  ├─ contracts.py
│  │  ├─ backend.py
│  │  ├─ outcome.py
│  │  ├─ recovery.py
│  │  ├─ failure.py
│  │  └─ runner.py
│  ├─ workbench/
│  │  ├─ __init__.py
│  │  ├─ contracts.py
│  │  ├─ shell.py
│  │  ├─ parsing.py
│  │  ├─ overview.py
│  │  ├─ configuration.py
│  │  ├─ maintenance.py
│  │  ├─ api.py
│  │  └─ factory.py
│  └─ gui.py
├─ ui/
│  ├─ html/
│  │  ├─ workbench.html
│  │  ├─ setup.html
│  │  └─ uninstall.html
│  ├─ css/
│  │  ├─ tokens.css
│  │  ├─ workbench.css
│  │  ├─ workbench-light.css
│  │  ├─ setup.css
│  │  ├─ uninstall.css
│  │  └─ wizard.css
│  ├─ js/
│  │  ├─ brand.js
│  │  ├─ api_contracts.js
│  │  ├─ wizard_runtime.js
│  │  ├─ workbench.js
│  │  ├─ setup.js
│  │  ├─ uninstall.js
│  │  ├─ time_math.js
│  │  ├─ time_dial.js
│  │  └─ color_fields.js
│  └─ fonts/       # 共享字体子集、许可与来源说明
├─ entrypoints/
│  ├─ setup_main.py
│  ├─ themescheduler_main.py
│  ├─ uninstall_main.py
│  ├─ theme_manager_bridge.ps1
│  ├─ current_appearance_bridge.ps1
│  ├─ task_scheduler_bridge.ps1
│  ├─ explorer_recovery_bridge.ps1
│  └─ 其他受管 Windows 桥接脚本
├─ assets/
├─ tests/
│  ├─ unit/        # 纯模型、解析和确定性服务
│  ├─ integration/ # 跨组件、GUI 契约和 Windows 适配器模拟
│  ├─ release/     # 打包、仓库结构和质量门禁
│  ├─ fixtures/    # 领域专用 Fake
│  ├─ ui/          # Node/DOM 测试资源
│  ├─ _support.py
│  └─ coverage_baseline.json
├─ build/
├─ pyproject.toml
├─ README.md
└─ .gitignore
```

职责如下：

| 组件 | 职责 |
| --- | --- |
| `errors.py` | 所有产品异常的共同根类，以及输入契约、数据可信度和 Windows 运行失败三类稳定边界 |
| `core.py` | 带时区时钟、时段判断、完整外观执行计划、结果和退出码契约 |
| `execution_lock.py` | 当前会话 Windows 命名互斥体及非阻塞单实例保护 |
| `auto_transaction.py` | 自动事务 v1、状态哈希绑定、严格转换和原子持久化 |
| `automation/backend.py`、`outcome.py` | Windows 自动切换适配器与稳定运行结果 |
| `automation/contracts.py`、`recovery.py`、`failure.py`、`runner.py` | Mixin 显式依赖、未完成事务恢复、失败收敛、幂等自动切换编排和结果汇总 |
| `cli/auto.py` | 阶段 4 只读计划及受确认保护的实机单次运行入口 |
| `persistence.py` | 通用 JSON 对象读取、带时区时间戳和原子替换；不得依赖诊断模块 |
| `config.py` | 配置读取、校验、迁移、默认值和原子保存 |
| `state.py` | 暂停状态、活动时段、执行结果和运行状态持久化 |
| `initial_setup.py` | 首次安装尚未完成配置的严格瞬态标记；自动链路据此阻止外观写入，首次有效保存后由工作台清除 |
| `backup.py` | 首次安装恢复点清单的严格结构和不可覆盖边界 |
| `log_policy.py` | v2 结构化日志、v1 兼容读取、验证/回滚状态、隐私边界、轮换和运行事务保留参数 |
| `runtime_retention.py` | 生成可检查的运行事务清理计划，并只删除受控目录内的明确目标 |
| `appearance.py` | 当前外观只读组合根：应用/系统模式与自动取色读注册表，强调色经公开 WinRT `UISettings` 读取；活动主题仅作分裂诊断；安装备份所需 DWM 读取保持独立，不提供写入 |
| `accent_profile.py` | 最小语义强调色配置的结构版本、严格校验和主题捕获转换 |
| `accent_service.py` | 强调色捕获、应用模式与强调色的复合主题事务、运行清单和结果汇总 |
| `accent_theme.py` | 动态主题副本、可选目标 `AppMode`、具名 V2 应用结果、视觉失败列表、`IThemeManager2` 索引回退及完整主题备份兜底；不再暴露 V1 Apply |
| `manual_appearance_service.py` | 以独立手动意图运行当前时段外观事务；允许暂停时显式应用且不改变暂停状态 |
| `storage.py` | `profiles/`、`runtime/`、`backup/`、`logs/` 用途目录和路径约束 |
| `explorer_recovery.py` | 当前会话 Explorer 显式故障恢复适配器；不得进入自动链路 |
| `cli/data.py` | 阶段 3 离线初始化、严格验证和日志冒烟入口；不得修改 Windows |
| `cli/maintenance.py` | 安装恢复点只读检查、受确认保护的首次捕获及安装前外观恢复入口 |
| `scheduler/triggers.py`、`models.py` | 单任务 v2 的触发器、动作、主体、设置与规范化任务模型 |
| `scheduler/specification.py`、`inspection.py` | 四个固定触发器、可选延后触发对、冻结规则、字段级比较与检查 |
| `scheduler/mutation.py` | 唯一任务后端协议、完整定义备份、创建/修复/启停/删除及失败恢复 |
| `scheduler_windows.py` | 隔离 Task Scheduler 2.0 COM 桥接适配、规范化读取和完整 XML 备份恢复 |
| `windows_subprocess.py` | PowerShell 等 Windows 辅助进程的共享无窗口启动参数，防止 GUI 和自动链路闪出控制台 |
| `cli/scheduler.py` | 离线任务计划、只读检查及受确认保护的创建/修复、启停、立即运行和删除入口 |
| `configuration_service.py` | 使用自动核心同一互斥锁，组合严格配置保存、任务完整更新、待处理延迟任务/令牌取消、读回验证、日志和双侧失败回滚 |
| `maintenance_service.py` | 使用同一执行锁和未完成事务防护，编排暂停先行、可信安装恢复点加载、安装前外观恢复、读回验证与维护日志 |
| `control.py` | 暂停/恢复的纯状态转换、结果和退出码契约 |
| `control_service.py` | 共享执行锁、未完成事务防护、可信状态提交和控制日志编排 |
| `cli/control.py` | 开发期只读状态与受确认暂停/恢复入口；不属于正式安装态命令路由 |
| `notification_contracts.py` | 固定 AUMID、隐私有界通知请求和不改变核心结果的投递结果 |
| `notification_dedup.py` | 错误事件/结果键的严格六小时去重缓存；仅在真实投递成功后记账，属于可删除运行时数据 |
| `notification_protocol.py` | 生成和严格解析通知动作 URI；切换按钮只接受固定端点、动作及 128 位一次性令牌，错误通知正文只接受无参数 `health/` 入口 |
| `switch_override.py` | 一次性切换决定、五分钟预提醒窗口、重复延后、固定边界收束和严格持久化 |
| `scheduled_notifications.py` | 将受保护的一次性覆盖状态转换为三按钮预通知，并生成非交互成功通知 |
| `scheduled_auto.py` | 在共享执行锁内协调固定/延迟预提醒、跳过、延迟边界收束和核心自动事务；核心锁释放后等待 5 秒发送成功通知 |
| `notification_action_service.py` | 严格消费安全 URI 与一次性令牌；确认/跳过只改决定，延迟事务式替换一次性触发对，不直接切换主题 |
| `cli/notification_action.py` | 无标准流的安装态 URI 协议入口；切换动作保持无 GUI，固定 `health/` 动作只打开或聚焦维护区并触发只读检查 |
| `gui_activation.py` | 使用当前会话命名事件唤醒已打开的按需 GUI；不建立常驻服务、磁盘队列或轮询进程 |
| `notifications_windows.py` | 隔离调用系统内置 WinRT PowerShell 桥，隐藏辅助进程并将投递失败降级为结果 |
| `protocol_registration.py` / `protocol_registration_windows.py` | 固定协议命令、注册表树捕获、严格读取、写入、删除与精确恢复 |
| `windows_identity.py` | 设置、读回并只读检查正式进程的显式 AppUserModelID |
| `notification_identity_service.py` | 显式修复开始菜单快捷方式、已有桌面快捷方式及安全 URI 协议；失败时按原始字节和注册表树回滚 |
| `health_contracts.py` | 健康检查四级状态、稳定检查 ID 和显式修复动作 |
| `health_service.py` | 11 项配置、数据、载荷、权限、任务、Windows 与通知能力检查；除一次性权限探针外只检不修 |
| `diagnostics.py` | 只读运行环境与 Windows 版本信息；不再包含主题注册表快照或离线差异原型 |
| `cli/app.py` | 正式安装态统一入口；延迟分派无参数 GUI、`auto` 和 `maintenance`，自动分支不得导入 GUI |
| `cli/gui.py` | 当前无包内调用者的源码态开发/验收启动入口；支持显式数据根、只读系统导入和受控实机写入，不属于正式安装态主路由 |
| `resources.py` | 源码树与 PyInstaller `_MEIPASS` 的统一只读资源定位 |
| `webview_runtime.py` | `gui.py`、`setup_gui.py`、`uninstall_gui.py` 共用的 WebView2 检测和原生错误提示；属于生产必需共享运行时 |
| `lifecycle/layout.py` | 程序根、数据根和受管目标路径约束 |
| `lifecycle/payload.py` | payload 文件、版本化清单、捕获和精确树验证 |
| `lifecycle/installation.py` | 安装记录、记录存储和当前用户安装登记 |
| `lifecycle/transaction.py` | 生命周期事务状态、不可变字段和严格转换 |
| `lifecycle/_validation.py` | 上述契约共享的结构版本、时间、版本、哈希和安全路径校验 |
| `lifecycle/deployment/journal.py`、`filesystem.py` | 部署事实日志、受控文件系统与精确旧树证据 |
| `lifecycle/deployment/service.py` | 载荷暂存复验、逐目录切换、中断恢复、延迟完成、精确回滚和损坏重装 |
| `system_integration.py` | 当前用户安装登记、通知身份、开始菜单/可选桌面快捷方式与单任务 v2 定义的统一事务、读回验证和逆序回滚 |
| `system_integration_windows.py` | HKCU 登记、当前用户已知文件夹、WSH `.lnk` 及无窗口 PowerShell 桥接适配器 |
| `system_integration_backup.py` | 实机验收前登记全值、快捷方式原始字节及任务完整 XML 的严格快照和逐项恢复 |
| `cli/system_integration.py` | 阶段 8.3 只读预检、不可覆盖快照，以及受显式确认保护的应用/恢复入口 |
| `setup_contracts.py` | Setup 白名单输入、首次安装/重装/升级分类、降级和不可信旧树拒绝 |
| `setup_data.py` | 首次安装数据初始化、完整保留数据严格验证和仅撤销本次新建数据 |
| `setup_service.py` | 两阶段程序部署、系统集成、自检及失败逆序回滚的安装协调器 |
| `setup_windows.py` | 当前用户已知目录、HKCU、任务、进程和双互斥的 Windows Setup 组合根 |
| `setup_gui_api.py`、`setup_gui.py` | Setup 最小白名单 pywebview API、唯一后台会话、真实阶段轮询、关闭拦截、固定日志入口、WebView2 前置检测与单窗口启动 |
| `cli/preview.py`、`setup_preview.py` | 统一源码安全预览入口及不调用 Windows 安装适配器的顶层 Setup 预览适配器；演示操作与结果分支，日志仅写显式开发目录 |
| `ui/html/setup.html`、`ui/css/setup.css`、`ui/js/setup.js` | 随单文件 Setup 嵌入的本地结构、样式和交互；安装根只读，不加载远程页面 |
| `uninstall_contracts.py` | 独立卸载请求、保留选项、固定清理矩阵和可恢复卸载事实日志的严格模型 |
| `uninstall_service.py` | 取得生命周期/自动锁后，按任务、外观、快捷方式、登记、程序根、数据和自清理的固定顺序编排卸载 |
| `uninstall_windows.py` | 精确进程识别、临时副本、固定 PowerShell 自清理及 Windows 卸载适配 |
| `cli/uninstall.py` | 独立卸载器的延迟 GUI 路由、只读检查、一次性授权请求验证和临时执行入口；原生选择器只保留给注入式兼容测试 |
| `uninstall_gui_api.py`、`uninstall_gui.py` | 三选项经典卸载向导的最小白名单 API、唯一后台会话、关闭拦截、安装态到临时副本的交接与 WebView2 单窗口启动 |
| `cli/preview.py`、`uninstall_preview.py` | 统一源码安全预览入口及不暂存卸载器、不删除文件也不写 Windows 的顶层卸载预览适配器；可演示完成和部分失败结果 |
| `ui/html/uninstall.html`、`ui/css/uninstall.css`、`ui/js/uninstall.js` | 欢迎、集中选项、自然语言确认、真实进度和结果页；复用共享向导令牌且不加载远程页面 |
| `workbench/contracts.py` | pywebview 注入依赖的具名 Protocol、传给 JavaScript 的稳定 TypedDict 和内部摘要数据类 |
| `workbench/parsing.py`、`overview.py`、`configuration.py`、`maintenance.py` | pywebview 严格输入、分段只读摘要、受确认配置与维护用例 |
| `workbench/shell.py`、`api.py`、`factory.py` | 固定 Shell 目标、唯一 JS API 对象和 Windows 生产装配根 |
| `gui.py` | 单窗口、三标签工作外壳；通过前置检查后才延迟导入 Workbench 并启动 Edge Chromium，不复制核心逻辑 |
| `ui/html/`、`ui/css/`、`ui/js/` | 三套本地界面按结构、样式和交互分层；工作台的计划页包含同窗口概览/设置视图、时间轮盘和颜色输入，所有界面只调用白名单 API，不加载远程页面 |
| `ui/css/tokens.css`、`ui/js/brand.js`、`ui/fonts/` | 三套界面共用字体、品牌语义色和唯一品牌 SVG；字体子集、许可和来源说明随本地资源交付 |
| `ui/css/wizard.css`、`ui/js/wizard_runtime.js` | Setup/Uninstall 共用向导布局、组件样式、DOM 摘要和五页导航；业务编排仍由各页面脚本拥有 |
| `ui/js/api_contracts.js` | JavaScript 侧 pywebview 响应运行时契约；在字段进入页面状态前拒绝缺失或类型漂移 |

`theme_scheduler.cli` 是统一的进程入口适配层：模块名使用命令名，不再重复
`_cli` 后缀。它只负责参数、标准流、退出码和延迟分派，并向内调用服务、聚焦
子包或 Windows 组合根。`entrypoints/` 仍只是 PyInstaller 的极薄启动脚本；核心
业务规则不得反向依赖 `cli`。依赖方向固定为：

```text
entrypoints → cli → service / 聚焦子包 → contracts / core
```

`*_service.py` 表示用例、事务和回滚编排，`*_windows.py` 表示 Windows 适配器，
`*_contracts.py` 表示稳定数据契约。`setup_preview.py` 和
`uninstall_preview.py` 是顶层无写入 GUI 预览适配器，不是 CLI，也不进入通用
`lifecycle/` 契约包。本次整理延续 M15 的聚焦子包边界，不重新按技术层全面拆包。

阶段 12 的目标前端结构保持单向数据流：

```text
后端只读 overview
→ 前端规范化为页面草稿
→ 概览进入设置视图；昼夜标签、表盘和颜色字段只更新草稿
→ 前端白名单 API 严格校验提交
→ 组合配置/profile 服务取得自动互斥
→ 配置—任务事务读回
→ 返回分阶段结构化结果
```

12 小时交互表盘和 24 小时总览只负责 `HH:mm ↔ 角度`、拖动状态和 SVG 呈现；前端只记录一次边界会话中两根指针是否完成有效拖动，用于自动切换编辑标签。昼夜归属、跨午夜计算、主题应用、任务更新和持久化仍由现有 Python 业务层负责。前端不得根据环段自行决定当前目标 profile。

操作 GUI 的视觉主题完全留在前端：`workbench.css` 提供 Dracula 深色基础，
`workbench-light.css` 是明确命名的 NameToolsSuite 风格浅色覆盖，并由 HTML
样式表链接的 `prefers-color-scheme: light` 条件跟随 Windows 默认应用模式。
手动按钮只在内存中改变这张已知样式表链接的 `media` 条件，第二次点击恢复系统
条件；JavaScript 不再遍历或改写 CSSOM 规则。该状态不进入后端白名单 API、
`config.json`、profile、任务、Web 存储或自动核心，因而不会产生第二套可漂移的
业务主题状态。

中文 GUI 字体同样作为本地前端资源交付：唯一 `tokens.css` 通过 `@font-face`
加载从 Source Han Sans SC 生成的界面字符子集，子集使用项目专用字体族名并随附
SIL OFL 许可。这样不依赖目标机器字体安装或注册；若资源加载异常，仍按 Noto
CJK、微软雅黑和 Segoe UI 顺序回退。完整约 16.5 MB 字体不进入载荷；前端文案
变化后由 `tools/build_gui_font.py` 扫描工作 GUI、Setup、共享生命周期前端及
Uninstall 并重建子集。

Setup 的 pywebview 白名单 API 将确认后的安装请求转为单一后台会话；前端只轮询会话标识对应的阶段、结果和日志可用性。`SetupService` 在原事务边界报告准备数据、部署、系统集成、验证、提交、同步或回滚阶段，报告器异常被隔离，不能改变安装结果。后台会话运行时，页面关闭 API和 pywebview 原生 `closing` 事件读取同一状态并拒绝退出；结束与日志原子落盘后才开放关闭。

`diagnostics.py` 是验证辅助组件，不属于自动切换链路。它不提供注册表写入、任务计划修改或自动恢复接口；候选观察范围不能替代阶段 2 冻结后的正式强调色模型。

## 7. 关键运行链路

### 7.1 自动切换

```text
计划触发或错过后补运行
→ ThemeScheduler.exe auto
→ 取得当前会话单实例锁
→ 加载并校验配置、状态和目标强调色配置；不可信则停止
→ paused=true 时安全退出
→ 按当前时间计算目标时段
→ 同一可信目标时段直接 no-change
→ 读取用户显式保存的完整目标外观，不捕获或覆盖离开时段
→ 快照四项注册表状态，并在管理主题中补丁目标 AppMode、SystemMode 和语义强调色
→ 写入开始菜单/任务栏及标题栏/窗口边框两个显示位置
→ 通过隔离桥接调用 IThemeManager2 注册并选择管理主题
→ 验证主题颜色、两种模式、两个显示位置和桥接转换结果
→ 记录 windows-verified
→ 原子更新状态
→ 记录 state-committed，写入日志并完成事务
```

重复运行必须是幂等的。同一可信目标时段内多次补运行直接跳过 Windows 写入，不得造成反向切换或破坏已保存计划。状态损坏时禁止自动系统修改；进程在 Windows 验证与状态提交之间中断时，下一次运行必须依据自动事务清单和运行前状态哈希恢复，不能直接重跑。

### 7.2 配置更新

```text
GUI 输入
→ 校验时间、昼夜两种模式、强调色和两个显示位置
→ 原子保存 config.json
→ 更新任务触发器
→ 读回验证任务计划
→ 向用户显示完整结果
```

配置保存和任务更新构成一个业务事务。阶段 7 已实现后端无关的 `ConfigurationService`：进入事务前取得与 `auto` 相同的命名互斥并拒绝未完成自动事务；先严格构建目标任务，再保存配置、完整协调任务并读回验证。任务失败时恢复旧配置，任务组件恢复其完整旧定义；任一侧无法确认恢复即返回部分失败，不显示成功。

### 7.3 用户控制

```text
暂停/恢复
→ 取得与 auto 相同的当前会话锁
→ 拒绝未完成自动事务或不可信状态
→ 只更新 paused 并读回验证
→ 写入结构化控制日志

```

恢复只解除暂停，不调用自动核心。计划任务在暂停期间保持启用，因此暂停不引入任务定义漂移。概览页不提供通用立即同步；设置页允许在计划成功保存后显式应用当前时段。完整契约见 [CONTROLS.md](CONTROLS.md)。

### 7.4 安装、升级与卸载

Setup 在导入 pywebview 前以原生能力检查 WebView2，并先验证内置 payload 的精确文件树。六项核心数据全部不存在时，安装器只创建安全默认配置、当前外观派生的两份颜色记录、`paused=true` 状态和独立 `initial-setup.json` 临时标记；完整昼夜外观由安装完成后的主 GUI 统一编辑，第一次成功保存才解除暂停并删除标记，且不立即同步 Windows。首次设置标记存在时，主 GUI 隐藏暂停和恢复这组运行控制，只提供“保存并启用”。自动入口和普通恢复在标记存在时均拒绝继续；标记损坏按不可信数据处理。完整保留数据必须逐项严格加载，残缺数据停止。安装在首次修改系统设置前捕获真实个性化状态：schema v2 精确保存 `AppsUseLightTheme`、`SystemUsesLightTheme` 和两处 `ColorPrevalence` 的存在性、类型和值，强调色以 DWM 当前语义色为权威；活动 `.theme` 提供主题身份、自动取色和事务恢复副本。升级和损坏重装先把新载荷解包到程序根内的独立暂存目录，逐文件验证后以目录交换激活；不得覆盖旧 `app` 形成新旧混装。

文件切换采用两阶段提交：活动载荷验证后保持 `committed` 和旧 `.rollback`，再创建/更新登记、快捷方式及任务并完成自检；全部成功才清理旧树。后置失败时先恢复系统集成，再恢复旧程序树，最后只删除本次新建的数据。Setup 不提供立即同步；完成页以“完成并打开 ThemeScheduler”为主操作、“退出”为次操作。启动前必须已有成功且验证通过的安装结果；无参数打开已安装主 GUI，失败时保留结果页并显示内联错误。

卸载器不依赖主程序 `_internal`：安装目录中的自包含 GUI 先集中取得三个选择并生成受令牌、哈希、时限和固定根约束的请求，再复制到严格限定的临时工作区。安装态窗口退出释放文件锁后，临时副本使用同一向导视觉显示真实进度和结果；它阻止后续任务运行，按用户选择恢复 ThemeScheduler 接管前的完整外观，最后清理程序、登记、脚本、缓存和选定的用户数据。旧 schema v1 恢复点保持其原有范围，schema v2 恢复点还原两种模式和两个显示位置；用户关闭完成页后，无窗口脚本清理临时副本。完整契约见 [INSTALLATION.md](INSTALLATION.md)。

## 8. 数据架构

| 数据 | 所有者 | 主要内容 | 生命周期 |
| --- | --- | --- | --- |
| `config.json` | 配置组件 | 日夜时间、昼夜完整外观、通知偏好、结构版本 | 安装后长期保留；v1 只读兼容，用户确认后写为 v2 |
| `state.json` | 状态组件 | 暂停、活动时段、上次运行和结果 | 运行时持续更新，可重建部分字段 |
| `profiles/day.json`、`profiles/night.json` | 强调色组件 | 用户显式保存的昼夜最小语义强调色，不保存完整旧主题 | 首装初始化或 GUI 编辑，升级时保留；自动边界只读 |
| `runtime/` | 强调色与通知编排组件 | 当次源主题备份、管理主题、事务清单及 `pending-switch.json` | 运行时生成；主题事务按保留策略清理，一次性决定在消费、过期或固定边界取代后删除 |
| `backup/install.json`、`backup/install.theme` | 安装/卸载组件 | 安装前完整外观注册表快照、语义强调色和规范化主题恢复副本 | 首次安装生成，恢复或卸载时使用；v1/v2 兼容读取 |
| `logs/events.jsonl` | 日志组件 | 固定字段的 JSON Lines 事件 | 1 MiB 轮换，保留 5 个历史文件 |

所有持久化 JSON 都应包含 `kind` 和结构版本，并拒绝未知字段及未知版本；写入采用临时文件、刷盘和同目录替换的原子流程。主题文件先写入并刷盘，引用它的清单最后提交，清单保存 SHA-256。敏感运行中断不得把半写文件认作有效数据。通用读写只由 `persistence.py` 提供，`diagnostics.py` 不再拥有正式持久化职责。强调色数据由 `accent_profile.py`、`accent_service.py` 与 `accent_theme.py` 共同管理；经典六字段历史诊断原型已删除，其他组件只能通过公开用例访问。

## 9. 外部集成边界

### 9.1 Windows 注册表与 Shell

外观操作仅面向当前用户的个性化设置。正式自动与显式手动应用路径都在管理主题中组合目标 `AppMode`、`SystemMode` 与语义强调色，并通过 Personalize/DWM 两处 `ColorPrevalence` 控制强调色显示位置。四项注册表值在写入前精确快照并纳入同一回滚结果。阶段 1 的通用直接注册表主题原型与经典六颜色字段原型仍已退役；0.1.5 只为微软公开语义明确的两个显示开关提供受控写入，不把它们作为颜色本身。

强调色主路径在隔离的 Windows PowerShell/.NET 进程中调用未公开的 `IThemeManager2.AddAndSelectTheme`。桥接只暴露 `CurrentV2`、`ApplyV2` 和 `SetV2`，已退役无正式调用方的 V1 Apply 与 Probe 动作。调用使用忽略背景、光标、桌面图标、声音和屏保的应用标志，主进程设置超时并记录调用前主题索引；失败时优先通过 `SetCurrentTheme` 回到原索引并验证视觉状态，未恢复时再应用完整主题备份。程序每次从当前活动主题创建产品管理副本；0.1.5 自动入口设置目标 `AppMode`、`SystemMode`、语义强调色和显示位置，但不得原地覆盖用户主题。

重启 Windows Explorer 不是正常刷新步骤。只有主路径失败或用户人工确认界面未同步时，维护界面才可提供当前用户会话内的显式恢复动作；不得由计划任务静默执行，不得以进程名无条件强制终止所有 Explorer 进程。

### 9.2 Windows 任务计划程序

第一版固定使用根任务 `\ThemeScheduler`。阶段 9 的 v2 定义固定包含 `DayPrepare`、`DayBoundary`、`NightPrepare` 和 `NightBoundary` 四个每日触发器；用户延迟本次切换时，临时附加成对的 `DeferredPrepare`、`DeferredBoundary` 一次性触发器。计划具备以下语义：

- 固定的昼夜预提醒和边界触发器，以及由可信一次性覆盖状态派生的可选延后触发对；
- 错过计划时间后尽快运行；
- 不唤醒计算机；
- 所有触发器都调用同一个 `auto` 入口；
- 当前用户交互式上下文、最低权限和绝对安装路径明确可查；
- 多实例策略为 `IgnoreNew`，允许电池供电运行，不依赖网络；
- 任务定义整体注册和读回验证，改时不得追加或遗留旧触发器。

正式 Windows 适配器通过隔离 PowerShell 进程调用 Task Scheduler 2.0 COM API；本地化的 `schtasks.exe` 文本不作为机器读取源。任务检查使用版本化规范模型报告字段级漂移，创建、更新、修复和删除均须幂等；失败时恢复原完整定义。任务、主题管理和 Explorer 维护桥接均通过共享适配器设置 `CREATE_NO_WINDOW` 与隐藏启动信息，GUI 首次概况读取和后续控件调用不得闪出 PowerShell/CMD。详细契约见 [SCHEDULER.md](SCHEDULER.md)。

任务计划调用的正式可执行文件必须使用 Windows GUI 子系统构建，正常自动运行不得弹出控制台窗口。窗口隐藏属于程序构建属性，不通过任务的 `Hidden` 设置或隐藏命令行实现；任务定义本身保持可见、可检查。

任务后端当前为每个操作启动一个隐藏的隔离 PowerShell 进程。1.0 不预设批量桥接
一定更好：先测量冷启动、单次读取、连续读取和完整健康检查，将进程启动与 COM 操作
耗时分开。只有达到 [ROADMAP_1.0.md](ROADMAP_1.0.md) 的阈值，才增加批量只读动作；
写操作、完整 XML 备份和回滚继续保持单操作事务边界。

### 9.3 Windows 通知

阶段 9 启用状态通知后，每个固定或延迟切换具有提前 5 分钟的预通知；按钮动作只写受 nonce 和到期时间保护的一次性覆盖状态，真正的主题应用仍由 `auto` 在计划执行点完成。实际切换验证成功 5 秒后发送成功通知。关闭状态通知时按原固定边界静默切换。失败、配置损坏、任务异常、暂停/恢复及修复结果可以通知；通知不可用时，日志是可靠后备通道。

错误通知正文使用唯一、无参数的 `themescheduler-action://health/` 协议入口。点击时，若 GUI 已关闭则打开维护区，若已运行则通过当前会话命名事件恢复并聚焦原窗口；随后自动执行只读健康检查并定位报告。该入口不接受路径、查询参数或修复动作，也不自动修改任务、主题、配置或状态。成功通知仍为纯提示，预通知仍只保留三个受 nonce 保护的切换按钮。按需 GUI 关闭后命名事件随进程释放，不形成后台常驻。

### 9.4 WebView2

WebView2 只承担按需 GUI 的渲染，不属于自动切换链路。安装器和正式程序均通过独立检测适配器确认 Runtime；桥接 DLL 不能替代系统 Runtime。缺失、版本不可用或初始化失败时，用原生 Windows 对话框报告并提供修复路径，不静默选择旧渲染器。WebView 用户数据与程序文件分离，升级保留，卸载时随用户数据选择清理。

## 10. 质量属性

### 10.1 可靠性

- 自动执行按当前时间计算目标，不信任触发器名称。
- 注册表写入后读回验证。
- 配置和状态使用原子写入。
- 状态来源不可信时不覆盖已有颜色快照。
- 安装、升级、卸载均按有序步骤执行并记录结果。

### 10.2 安全性

- 不要求不必要的管理员权限，不写入系统目录。
- 不关闭 Defender 或 SmartScreen，不创建自动排除项。
- 不使用 UPX、代码混淆、隐藏命令或动态下载并执行应用代码；经用户确认和签名验证的微软 WebView2 Evergreen 系统依赖安装除外。
- 发布固定名称、版本元数据和 SHA-256，并保持构建可复现。
- 首版自有可执行文件不采用 Authenticode 签名；少量熟人分发通过发布哈希、载荷清单、可复现构建记录和明确的 SmartScreen 说明建立信任。微软 WebView2 安装程序的签名验证不受此决定影响。
- pywebview 只加载本地资源，正式构建关闭调试，前端只能调用最小白名单 API。
- WebView2 安装必须由用户知情，缺失时不得以远程页面或旧渲染器绕过。

### 10.3 可维护性

- GUI 和命令入口不复制核心切换逻辑。
- Windows 访问封装在主题、强调色和调度组件中。
- 数据结构可版本化迁移。
- 核心判断和失败策略可在非 GUI 测试中验证。
- 产品自定义异常均继承 `ThemeSchedulerError`；调用边界可以统一捕获，又不破坏
  既有 `ValueError` / `RuntimeError` 分类。
- 任务计划后端只定义一份 Protocol；Workbench 的 JSON 线结构使用 TypedDict，
  注入依赖使用具名 Protocol。
- 全源码 Pyright basic、核心边界 strict 棘轮、内部依赖无环和热点入口长度由门禁保护；详见
  [CODE_QUALITY.md](CODE_QUALITY.md)。
- 测试依赖项目环境的可编辑安装，不在每个测试文件中修改 `sys.path`；共享构造
  使用普通 unittest 支持模块。
- 1.0 发布前把平铺测试迁移到 `unit/integration/release`，但必须保持 quick/affected
  功能簇、Coverage 源码范围和发布发现集合不变；迁移本身不得造成漏测或重复执行。
- 发布输出与递归清理必须先证明目标是项目根的严格后代；源码分发由
  `MANIFEST.in` 和发布源码清单双重覆盖。

### 10.4 可移植性

第一版只承诺 Windows 11 x64。换设备时应重新安装并重新注册计划任务；可迁移的是安装器和配置导出文件，而不是旧设备已经注册的任务。ARM64、企业组策略和多用户环境暂不承诺。

## 11. 已冻结的架构决策

1. 使用 CPython 3.12 x64；GUI 使用 `pywebview==6.2.1`、本地 Web 资源和 Edge Chromium。
2. 使用 Windows 任务计划程序，不部署常驻服务或第三方调度器。
3. 安装器为单文件，正式主程序为 PyInstaller `onedir`；独立卸载器为不依赖主 `_internal` 的自包含无控制台产物。
4. 默认按当前用户安装到 `%LOCALAPPDATA%\Programs`。
5. 程序文件与用户数据分离，持久化数据使用版本化 JSON。
6. 调度器只触发，核心程序按当前时间决策。
7. Windows 设置负责生成强调色，程序负责快照和恢复。
8. 不使用削弱系统安全性的打包或安装策略。
9. 昼夜强调色长期保存为最小语义配置，完整主题仅用于动态应用和事务回滚。
10. 用户数据按 `profiles/`、`runtime/`、`backup/`、`logs/` 分区管理。
11. 强调色正常路径使用隔离的 `IThemeManager2` 桥接；Explorer 重启仅为用户确认的故障兜底。
12. 通用持久化只由 `persistence.py` 提供，正式业务和诊断模块均作为调用者。
13. v1 正式 JSON 使用精确字段集；未知字段、未知版本和错误类型均拒绝。
14. 配置或状态不可信时禁止自动系统修改；首次安装备份损坏时禁止覆盖原证据。
15. 日志为固定字段 JSON Lines，按 1 MiB×5 轮换；运行事务按冻结保留参数清理。
16. 任务计划固定为一个根任务：四个每日预提醒/边界触发器及至多一对受可信状态绑定的一次性延后触发器；全部调用相同 `auto`，使用当前用户交互式最低权限、错过补运行、不唤醒和 `IgnoreNew`。
17. 恢复只解除持久暂停，不自动切换；产品不暴露脱离计划编辑的通用立即同步。
    实际切换由计划边界驱动，或由设置页在完整保存后显式应用真实当前时段。
18. GUI 为单窗口、分组式、按需启动且关闭即退出；不设托盘，不检测或控制 Windows 色温“夜间模式”。
19. 使用 `uv` 和 `uv.lock` 管理项目专用构建环境；运行依赖与 PyInstaller 构建依赖分组锁定。
20. 正式主程序只保留一个无控制台入口并采用 PyInstaller `onedir`；单文件只用于 GUI 安装器和独立卸载器，不用于任务或日常 GUI。
21. WebView2 Runtime 由安装器检测并通过 Evergreen 路径修复；不降级到 MSHTML。
22. 源代码 GUI 默认是安全预览：必须显式提供数据根，任务和 Windows 写入关闭；启用实机写入还必须提供明确的正式可执行文件目标。
23. 当前用户“已安装的应用”登记提供“更改”和“卸载”：前者进入主 GUI 维护区，后者调用独立卸载器。
24. Setup 不长期缓存；同版本重装和升级采用暂存、哈希验证、目录交换和旧版本回滚，不直接覆盖损坏程序。
25. 错误通知点击只进入固定只读健康检查；修复继续由维护区独立按钮、明确确认和既有事务执行。现有 GUI 通过当前会话命名事件聚焦，不引入托盘、服务或磁盘轮询。
26. 阶段 10 发布候选必须绑定同一源码身份下通过的 release 测试报告；无 Git 时以逐文件 SHA-256 的 `themescheduler.source-manifest` 作为等价源码标识，并同时记录工具链、锁文件、PE 属性、载荷及发布物哈希。
27. 产品异常必须进入统一根类；任务后端协议保持单一来源，聚焦包静态类型和内部依赖无环作为阶段 15 代码质量门禁。
28. 发布候选必须在同一源码身份下通过显式锁定的 Pyright 零诊断、Ruff lint 与
    格式门禁，并保存绑定两个工具版本、配置哈希和完整复杂度清单的
    `quality-report.json`。
29. 0.1.5 完整外观通过资格验收后冻结 1.0 功能；测试整理、风险安全网、strict
    扩大和任务桥测量按顺序完成，最终统一升版并重新构建 1.0.0。

尚未完成的技术事项及验证计划统一记录在 [BACKLOG.md](BACKLOG.md)。
