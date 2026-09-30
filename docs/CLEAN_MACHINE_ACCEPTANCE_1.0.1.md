# ThemeScheduler 1.0.1 最终候选干净设备验收

状态：执行清单模板，不是验收结果。只有绑定到最终源码身份和冻结候选哈希后，
本清单的执行记录才构成 1.0.1 证据。

本清单仅适用于 `1.0.1`。历史 [TS-142 清单](CLEAN_MACHINE_ACCEPTANCE.md)
及其 `0.1.2` 结论保持原样，不得用本清单改写或替代。

## 1. 验收范围

验证 Windows 11 x64 普通用户、无 Python 环境中的最终候选安装、首次设置、计划边界、
从 `1.0.0` 升级、卸载和外观恢复。自动化只读检查不能代替人工确认可见的 Windows
外观、通知、SmartScreen 和安装/卸载界面。

验收工具为 [clean_machine_check.ps1](../tools/clean_machine_check.ps1)。它不会安装、
启动、修复、切换或卸载产品；只有显式传入新的 `-OutputPath` 时才写 JSON 证据。
真实系统写入由人工操作 Setup、GUI、任务计划程序或卸载器完成。

## 2. 候选绑定与测试环境

执行前从最终候选的 `evidence/release-manifest.json` 记录 Git commit、
`sourceTreeSha256`、测试报告路径和候选 A/B 哈希。A/B 必须绑定相同源码身份；只把
通过校验的完整候选根复制到测试机。不要从旧候选复制安装器或 payload。

| 项目 | 验收值 |
| --- | --- |
| 版本 | `1.0.1` |
| 候选源码 commit / sourceTreeSha256 | 从最终 release manifest 记录 |
| Release root | 测试机上的候选目录，包含 `dist/` 与 `evidence/` |
| Setup SHA-256 | 从该候选 `dist/SHA256SUMS.txt` 读取，并显式传给脚本 |
| Authenticode | `NotSigned`，如实记录 |
| payload | 以该候选 `evidence/bundle-evidence/payload-manifest.json` 为准 |
| 安装根 / 数据根 / 任务 | `%LOCALAPPDATA%\Programs\ThemeScheduler` / `%LOCALAPPDATA%\ThemeScheduler` / `\ThemeScheduler` |

测试机要求：Windows 11 x64、普通当前用户、未安装 Python/uv/项目源码；保持 Defender、
SmartScreen 和通知的默认状态，不创建安全排除项。推荐使用可回滚的全新系统快照。

以下 PowerShell 示例假定把候选根复制到 `C:\TS101\candidate`，把同一源码提交中的
验收脚本复制到 `C:\TS101\clean_machine_check.ps1`，并预先创建空证据目录
`C:\TS101-Evidence`。正式执行时替换为实际路径，并保留每次检查的独立输出文件。

```powershell
$releaseRoot = 'C:\TS101\candidate'
$checkScript = 'C:\TS101\clean_machine_check.ps1'
$evidenceRoot = 'C:\TS101-Evidence'
$setupHash = (
  Get-Content "$releaseRoot\dist\SHA256SUMS.txt" |
    Where-Object { $_ -match 'ThemeScheduler-Setup\.exe$' } |
    ForEach-Object { ($_ -split '\s+')[0] }
)
```

## 3. 场景 A：全新安装与计划边界

### A1. 安装前检查

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File $checkScript `
  -Phase PreInstall `
  -ReleaseRoot $releaseRoot `
  -ExpectedVersion 1.0.1 `
  -ExpectedSetupSha256 $setupHash `
  -OutputPath "$evidenceRoot\01-pre-install.json"
```

要求所有阻断检查通过：候选哈希、版本、payload、签名状态和测试环境均匹配；产品
程序、数据、任务、登记、协议和快捷方式尚不存在。WebView2 缺失或检测失败时，产品
必须显示原生错误提示并停止启动，不得自行运行 Runtime 安装程序。

### A2. 安装与首次设置

从同一候选启动 `ThemeScheduler-Setup.exe`，不提升权限、不选择立即同步。记录实际
安全提示、发布者文字、Setup 界面和安装结果。确认安装没有改变当前 Windows 外观，
主 GUI 可见，且任务动作使用安装态 `ThemeScheduler.exe auto`。

运行安装后只读检查：

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File $checkScript `
  -Phase PostInstall `
  -OutputPath "$evidenceRoot\02-post-install.json"
