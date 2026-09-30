# ThemeScheduler 1.0.1 发布契约

## 1. 目标与范围

本契约定义面向 Windows 11 x64、少量熟人分发的 `1.0.1` 维护版。该版本基于
`1.0.0`，包含通知消费和迁移重试修复、Setup partial 结果传播、Queue/有限锁等待/
future AutoRetry、SKIPPED occurrence 抑制、pending 与任务对账、主题应用及每次回滚的
有界真实读回、昼夜循环间隔验证、事件日志落盘脱敏、PE 数值版本元数据校验，以及既定
GUI、WebView2 文档和 Windows CI 收口。
候选继续采用当前用户安装、单文件 Setup、
`onedir` 主程序和独立单文件卸载器。目标设备不需要 Python、pip、uv 或虚拟环境。

`1.0.1` 尚未冻结最终候选或正式发布。合并源码身份
`3fd72090b577fc7f2c0d9dfbeecd86d095d23d89` 的 Windows CI 和 647 项 release gate
只证明该身份；所有后续源码或发布文档变化都必须绑定新的完整报告、构建和验收证据。

`0.1.5 RC2` 的自动门禁、双构建和 Defender 结果只证明功能候选；测试结构、类型
配置、文档或任务桥一旦变化，旧报告和旧二进制不能为 1.0 背书。阶段顺序和完成定义
见 [ROADMAP_1.0.md](ROADMAP_1.0.md)。

首版自有 EXE 不采用 Authenticode。该决定不降低 Defender、SmartScreen
或 Smart App Control 设置。程序不随包运行 WebView2 安装程序；Runtime 缺失时
显示原生错误提示并停止启动，用户需另行安装后重试。

## 2. 冻结构建输入

- CPython 3.12 x64；
- `uv.lock` 和项目专用 `.venv`；
- `pyinstaller==6.20.0`；
- `pywebview==6.2.1`；
- `pyright==1.1.411` 与 `ruff==0.15.20`；
- Windows 11 x64；
- `PYTHONHASHSEED=0`；
- `SOURCE_DATE_EPOCH=1767225600`；
- 最终构建前，`pyproject.toml`、包版本、三份 EXE 版本资源、安装器文案、用户文档
  和载荷清单版本必须统一为 `1.0.1`；
- `assets/ThemeScheduler.ico` 为七尺寸确定性 ICO。

无论 Git 工作树状态如何，发布候选都使用
`themescheduler.source-manifest` v1 作为构建源码身份：逐项记录源码、入口、
前端、打包脚本、资产、测试和文档的相对路径、大小及 SHA-256，再对规范
文件数组计算 `sourceTreeSha256`。`artifacts/`、`.venv/`、缓存和字节码不进入
源码身份。

## 3. 测试与构建绑定

正式候选必须先执行：

```powershell
uv sync --locked --group build --group quality

.venv\Scripts\python.exe tools\test.py integration
uv run --locked --group quality python tools\test.py coverage
uv run --locked --group quality python tools\coverage_guard.py check
.venv\Scripts\python.exe tools\test.py release
```

该报告必须满足：

- `mode=release`、`success=true`；
- 全套单元测试、`compileall`、`uv lock --check`、Pyright 零诊断、Ruff lint 和
  Ruff format 均通过；
- 报告使用 schema v2，五项辅助检查分别具有唯一 `checkId`，旧的、不含质量门禁
  的 release 报告不能用于新候选；
- `sourceIdentity` 与构建开始时重新计算的源码身份一致；
- 报告位于 `artifacts/test-reports/`。

随后使用同一项目环境构建：

```powershell
.venv\Scripts\python.exe packaging\build_release.py `
  --output-root artifacts\build\1.0.1-release-a `
  --version 1.0.1 `
  --python .venv\Scripts\python.exe `
  --test-report artifacts\test-reports\<release-report>.json
```

构建脚本拒绝既有输出根、版本漂移、图标漂移、旧测试报告、非 CPython
3.12 x64、非 Windows、非 PE32+ x64 或控制台子系统产物。

## 4. 候选输出与证据

候选根至少包含：

```text
dist/
  ThemeScheduler-Setup.exe
  RELEASE-README.md
  SHA256SUMS.txt
evidence/
  release-manifest.json
  source-manifest.json
  quality-report.json
  build-environment.json
  release-test-report.json
  pyinstaller-warnings/
  bundle-evidence/
release-layout.json
SHA256SUMS.txt
```

`dist/` 是唯一面向普通用户分发的目录；`evidence/` 是开发和审计证据，不要求
随安装器发送。`release-layout.json` 声明两类根目录和版本。
`evidence/release-manifest.json` 记录源码身份、工具链、锁文件、测试报告、载荷清单、
Setup/主程序/卸载器的大小和 SHA-256、PE 架构/子系统以及安全构建声明。
`quality-report.json` 与源码身份绑定：Pyright 零诊断、Ruff lint 和 format 是
阻断门禁，报告保存两个工具的锁定版本；复杂度
`C901/PLR0911/PLR0912/PLR0913/PLR0915/PLR1702` 作为非阻断明细保存。
根 `SHA256SUMS.txt` 绑定分发文件和关键证据；`dist/SHA256SUMS.txt` 只绑定
Setup 与用户说明。`dist/RELEASE-README.md` 明确说明未签名状态、哈希验证、
安全边界、安装恢复方式和已知限制。

