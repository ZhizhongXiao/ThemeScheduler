# ThemeScheduler Windows 打包方案

状态：`1.0.1` 已正式发布，`v1.0.1` 固定绑定源码提交
`889dbe81a3e67e48780f16768e730ce3d695ec52` 和 `sourceTreeSha256`
`5f24ff2c93f5f19b5a5e07d59ce1863c6d93ecc5467deba3678eaaa9657eaf57`。本文件中的构建
命令用于复核或未来候选流程；任何新候选仍须绑定自己的 integration、coverage、Coverage
棘轮、release、Windows CI、双构建和验收证据，且不得覆盖正式目录。
每个版本的 `dist/` 只放用户分发文件，`evidence/` 保存发布清单、构建环境、
测试/质量报告和展开载荷证据；根 `SHA256SUMS.txt` 绑定两部分。
`0.1.1` 的经典 Setup/Uninstall、同版本修复、双配色与钟表边界、损坏卸载、
自删除和零残留已完成开发机当前用户验收；干净无 Python 设备验证也已完成。

本目录用于保存 ThemeScheduler 的 PyInstaller 配置、安装器资源、版本元数据和可复现构建说明。阶段 7 完成配置 GUI 后进入安装生命周期实现；阶段 10 才形成正式发布候选。

## 1. 发布形态

ThemeScheduler 是需要任务计划、快捷方式、升级、修复和卸载能力的安装型产品，不采用便携单 EXE 作为正式运行形态。

- 用户下载一个单文件 GUI 安装器 `ThemeScheduler-Setup.exe`；
- 安装器把正式主程序部署到 `app/` 下的 PyInstaller `onedir`；
- 正式主入口统一为无控制台的 `app\ThemeScheduler.exe`；
- `maintenance\Uninstall.exe` 是不依赖主 `_internal` 的自包含无控制台卸载器；
- `ThemeScheduler.exe auto` 保持阶段 5 已验收的任务计划命令契约；
- `ThemeScheduler.exe maintenance` 打开现有 GUI 的轻量维护区；
- GUI 按需启动，关闭窗口即退出，不常驻、不创建托盘进程；
- 默认按当前用户安装到 `%LOCALAPPDATA%\Programs\ThemeScheduler`；
- 用户数据继续存放在 `%LOCALAPPDATA%\ThemeScheduler`，升级不得覆盖。

`onedir` 可避免计划任务每次启动时先释放单文件临时目录，使启动路径、依赖位置、日志诊断和升级回滚边界更稳定。单文件用于 Setup 交付以及必须独立于主程序损坏面的卸载器，不用于计划任务或日常 GUI。完整目录与事务契约见 [安装生命周期契约](../docs/INSTALLATION.md)。

## 2. 锁定工具链

开发和发布使用项目专用环境，不依赖全局 Python 包：

- CPython 3.12 x64；
- `uv` 管理 `.venv`、运行依赖、构建依赖和 `uv.lock`；
- 运行依赖锁定 `pywebview==6.2.1`；
- `build` 依赖组锁定 `pyinstaller==6.20.0`；
- `quality` 依赖组锁定 `pyright==1.1.411` 与 `ruff==0.15.20`；
- 构建和测试使用 `uv run --locked`；
- 目标设备不需要 Python、pip、uv 或虚拟环境。

`pyproject.toml` 已分离运行依赖和 `build` 构建依赖，`uv.lock` 已生成。锁文件属于发布输入，必须提交并在构建前执行一致性检查。

以下命令记录 `1.0.1` 候选的可复现构建流程，供审计或未来复核。执行时必须使用新的、
不存在的候选输出根；不能直接写入或覆盖 `artifacts/releases/` 下的正式版本，也不能
用复核构建替换 GitHub 上已发布的 1.0.1：

```powershell
uv sync --group build --group quality

.venv\Scripts\python.exe tools\test.py release

.venv\Scripts\python.exe packaging\build_release.py `
  --output-root artifacts\build\1.0.1-release-a `
  --version 1.0.1 `
  --python .venv\Scripts\python.exe `
  --test-report artifacts\test-reports\<当前源码的-release-report>.json
```

