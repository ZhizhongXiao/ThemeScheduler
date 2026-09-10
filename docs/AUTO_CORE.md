# ThemeScheduler 自动切换核心契约

状态：`0.1.2` 当前契约。

本文定义时间决策、可信边界、单实例、复合主题事务、退出码和中断恢复语义。持久化结构以 [PERSISTENCE.md](PERSISTENCE.md) 为准，Windows 主题接口以 [DESIGN.md](DESIGN.md) 为准。

## 1. 范围

`auto` 核心不创建、修改或删除真实计划任务；任务定义与协调由任务计划集成层负责。

`auto` 不得：

- 修改 `SystemUsesLightTheme` 或管理主题的 `SystemMode`；
- 依赖触发器名称决定昼夜目标；
- 自动重启 Explorer；
- 在配置、状态或目标强调色配置不可信时修改 Windows；
- 把经典六个强调色注册表字段作为正式恢复源。

## 2. 时间决策

时钟必须返回带 UTC 偏移的当前本地时间。测试使用注入时钟，生产入口使用 `datetime.now().astimezone()`。

区间是以本地墙上时间表示的圆环：

- `dayStart` 包含在昼间；
- `nightStart` 包含在夜间；
- `dayStart < nightStart` 时，`dayStart ≤ now < nightStart` 为昼间；
- `dayStart > nightStart` 时，`now ≥ dayStart` 或 `now < nightStart` 为昼间；
- 两个边界相同继续由配置契约拒绝。

夏令时、手动校时、休眠和关机恢复后不补算历史目标，只按实际启动时的本地墙上时间计算一次当前目标。

## 3. 可信加载与计划

单实例锁取得后，依次严格加载配置、状态和目标强调色配置。

- 配置或状态缺失、损坏、版本未知：返回 `data-untrusted`，不修改 Windows。
- `paused=true`：返回 `paused`，不计算或携带写入目标。
- `lastResult=success` 且 `activeProfile` 已等于当前目标：返回 `no-change`，不学习、不应用，保留本时段内用户的手动调整。
- 其他可信状态：生成 `apply` 计划。

只有同时满足以下条件才能学习即将离开的强调色：

1. `lastResult=success`；
2. `activeProfile` 为 `day` 或 `night`；
3. `activeProfile` 与当前目标不同；
4. 当前活动主题可以严格解析；
5. 捕获结果可以严格写入对应配置并读回。

`never`、`failed`、`partial` 或 `activeProfile=null` 均跳过学习。跳过学习不等于允许使用损坏状态；损坏状态在加载阶段已经停止。

## 4. 复合主题事务

阶段 4 不采用“先写 `AppsUseLightTheme`，再套用保留旧 `AppMode` 的强调色主题”的串行组合。该顺序可能让后一步恢复旧应用模式。

正式路径必须从当前完整主题生成一个管理副本，并在同一副本中只修改：

- `VisualStyles.AutoColorization`；
- `VisualStyles.ColorizationColor`；
- `VisualStyles.AppMode`。

`VisualStyles.SystemMode` 必须保持调用前原值。随后用隔离的 `IThemeManager2` 一次应用并同时验证应用模式、强调色、Windows 模式和主题管理器转换。完整 `before.theme` 是该复合事务的回滚依据。

阶段 1 的 `theme.py` 仍保留为单独验证和维护能力；`auto` 不应把它与强调色服务串成两个不可统一回滚的 Windows 写入。

## 5. 状态提交与失败分级

状态只能在复合主题完成读回验证后提交。`activeProfile` 和 `lastAppliedProfile` 只在确认目标生效后更新。

| 结果 | Windows 可能变化 | 状态处理 | 退出码 |
| --- | --- | --- | ---: |
| `applied` | 已验证为目标 | 提交 `success` | 0 |
| `no-change` | 无 | 保持可信状态 | 0 |
| `paused` | 无 | 不变 | 10 |
| `already-running` | 无 | 不变 | 11 |
| `data-untrusted` | 无 | 不覆盖损坏证据 | 20 |
| `apply-failed-rolled-back` | 已恢复调用前状态 | 记录 `failed`；活动归属不前移 | 30 |
| `partial-failure` | 无法证明完整目标或完整回滚 | 尽力记录 `partial`，下次禁止学习 | 40 |
| `fatal-failure` | 未分类故障 | 不声称成功 | 50 |

