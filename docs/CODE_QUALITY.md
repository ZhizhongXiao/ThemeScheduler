# ThemeScheduler 代码质量基线

## 1. 定位

本文记录当前可维护性事实和自动门禁。它不替代业务测试，也不授权访问
Windows 注册表、主题、任务计划或安装目录。

质量检查环境：

- CPython 3.12；
- Pyright `1.1.411`，由 `quality` 依赖组锁定；
- Ruff `0.15.20`，由 `quality` 依赖组锁定；
- Coverage.py `7.15.3`，由 `quality` 依赖组锁定；
- 静态与覆盖率基线复核日期：2026-08-09。

## 2. 已纳入门禁的结构

### 2.1 统一异常树

`theme_scheduler.errors` 定义：

```text
ThemeSchedulerError
├─ ContractError
├─ DataError
└─ ThemeSchedulerRuntimeError
```

所有产品自定义 `*Error` 都必须继承 `ThemeSchedulerError`。三个中间类同时保留
对应的内置 `ValueError` 或 `RuntimeError` 身份，因此原有精确异常捕获和调用方
兼容性不变；根类允许应用边界统一捕获项目错误。项目类不命名为
`RuntimeError`，避免遮蔽 Python 内置类。

### 2.2 单一调度协议

任务计划后端统一使用 `scheduler.mutation.TaskSchedulerBackend`。
Workbench 不再维护字段不完整的 `SchedulerFacade` 副本。

### 2.3 热点入口

- `FileDeploymentService.deploy()` 是不超过 60 行的阶段编排器；
- `AutoRunner.run_locked()` 是不超过 40 行的决策编排器；
- `GuiOverviewMixin.get_overview()` 及 profile、backup、task 三个摘要构建器均不超过
  40 行。

部署和自动切换的验证、准备、执行、提交、失败恢复已经分开。长度门禁只保护入口
不重新膨胀，不要求把每个私有阶段机械拆成碎片。

### 2.4 Workbench 线协议

`workbench/contracts.py` 使用具名 `Protocol` 表达注入依赖，使用 `TypedDict`
表达传给 JavaScript 的稳定字段，并以 `OverviewSections` 数据类组合内部摘要。
前后端仍通过 JSON 对象通信，但 Python 端的字段重命名和缺失可由静态检查及契约
测试发现。

## 3. 当前静态事实

Pyright 默认门禁覆盖完整 `src/theme_scheduler`：

```powershell
uv sync --group quality
uv run --group quality pyright
```

结果为 `0 errors, 0 warnings, 0 informations`。配置只有一个来源：
`pyproject.toml` 的 `[tool.pyright]`；旧 `pyrightconfig.json` 已删除，发布源码清单和
契约测试会阻止双配置回流。

全源码采用 `basic` 基线；以下七个高价值边界进入 strict 棘轮：

- `scheduler/models.py`：任务 JSON 契约；
- `lifecycle/installation.py`：安装记录与登记契约；
- `automation/runner.py`：自动切换主编排器；
- `automation/recovery.py`：未完成自动事务恢复状态机；
- `automation/contracts.py`、`automation/outcome.py`：恢复与编排共享的类型边界。
- `accent_theme.py`：管理主题构建、`IThemeManager2` 索引验证及两层回滚边界。

曾直接对 `scheduler/`、`lifecycle/`、`automation/` 三个目录启用 strict，探针产生
138 个错误和 12 个警告；多数来自跨模块下划线助手和反序列化后的防御性
`isinstance`，不等于 138 个运行缺陷。因此不以关闭 strict 规则或批量忽略伪造通过，
而是保留全源码 basic 零诊断，并逐文件扩大 strict 列表。标准 Pyright 1.1.411 不支持
basedpyright 的 `reportAny` 和 `reportUnannotatedClassAttribute`，配置中不写无效项。

内部依赖图由契约测试实时生成并保持 0 个环；阶段 15 聚焦包均使用显式
`__all__` 暴露公共名称。文档不再复制容易漂移的模块数和边数。

Ruff 在 `pyproject.toml` 中显式固定 Python 3.12、88 字符格式、lint 规则
`E/F/W/I/N/UP/C4/SIM/B` 和 formatter 行为，不依赖默认规则。三个有意豁免为：

- `E501`：物理换行交给 formatter；
- `SIM105`：次要日志或清理失败不得遮蔽主结果；
- `UP042`：`StrEnum` 会改变 `str(member)`，需要独立迁移验证。

测试中的 `winreg` 假对象必须保留原生 API 大小写，仅对该文件豁免 `N802`。

复杂度观察基线：

```powershell
uv run --group quality ruff check --select C901,PLR0911,PLR0912,PLR0913,PLR0915,PLR1702 src
```

当前工作树共 68 项，较本轮开始的 77 项减少 9 项。`automation/recovery.py`
不再触发 `C901/PLR0911/PLR0912/PLR0915`；无正式入口、打包引用或生产调用的
阶段 2 `cli/accent.py` 已在底层能力测试保留后退役。全仓数字仅用于选择下一批
真实热点，不作为要求一次清零的发布门槛；依赖注入参数数量继续单独观察。

