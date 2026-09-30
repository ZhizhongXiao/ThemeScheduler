# 任务计划集成契约

状态：`0.1.3` v2 任务拓扑当前契约；0.1.2 实机验收继续有效
适用范围：Windows 11 x64、当前用户安装、单根任务

## 1. 目的

本文冻结 Windows 任务计划程序的拓扑、任务定义、读取比较、更新恢复和验收边界。任务计划程序只负责按时启动；主题目标始终由同一个 `auto` 核心依据实际本地时间决定。

真实任务操作必须另行取得明确授权；一次授权不延伸到后续安装、升级或其他实机写入。

## 2. 冻结拓扑

第一版只注册一个任务：

```text
\ThemeScheduler
```

当前期望定义包含四个每日触发器：

| 触发器 ID | 默认本地时间 | 动作 |
| --- | --- | --- |
| `DayPrepare` | `06:10` | `ThemeScheduler.exe auto` |
| `DayBoundary` | `06:15` | `ThemeScheduler.exe auto` |
| `NightPrepare` | `23:40` | `ThemeScheduler.exe auto` |
| `NightBoundary` | `23:45` | `ThemeScheduler.exe auto` |

触发器 ID 只用于读取、诊断和修复，不传入核心，也不表示应强制应用哪个配置。两个边界时间必须不同，预提醒时间由对应边界减 5 分钟派生；更新时间时以完整任务定义替换旧定义，不追加触发器。

不设置登录、解锁、系统恢复或周期轮询触发器。这样用户在两个固定边界之间通过 Windows 设置进行的手动调整不会被无条件同步覆盖。

### 2.1 通知与延迟触发器

交互预通知保持一个根任务和同一个 `ThemeScheduler.exe auto` 动作，`themescheduler.task-spec` 已升级为 v2：

| 触发器 ID | 类型 | 时间 |
| --- | --- | --- |
| `DayPrepare` | 每日 | `dayStart-5min` |
| `DayBoundary` | 每日 | `dayStart` |
| `NightPrepare` | 每日 | `nightStart-5min` |
| `NightBoundary` | 每日 | `nightStart` |
| `DeferredPrepare` | 可选一次性 | `deferredAt-5min` |
| `DeferredBoundary` | 可选一次性 | `deferredAt` |

两项 `Deferred*` 必须同时存在或同时不存在，并与受信任的一次性覆盖状态绑定。用户在预通知中选择：

- `确认` 或无操作：不改变当前执行点；
- `跳过本次`：执行点不应用主题；`SKIPPED` 状态保留为当前 occurrence 的抑制标记，
  直到 `nextFixedAt`；同一 occurrence 的后续任何 auto 入口继续返回 `SKIPPED`，不调用核心主题事务；
- `延迟 30 分钟`：把当前执行点及其预通知整体后移 30 分钟，以新的 `DeferredPrepare`/`DeferredBoundary` 完整替换旧临时对；例如原定 `T` 改为 `T+25min` 再提醒、`T+30min` 再执行。

延迟动作会立即废止上一轮 nonce；新的 `DeferredPrepare` 到点后才生成新 nonce 并显示相同三个按钮，因此可以再次延迟且每轮都有完整五分钟提示。任何延迟不得越过下一固定昼夜边界。执行或跳过后，任务定义中都要删除并读回验证临时 `Deferred*` 触发对；`SKIPPED` pending 标记本身保留到 `nextFixedAt`，即使关闭状态通知也不得提前删除。任一 auto 在 `now >= nextFixedAt` 时先 supersede 并清除旧标记，再处理新固定周期，因此正确性不依赖下一次提醒触发。

## 3. 动作与正式入口

安装后的正式动作必须是：

```text
Executable       = <绝对安装路径>\ThemeScheduler.exe
Arguments        = auto
WorkingDirectory = <绝对安装目录>
```