`build_payload.py` 拒绝覆盖既有输出根。输出包含 `payload/app/`、`payload/maintenance/Uninstall.exe` 和位于载荷树外的 `payload-manifest.json`；清单自身不进入哈希树，避免循环哈希。

`build_release.py` 还要求候选输出根是项目根的严格后代；异常和最终清理在调用
`shutil.rmtree()` 前再次解析并验证目标，拒绝项目根本身、项目外路径和经链接
解析后越界的工作目录。

`ThemeSchedulerSetup.spec` 只接受由环境变量显式指定、已经组装完成的 bundle 根，把 `payload/`、sidecar 清单、按 HTML/CSS/JavaScript 分层的本地 `ui/` 资源、内嵌字体和必要桥接资源嵌入一个无控制台 EXE。`packaging/build_release.py` 先验证 Pyright 零诊断和 Ruff lint/format、记录非阻断复杂度观察，再构建主程序、独立卸载器、载荷和 Setup；所有输出根都必须预先不存在。该入口要求 `--test-report` 指向同一源码身份下成功的 schema v2 `release` 全量测试报告，并自动生成源码清单、质量报告、构建环境、PE 校验、发布清单、公开说明和校验和文件。完整命令见 [发布契约](../docs/RELEASE.md)。

项目根 `MANIFEST.in` 为 setuptools sdist 显式收录源码、入口、UI、打包配置、
资产、文档、测试和工具；正式 PyInstaller 发布仍以
`release_tools.capture_source_manifest()` 和 spec/payload 清单为权威，不把
sdist 当作用户安装包。

候选根本身也使用 `dist/`、`evidence/`、`release-layout.json` 和根
`SHA256SUMS.txt`。候选完成验收后，才可作为一个整体事务式移入
`artifacts/releases/<版本>/`；不要只复制 Setup 后声称正式发布证据完整。

在 Windows 上，组装器会在原子目录切换前让完整暂存树重新继承输出父目录 ACL。此步骤只修复文件可读/可执行权限，不改动文件内容，解决隔离构建账户的 owner-only ACL 被带入发布包后其他用户无法启动 EXE 的问题。

阶段 8.2 曾使用独立工作区脚本演练损坏重装；该一次性入口已在阶段 11 按 D-12 删除。其安全边界和验收结论保留在历史测试文档中，当前文件事务由正式部署、重装、回滚服务及其自动测试覆盖。

## 3. pywebview GUI

- 阶段 7 使用 pywebview 和随程序发布的本地 HTML、CSS、JavaScript；
- Windows 后端固定为 `edgechromium`，不降级到 MSHTML；
- 前端只通过最小白名单 API 调用业务用例，不复制自动切换、注册表或任务逻辑；
- GUI 模块延迟导入，`ThemeScheduler.exe auto` 不初始化 WebView；
- 正式构建关闭调试模式，不加载远程页面；
- WebView2 用户数据固定到 `%LOCALAPPDATA%\ThemeScheduler\WebView2`。

## 4. WebView2 Runtime

PyInstaller 打入的 pywebview 桥接 DLL 不等于 WebView2 Runtime。Setup、主 GUI 和卸载 GUI 在导入 pywebview 前检测 Runtime。检测失败或缺失时显示原生错误提示并停止启动；程序不下载或运行 WebView2 安装程序，也不降级到 MSHTML。用户需另行安装 Microsoft Edge WebView2 Runtime，再重新启动对应程序。

## 5. PyInstaller 规则

- 正式程序为 `onedir`、`windowed`/无控制台；
- 保留一个主 EXE，通过命令参数进入 `auto`、GUI 和维护用例；
- 独立卸载器为 `windowed`/无控制台、自包含产物，不从主程序 `_internal` 加载依赖或桥接脚本；
- 独立卸载器打包 pywebview、本地卸载前端、共享生命周期向导、字体子集及必要主题/任务/快捷方式桥接；它仍是单文件自包含产物，不从主程序 `_internal` 加载任何资源；
- 临时执行同时绑定一次性令牌、15 分钟有效期、默认安装根、卸载器 SHA-256 和清理脚本 SHA-256；
- GUI 资源、图标和版本信息显式写入 spec；
- 不使用 UPX、代码混淆、加壳、隐藏命令或动态下载并执行应用代码；
- 不从开发机器的全局环境收集未声明依赖；
- spec 不包含机器专属绝对路径；
- 任务计划目标必须指向稳定安装路径中的主 EXE；
- 安装器是独立单文件产物，不让任务计划或日常 GUI 直接运行安装器；
- 不长期缓存完整 Setup；损坏文件修复由用户重新运行 Setup 完成。