成功学习的离开时段配置可以在后续目标应用失败时保留，因为它记录的是切换前已经验证的用户颜色，而不是目标时段归属。

日志写入失败不得撤销已经验证且已提交的 Windows 结果，但必须在命令输出中报告 `partial-failure`。通知只是日志之外的后备展示，失败不改变核心事务判定。

## 6. 单实例锁

正式实现采用当前会话 `Local\` 命名互斥体：

```text
Local\ThemeScheduler.Auto.<data-root-hash>
```

哈希使用规范化、大小写折叠后的数据根绝对路径，名称不暴露用户名或完整路径。锁为非阻塞取得，覆盖可信加载、学习、Windows 应用、状态提交、日志和运行事务清理的完整周期。

进程退出或崩溃后由 Windows 释放互斥体。测试通过 `ExecutionLock` 替身验证占用和释放，不依赖真实全局锁。

运行事务清理只能在锁内进行，并必须把当前自动事务和当前强调色事务作为受保护目录传入。

## 7. 自动事务与中断恢复

正式编排需要在强调色事务旁保存 `themescheduler.auto-transaction` v1 清单，至少记录：

- 唯一事务 ID 和带偏移时间戳；
- 目标时段、可选学习来源；
- 运行前状态文档 SHA-256；
- 关联的强调色事务目录；
- `planned`、`windows-verified`、`state-committed`、`completed`、`failed` 或 `partial` 状态；
- 失败代码和回滚结果。

恢复规则：

- `planned` 中断：Windows 和状态尚未提交，保留失败证据后可重新计划；
- `windows-verified` 中断：禁止学习；只有当前 Windows 仍匹配目标且运行前状态哈希未变化时，才能补交状态；
- `state-committed` 中断：补写恢复日志并完成清单，不重复应用；
- 清单损坏、状态哈希变化或 Windows 无法匹配：返回 `data-untrusted` 或 `partial-failure`，等待显式修复。

这套清单用于覆盖“Windows 已改变但状态尚未提交”的崩溃窗口，不能用默认状态或静默重跑掩盖。

## 8. 实现契约

`core.py` 提供无副作用的决策层：

- `SystemClock` / `Clock`；
- `target_profile_at`；
- `learning_source`；
- `plan_auto_run`；
- `AutoPlanKind`、`AutoResultKind` 和 `AutoExitCode`；
- `ExecutionLock`；
- `mutex_name_for_data_root`。

`accent_theme.py` 和 `accent_service.py` 已兼容可选目标应用模式；省略该参数时保持阶段 2 的“应用模式不变”行为。

阶段 6 曾以 `force_apply=true` 和日志来源 `manual` 验证“立即同步”。阶段 13 已删除全部正式产品入口；`force_apply` 只作为自动核心的测试缝与受控诊断能力保留。计划任务始终使用 `force_apply=false`，用户可见切换只由固定或延迟边界驱动。

正式实现还包括：

- `execution_lock.py`：当前会话 Windows 命名互斥体，非阻塞取得并可靠释放；
- `auto_transaction.py`：严格的 `themescheduler.auto-transaction` v1、状态哈希绑定和相邻状态转换；
- `automation/backend.py`：Windows 能力探针和系统适配；
- `automation/recovery.py`、`failure.py`：未完成事务恢复与失败收敛；
- `automation/runner.py`：可信加载、学习、复合应用、状态提交、日志和清理编排；
- `python -m theme_scheduler.cli.auto`：只读 `plan` 和双确认保护的实机开发命令 `run`。

真实命名互斥体已完成瞬时冒烟：第二实例被拒绝，释放后可重新取得。该测试没有持久化系统修改。