动作不接受 `day`、`night` 或触发器 ID 参数。程序和工作目录不得使用环境变量、相对路径或当前源码目录。

`python -m theme_scheduler.cli.auto run --confirm-live-auto-write` 是实机开发命令，不是正式计划任务入口。正式 `ThemeScheduler.exe auto` 仅在安装形态中启用：它使用默认用户数据目录、无测试时间覆盖，并保留环境校验、严格数据校验、命名互斥、稳定退出码和中断恢复。源码命令若用于临时实测，必须采用独立任务名称和另行授权，验收结束后删除，不能冒充最终安装定义。

正式可执行文件必须使用无控制台的 Windows GUI 子系统构建。任务保持 `Hidden=false` 以便检查，但正常调度启动不得弹出控制台窗口；不得通过隐藏任务、隐藏命令行或额外包装脚本掩盖控制台构建问题。应用自身调用任务计划 PowerShell 桥接时还必须使用 `CREATE_NO_WINDOW` 和隐藏启动信息，保证 GUI 启动概况读取、检查和修复任务时不闪出子进程控制台。

## 4. 用户和任务设置

冻结设置如下：

| 项目 | 值 |
| --- | --- |
| 用户 | 安装该产品的当前用户，可读回为规范用户标识 |
| 登录类型 | `InteractiveToken`，仅用户登录时运行 |
| 运行级别 | `LeastPrivilege` |
| 错过后补运行 | 开启 |
| 唤醒设备 | 关闭 |
| 多实例 | `Queue` |
| 电池供电启动 | 允许 |
| 转为电池供电后停止 | 关闭 |
| 需要网络 | 否 |
| 隐藏任务 | 否 |
| 执行时间限制 | 5 分钟 |

交互式当前用户上下文是主题应用的必要边界；第一版不保存用户密码，不在未登录会话中运行，也不申请管理员权限。

Task Scheduler 按 `Queue` 在当前实例结束后启动排队实例，并在五分钟执行上限时终止超时任务。scheduled auto 最多等待主互斥体 30 秒；锁争用或明确的调度器临时故障会安排 future AutoRetry。`auto` 始终按实际启动时间计算目标。

主互斥体超时后，本次 auto 放弃核心处理，确认未持有主锁，再单独取得 scheduler-mutation 互斥体安排一次性 `AutoRetry`；这条路径不再尝试主锁。正常写操作按 execution mutex → scheduler-mutation mutex 的顺序取锁。已有 `start_at > now` 的 Retry 会原样保留；没有未来预约时，以 `ceil_to_whole_minute(now + 1 minute)` 创建新预约。过期 Retry 不作为恢复保障。主题应用或验证失败（包括已验证回滚成功）、不可信数据和未验证成功的回滚均不安排 Retry。

期望 TaskSpec 只接受四种精确触发器集合：四个固定触发器；固定触发器加 `AutoRetry`；固定触发器加完整 `DeferredPrepare`/`DeferredBoundary` 对；或固定触发器加延后对和 `AutoRetry`。只有 `defer_count > 0` 且决策不是 `SKIPPED` 时才派生延后对。auto 持锁后严格读取 pending，先清理已到 `nextFixedAt` 的旧 pending，再派生期望任务并对账，最后才处理 WAIT、APPLY 或 SKIP。

## 5. 时间语义

四个固定触发器使用本地墙上时间和每日间隔 1。适配器不得自行把配置时间固定换算为 UTC。创建时的 `StartBoundary` 日期只是 Task Scheduler 所需的锚点；读取比较时只规范化并比较本地 `HH:mm`、每日间隔、启用状态和稳定 ID。可选延后触发对保存带 UTC 偏移、整分钟对齐的绝对执行时间，两者必须精确相差 5 分钟。

时区变化、夏令时、休眠、关机和手动校时之后，以 Windows 实际启动进程时的带偏移本地时间为准。程序不补算历史配置，也不信任触发器名称。

## 6. 规范模型和读取比较

