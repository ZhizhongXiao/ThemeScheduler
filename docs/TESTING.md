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

`0.1.2` 结项时 `integration` 与 `release` 均为 579 项通过，Coverage 逐模块棘轮
通过。数字只描述该次候选，不作为未来固定断言。

真实主题、任务、安装和卸载不由通用命令自动执行，必须使用对应验收清单并单独
授权。干净设备生命周期见 [CLEAN_MACHINE_ACCEPTANCE.md](CLEAN_MACHINE_ACCEPTANCE.md)，
覆盖率和静态质量规则见 [CODE_QUALITY.md](CODE_QUALITY.md)。
