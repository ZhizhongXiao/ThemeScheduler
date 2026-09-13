# ThemeScheduler 持久化契约

版本：数据契约 v1
状态：`0.1.5` 候选当前契约

## 1. 目的与边界

本文定义稳定的数据契约、损坏语义、日志参数和运行文件保留参数。实现所有者是 `persistence.py`、`config.py`、`state.py`、`backup.py`、`log_policy.py` 与 `storage.py`。

本轮只建立契约和安全基线，不创建真实用户配置、不捕获安装备份、不写运行日志、不修改注册表、任务计划或 Windows 个性化设置。

## 2. 通用持久化规则

- 所有正式 JSON 根必须是对象，采用 UTF-8 和 LF；
- 所有正式文档必须包含精确的 `kind` 和 `schemaVersion`；
- v1 对未知字段、缺失字段、未知版本和错误类型一律拒绝；
- 默认值只允许用于明确的首次安装初始化或用户确认的重置操作，不能作为读取损坏文件后的静默恢复；
- 写入使用同目录临时文件、文件刷盘和 `os.replace`；
- 替换前失败时保持原文件，临时文件尽力清理；
- 残留临时文件不是有效配置，不能被加载为正式状态；
- 未来迁移必须逐版本显式实现，迁移前保留原文件；迁移失败不得覆盖来源。

通用 JSON 读写由 `persistence.py` 独立提供。正式业务组件不得通过 `diagnostics.py` 间接获得持久化能力。

## 3. `config.json`

路径：`%LOCALAPPDATA%\ThemeScheduler\config.json`

```json
{
  "kind": "themescheduler.config",
  "schemaVersion": 1,
  "schedule": {
    "dayStart": "06:15",
    "nightStart": "23:45"
  },
  "profiles": {
    "day": {
      "appsTheme": "light"
    },
    "night": {
      "appsTheme": "dark"
    }
  },
  "notifications": {
    "errors": true,
    "statusChanges": true
  }
}
```

规则：

- 时间必须为本地 24 小时制 `HH:mm`，且昼夜边界不能相同；
- `appsTheme` 只能为 `light` 或 `dark`；
- 通知字段必须为布尔值；
- 文件缺失、非法或版本未知时禁止自动修改系统；
- `AppConfig.defaults()` 只供明确的首次安装初始化使用。

## 4. `state.json`

路径：`%LOCALAPPDATA%\ThemeScheduler\state.json`

```json
{
  "kind": "themescheduler.state",
  "schemaVersion": 1,
  "paused": false,
  "activeProfile": null,
  "lastRunAt": null,
  "lastAppliedProfile": null,
  "lastResult": "never"
}
```

规则：

- `activeProfile` 和 `lastAppliedProfile` 只能为 `day`、`night` 或 `null`；
- `lastResult` 只能为 `never`、`success`、`partial` 或 `failed`；
- 时间戳必须为带 UTC 偏移的 ISO 8601；
- `never` 要求最后执行时间和最后应用时段均为 `null`；
- `success` 要求最后执行时间和最后应用时段均存在；
- `AppState.initial()` 只供显式初始化；
- 普通运行中状态缺失、截断、非法或版本未知时抛出 `UntrustedStateError`，自动系统修改必须停止。

阶段 6 的暂停和恢复继续使用同一个 v1 结构，不增加迁移字段。控制写入只能在取得与 `auto` 相同的命名互斥体、确认没有未完成自动事务并成功严格加载现有状态后执行；只替换 `paused`，其余字段逐值保留。状态提交成功而日志失败时不回滚可信状态，但必须报告部分成功。详细语义见 [CONTROLS.md](CONTROLS.md)。

## 5. `backup/install.json`

路径：

```text
backup/install.json
backup/install.theme
```

schema v1 清单包含原有应用模式和主题恢复信息；0.1.5 新建的 schema v2 清单在此
基础上增加 `appearanceRegistry`。清单固定包含：

- `kind = "themescheduler.install-backup"`；
- `schemaVersion = 1`（兼容读取）或 `2`（新建）；
- 捕获时间、创建程序版本和 Windows build；
- `AppsUseLightTheme` 的存在性、注册表类型和值；
- 原活动主题路径；
- 固定相对备份名 `install.theme`；
- `install.theme` 的小写 SHA-256；
- 原主题的 `AutoColorization`、`ColorizationColor`、`AppMode` 和 `SystemMode`。
- schema v2 的 `AppsUseLightTheme`、`SystemUsesLightTheme`、Personalize
  `ColorPrevalence` 和 DWM `ColorPrevalence` 精确存在性、类型和值。

