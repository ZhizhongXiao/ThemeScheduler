# 产物保留与清理

本文定义 ThemeScheduler 的发布产物边界。`artifacts/` 不进入 Git；普通源码克隆不
依赖其中内容，正式二进制通过 GitHub Release 单独分发。

## 目录职责

| 路径 | 用途 | 保留策略 |
| --- | --- | --- |
| `releases/<版本>/` | 已验收正式发布及自包含证据 | 当前版本保留；被替代版本按清理计划废弃 |
| `acceptance/<批次>/` | 不可由自动测试替代的实机结论 | 保留当前版本精简证据 |
| `quality/`、`test-reports/` | 自动门禁与 Coverage 输出 | 可重建，只保留当前收口报告 |
| `maintenance/` | 当前机器可读保留/清理计划 | 只保留最新有效计划 |
| `build/` | 候选和重复构建 | 正式冻结后可清理 |
| `preview/`、`diagnostics/` | 安全预览和诊断 | 问题关闭后可清理 |
| `cold-archive/` | 被取代正式版本的校验归档 | 依发布策略保留 |

## 1.0.1 正式发布目录

`v1.0.1` 已正式发布。发布源码提交固定为
`889dbe81a3e67e48780f16768e730ce3d695ec52`，`sourceTreeSha256` 为
`5f24ff2c93f5f19b5a5e07d59ce1863c6d93ecc5467deba3678eaaa9657eaf57`。正式目录
`artifacts/releases/1.0.1` 包含：

```text
1.0.1/
├─ dist/                    # Setup、用户说明和公开 SHA-256
├─ evidence/                # 源码、质量、构建、PE 与 payload 证据
├─ release-layout.json
└─ SHA256SUMS.txt           # 绑定正式发布根
```

不得只保留 Setup 后宣称发布证据完整。向普通用户分发时只需提供 `dist/` 中的三个
文件。

`1.0.1` 是当前正式版本；`1.0.0` 的发布包、Release 附件和标签继续作为历史版本保留。
新版本必须从对应源码身份重新构建，不得用旧候选改名替代。后续候选继续留在 `build/`，
不得覆盖当前正式发布。发布后的文档提交不改变 `v1.0.1`、Release 附件或上述源码身份。

## D-12 清理规则

1. 永久保护当前正式发布；废弃被替代版本前，先确认新版本正式发布且附件可下载；
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