“可复现”首先指锁定输入、命令和完整证据可复跑；正式候选还应在同一源码
身份和工具链下连续构建两次，比较 Setup、主程序、卸载器和载荷清单哈希。
只有实际相同后才可声明字节级重复构建通过。

## 5. 本机离线门槛

候选安装前必须完成：

1. 最终源码上依次完成 `integration`、`coverage`、`coverage_guard.py check` 和 `release`
   测试；旧源码身份的报告不得复用；
2. 两次构建的关键二进制及载荷清单哈希比较；
3. 三个 EXE 均为 PE32+ x64、Windows GUI 子系统；
4. 三个 EXE 的版本、说明、原始文件名和 CompanyName 精确读回；
5. Authenticode 状态如实为 `NotSigned`；
6. payload 精确文件集合、大小和 SHA-256 复验；
7. `SHA256SUMS.txt` 全部读回一致；
8. PyInstaller 警告保存并审阅，不忽略真实缺失模块；
9. `auto` 隔离冒烟不初始化 WebView、不创建数据、不写 Windows。

## 6. 实机与兼容性门槛

实机写入仍按 [INSTALLATION.md](INSTALLATION.md) 单独授权。1.0 继承 `0.1.3`
生命周期证据、0.1.4 当前外观读取验收和通过后的 0.1.5 完整外观矩阵，并执行最终
候选的最小充分生命周期：

- 通过 Setup 安装或升级 `1.0.1`，确认版本、GUI 与任务入口正常；
- 昼间、夜间分别设置不同的 Windows 模式、应用模式、强调色和两个显示位置；
- “导入当前 Windows 外观”必须同时读取两种模式、当前颜色和两个显示位置；
- 导入只更新对应时段草稿，不立即保存、更新任务或改变 Windows 外观；
- 保存后重新读取，确认 config schema v2、profile 和任务一致；
- “保存并应用当前时段”以及一次真实计划边界均应用完整目标；
- 人工验证开始菜单/任务栏与标题栏/窗口边框开关分别生效；
- 注入应用或验证失败时，自动测试必须证明主题与四项注册表状态共同回滚；
- 调试页能报告 WinRT 来源以及活动主题/实时状态是否分裂；
- 维护页与调试页的操作分组、使用时机和数据影响符合
  [最终用户安装指南](USER_INSTALLATION.md)；原始输出复制保持完整，完整健康检查的
  精简摘要入口不变；
- 用最终候选完成控制面板卸载、退出自清理和零残留复验。

恢复安装前外观时，卸载器只有在第一次结果确认自动切换仍处于暂停状态后，才允许把
完整恢复服务重试一次。第二次仍必须同时满足外观已应用、Windows 读回一致、系统模式
保持和暂停状态可信；否则继续停止卸载并保留程序、数据和详细的两次错误，不得把重试
当作降低验证标准的途径。

阶段 9–10 已实机通过且本轮未改变语义的睡眠、关机、静默边界、通知按钮 nonce
和健康入口不机械重跑，由完整自动回归和历史证据承接。1.0.1 最终候选步骤见
[CLEAN_MACHINE_ACCEPTANCE_1.0.1.md](CLEAN_MACHINE_ACCEPTANCE_1.0.1.md)；旧版
TS-142 的 `0.1.2` 记录保存在 [CLEAN_MACHINE_ACCEPTANCE.md](CLEAN_MACHINE_ACCEPTANCE.md)。

最好在另一台干净 Windows 11 x64 设备完成首次安装和无 Python 验证；若只在
开发机验收，发布记录必须明确标为“开发机当前用户生命周期验证”，不得写成
干净设备已经通过。

WebView2 已存在时 GUI 必须启动。Runtime 缺失或检测失败时，Setup、主 GUI 和卸载
GUI 必须显示原生错误提示并停止启动；程序不得启动 Runtime 安装程序。可在隔离
测试设备上验证缺失 Runtime 的路径，不能为测试而移除开发机上的系统 Runtime。

## 7. Defender 与 SmartScreen

- 对最终 Setup 和解包后的三个自有 EXE执行 Defender 自定义扫描并记录时间、
  引擎/定义版本和结果；
- 通过真实下载或带网络来源标记的副本观察 SmartScreen，记录实际界面；
- 不提交安全排除项，不关闭保护，不把“未触发提示”冒充已建立信誉；
- 如发生误报，保留文件哈希和检测名称，通过 Microsoft 官方样本提交流程处理。

## 8. 已知限制

- 仅 Windows 11 x64；
- 仅两个固定时段；
- 不管理 Windows 色温夜间模式；
- 不提供后台自动更新；
- 自有 EXE 未签名，可能显示 SmartScreen 或未知发布者提示；
- 强调色主路径依赖已隔离且经实机验证的未公开 `IThemeManager2`；
- WebView2 Runtime 是系统依赖，不等同于随程序打包的桥接 DLL。

