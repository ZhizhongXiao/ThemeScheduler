# 产物保留与清理

本文定义 ThemeScheduler 的发布产物边界。`artifacts/` 不进入 Git；普通源码克隆不
依赖其中内容，正式二进制通过 GitHub Release 单独分发。

## 目录职责

| 路径 | 用途 | 保留策略 |
| --- | --- | --- |
| `releases/<版本>/` | 已验收正式发布及自包含证据 | 当前版本完整保留 |
| `acceptance/<批次>/` | 不可由自动测试替代的实机结论 | 保留当前版本精简证据 |
| `quality/`、`test-reports/` | 自动门禁与 Coverage 输出 | 可重建，只保留当前收口报告 |
| `maintenance/` | 当前机器可读保留/清理计划 | 只保留最新有效计划 |
| `build/` | 候选和重复构建 | 正式冻结后可清理 |
| `preview/`、`diagnostics/` | 安全预览和诊断 | 问题关闭后可清理 |
| `cold-archive/` | 被取代正式版本的校验归档 | 依发布策略保留 |

## 当前正式发布

`0.1.3` 位于 `artifacts/releases/0.1.3`，目录必须整体保留：

```text
0.1.3/
├─ dist/                    # Setup、用户说明和公开 SHA-256
├─ evidence/                # 源码、质量、构建、PE 与 payload 证据
├─ release-layout.json
└─ SHA256SUMS.txt           # 绑定正式发布根
```

不得只保留 Setup 后宣称发布证据完整。向普通用户分发时只需提供 `dist/` 中的三个
文件。

`0.1.5 RC2` 只位于 `build/` 和对应精简验收目录，是 1.0 功能资格候选，不得移动到
`releases/1.0.0` 或仅改名发布。完成 1.0 工程收口后必须从统一升版的源码重新生成
最终候选；通过门禁、双构建、Defender 和生命周期验收后，才整体冻结
`artifacts/releases/1.0.0`。

## D-12 清理规则

1. 永久保护当前正式发布；
2. 真实清理前先运行 `python tools/artifacts.py plan`，不得覆盖既有计划；
3. 用 `python tools/artifacts.py verify --plan <计划>` 复核路径、文件数、大小和
   树哈希；
4. 清理必须再次取得明确授权；
5. 不删除计划生成后发生变化的目标；
6. 不递归删除项目根、`releases/` 或计划未列出的路径。

当前路径迁移后的有效计划是
`artifacts/maintenance/stage18-retention-plan.json`。旧阶段计划和流水账已在
`0.1.2` 结项时移出并由用户删除。

## GitHub 边界

`.gitignore` 排除整个 `artifacts/`、虚拟环境、缓存、覆盖率数据库和本地日志。
GitHub 仓库保存可重建源码；GitHub Release 上传冻结 `dist/`，不要提交展开 payload、
WebView2 用户数据、测试日志或机器专属验收文件。
