# TS-142 干净 Windows 设备验收

## 1. 目的与结论边界

本清单验证最终 `0.1.2` 在另一台干净、无 Python 的 Windows 11 x64 设备上的
真实生命周期。它补足开发机离线测试和当前用户实机验收不能证明的部分：

```text
冻结发布物 → 从零安装 → 首次保存 → 任务边界 → 自动切换
             ↘ 0.1.1 保留数据升级 → 独立卸载 → 外观/残留核对
```

只有全部场景和人工观察完成后，才能关闭 `TS-142`。静态哈希、安装后文件检查或
虚拟机单独截图都不能替代计划任务与可见 Windows 外观的真实边界验收。

验收工具为 [clean_machine_check.ps1](../tools/clean_machine_check.ps1)。它只读
系统和产品文件；只有显式传入新的 `-OutputPath` 时才写一份 JSON 证据。它不会
安装、启动、修复、切换或卸载产品，也不会修改执行策略、注册表、任务或外观。

工具不使用非公开方式“静态解包”PyInstaller 单文件 Setup。取而代之的是三段绑定：
先校验 Setup 整体 SHA-256，再校验正式发布证据中的冻结 payload，最后在真实安装
后依据安装态清单逐文件复验。这样既不依赖 Python，也不会把第三方解包器本身当作
可信根。

## 2. 测试环境

- Windows 11 x64 24H2 或当前支持版本，普通当前用户、非提升 PowerShell；
- 未安装 Python、uv、项目源码或开发依赖；WindowsApps 的 Python 商店别名不算
  可用 Python 运行时；
- 最好使用可回滚的全新虚拟机快照，并关闭共享的开发机 `%LOCALAPPDATA%`；
- 保持 Defender、SmartScreen 和通知设置的真实默认状态，不创建安全排除项；
- 将完整 `artifacts/releases/0.1.2/` 和验收脚本复制到测试机；普通分发仍只需
  `dist/`，完整目录仅用于验收；
- 预先创建空的证据目录，例如 `C:\TS142-Evidence`。

当前冻结值：

| 项目 | 值 |
| --- | --- |
| 版本 | `0.1.2` |
| Setup SHA-256 | 从 `dist/SHA256SUMS.txt` 读取并通过 `-ExpectedSetupSha256` 显式传入 |
| 预期 Authenticode | `NotSigned` |
| 冻结 payload | 以 `evidence/bundle-evidence/payload-manifest.json` 的实际清单为准 |
| 安装根 | `%LOCALAPPDATA%\Programs\ThemeScheduler` |
| 数据根 | `%LOCALAPPDATA%\ThemeScheduler` |
| 任务 | `\ThemeScheduler` |

PowerShell 示例中的 `-ExecutionPolicy Bypass` 只影响该子进程，不改变机器或用户的
持久执行策略。

## 3. 场景 A：全新安装与首次边界

### A1. 发布物与安装前基线

```powershell
$releaseRoot = 'C:\TS142\0.1.2'
$setupHash = (
  Get-Content "$releaseRoot\dist\SHA256SUMS.txt" |
    Where-Object { $_ -match 'ThemeScheduler-Setup\.exe$' } |
    ForEach-Object { ($_ -split '\s+')[0] }
)
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File C:\TS142\clean_machine_check.ps1 `
  -Phase PreInstall `
  -ReleaseRoot $releaseRoot `
  -ExpectedVersion 0.1.2 `
  -ExpectedSetupSha256 $setupHash `
  -OutputPath C:\TS142-Evidence\01-pre-install.json
```

要求：

- `passed=true`；Setup 哈希、版本、根校验和及冻结 payload 清单全部匹配；
- Python/py 没有可用运行时，产品程序、数据、任务、登记、协议及快捷方式不存在；
- WebView2 已安装时记录版本；未安装时该项只警告，随后必须确认 Setup 在任何产品
  写入前安全阻断并给出清楚提示；
- Setup 当前未签名，`NotSigned` 是如实结果，不把它误判为签名验证通过；
- 要验证 SmartScreen，应使用浏览器取得的副本，或只给验收副本附加
  `ZoneId=3`；再次核对主数据流 SHA-256 不变。没有 ZoneId 时记录警告，不宣称
  SmartScreen 已测。

### A2. 首次安装

双击同一份 `ThemeScheduler-Setup.exe`：

1. 记录出现的是 SmartScreen、打开文件安全警告或没有警告，并记录发布者文字；
2. 不提升权限，不创建桌面快捷方式，不选择立即同步；
3. 确认 Setup 单窗口完成、无 CMD 闪窗，完成后主 GUI 自动可见；
4. 在第一次保存前运行：

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File C:\TS142\clean_machine_check.ps1 `
  -Phase PostInstall `
  -OutputPath C:\TS142-Evidence\02-post-install.json
```

要求 `passed=true`，并人工确认安装没有改变当前应用模式或强调色。报告应确认安装
记录、157 文件、独立卸载器、开始菜单、当前用户卸载登记、通知协议和
`\ThemeScheduler` 动作均正确；任务只能调用安装态 `ThemeScheduler.exe auto`，
不能调用 Python。

### A3. 首次初始化与自动切换

