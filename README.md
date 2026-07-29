# OxyViewer — 鱼类耗氧率数据可视化分析与计算工具

基于 PyQt5 + pyqtgraph 的 PreSens OXY-10 SMA 溶氧数据桌面分析软件。支持多通道管理、循环参数编辑、GPU 加速渲染、耗氧率(RMR)自动计算。

## 快速开始

### 环境要求

- **Python 3.10+**（必须）
- **R 4.4+**（可选，仅 RMR 计算需要）

### 首次使用

**双击 `run.bat`** 即可，首次运行会自动：
1. 创建 Python 虚拟环境 (`venv/`)
2. 安装 Python 依赖 (`pyqtgraph`, `numpy`, `openpyxl`)
3. 检测 R 环境，若存在则自动安装 R 包到 `renv/` 虚拟环境

无 R 环境时，查看、调参、导出功能完全正常，仅「数据计算」按钮不可用。

### 手动 Python 环境

```bash
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python main.py
```

### R 环境（可选）

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

- **通道范围**：可调 1~N，回车确认
- 每通道可选类型：`Fish`（实验鱼）/ `Blank`（空白对照）/ `特殊`（独立参数）
- 点击通道号按钮可切换当前通道并重新加载
- 「保存到通道设置」将修改写入 CSV

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
- **数据展示**：数据点(大小 1~6)、数据线(宽度 0.5~3)、趋势线(宽度 1~3)
- **斜率计算区**：显示粉红色斜率计算窗口
- **时间格式**：秒 / 分 / 时（切换后瞬时更新，无需重绘）
- **循环切换**：滑块 / 左右箭头快速切换，仅更新高亮和局部视图

### 5. 数据计算（需 R）

- 选择导出路径后点击「计算当前通道」或「计算所有通道」
- 确认对话框显示计算参数，确认后调用 `calc_rmr.R` 通过 respR 包计算耗氧率
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

## 项目结构

```
oxyviewer/
├── main.py              # 入口 + R 环境初始化
├── viewer.py            # 主窗口 UI + 事件处理
├── plots.py             # pyqtgraph 渲染器（GlobalRenderer/LocalRenderer）
├── data_loader.py       # xlsx 读取 + 参数解析 + 循环边界计算
├── cycle_analyzer.py    # 线性回归斜率计算
├── calc_rmr.R           # respR R 脚本（耗氧率批量计算）
├── run.bat              # Windows 一键启动
├── logo.png             # 应用图标
├── requirements.txt     # Python 依赖
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

### v1.2.0
- 数据计算模块：respR R 脚本 (calc_rmr.R)，一键计算耗氧率
- 左侧数据计算面板（可折叠）：文件名/文件夹/计算按钮/进度条
- renv R 虚拟环境：首次运行自动安装，TUNA 清华镜像
- csv.DictReader/DictWriter：全部用列名匹配，防串列
- 启动控制台详情：Python 版本、venv 路径、包版本、R 检测
- 属性级渲染优化：温度/气压/点/线/趋势/斜率 O(1) 切换
- _redraw_cycle 快速循环切换
- 移除 panel.setFixedWidth，QSplitter 控制宽度
- 应用图标 logo.png
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
