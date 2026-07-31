# 经管论文数据自动处理器

一个在本机运行的问卷数据分析工具。上传 CSV/XLSX、通过双栏选择器构建量表，并按需要添加多条回归路径后，系统会执行可复现的经管论文常用分析，生成 HTML 报告、Excel 结果表、调节图、JSON 配置与完整审计包。

## 已实现

- 验证性因子分析（CFA）：MLW、标准化载荷、CFI/TLI/RMSEA/SRMR、CR、AVE、Cronbach α、HTMT；
- 共同方法偏差：未旋转 PCA/Harman 降维检验、KMO、Bartlett、ULMC trait-only 与 trait+method 对比；
- 描述性统计与 Pearson/Spearman 相关分析，含每对变量实际 N 和 Pearson 置信区间；
- 主效应回归、中介回归与层级调节回归，默认 HC3 稳健标准误、VIF 和影响点诊断；
- 简单中介 Bootstrap 检验，支持 percentile/BCa 置信区间；
- 调节检验、简单斜率、Johnson-Neyman 边界与 95% 置信带图；
- 被调节中介：PROCESS Model 7（第一阶段）与 Model 14（第二阶段），报告条件间接效应和被调节中介指数的 Bootstrap 区间；
- 多模型路径清单：可新增、复制或删除最多 20 条主效应、中介、调节及被调节中介路径；每条路径独立选择 X、Y、M、W、控制变量与调节阶段；
- 量表双栏编辑器：左侧显示可用数值题项，通过箭头移入右侧，并可逐题标记反向计分；同一题项不会被重复分配给多个量表；
- 设置快照：可导出量表、路径、控制变量和统计推断参数，并在上传相同结构的数据后重新导入；
- 每次运行保存数据哈希、实际配置、随机种子、软件版本、日志和机器可读结果。

## macOS 桌面软件

Apple Silicon（arm64）发行包提供 `.dmg` 与 `.zip` 两种格式，支持 macOS 14 及以上版本。下载后可将 `Econ Paper Analyzer.app` 拖入“应用程序”目录并双击运行。桌面版使用应用内 WebKit 窗口和 Python 直连桥接，不会监听 `127.0.0.1` 端口、不需要浏览器，也不存在浏览器页面与后端版本错配的问题。

当前发行包采用 ad-hoc 签名，尚未使用 Apple Developer ID 公证。首次打开时可能需要在 Finder 中右键应用并选择“打开”。正式对外分发前应完成 Developer ID 签名、公证和 stapling。

桌面版分析结果保存在：

```text
~/Library/Application Support/EconPaperAnalyzer/runs
```

## Windows 桌面软件

Windows x64 发行包同时提供两种格式：

- `econ-paper-analyzer-windows-x64-v<版本号>.zip`：免安装版，解压后运行 `econ-paper-analyzer.exe`；
- `econ-paper-analyzer-windows-x64-v<版本号>-setup.exe`：安装版，安装到当前用户的 Programs 目录，创建开始菜单项，可选桌面快捷方式，并在系统设置中提供卸载入口。

当前 Windows 包尚未使用代码签名。首次运行或安装时，Windows SmartScreen 可能显示来源提示；正式对外分发前应使用代码签名证书签名安装程序与主程序。

若通过微信、网盘或浏览器下载了 ZIP，先在 ZIP 文件的“属性”中选择“解除锁定”，再解压；优先使用 `-setup.exe` 安装版。桌面程序启动时也会自动清除自身 Python 运行组件的下载来源标记。

## 源码快速启动

macOS 可双击 `run.command`。首次运行会建立项目自己的 `.venv` 并安装依赖，然后自动打开浏览器。默认从以下地址开始；如果端口已占用会自动顺延：

```text
http://127.0.0.1:8765
```

也可以在终端执行：

```bash
cd econ-paper-analyzer
./run.command
```

首次安装需要联网。应用不会把问卷发送到在线分析服务；原始上传副本存放在 macOS 用户临时目录。源码版生成的报告保存在本项目中，因此项目位于云同步目录时，报告也可能随之同步。

## 使用流程

1. 上传 `.csv` 或 `.xlsx`；Excel 可切换工作表。
2. 为每个构念添加量表，从左侧选择题项并用箭头移入右侧；按需标记反向题并设置量表上下限。
3. 添加任意多条路径模型，为每条路径分别选择模型类型、X、Y、M、W、控制变量和调节阶段。
4. 选择全局分析模块、Bootstrap 次数、置信区间和相关方法。
5. 需要复用方案时，在顶栏导出设置；下次先上传数据，再导入该 JSON 设置文件。
6. 先查看预检提示，再运行并下载报告或完整审计包。