`scheduler/models.py` 与 `scheduler/specification.py` 定义 `themescheduler.task-spec` v2，并只为识别、修复阶段 5 已安装定义而兼容读取 v1。期望定义严格包含：

- 根任务路径和任务启用状态；
- 四个稳定 ID 的每日触发器，以及零个或两个稳定 ID 的一次性延后触发器；
- 可执行文件、参数和工作目录；
- 当前用户、登录类型和运行级别；
- 补运行、唤醒、多实例、电池、网络、隐藏和超时设置。

Windows 读取器必须把实际任务规范化为同一模型。实际定义可以包含错误值或额外旧触发器，以便 `check` 报告字段级差异；只有用于注册的期望定义必须满足全部冻结约束。

检查结果区分：

- `absent`：任务不存在；
- `valid`：所有受管字段一致；
- `drifted`：任务存在，但至少一个字段不一致；
- `unreadable`：Windows 返回的定义无法安全解析或访问失败。

任务运行历史、上次运行结果和下次运行时间属于诊断信息，不进入定义一致性比较。

## 7. Windows 适配边界

正式适配器使用隔离的 Windows PowerShell 进程访问 Task Scheduler 2.0 COM API。主进程只通过版本化 JSON 请求和结果交互，并负责超时、退出码和结构校验。桥接负责：

- 解析当前用户的规范标识；
- 读取并规范化完整任务定义；
- 以一个注册调用替换完整单任务定义；
- 设置任务启用状态；
- 删除任务；
- 返回原始错误码和必要的非本地化字段。

`schtasks.exe` 可保留为人工诊断工具，不作为正式读取比较的数据源；不得解析其随系统语言变化的表格文本。

`python -m theme_scheduler.cli.scheduler plan` 和 `verify-snapshot` 完全离线，不调用 COM，也不访问或修改任务计划程序。正式产品另提供：

- `probe`：只读连接和当前用户 SID 探针；
- `check`：只读读取并与配置生成的期望定义比较；
- `apply`：受确认保护的创建或完整修复；
- `enable`、`disable`、`delete`：受确认保护的维护动作。
- `run`：使用独立的 `--confirm-live-task-run` 立即启动已注册动作，不修改任务定义。

所有写入命令缺少 `--confirm-live-task-write` 时，必须在连接 COM 前拒绝执行；立即运行缺少 `--confirm-live-task-run` 时同样必须拒绝。

## 8. 创建、更新、修复与删除

创建、更新和修复使用同一个协调流程：

```text
严格生成期望定义
→ 读取并保存原完整定义
→ 已一致则 no-change
→ 一次性注册完整期望定义
→ 重新读取并逐字段比较
→ 一致则提交成功
→ 失败则恢复原定义；原来不存在则删除可能的部分定义
→ 再次读取验证恢复结果
```

单任务完整替换避免“双任务只成功一项”的部分状态，也确保改时后没有旧触发器残留。注册进程若在读回前中断，下一次 `check/repair` 依据 `config.json` 重新生成期望定义并收敛，不依赖上一次进程内状态。

删除先读取目标任务，只删除精确任务路径，并读回确认不存在。任务本来不存在视为幂等成功。卸载流程仍应先禁用、停止正在运行的实例，再删除任务。

## 9. 配置与任务的一致性

`scheduler/` 不修改 `config.json`。期望触发器总是由已经严格加载的配置生成。

配置与 Windows 任务不属于同一原子存储，配置界面后续必须采用可恢复的业务事务：

```text
验证新配置和新任务定义
→ 保存旧配置与旧任务定义
→ 原子保存新配置
→ 注册并读回新任务
→ 失败则恢复旧配置和旧任务
→ 最终运行 check
```

进程在两项提交之间中断时，下次自检以有效 `config.json` 为期望源，明确报告并修复任务漂移。不能在任务更新失败时仍向用户显示完整保存成功。
