# ThemeScheduler GUI 字体子集

`ThemeSchedulerSourceHanSans.otf` 来源于 Adobe Source Han Sans SC Regular
2.005（思源黑体简体中文常规体），只保留工作 GUI、Setup、共享生命周期
前端和 Uninstall 当前 HTML、CSS、JavaScript 使用的字符及基础 ASCII。

- 原字体：`SourceHanSansSC-Regular.otf`
- 官方发布资产：`09_SourceHanSansSC.zip`（Adobe 2.005R）
- 官方压缩包 SHA-256：`EF7364F7AC2564BE1AE9C1D74276DE2653FE38B73449070398C4FC0B7E032FF1`
- 原字体 SHA-256：`F1D8611151880C6C336AABEAC4640EF434FA13CBFBF1FFE82D0A71B2A5637256`
- 当前子集大小：约 179 KB；完整原字体约 16.5 MB
- 内部字体族名：`ThemeScheduler Source Han Sans`
- 许可：SIL Open Font License 1.1，见 `OFL-1.1.txt`
- 打包：`ui/` 已作为 PyInstaller 数据目录整体收集，无需单独增加
  spec 条目

字体子集属于修改版本，因此内部 family/full/PostScript 名称均已改为项目专用名称，
不继续使用上游保留字体名称。后续若增加新的 GUI 文案，应使用 FontTools
从同版本 SC Regular 原字体重新生成子集：

```powershell
python tools/build_gui_font.py `
  --source "$env:LOCALAPPDATA\Microsoft\Windows\Fonts\SourceHanSansSC-Regular.otf"
```

该工具默认扫描 `ui/` 内的 HTML、CSS 和 JavaScript，并同时更新
项目专用 family/full/PostScript 名称。