```

要求安装版本、逐文件 payload、卸载器、开始菜单、当前用户登记、通知协议和计划任务
符合候选清单；文件数量以 payload manifest 为准，不使用历史版本的固定数量。

### A3. 确认与跳过语义

先设置明显不同的昼夜模式和颜色，把下一固定边界设在约 7 分钟后，保存并启用，关闭
GUI。确认保存本身不改变外观，预通知含“确认、跳过本次、延后 30 分钟”，确认路径
按计划应用并收到状态通知。

再用一个新固定 occurrence 执行“跳过本次”验收：

1. 在预通知中点击“跳过本次”，记录该 occurrence 的目标外观和边界时间；
2. 到达边界后确认目标外观没有被应用；
3. 边界后手动启动一次 `\ThemeScheduler` 计划任务，确认仍不应用被跳过的目标，
   且任务结束结果仍表示本 occurrence 已跳过；
4. 到 `nextFixedAt` 后确认旧抑制标记被清理，下一固定周期仍可正常切换。

这是本轮发布阻塞修复对应的变更驱动实机验收项，不能只由单元测试或注册表快照替代。

用实际目标值采集边界后状态：

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File $checkScript `
  -Phase PostBoundary `
  -ExpectedAppsTheme dark `
  -ExpectedColorizationColor 0XC4FFB900 `
  -OutputPath "$evidenceRoot\03-post-boundary.json"
```

模式和颜色参数必须换成本轮真实目标。人工观察开始菜单/任务栏与标题栏/窗口边框
强调色显示位置；检查通知中心，记录真实任务结果和外观读回。另验证 GUI 关闭后边界
仍执行、重启登录后任务仍存在，以及错过边界后按当前本地时间补运行。

## 4. 场景 B：从 1.0.0 升级并保留数据

从新的 `1.0.0` 安装快照开始，不在场景 A 的安装态伪造升级。设置非默认计划和外观，
保存升级前证据：

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File $checkScript -Phase PreUpgrade -ExpectedVersion 1.0.0 `
  -OutputPath "$evidenceRoot\04-pre-upgrade.json"
```

直接运行最终 `1.0.1` Setup 进行升级，不预先卸载、不立即同步；随后采集：

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File $checkScript -Phase PostUpgrade -ExpectedVersion 1.0.1 `
  -OutputPath "$evidenceRoot\05-post-upgrade.json"
```

比较升级前后的 `facts.dataFiles`。配置、状态、昼夜 profiles、首次安装恢复点和日志应
按保留契约处理；登记版本和安装 payload 应更新到 `1.0.1`，原计划仍启用。不得把为
触发测试而手动改写的状态当作升级保留证据。

## 5. 场景 C：卸载与外观恢复

记录卸载前完整 Windows/应用模式、强调色及两个强调色显示位置。从控制面板启动独立
卸载器，选择恢复首次安装前外观，并按清单选择配置和日志保留策略。确认结果页可见，
点击退出后进程与临时目录自行清理。采集：

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File $checkScript -Phase PostUninstall `
  -OutputPath "$evidenceRoot\06-post-uninstall.json"
```

要求程序根、任务、登记、协议、快捷方式、产品进程及卸载临时根均符合选择的清理策略；
人工确认 Windows 外观恢复到安装前值。另用新的系统快照验证保留数据/日志的卸载选项。

## 6. 安全证据与退出条件

- 对最终候选 Setup、主程序和卸载器进行 Defender 扫描，记录哈希、时间、引擎/定义
  版本和结果；不关闭保护、不创建排除项；
- 通过真实下载或带网络来源标记的候选副本观察 SmartScreen，记录实际界面；未签名时
  “发布者未知”属于如实提示；
- 保留候选 A/B 的源码身份、Setup/主程序/卸载器/payload 哈希、PE 架构和数值/字符串
  版本读回、完整测试报告、Coverage 棘轮及 Windows 版本/build；
- 保留每阶段 JSON、可见 UI/通知/切换/卸载记录、安装前与卸载后的外观值，以及升级前后
  数据比较；失败时记录准确时间、错误和是否发生系统写入；
- 所有阻断项通过后才可冻结 `artifacts/releases/1.0.1`、打 `v1.0.1` 并创建正式
  GitHub Release。单台设备结果只代表实际执行的 Windows 版本和范围。
