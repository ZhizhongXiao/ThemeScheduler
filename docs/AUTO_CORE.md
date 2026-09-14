# ThemeScheduler 自动切换核心契约

状态：`0.1.5` 候选契约。

本文定义时间决策、可信边界、单实例、复合主题事务、退出码和中断恢复语义。持久化结构以 [PERSISTENCE.md](PERSISTENCE.md) 为准，Windows 主题接口以 [DESIGN.md](DESIGN.md) 为准。

## 1. 范围

`auto` 核心不创建、修改或删除真实计划任务；任务定义与协调由任务计划集成层负责。

`auto` 不得：

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
- `lastResult=success` 且 `activeProfile` 已等于当前目标：返回 `no-change`，不重复应用。
- 其他可信状态：生成 `apply` 计划。

自动入口不得学习或覆盖昼夜外观。`profiles/day.json` 和 `profiles/night.json` 只由
首次安装初始化、GUI 显式编辑或“导入当前 Windows 外观”更新；自动边界只消费
已经验证的计划。旧事务中的 `learnProfile` 仅为历史日志兼容字段，新事务固定为 `null`。

## 4. 复合主题事务

正式路径不采用多个互不回滚的独立写入步骤。

正式路径从当前主题描述生成受管副本。标准主题直接复用其 `[VisualStyles]`；Windows
11 新式主题缺少该节时，以实时 WinRT/注册表状态只在事务副本中补齐应用所需字段，
不得修改原活动主题。受管副本只改变：

- `VisualStyles.AutoColorization`；
- `VisualStyles.ColorizationColor`；
- `VisualStyles.AppMode`；
- `VisualStyles.SystemMode`。

应用前同时快照 `AppsUseLightTheme`、`SystemUsesLightTheme` 以及 Personalize/DWM
两处 `ColorPrevalence` 的存在性、类型和值。两个强调色显示位置按计划写入，随后用
隔离的 `IThemeManager2` 一次应用管理主题，并验证两种模式、颜色、显示位置和主题
管理器转换。原始 `before.theme`、可应用的 `rollback.theme` 与注册表快照共同构成
回滚依据；任一验证失败都必须恢复两者，不能提交半应用状态。壁纸、声音、光标、
图标、`.A/.W` 变体和已有主题选择器均不得因补全过程被替换。

阶段 1 的直接注册表写入原型已经退役，不作为维护或回滚兜底。自动与显式手动应用都使用同一复合主题事务，回滚依次尝试恢复原主题索引和完整主题备份。

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
| `partial-failure` | 无法证明完整目标或完整回滚 | 尽力记录 `partial`，阻止后续自动写入 | 40 |
| `fatal-failure` | 未分类故障 | 不声称成功 | 50 |

日志写入失败不得撤销已经验证且已提交的 Windows 结果，但必须在命令输出中报告 `partial-failure`。通知只是日志之外的后备展示，失败不改变核心事务判定。

## 6. 单实例锁

正式实现采用当前会话 `Local\` 命名互斥体：

```text
Local\ThemeScheduler.Auto.<data-root-hash>
```

哈希使用规范化、大小写折叠后的数据根绝对路径，名称不暴露用户名或完整路径。锁为非阻塞取得，覆盖可信加载、Windows 应用、状态提交、日志和运行事务清理的完整周期。

进程退出或崩溃后由 Windows 释放互斥体。测试通过 `ExecutionLock` 替身验证占用和释放，不依赖真实全局锁。

运行事务清理只能在锁内进行，并必须把当前自动事务和当前强调色事务作为受保护目录传入。

## 7. 自动事务与中断恢复

正式编排需要在强调色事务旁保存 `themescheduler.auto-transaction` v1 清单，至少记录：

- 唯一事务 ID 和带偏移时间戳；
- 目标时段；历史 `learnProfile` 字段保留但新事务固定为 `null`；
- 运行前状态文档 SHA-256；
- 关联的强调色事务目录；
- `planned`、`windows-verified`、`state-committed`、`completed`、`failed` 或 `partial` 状态；
- 失败代码和回滚结果。

恢复规则：

- `planned` 中断：Windows 和状态尚未提交，保留失败证据后可重新计划；
- `windows-verified` 中断：只有当前 Windows 仍匹配完整目标且运行前状态哈希未变化时，才能补交状态；
- `state-committed` 中断：补写恢复日志并完成清单，不重复应用；
- 清单损坏、状态哈希变化或 Windows 无法匹配：返回 `data-untrusted` 或 `partial-failure`，等待显式修复。

这套清单用于覆盖“Windows 已改变但状态尚未提交”的崩溃窗口，不能用默认状态或静默重跑掩盖。

## 8. 实现契约

`core.py` 提供无副作用的决策层：

- `SystemClock` / `Clock`；
- `target_profile_at`；
- `plan_auto_run`；
- `AutoPlanKind`、`AutoResultKind` 和 `AutoExitCode`；
- `ExecutionLock`；
- `mutex_name_for_data_root`。

`accent_theme.py` 和 `accent_service.py` 接受完整外观目标；对旧配置省略的新字段保持
原值，直到用户在 GUI 中确认并保存迁移后的昼夜计划。

自动运行使用 `RunIntent.AUTOMATIC`，遵守暂停和无变化判定。设置页的“保存并应用当前时段”在计划提交成功后，通过独立 `ManualAppearanceService` 使用 `RunIntent.MANUAL_CURRENT`；它允许暂停时显式应用、不会解除暂停。旧 `force_apply` 入口已删除。

正式实现还包括：

- `execution_lock.py`：当前会话 Windows 命名互斥体，非阻塞取得并可靠释放；
- `auto_transaction.py`：严格的 `themescheduler.auto-transaction` v1、状态哈希绑定和相邻状态转换；
- `automation/backend.py`：Windows 能力探针和系统适配；
- `automation/recovery.py`、`failure.py`：未完成事务恢复与失败收敛；
- `automation/runner.py`：可信加载、完整外观复合应用、状态提交、日志和清理编排；
- `python -m theme_scheduler.cli.auto`：只读 `plan` 和双确认保护的实机开发命令 `run`。

真实命名互斥体已完成瞬时冒烟：第二实例被拒绝，释放后可重新取得。该测试没有持久化系统修改。

