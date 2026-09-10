# ThemeScheduler

ThemeScheduler 是面向 Windows 11 的个人昼夜主题计划工具。它按两个固定边界切换
默认应用模式和强调色，使用 Windows 任务计划程序运行，并提供 pywebview 工作台、
单文件 Setup、独立卸载器、交互通知和只读健康检查。

当前正式版本为 `0.1.2`，M17 已完成结项发布：

- 开发机当前用户范围的安装、修复重装、计划边界、损坏卸载、可见结果页、
  退出自清理和零残留已验收；
- 阶段 15/16 的源码拆包、异常/类型边界、测试/覆盖率治理和 GUI 工程整改已完成；
- 最终门禁、Coverage 棘轮、双构建一致性和 Defender 扫描均已通过；
- 另一台干净、无 Python 的 Windows x64 设备已完成安装、计划边界、升级和卸载验收。

正式分发位于 `artifacts/releases/0.1.2/dist/`。普通用户只需要
`ThemeScheduler-Setup.exe`、`RELEASE-README.md` 和 `SHA256SUMS.txt`。
另一台无 Python 设备的发布验收按
[TS-142 清单](docs/CLEAN_MACHINE_ACCEPTANCE.md)执行。

## 文档

- [文档索引](docs/INDEX.md)：按任务选择最小阅读范围；
- [用户安装指南](docs/USER_INSTALLATION.md)：安装、修复和卸载；
- [架构](docs/ARCHITECTURE.md) 与 [设计](docs/DESIGN.md)：开发边界；
- [维护清单](docs/BACKLOG.md) 与 [代码质量](docs/CODE_QUALITY.md)：当前状态；
- [发布契约](docs/RELEASE.md) 与 [打包说明](packaging/README.md)：构建发布。

阶段性测试流水账已移出源码文档；当前事实以文档索引和发布证据为准。

## 目录

| 路径 | 用途 | 进入安装包 |
| --- | --- | --- |
| `src/theme_scheduler/` | 业务、Windows 适配、生命周期和 GUI API | 是 |
| `entrypoints/` | 主程序、Setup、Uninstall 和开发入口 | 对应入口是 |
| `ui/` | 本地 HTML/CSS/JavaScript、共享字体和品牌资源 | 是 |
| `assets/` | 图标和发布静态资源 | 按清单 |
| `packaging/` | PyInstaller、版本资源和发布工具 | 否 |
| `tools/`、`tests/` | 测试、产物治理和开发辅助 | 否 |
| `docs/` | 当前契约、维护说明和用户文档 | 仅提炼必要内容 |
| `artifacts/` | 当前发布、冷归档、验收和测试报告 | 仅当前 `releases/*/dist` 用于分发 |
| `.venv/` | 可由锁文件重建的项目环境 | 否 |

不要把 `.venv`、`ui/` 源码或展开的 payload 当作便携程序分发。

## 开发环境

- Windows 11 x64；
- CPython 3.12 x64；
- `uv` 管理项目环境和锁文件；
- Edge WebView2 Runtime；
- 真实主题、任务、安装和卸载写入必须逐项授权。

```powershell
uv sync --group quality
```

## 测试

```powershell
python tools/test.py quick gui
python tools/test.py affected gui
python tools/test.py quick stage15
python tools/test.py integration
uv run --group quality python tools/test.py coverage
python tools/test.py release
uv run --group quality pyright
uv run --group quality ruff check src entrypoints packaging tools tests
uv run --group quality ruff format --check src entrypoints packaging tools tests
```

- `quick`：日常聚焦测试；
- `affected`：功能批次收口；
- `integration`：跨组件离线回归；
- `coverage`：以 `integration` 同一测试集合采集行/分支覆盖率，并写入
  `artifacts/quality/coverage.json`；不执行 Pyright/Ruff；
- `release`：候选冻结前运行全量测试、编译/锁文件检查及 Pyright/Ruff 门禁；
- 实机写入没有通用测试命令，必须使用对应验收步骤。

详细选择规则见 [测试指南](docs/TESTING.md) 和
[代码质量基线](docs/CODE_QUALITY.md)。

## 安全 GUI 预览

以下入口使用独立数据根；后端会阻止任务和 Windows 写入：

```powershell
uv run themescheduler-preview workbench `
  --data-root artifacts/preview/gui
```

源码模式只有同时提供 `--allow-live-writes` 和明确的正式 `--executable` 时才允许
经界面确认的写入；不得将 Python 解释器作为任务目标。
