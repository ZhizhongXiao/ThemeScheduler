# ThemeScheduler 0.1.3 发布契约

## 1. 目标与范围

当前流程生成面向 Windows 11 x64、少量熟人分发的 `0.1.3` 候选。
候选继续采用当前用户安装、单文件 Setup、`onedir` 主程序和独立单文件
卸载器。目标设备不需要 Python、pip、uv 或虚拟环境。

首版自有 EXE 不采用 Authenticode。该决定不降低 Defender、SmartScreen
或 Smart App Control 设置，也不改变随包运行 Microsoft WebView2 安装程序
时必须验证微软签名的要求。

## 2. 冻结构建输入

- CPython 3.12 x64；
- `uv.lock` 和项目专用 `.venv`；
- `pyinstaller==6.20.0`；
- `pywebview==6.2.1`；
- `pyright==1.1.411` 与 `ruff==0.15.20`；
- Windows 11 x64；
- `PYTHONHASHSEED=0`；
- `SOURCE_DATE_EPOCH=1767225600`；
- `pyproject.toml`、包版本、三份 EXE 版本资源和载荷清单版本均为 `0.1.3`；
- `assets/ThemeScheduler.ico` 为七尺寸确定性 ICO。

项目当前不是 Git 仓库，因此发布候选使用
`themescheduler.source-manifest` v1 作为等价源码身份：逐项记录源码、入口、
前端、打包脚本、资产、测试和文档的相对路径、大小及 SHA-256，再对规范
文件数组计算 `sourceTreeSha256`。`artifacts/`、`.venv/`、缓存和字节码不进入
源码身份。

## 3. 测试与构建绑定

正式候选必须先执行：

```powershell
uv sync --group build --group quality

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
  --output-root artifacts\build\stage19-0.1.3-rcN `
  --version 0.1.3 `
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

1. `quick stage14`、`affected stage14`、一次 `integration` 和最终 `release` 测试通过；
2. 两次构建的关键二进制及载荷清单哈希比较；
3. 三个 EXE 均为 PE32+ x64、Windows GUI 子系统；
4. 三个 EXE 的版本、说明、原始文件名和 CompanyName 精确读回；
5. Authenticode 状态如实为 `NotSigned`；
6. payload 精确文件集合、大小和 SHA-256 复验；
7. `SHA256SUMS.txt` 全部读回一致；
8. PyInstaller 警告保存并审阅，不忽略真实缺失模块；
9. `auto` 隔离冒烟不初始化 WebView、不创建数据、不写 Windows。

## 6. 实机与兼容性门槛

实机写入仍按 [INSTALLATION.md](INSTALLATION.md) 单独授权。`0.1.3` 使用变更驱动
矩阵，至少覆盖：

- 当前无安装基线上的经典 Setup 首次安装，默认不立即同步；
- GUI 昼夜双配色、12/24 小时表盘和两个方向的计划边界；
- GUI、维护入口、通知协议、任务和控制面板登记；
- 同版本经典 Setup 修复/重装和数据保持；
- 主 EXE 损坏时经典 Uninstall 保持外观并保留数据/日志；
- 保留数据再次安装后，最终恢复安装前外观并完成零残留卸载。

阶段 9–10 已实机通过且本轮未改变语义的睡眠、关机、静默边界、通知按钮 nonce
和健康入口不机械重跑，由完整自动回归和历史证据承接。详细批次见
[CLEAN_MACHINE_ACCEPTANCE.md](CLEAN_MACHINE_ACCEPTANCE.md)。

最好在另一台干净 Windows 11 x64 设备完成首次安装和无 Python 验证；若只在
开发机验收，发布记录必须明确标为“开发机当前用户生命周期验证”，不得写成
干净设备已经通过。

WebView2 已存在路径必须通过。缺失联网和缺失离线路径如果没有可安全恢复的
专用测试设备，可以只验证原生检测和错误呈现，并把真实 Runtime 安装列为
已知未验收项，不能在主开发机上为测试而破坏系统 Runtime。

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

## 9. `0.1.3` 正式发布状态

`0.1.3` 在 0.1.2 稳定生命周期之上增加“保存并应用当前时段外观”，删除旧立即
同步与阶段 1 主题/强调色原型，并完成维护/调试入口收口。工作台使用居中模态确认
窗；操作结果按权重显示中央中文短提示，低权重且已有页面反馈的操作不再弹出。
最终测试数量、源码身份、二进制哈希、PE 属性和 Defender 结果以
`artifacts/releases/0.1.3/evidence/` 中的冻结机器证据为准。

开发机当前用户精简生命周期已经完成：首次安装不创建桌面快捷方式且不立即同步；
计划边界、三按钮预通知、实际主题切换和成功通知通过；同版本修复保留受保护数据；
最终卸载恢复安装前外观并删除全部产品数据和系统集成。RC5 进一步确认原选择窗口
交接后的临时卸载窗口、进度与结果页均可见；点击结果页“退出”后两个卸载进程和
临时目录约 1 秒内自动清零，不依赖任务管理器或两分钟异常保底。

`0.1.3` 的发布结论只在完整门禁、连续双构建、PE/payload 复验和 Defender 扫描
通过后成立；冻结目录中的发布清单和 SHA256SUMS 是哈希的唯一权威来源。

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
