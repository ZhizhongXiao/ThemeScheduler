# 测试指南

日常开发按影响范围选择最小充分测试：

```powershell
python tools/test.py quick <group>
python tools/test.py affected <group>
python tools/test.py integration
uv run --group quality python tools/test.py coverage
python tools/test.py release
```

- `quick`：单组件快速反馈；
- `affected`：一个功能簇及相关契约；
- `integration`：跨组件离线回归；
- `coverage`：运行 integration 集合并生成行/分支报告，不重复执行静态检查；
- `release`：全量测试、编译、锁文件、Pyright、Ruff lint/format 和发布契约门禁。

`0.1.5 RC2` 的资格门禁为 577 项 release 测试通过，Coverage 逐模块棘轮通过。
数字只描述该次源码身份，不作为未来固定断言；后续文档和测试结构变化后必须生成
新的报告。完整外观人工实机矩阵也已于 2026-09-13 通过并恢复测试基线。

真实主题、任务、安装和卸载不由通用命令自动执行，必须使用对应验收清单并单独
授权。干净设备生命周期见 [CLEAN_MACHINE_ACCEPTANCE.md](CLEAN_MACHINE_ACCEPTANCE.md)，
覆盖率和静态质量规则见 [CODE_QUALITY.md](CODE_QUALITY.md)。

`0.1.5` 回归必须覆盖：config v1→v2 兼容读取、完整 GUI API 契约、权威当前外观
导入、两种模式和两个强调色显示位置的计划传递、主题与四项注册表的复合回滚、
安装恢复点 v1/v2 兼容，以及自动入口不再学习 profile。真实 Windows 验收放在全部
自动门禁和候选构建之后。

## 1.0 测试治理顺序

0.1.5 完整外观实机矩阵已经通过；现按 [ROADMAP_1.0.md](ROADMAP_1.0.md)依次执行：

1. **已完成**：测试迁移到 `tests/unit`、`tests/integration` 和 `tests/release`；
2. **已完成**：`tools/test.py` 显式遍历三个测试包，保持 quick/affected 的功能簇语义；
3. **已完成**：补 GUI 启动、Explorer 恢复、Windows 组合适配器和任务桥失败分支；
4. 在新增安全网下逐文件扩大 Pyright strict；
5. 最后建立任务桥只读性能基线。

迁移前记录已保存为忽略的质量证据；迁移后的全部模式无原测试缺失或重复，且根目录
不再接受 `test_*.py`。Ruff 逐文件规则已递归化，发布源码清单继续递归纳入测试，
`MANIFEST.in` 无需列举测试子包。Coverage 的生产源码范围和逐模块下限保持不变。

高风险测试以故障是否会造成系统误写、丢失恢复点、遗漏任务或留下进程为排序依据。
真实 Windows API 难以稳定自动化时，优先使用结构化 Fake/Mock 固定输入、调用顺序、
超时、隐藏窗口参数、错误类型和回滚结果，不为提高总覆盖率机械测试简单数据类。

阶段 C 的直接入口是 `tests/integration/test_gui_runtime.py`、
`tests/integration/test_explorer_recovery.py`、完整外观事务测试和
`tests/integration/test_scheduler_windows.py`；这些测试不执行真实 Explorer 重启、
任务写入或 Windows 外观写入。

任务桥性能测量不进入普通 integration。基准必须单独记录环境、预热、样本数、p50、
p95 和最大值；默认只读现有产品任务。只有完整健康检查 p50 超过 1 秒且多进程启动
占总耗时至少 50%，才建议实现批量只读桥接。