若任一值原本不存在，schema v2 保留 `exists=false`；恢复时必须重新删除由事务创建的
值。旧 schema v1 保持原范围，不伪造安装时未捕获的三个字段。

安装备份是首次安装恢复点：

- 普通运行、修复和升级不得覆盖；
- 同版本重装和损坏重装也不得覆盖；
- 文件损坏或主题哈希不匹配时不得重新生成并冒充原始恢复点；
- 只有用户明确执行“重新建立恢复点”时，未来维护入口才可创建新恢复点；
- 完整捕获和恢复流程属于阶段 3/安装生命周期后续实现，本轮只冻结清单。

## 6. 损坏处理矩阵

| 数据 | 缺失或损坏时的行为 | 是否自动覆盖 |
| --- | --- | --- |
| `config.json` | 报错并禁止系统修改 | 否 |
| `state.json` | 标记状态不可信，自动执行停止 | 否 |
| `profiles/day.json`、`profiles/night.json` | 对应时段不可应用，也不得污染另一时段 | 否 |
| `backup/install.json` 或 `install.theme` | 禁止声称可自动恢复，保留证据 | 否 |
| `runtime/*/journal.json` | 视为不完整事务，按失败证据保留 | 否 |
| 日志文件 | 新日志可继续轮换写入；不得据日志反推运行状态 | 不适用 |

损坏文件默认原地保留。隔离、重命名或删除必须由未来明确的修复用例执行，不能发生在自动切换路径。

阶段 8 的显式维护、重装和卸载是允许处理损坏文件的受控用例：程序根按 [INSTALLATION.md](INSTALLATION.md) 的独占目录和事务规则整体替换或删除；用户数据只有在用户选择和严格根路径验证后清理。该例外不放宽自动切换的保守策略。

阶段 8.2 的文件切换额外使用程序根内两份原子 JSON 证据：

- `.<transaction-id>.json`：`themescheduler.lifecycle-transaction` v1，记录计划、准备、变更、提交、完成、回滚和部分失败状态；
- `.<transaction-id>.deployment.json`：`themescheduler.deployment-journal` v1，绑定操作、部署前顶层条目、完整旧树 SHA-256/条目数、已移入回滚的旧条目及已激活的新组件。

部署日志的身份与旧树证据不可变，移动/激活数组只能单向增长。`mutating` 或 `partial` 恢复必须把 `.rollback` 内容还原后重新计算完整旧树证据；不匹配时保持 `partial`，不得声称回滚成功。`committed` 只在活动 `app/maintenance`、载荷清单和安装记录全部匹配后出现，恢复时可以复验并完成暂存/回滚清理。

## 7. 日志契约

路径：`logs/events.jsonl`

格式：每行一个 UTF-8 JSON 对象，`kind = "themescheduler.event"`。当前写入
`schemaVersion = 2`；读取器继续接受 v1，并在内存中补齐 v2 默认字段，不要求
为了升级重写或截断既有日志。

固定字段为：

- `occurredAt`；
- `level`；
- `event`；
- `result`；
- `trigger`；
- `targetProfile`；
- `transactionId`；
- `errorCode`；
- `message`；
- `verification`：`passed`、`failed` 或 `null`；
- `rollbackAttempted`；
- `rollbackSucceeded`：`true`、`false` 或 `null`，仅在已经尝试回滚时可用。

不允许任意 `details` 对象。消息最多 500 字符，不能记录用户名、邮箱、完整环境转储、主题内容或不必要的绝对用户路径。
自动切换在建立事务后由运行器自动注入 `transactionId`，调用点无需重复传递；
恢复旧事务时仍使用事务证据中的明确 ID。

轮换参数冻结为：

```text
单文件最大：1 MiB
历史文件数：5
```

## 8. 运行事务保留

- 成功的 `runtime/accent-*`：保留最近 10 个；
- 失败或不完整事务：保留 30 天，并且无论时间至少保留最近 10 个；
- 清理器不得删除当前正在使用的事务；
- 清理实现和并发保护属于阶段 3 正式开发，本轮只冻结参数。
