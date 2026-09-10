# 用户控制契约

状态：`0.1.2` 当前契约  
当前适用范围：暂停和恢复；产品不提供手动立即同步

## 1. 目的

本文冻结当前的暂停/恢复语义、并发边界、失败结果和验收要求。产品定位为计划部署器，手动立刻套用主题与此定位重叠，也可由 Windows 设置直接完成，因此不再暴露该功能。

## 2. 冻结行为

### 2.1 暂停

暂停只把可信 `state.json` 的 `paused` 从 `false` 改为 `true`：

- 保留 `activeProfile`、`lastRunAt`、`lastAppliedProfile` 和 `lastResult`；
- 不禁用、修改或删除 `\ThemeScheduler`；
- 不修改主题、应用模式、强调色或 Windows 模式；
- 已暂停时再次暂停返回 `no-change`。

计划任务在暂停期间仍可启动 `auto`。自动核心读取 `paused=true` 后记录 `auto.paused` 并退出，不创建新的主题事务。

### 2.2 恢复

D-05 最终修订为：

> 恢复只解除暂停，不自动切换；不提供手动立即套用外观操作。

恢复只把可信 `state.json` 的 `paused` 从 `true` 改为 `false`。当前主题保持不变，正常自动切换等待下一固定边界；已恢复时再次恢复返回 `no-change`。

界面和命令结果必须说明自动切换将在下一计划边界执行。需要临时改变外观时，用户使用 Windows“个性化 → 颜色”。

### 2.3 不提供立即同步

`AutoRunner.force_apply` 只保留为自动核心的测试缝与受控诊断能力，不构成产品入口。

## 3. 并发与中断边界

暂停、恢复和计划任务运行共享按数据根派生的当前会话命名互斥体：

- 不能取得锁时返回 `already-running`，不等待、不写状态；
- 不强行终止已经开始的主题事务；
- 控制状态写入前若存在未完成的自动事务，暂停或恢复必须保守拒绝，先由自动恢复或维护流程收敛；
- `AutoRunner` 的既有中断恢复优先于新的自动计划。暂停阻止新主题应用，但不能抹除或伪造暂停前已经发生的 Windows 事务证据；
- 状态写入使用 `StateStore.save` 的可信加载、原子替换和读回验证。

## 4. 数据与日志

阶段 6 不升级 `themescheduler.state` v1。`paused` 已是严格布尔字段，普通读取不得在缺失或损坏时重建默认状态。

控制日志使用现有 `themescheduler.event` v1：

| 操作 | 事件 | 触发来源 |
| --- | --- | --- |
| 暂停成功/无变化 | `control.paused` | `manual` |
| 恢复成功/无变化 | `control.resumed` | `manual` |

状态提交成功而日志失败时，不回滚可信的暂停状态；结果必须为 `partial-failure` 并明确 `stateChanged=true`、`logWritten=false`。状态提交失败时保留原状态并返回失败。