`tools/test.py release` 显式执行 Pyright、Ruff lint 和 Ruff format，并把三个
`checkId` 写入 schema v2 测试报告；发布构建会再次执行同一组质量门禁，以阻止
测试报告生成后的工具或源码漂移。构建再把完整复杂度明细写入
`evidence/quality-report.json`。报告绑定 Pyright/Ruff 版本、配置哈希和源码身份，
所有 finding 路径均规范化为项目相对路径。

### 3.1 覆盖率治理

`tools/test.py coverage` 使用当前虚拟环境中的同一 Python 解释器执行 Coverage.py，
先清除旧数据，再以 `integration --no-report` 的完整测试集合采集行覆盖和分支覆盖，
最后写入：

- `artifacts/quality/coverage.json`：Coverage.py 原始逐文件报告；
- `artifacts/quality/coverage-run.json`：测试、JSON、文本报告退出码和源码身份。

推荐入口为：

```powershell
uv run --group quality python tools/test.py coverage
```

该模式不执行 Pyright、Ruff 或发布构建。即使测试失败也保留诊断 JSON，但返回原始
非零退出码，且运行元数据将 `success` 记为 `false`。`tools/coverage_guard.py`
只接受测试全绿、报告与当前源码身份一致的结果；冻结基线要求显式
`--confirm-baseline-write`，并拒绝覆盖已有基线。之后的 `check` 同时阻止总体行/分支
覆盖率、既有逐模块覆盖率下降，以及新增生产模块保持 0% 行覆盖。Coverage.py
使用 `source=theme_scheduler`，因此仍存在但未执行的模块会以 0% 出现；报告中缺失的
旧模块代表源码已退役，守卫允许这种有意删除。

2026-08-05 首次正式基线覆盖 110 个 Python 文件：

| 指标 | 结果 |
| --- | ---: |
| 可执行语句 | 10,901 |
| 行覆盖率 | 78.6% |
| 分支数 | 3,184 |
| 分支覆盖率 | 60.5% |
| Coverage.py 综合覆盖率 | 74.5% |
| 0% 行覆盖生产模块 | 0 |

这推翻了按“无同名测试文件/无直接 import”得到的“24 个模块完全无测试”判断；五个
聚焦子包也已有功能路径覆盖。payload 临时目录 ACL 继承实现已显式启用并重置
继承，失败断言会输出保护状态与 SDDL；完整 Coverage 模式 561 项测试全绿后，正式
逐模块基线已写入 `tests/coverage_baseline.json`，`coverage_guard.py check` 通过。
基线本身属于生成的质量棘轮，不参与被测源码身份计算，避免首次冻结后自失效。

当前 0.1.5 RC2 报告覆盖 108 个 Python 文件、10,421 条可执行语句：577 项测试
全绿，8,537 条语句和 1,902/3,050 个分支被覆盖，Coverage.py 综合覆盖率约
77.5%，`coverage_guard.py check` 通过。正式基线保留首次冻结值，只作为不回退下限，
不因后续提升自动改写。

当前最低覆盖区域主要集中在 GUI 启动、Explorer 恢复、源码 CLI 和 Windows 组合
适配器。后续按故障风险补分支测试，不为提高总百分比机械测试简单数据类；测试目录
重组也在正式基线冻结后进行，避免同时改变测量工具和发现规则。

### 3.2 1.0 质量收口

67 个 `test_*.py` 已分为 `unit/integration/release` 三个测试包，数量分别为
24、32、11；`tools/test.py` 显式遍历三个发现根。迁移前冻结的 577 项测试在所有
quick/affected、integration 和 release 模式中均无漏测、无重复，新增目录与发现
契约后为 579 项。目录整理未改变 Coverage 的生产源码范围或逐模块棘轮。

目录稳定后补 GUI 启动、Explorer 恢复、Windows 组合适配器和 Task Scheduler 错误
分支。随后以单文件为单位评估 `configuration_service.py`、
`maintenance_service.py`、`automation/backend.py`、`scheduled_auto.py` 和
`accent_service.py`；只有 strict 零诊断的文件才加入 `[tool.pyright].strict`。

任务桥只先建立只读耗时基线。没有达到 [ROADMAP_1.0.md](ROADMAP_1.0.md) 的性能
阈值时，维持现有每次启动隔离 PowerShell 进程的实现是合格结论；不把无数据的批量
桥接当作代码质量提升。

## 4. 自动验证

```powershell
python tools/test.py quick stage15
python tools/test.py affected stage15
python tools/test.py integration
uv run --group quality python tools/test.py coverage
```

`tests/unit/test_errors.py` 自动发现产品源码中的自定义异常并校验根类；
`tests/release/test_quality_contracts.py` 校验测试目录结构、单一调度协议、热点入口长度、Workbench
稳定字段、内部依赖无环，以及 Pyright 全源码/strict 棘轮只有一个 TOML 配置来源。

当前 Pyright 为零诊断，Ruff lint/format 通过；最终 integration、Coverage 守卫与
复杂度报告在每轮收口时重新生成，不在此复制历史候选的旧测试数。