界面中的“载入示例”会使用 `examples/demo_survey.csv` 填好主效应、中介、调节和 Model 7 四条路径，可用于验证全流程。

## 输入约定

- 第一行必须是唯一且非空的列名；每行是一名独立受访者。
- 题项以及每条路径中的 X/Y/M/W 和控制变量必须能转换为数值。
- 反向计分采用 `最小值 + 最大值 - 原值`。
- 至少达到量表题项数的 80%（界面可调）时才计算量表均值。
- CFA、Harman 与 ULMC 使用反向处理后的原始题项；回归类分析使用量表得分或用户选择的原始连续变量。
- 首版缺失处理为完全案例；报告会明确实际样本量。
- BCa 区间最多处理 2,500 个完整案例；更大样本请选择 percentile 区间，以避免逐例 Jackknife 带来的过长计算时间。
- Bootstrap 计算预算为“完整案例数 × 抽样次数”不超过 10,000,000；超出时系统会要求降低次数或缩小样本。

## 结果目录

源码版结果保存在项目的 `.runtime/runs/run-*/`；桌面版结果保存在 `~/Library/Application Support/EconPaperAnalyzer/runs/run-*/`。每次运行包括：

- `report.html`：可打印的完整中文报告；
- `tables.xlsx`：论文结果表及绘图数据；
- `results.json`：完整机器结果；
- `analysis_config.json`：可复现配置；
- `model-##_moderation_plot.png/.svg`：各调节路径独立生成的调节效应图；旧版单模型配置仍使用 `moderation_plot.png/.svg`；
- `analysis.log`：模块运行日志；
- `analysis_bundle.zip`：以上文件的归档。

源码版上传副本保存在 macOS 用户临时目录；桌面版上传副本保存在 `~/Library/Caches/EconPaperAnalyzer/uploads`。两者都可用 `EPA_UPLOAD_ROOT` 自定义且不会写入项目目录。源码版的 `.runtime/runs/` 保存可复现结果并已加入 `.gitignore`。

## 统计边界

当前版本面向横截面、单层、独立观测，以及连续 Y、M、W 的模型。Likert 题项按近似连续变量使用 MLW；少于 5 类且偏态明显的有序题项，正式论文更适合用 lavaan 的 WLSMV/polychoric 流程复核。二分类/有序因变量或中介、分类调节变量、纵向/多层数据、抽样权重、多重插补、平行或链式中介尚未实现，不能直接套用本工具的 OLS 结果。

Harman 检验只用于低灵敏度筛查；“第一主成分低于阈值”不代表共同方法偏差不存在。ULMC 可能不收敛或吸收反向题措辞效应，系统会保留错误而不会把失败解释为“没有偏差”。修改指数不会被自动用于连接残差。

横截面 Bootstrap 中介结果应表述为“间接关联”；若要声称因果机制，还需要时间顺序和更强的研究设计。

## 开发与测试

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python examples/generate_demo.py
PYTHONPATH=. .venv/bin/python -m pytest
PYTHONPATH=. .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

## 构建 macOS 发行包

构建机需要 macOS、Apple Silicon Python 与系统自带的 `codesign`、`ditto`、`hdiutil`：

```bash
.venv/bin/python -m pip install -r requirements-build.txt
./scripts/build_macos.sh
```

脚本结束时会打印临时 staging `.app` 的实际路径；ZIP、DMG 和 SHA-256 校验文件位于 `dist/releases/v<版本号>/`。发行包目录已加入 `.gitignore`，应通过 GitHub Release 上传，不能提交到 Git 历史。当前包使用 ad-hoc 签名且未做 Apple Developer ID 公证；发布前还必须通过 macOS 原生窗口测试。

## 构建 Windows 发行包

Windows 10/11 x64 构建机需要 x64 Python 3.11-3.13、PowerShell、Inno Setup 6 及 Microsoft Edge WebView2 Runtime（Windows 11 通常已内置）：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-build.txt
choco install innosetup --no-progress --yes
.\scripts\build_windows.ps1 -CreateInstaller
```

该脚本生成免安装 ZIP、安装版 `-setup.exe` 和涵盖两者的 `.sha256` 文件，位于 `dist/releases/v<版本号>/`。提交带有 `v*` 标签的 GitHub 仓库时，GitHub Actions 会在原生 Windows runner 自动生成并上传两种格式到对应 Release；手动运行 `Windows Package` 会生成可下载的构建产物。

## 许可

当前项目未授予开源许可。未经权利人明确许可，不得复制、修改或再分发；如需公开开源，请先选择并添加合适的 `LICENSE`。