## 6. 安装与更新

安装器负责系统支持和 WebView2 检查、安装前个性化备份、`onedir` 部署、独立卸载器、初始数据、快捷方式、任务计划、当前用户安装登记和安装自检。安装登记的“更改”进入主 GUI 维护区，“卸载”进入独立卸载器。

普通用户的安装、安全验证、首次配置、修复和三个卸载选项说明见
[最终用户安装指南](../docs/USER_INSTALLATION.md)。本节只定义构建与生命周期
实现边界。

升级和同版本重装保留用户配置、昼夜完整外观、状态、首次安装备份及日志。新载荷先解包到 `.staging` 并按 SHA-256 验证，再以目录交换替换旧程序；不得直接覆盖损坏目录。旧组件暂存于 `.rollback`，新版本自检通过后才删除，失败则恢复旧程序、任务和登记。

卸载器以经典单窗口向导集中显示“恢复安装前外观”“保留配置及昼夜颜色”“保留日志”三个选择，并在同一套界面中显示自然语言确认、真实进度和结果。确认后，安装目录中的进程只负责生成严格请求并交接给已验证的临时自包含副本；临时副本不依赖主程序，删除任务、登记、快捷方式、整个程序根、桥接脚本和缓存。选择恢复时，v2 安装恢复点还原 Windows/应用模式、强调色及两个显示位置；旧 v1 恢复点保持当前 Windows 模式和显示位置。用户关闭完成页后再由固定无窗口脚本自清理临时目录。

正式发布不实现后台自更新服务。后续版本由用户运行新的安装器完成覆盖升级；安装器必须处理正在运行的 GUI、任务执行和失败回滚。

## 7. 可复现发布记录

每个候选版本至少保存：

- 应用版本、Git 提交或等价源码标识；
- Python、uv、PyInstaller 和 pywebview 版本；
- `uv.lock` SHA-256；
- 安装器和 `onedir` 文件清单及 SHA-256；
- 完整测试结果与 Windows 11 构建号；
- 构建命令、构建时间和已知限制；
- 可选 CycloneDX SBOM。

## 8. 发布验收

1.0.1 最终候选在另一台无 Python 设备上的可执行步骤和只读证据采集入口见
[1.0.1 干净设备验收清单](../docs/CLEAN_MACHINE_ACCEPTANCE_1.0.1.md)。
历史 `0.1.2` 证据仍保存在 [TS-142 清单](../docs/CLEAN_MACHINE_ACCEPTANCE.md)，
不得把其中的旧版本路径或文件数用于 1.0.1。

- 在干净 Windows 11 x64、普通当前用户和无 Python 环境中完成全新安装；
- WebView2 已存在时 GUI 可启动；Runtime 缺失或检测失败时显示原生错误提示并停止启动，程序不运行 Runtime 安装程序；
- GUI 可在常见 DPI、中文及空格路径下使用；
- `ThemeScheduler.exe auto` 不初始化 GUI、不弹控制台窗口；
- 四个固定任务触发器、可选延后触发对、错过补运行和跨重启行为通过；
- 覆盖升级保留用户数据且不留下旧程序文件；
- 主 EXE、`_internal`、主桥接脚本或安装清单损坏后，独立卸载器仍能完成受选项控制的清理；
- 无需用户预清理即可同版本重装，安装结果不混入旧损坏文件；
- 控制面板“更改”和“卸载”分别进入维护区与独立卸载器；
- 修复、卸载两种个性化选项和失败回滚通过；
- Defender/SmartScreen 行为有记录，不通过安全排除项规避；
- 发布物哈希和锁定依赖可由记录命令复现。