## 9. 1.0 路线状态

`0.1.4` 已修复“导入当前 Windows 外观”误把可能滞后的活动 `.theme` 当作实时
权威的问题：应用模式改读 `AppsUseLightTheme`，强调色改读公开 WinRT
`UISettings.GetColorValue(Accent)`，系统模式和自动取色分别从其权威注册表值读取。
活动主题只用于调试差异和颜色高字节兼容，不得覆盖实时应用模式或可见 RGB。

2026-09-13 的 0.1.4 RC2 开发机当前用户定向验收确认：“导入当前 Windows 外观”能够
取得实时选择，“保存并应用当前时段”能够完成。RC1 首次复验曾被一份旧的
`partial` 自动事务安全阻止；当时 GUI 又因可空 changed 字段违反前端布尔契约而
遮蔽了真实原因。RC2 已在工作台 API 边界将三个 changed 字段规范为布尔值，并以
失败分支回归测试固定该行为。旧事务完整隔离留证后，产品事务守卫、配置、状态和
昼夜 profile 均读回有效，RC2 实机复验不再出现 API 契约错误。

开发机当前用户精简生命周期已经完成：首次安装不创建桌面快捷方式且不立即同步；
计划边界、三按钮预通知、实际主题切换和成功通知通过；同版本修复保留受保护数据；
最终卸载恢复安装前外观并删除全部产品数据和系统集成。RC5 进一步确认原选择窗口
交接后的临时卸载窗口、进度与结果页均可见；点击结果页“退出”后两个卸载进程和
临时目录约 1 秒内自动清零，不依赖任务管理器或两分钟异常保底。

`0.1.5 RC2` 已在同一源码身份下通过 577 项 release 测试、Coverage 棘轮、连续
双构建、PE/payload 复验和 Defender 扫描。2026-09-13 的 RC2-A 实机矩阵又确认了
完整外观导入、仅保存、手动应用和真实任务边界，并在结束后精确恢复数据、任务及
Windows 外观。0.1.5 因此成为 1.0 功能基线，不要求单独对外发布。

此后加入的动态主题兼容支持 Windows 11 缺少 `[VisualStyles]` 的 `.A/.W` 描述和有
`[VisualStyles]`、缺 `ThemeId` 的系统预设主题。最新 0.1.5 兼容候选通过 603 项
release 测试与 Coverage 棘轮，并在真实 `spotlight.theme` 上完成应用；该候选仍只
作为 1.0 输入基线，不替代最终 1.0 双构建和生命周期证据。

测试目录/发现规则整理、高风险 Windows 分支、12 文件 Pyright strict 棘轮和任务桥
性能决策均已完成。`1.0.0 RC6` 在有限恢复重试、完整外观桥接和卸载收尾修复后通过
607 项 release 测试、Coverage 棘轮、连续双构建、PE/payload 校验和 Defender 扫描。
实机从 0.1.5 升级后，版本、GUI、任务、完整健康状态及受保护数据均通过；最终候选又由
资源管理器启动 Setup，在真实桌面用户环境完成外观恢复和控制面板卸载。程序根、数据根、
当前用户登记、任务、协议、快捷方式、相关进程和临时卸载目录均核对为无残留。Codex
隔离进程曾对数据根和 HKCU 返回与桌面用户环境不同的虚拟视图，因此生命周期结论只采用
资源管理器、控制面板及普通 Windows PowerShell 的真实用户上下文。

`1.0.0` 曾冻结在 `artifacts/releases/1.0.0`，其发布清单和 `SHA256SUMS.txt` 曾与
Git 标签 `v1.0.0` 绑定。按用户要求，待 `1.0.1` 正式发布并确认附件可下载后，废弃
`1.0.0` 发布包及 GitHub Release 附件；保留 `v1.0.0` 标签与 Git 源码历史供追溯。

TS-142 已在另一台无 Python 的 Windows x64 设备完成 SmartScreen、首次安装、关闭
GUI 后的计划边界、通知、`0.1.1 → 0.1.2` 六项数据保留升级及独立卸载验收。
最终前端布局和内嵌字体变更由同版本开发机实机验收承接；没有由单台设备推导全部
Windows 11 组合兼容性。

## 10. 开发产物保留策略

D-12 使用激进的滚动保留，而不是永久保存所有阶段目录：

- 当前版本完整保留正式发布目录；旧版本允许转为带逐文件 SHA-256 索引的冷归档；
- 当前开发周期的原始实机证据只保留到下一正式版本收口；
- 每个正式版本只在自身 `evidence/bundle-evidence/` 长期保留一份规范化展开
  payload，不再保留重复安装器、候选 payload、安装态副本或完整安装树；
- 更早阶段的 acceptance、diagnostics 和失败样本在关键结论进入当前文档、对应问题关闭后可以删除；
- 中间候选、重复载荷、临时主题事务和可重建缓存不作长期归档。

任何真实清理都必须先生成机器可读计划，保护正式发布目录，并取得独立授权。
