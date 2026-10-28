# OxyViewer — 鱼类耗氧率数据可视化分析与计算工具

基于 PyQt5 + pyqtgraph 的 PreSens OXY-10 SMA 溶氧数据桌面分析软件。支持多通道管理、循环参数编辑、GPU 加速渲染、耗氧率(RMR)自动计算。

## 快速开始

### 方式一：下载打包版（推荐）

从 [Releases](https://github.com/haexiao/OxyViewer/releases) 下载，两个版本按需选择：

| 版本 | 计算引擎 | 是否需要安装 R |
|---|---|---|
| `OxyViewer-R.exe` | R（respR + renv） | 需要 |
| `OxyViewer-Python.exe` | Python（resprpy） | **不需要** |

两个版本结果一致，只是默认引擎不同。

### 方式二：从源码运行

#### 环境要求

- **Python 3.10+**（必须）
- **R 4.4+**（可选 —— 只有选择 R 计算引擎时才需要）

#### 首次使用

**双击 `run.bat`** 即可，启动时会让你选择耗氧率计算引擎：

```
[1] R      （respR + renv） 传统引擎，依赖 R 环境
[2] Python （resprpy）     新引擎，无需 R
```

跳过询问可直接带参数启动：`run.bat R` 或 `run.bat python`。

选择之后 run.bat 会自动：

1. 创建 Python 虚拟环境 (`venv/`)
2. 安装 Python 依赖 (`pyqtgraph`, `numpy`, `openpyxl`, `resprpy`)
3. **按所选引擎检查对应依赖**：
   - **R 引擎**：检测 `Rscript`（缺失会提示），R 包装到 `renv/`
   - **Python 引擎**：检测 `resprpy`（缺失自动安装）

选 Python 引擎时完全不需要安装 R。两个引擎结果一致 —— 在 9 通道实测数据上最大相对差 `1.2e-13`（约 76% 的数值逐位相同）。

#### 手动 Python 环境

```bash
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python main.py
```

#### R 环境（可选）

```bash
# 安装 R: https://cran.r-project.org
# 安装 R 包（首次启动自动完成，也可手动）:
Rscript -e "install.packages(c('respR','lubridate','readxl'))"
```

## 操作指南

### 1. 选择数据

- **数据文件夹**：浏览选择包含 `*.xlsx` 文件的文件夹（如 `20260422 20/`）
- **参数文件**：选择 `meas_params.csv`（首次自动从数据文件夹上级 `raw/` 目录推测）
- 点击「加载数据」读取当前通道的 xlsx 文件

### 2. 通道管理

- **通道范围**：可调 1\~N，回车确认
- 每通道可选类型：`Fish`（实验鱼）/ `Blank`（空白对照）/ `特殊`（独立参数）
- 每个通道下方有**排除**输入框，填写该通道不参与计算的循环号（见下一节）
- 点击通道号按钮可切换当前通道并重新加载
- 「保存到通道设置」将类型修改写入 `meas_params.csv`
- 「保存到排除参数」将排除循环写入 `meas_exclude.csv`

### 3. 循环参数

| 参数 | 说明 |
|------|------|
| 循环数 | 只读，由 总时长÷周期 自动计算 |
| 起始偏移 | 实验开始后跳过的时间 (s) |
| 周期 | 只读，测量 + 冲洗 |
| 斜率起点 | 每个周期内开始计算斜率的时间点 (s) |
| 测量 | 关闭水泵测量氧降的时间 (s) |
| 冲洗 | 开启水泵恢复氧浓度的时间 (s) |
| 总时长 | 实验总时长 (s) |

修改后回车 → 自动重算 循环数/周期 → 点「保存到循环参数」写入 CSV。

### 4. 图像设置

- **数据显示**：温度 / 气压曲线开关
- **数据展示**：数据点(大小 1\~6)、数据线(宽度 0.5\~3)、趋势线(宽度 1\~3)
- **斜率计算区**：显示粉红色斜率计算窗口
- **时间格式**：秒 / 分 / 时（切换后瞬时更新，无需重绘）
- **循环切换**：滑块 / 左右箭头快速切换，仅更新高亮和局部视图- **游标信息**：第一行显示鼠标所指位置的时间 / 溶氧 / 温度 / 气压 / **所在循环号**；
  第二行显示**当前所选循环**的斜率、截距、R²（拖滑条即时重算，被排除会标注）

### 5. 排除循环（可选）

某些循环数据异常（探头被碰、气泡、断流等）时，可让它们**不参与耗氧率计算**，
但原始数据、图像仍然完整保留。

1. 在「参数文件 → 排除参数文件」点「**生成排除参数**」：
   会在循环参数文件所在文件夹生成 `meas_exclude.csv`，
   **一个通道一行**（`chamber_ID` 为单个数字），`exclude_cycles` 列留空
2. 在「通道设置」里给对应通道填排除循环，再点「**保存到排除参数**」

排除写法（英文逗号分隔，支持区间）：

| 写法 | 含义 |
|------|------|
| `3` | 只排除第 3 个循环 |
| `3,6,9-14` | 排除第 3、6、9、10、11、12、13、14 个循环 |
| `5-7` | 排除第 5、6、7 个循环 |

- **边输入边刷新**：敲进输入框立刻更新右侧预览（走属性级更新，不重建整幅图，
  与温度/气压开关、时间格式切换同一套机制）
- 回车/失焦时再做规范化（`9,10,11,12` → `9-12`）并对非法写法给出提示
- 输入到一半（如 `9-`）不会清空已有排除，等输入完整再生效
- 被排除的循环在预览图里显示为**灰色**：只有**斜率计算窗口那一块**变灰（不再给整个循环铺灰底），
  配合灰点和灰虚线趋势线；所有斜率窗口都只画填充，边框线和黄色高亮框一律不画
- 切到被排除的循环时，局部视图标题显示「（已排除，不参与计算）」，信息栏也会标注
- 计算时结果文件**行数不变**：这些循环的 `rank` / `row` / `time` / `endtime` / `oxy` / `endoxy` 照常输出，只有代谢结果列（`intercept_b0`、`slope_b1`、`rsq`、`rate.2pt`、`rate`）被置为 `NA`

> 排除设置按「数据日期 + 通道」记录在 `meas_exclude.csv` 中，
> 不同日期互不影响；不选该文件时相当于不排除任何循环。

> 界面提示：左侧栏内容过多时可纵向滚动；长提示文字自动换行，窗口缩小时控件不会被裁切；
> 左右分栏宽度可自由拖动，退出后会记住上次的宽度。

### 6. 数据计算

- 选择导出路径后点击「计算当前通道」或「计算所有通道」
- 左侧「渗透系数文件」可选 `chamber.csv`（**留空则用内置默认值**），选择后会显示读取到的通道数
- 左侧「排除参数文件」可选 `meas_exclude.csv`（**留空则排除任何循环都不生效**）
- 确认对话框显示计算参数、当前引擎、实际使用的渗透系数与排除参数文件，确认后调用 `calc_rmr.R`（R 引擎）或 `calc_rmr.py`（Python 引擎）计算耗氧率
- 计算结果保存为 `rmr{通道号}.csv`

## 数据格式

### xlsx 文件

PreSens OXY-10 SMA 输出，**第 6 个 sheet**：

| 列 | 内容 |
|:--:|------|
| 1 | 日期时间 |
| 6 | 溶氧 (mg/L) |
| 8 | 温度 (°C) |
| 10 | 气压 (hPa) |

### meas_params.csv

UTF-8 BOM, 逗号分隔, 13 列：

| meas_time | meas_type | rmr_type | chamber_ID | meas_batch | temperature | cycles | initial | cycle_length | cycle_start | cycle_time | flush_time | all_time |
|-----------|-----------|----------|------------|------------|-------------|--------|---------|--------------|-------------|------------|------------|----------|

- `chamber_ID`：逗号分隔的通道号，如 `"2,3,4,5,6,7,8,9"` 或单个 `"1"`
- `rmr_type`：`fish`（实验鱼）/ `blank`（空白对照）
- 空白行保留不删，修改参数时按日期+类型+通道匹配

模板见 `templates/meas_params.csv`，也可在 [Releases](../../releases) 页下载。

### chamber.csv（渗透系数）

UTF-8 BOM，两列：

| 列 | 含义 |
|------|------|
| `chamber_ID` | 通道号（1–9） |
| `k_values` | 该通道的渗透系数 |

- 用于计算前的溶氧校正：`oxy = O2 - k × (max_O₂ - O2)`
- **左侧面板不选择该文件时**，脚本使用内置默认值（即模板中的数值）
- 模板见 `templates/chamber.csv`，也可在 [Releases](../../releases) 页下载

### meas_exclude.csv（排除循环）

UTF-8 BOM，6 列。**一个通道一行**（`chamber_ID` 为单个整数）：

| 列 | 含义 |
|------|------|
| `meas_time` | 实验日期，如 `20260422` |
| `meas_type` | `before` / `rmr`（原样复制自 `meas_params.csv`） |
| `rmr_type` | `fish` / `blank` |
| `chamber_ID` | 通道号（**单个**数字） |
| `meas_batch` | 批次 |
| `exclude_cycles` | 该通道要排除的循环号，如 `3,6,9-14`；空 = 不排除 |

- 由界面「生成排除参数」自动按 `meas_params.csv` 展开生成（`"2,3,4"` → 三行）
- 计算时这些循环直接从计算区间里剔除，不出现在结果 CSV 中
- 文件缺失、缺列时界面会明确提示，**不会自动改写用户文件**
- 模板见 `templates/meas_exclude.csv`

## 项目结构

```
oxyviewer/
├── main.py              # 入口 + R 环境初始化
├── viewer.py            # 主窗口 UI + 事件处理
├── plots.py             # pyqtgraph 渲染器（GlobalRenderer/LocalRenderer）
├── data_loader.py       # xlsx 读取 + 参数解析 + 循环边界计算
├── cycle_analyzer.py    # 线性回归斜率计算
├── calc_rmr.R           # R 计算引擎（respR，耗氧率批量计算）
├── calc_rmr.py          # Python 计算引擎（resprpy，与上者接口一致）
├── run.bat              # Windows 一键启动（选择计算引擎）
├── packaging/           # 打包相关文件
│   ├── build.bat        #   一键打包 R 版 / Python 版
│   ├── engine_hook_r.py #   runtime hook：默认 R 引擎
│   ├── engine_hook_p.py #   runtime hook：默认 Python 引擎
│   ├── OxyViewer.ico    #   EXE 图标
│   └── self_test.py     #   渲染自检脚本
├── templates/           # 文件模板
│   ├── meas_params.csv  #   循环参数模板
│   ├── chamber.csv      #   渗透系数模板
│   └── meas_exclude.csv #   排除循环模板
├── logo.png             # 应用图标
├── requirements.txt     # Python 依赖
├── CHANGELOG.md         # 更新日志
└── README.md
```

启动时自动创建的目录：
- `venv/` — Python 虚拟环境
- `renv/` — R 虚拟环境（含 `renv.lock`）

## respR 包说明

本软件的 RMR 计算模块基于 **[respR](https://github.com/januarharianto/respR)** R 包（v2.3.4）。

- **作者**：Nicholas Carey, Januar Harianto
- **简介**：Import, Process, Analyse, and Calculate Rates from Respirometry Data
- **CRAN**：https://CRAN.R-project.org/package=respR
- **许可**：GPL-3

Calling sequence:
```r
respR::inspect(data, time=3, oxygen=4)
respR::calc_rate(dataint, from=..., to=..., by="time")
```

If you use respR in academic work, please cite:

> Carey, N. & Harianto, J. (2025). respR: Import, Process, Analyse, and Calculate Rates from Respirometry Data. R package version 2.3.4. https://CRAN.R-project.org/package=respR

## 许可

MIT License

## 更新日志

完整记录见 [CHANGELOG.md](CHANGELOG.md)。

### v1.5.0
- 新增**排除循环**：可指定某通道的若干循环不参与耗氧率计算
  - 「参数文件 → 排除参数文件」+「生成排除参数」按钮，按循环参数生成
    `meas_exclude.csv`（一个通道一行）
  - 「通道设置」每个通道下方新增排除输入框，支持 `3,6,9-14` 写法（回车即时规范化）
  - 新增「保存到排除参数」按钮，写入后立即刷新图像
  - 预览图中被排除的循环以灰色显示（浅灰底 + 灰点 + 灰虚线趋势线），
    局部视图标题与信息栏标注「已排除，不参与计算」
  - `calc_rmr.R` / `calc_rmr.py` 均新增第 6 个可选参数（排除参数文件），
    被排除循环不出现在结果 CSV 中
- 新增模板 `templates/meas_exclude.csv`

### v1.4.1
- 左侧「参数文件」改为一级区块，下含「循环参数文件」「渗透系数文件」两个二级条目
- 代码清理去重：引擎判定提取为 `engine.py`、标签样式提取为常量

### v1.4.0
- 新增「渗透系数」文件选择（`chamber.csv`）：计算时按文件读取各通道 k 值
  - 留空则使用内置默认值，行为与之前一致
  - R 与 Python 两个引擎都支持
- 新增文件模板 `templates/meas_params.csv` 与 `templates/chamber.csv`（Releases 页可下载）

### v1.5.0
- 新增**排除循环**：可指定某通道的若干循环不参与耗氧率计算（详见 [CHANGELOG.md](CHANGELOG.md)）
- 修复部分 xlsx 无法加载（`<dimension>` 元数据错误、时间列存为文本）
- 修复软件图标不显示（应用级图标 + Windows 任务栏 AppUserModelID）

### v1.3.2
- 修复打包版所有曲线不显示（pyqtgraph 所需的 `PyQt5._QOpenGLFunctions_*` 未被 PyInstaller 收集）
- 修复打包版图标缺失（EXE 图标 + 窗口图标）

### v1.3.1
- 发布拆分为 R 版 / Python 版两个独立 exe

### v1.3.0
- 双计算引擎（R / Python-resprpy），可在启动时选择

### v1.2.0
- 数据计算模块：respR R 脚本 (calc_rmr.R)，一键计算耗氧率
- 左侧数据计算面板（可折叠）：文件名/文件夹/计算按钮/进度条
- renv R 虚拟环境：首次运行自动安装，TUNA 清华镜像
- csv.DictReader/DictWriter：全部用列名匹配，防串列
- 启动控制台详情：Python 版本、venv 路径、包版本、R 检测
- 属性级渲染优化：温度/气压/点/线/趋势/斜率 O(1) 切换
- `_redraw_cycle` 快速循环切换
- 移除 panel.setFixedWidth，QSplitter 控制宽度
- 应用图标 logo.png

### v1.1.0
- 通道类型管理（Fish/Blank/特殊），可折叠面板
- 7 列循环参数，自动计算周期/循环数
- ScatterPlotItem GPU 批量渲染
- 类版渲染器，属性级更新（O(1) 切换温度/气压/数据线）
- Excel 文件存在性检测
- 精确参数保存（按日期+类型+通道匹配）

### v1.0.0
- 基础溶氧数据可视化
- 循环参数编辑和保存
- PyInstaller 独立 EXE 打包