1. 在 GUI 中设置昼夜模式和明显不同的两种颜色；
2. 把下一边界设为当前时间约 7 分钟后，开启预通知与状态通知；
3. 点击“保存并启用”，关闭 GUI，确认保存本身不立即改变外观；
4. 边界前约 5 分钟确认通知包含“确认、跳过本次、延后 30 分钟”；
5. 点击“确认”，边界时确认应用模式、窗口边框、任务栏和开始菜单切换；
6. 边界后约 5 秒确认成功通知；通知可能直接进入通知中心，须同时检查通知中心；
7. 使用本次目标值采集：

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File C:\TS142\clean_machine_check.ps1 `
  -Phase PostBoundary `
  -ExpectedAppsTheme dark `
  -ExpectedColorizationColor 0XC4FFB900 `
  -OutputPath C:\TS142-Evidence\03-post-boundary.json
```

颜色和模式参数必须换成本轮实际目标。要求任务最后结果为 `0`，注册表读回与人工
观察一致。再选择一次“延后 30 分钟”，确认新的预提醒仍在延后边界前 5 分钟有效。

### A4. 重启与错过边界

保持任务启用后分别验证：

- GUI 关闭时到达边界仍静默切换；
- 重启登录后任务继续存在且不弹 CMD；
- 关机跨过边界后重新登录，错过补运行最终切到当前应有时段；
- 成功通知允许有 Shell 投递延迟，但不得重复轰炸。

每次可复用 `PostBoundary` 采集不同文件，禁止覆盖旧证据。

## 4. 场景 B：从 0.1.1 升级

从全新快照开始，不在场景 A 的 `0.1.2` 上伪造升级：

1. 核对并安装冻结的 `0.1.1` Setup，使用其正式目录校验和复验；
2. 设置非默认时间、昼夜颜色和通知，完成一次边界；
3. 运行 `PreUpgrade -ExpectedVersion 0.1.1`，保存
   `04-pre-upgrade.json`；
4. 直接运行 `0.1.2` Setup，选择修复/升级，不预先卸载、不立即同步；
5. 运行 `PostUpgrade -ExpectedVersion 0.1.2`，保存
   `05-post-upgrade.json`；
6. 比较两份报告的 `facts.dataFiles`：配置、状态、昼夜 profile、首次安装恢复点
   和日志应按契约保留；安装记录版本更新为 `0.1.2`，程序 payload 完整；
7. 打开 GUI，确认原计划仍启用，再完成一次真实边界。

若必须改变 `state.json` 才能触发测试，应明确记录改变原因，不能把人为改写后的
状态当作升级保留证据。

## 5. 场景 C：独立卸载与恢复

在 `0.1.2` 安装态记录卸载前应用模式和强调色，然后从控制面板“程序和功能”进入
卸载器：

1. 选择“恢复安装前外观”；
2. 不保留配置及昼夜颜色，不保留日志；
3. 确认一个卸载向导完成，无连续消息框或 CMD 闪窗；
4. 点击结果页“退出”，等待临时卸载进程和目录自行清理；
5. 运行：

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File C:\TS142\clean_machine_check.ps1 `
  -Phase PostUninstall `
  -OutputPath C:\TS142-Evidence\06-post-uninstall.json
```

要求程序根、数据根、任务、登记、协议、快捷方式、产品进程和卸载临时根均不存在。
人工确认默认 Windows 模式未被改变，默认应用模式和强调色恢复到首次安装前值。

另做一次“保持当前外观＋保留配置和日志”的卸载，只需使用新快照；采集时增加
`-KeepData`，并确认保留根只包含契约允许的数据。

## 6. 证据与退出条件

`0.1.2` 的场景 A、B、C 已完成：静态发布检查、SmartScreen、首次安装、关闭 GUI
后的任务边界和通知、`0.1.1 → 0.1.2` 数据保持升级，以及独立卸载后的外观恢复和
零产品集成残留均符合预期。该结论只覆盖实际执行的 Windows x64 设备。

必须保留：

- 四至六份不覆盖的 JSON 报告；
- Windows 版本/build、WebView2 版本和 Python 不存在事实；
- 安全警告/SmartScreen、安装完成、首次 GUI、三按钮通知、切换结果和卸载结果页
  的截图或人工记录；
- 场景 A/C 的安装前与卸载后外观值；
- 场景 B 升级前后数据哈希比较结论；
- 任何失败的准确时间、页面、错误文本和是否发生系统写入。

关闭 `TS-142` 的最低条件：

- 场景 A、B、C 全部完成，所有阻断检查通过；
- 至少一次任务边界是在 GUI 已关闭、机器没有 Python 的条件下完成；
- 没有管理员权限、开发目录、环境变量或手工注册表修补依赖；
- 卸载后符合所选数据/外观策略且无产品集成残留；
- 文档只陈述实际执行的 Windows 版本和范围，不从单台设备推导全部 Windows 11
  兼容性。

## 7. 后续自动化层级

当前首选“可回滚 Hyper-V 虚拟机＋人工观察＋只读 JSON”。若以后重复发布增多，
可以按收益逐步增加：

1. 用 PowerShell Direct 自动复制冻结发布、运行各阶段检查并取回 JSON；
2. 用固定干净快照自动安装和卸载，但仍保留通知按钮、SmartScreen、Shell 颜色及
   GUI 可见性的人工确认；
3. 再考虑专用交互式 Windows 测试机上的 UI 自动化和截图比对。

Windows Sandbox 适合一次性安装冒烟，但不适合跨重启、睡眠、错过边界和长期通知
测试；非交互式 CI 的 `windows-latest` 也不能替代真实 Explorer、通知中心和用户
会话。因此首版不为“全自动”牺牲证据真实性。
