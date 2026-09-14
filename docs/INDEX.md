# ThemeScheduler 文档索引

## 默认读取顺序

普通开发任务只需读取：

1. [AI_Rules.md](AI_Rules.md)；
2. [BACKLOG.md](BACKLOG.md)；
3. 下表中与任务直接相关的一份契约。

不要默认递归扫描 `artifacts/`。需要历史原因时先用 `rg` 在当前契约中定位术语
或错误；阶段性流水账不再属于源码文档。

## 当前事实

| 任务 | 首选文档 |
| --- | --- |
| 当前状态与待办 | [BACKLOG.md](BACKLOG.md) |
| 1.0 阶段计划与完成定义 | [ROADMAP_1.0.md](ROADMAP_1.0.md) |
| 模块职责与数据流 | [ARCHITECTURE.md](ARCHITECTURE.md) |
| 用户行为与产品边界 | [DESIGN.md](DESIGN.md) |
| 类型、依赖、复杂度和 GUI 工程质量 | [CODE_QUALITY.md](CODE_QUALITY.md) |
| 配置、状态与 profile | [PERSISTENCE.md](PERSISTENCE.md) |
| 自动切换 | [AUTO_CORE.md](AUTO_CORE.md) |
| 任务计划 | [SCHEDULER.md](SCHEDULER.md) |
| 暂停与恢复 | [CONTROLS.md](CONTROLS.md) |
| 通知与健康检查 | [NOTIFICATIONS_AND_HEALTH.md](NOTIFICATIONS_AND_HEALTH.md) |
| 安装、修复与卸载开发 | [INSTALLATION.md](INSTALLATION.md) |
| 普通用户安装 | [USER_INSTALLATION.md](USER_INSTALLATION.md) |
| 构建与发布 | [RELEASE.md](RELEASE.md)、[打包说明](../packaging/README.md) |
| 干净设备发布验收 | [CLEAN_MACHINE_ACCEPTANCE.md](CLEAN_MACHINE_ACCEPTANCE.md) |
| 产物保留与清理 | [ARTIFACTS.md](ARTIFACTS.md) |
| 测试命令与层级 | [TESTING.md](TESTING.md) |

当前正式版本为 `0.1.3`，正式发布位于 `artifacts/releases/0.1.3`。当前源码已统一
升版为 `1.0.0` 发布候选；其 0.1.5 功能基线支持昼夜完整外观并取消自动学习，自动门禁、双构建和 Defender
及完整外观人工矩阵已经通过，现为 1.0 冻结功能基线。测试整理、高风险分支、
12 文件 strict 棘轮、任务桥测量和动态主题兼容均已完成；最新兼容候选通过 603 项
release 测试和 Coverage 棘轮。下一步按
[1.0 路线](ROADMAP_1.0.md)重新运行门禁、双构建和最终验收。0.1.2 的无 Python
Windows x64 TS-142 兼容性证据继续有效。

## 测试选择

| 情况 | 命令 |
| --- | --- |
| 单组件小改 | `python tools/test.py quick <group>` |
| 功能批次收口 | `python tools/test.py affected <group>` |
| 跨组件离线收口 | `python tools/test.py integration` |
| 发布候选 | `python tools/test.py release` |
| 真实系统写入 | 按对应验收步骤单独授权 |

`quick/affected` 默认不写报告。文档、注释或 CSS 微调不自动触发旧阶段实机回归。

## artifacts 读取边界

默认只读取：

- `artifacts/releases/<版本>/dist/`；
- `artifacts/cold-archive/` 中任务明确指定的旧版本索引；
- 对应版本的 `evidence/`；
- 当前任务明确指向的 acceptance 或 test report；
- `artifacts/maintenance/` 中现行计划。

禁止为了“了解项目”递归展开正式 payload、历史 acceptance 或整个 `artifacts/`。
