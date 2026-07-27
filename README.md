# OxyViewer — 溶氧数据可视化工具

用于间歇式代谢测量系统（PreSens OXY-10 SMA）的溶氧数据阅读器。支持查看溶氧、温度、气压的时间序列，按循环切割数据，计算每个循环的耗氧率斜率。

## 快速开始

### 1. 安装 Python 环境

需要 Python 3.9+。推荐使用 [Anaconda](https://www.anaconda.com/) 或 [Python 官方安装包](https://www.python.org/)。

### 2. 创建虚拟环境并安装依赖

```bash
cd OxyViewer
python -m venv venv
venv\Scripts\pip install -r requirements.txt
```

### 3. 启动

双击 `run.bat`，或命令行执行：

```bash
venv\Scripts\python main.py
```

## 数据准备

### 数据文件夹

按日期和温度命名的文件夹，例如：

```
20260422 20/     ← 2026年4月22日, 20°C
├── 1.xlsx      ← 通道 1
├── 2.xlsx      ← 通道 2
├── ...
└── 9.xlsx      ← 通道 9
```

每个 xlsx 文件的第 6 个 sheet 为测量数据，必须包含以下列：
- **Date**（日期时间）
- **Oxygen**（溶氧值, mg/L）
- **Temperature**（温度, °C）
- **Pressure**（气压, hPa）

### 循环参数文件 (meas_params.csv)

CSV 格式，定义每个实验的循环参数：

| 字段 | 说明 |
|------|------|
| meas_time | 日期 (如 20260422) |
| meas_type | before / rmr |
| rmr_type | fish / blank |
| meas_batch | 批次号 |
| temperature | 温度 |
| cycles | 循环次数 |
| initial | 初始值 |
| cycle_length | 每个循环时长 (秒) |
| cycle_start | 斜率计算起点 (距循环起点的秒数) |
| cycle_time | 斜率计算终点 (距循环起点的秒数, 不是窗口时长) |
| flush_time | 冲洗时间 (秒) |
| all_time | 总时间 (秒) |

### 注意事项

- 数据文件夹名称中的日期（前 8 位数字）必须与 `meas_params.csv` 中 `meas_time` 匹配
- 测量类型（fish/blank）通过 Fish/Blank 单选按钮切换
- 循环参数可手动修改后按 Enter 生效，或点击"保存到循环参数"写回 CSV
- **保存前务必备份原参数文件！**

## 功能

- 全局时间序列预览（溶氧/温度/气压）
- 单循环放大视图 + 线性回归趋势线
- 斜率计算区间红色高亮标记
- 数据游标实时显示时间、溶氧、温度、气压
- 可调节数据点大小、线条粗细、趋势线粗细
- 时间单位切换（秒/分/时）
- GPU 加速渲染（pyqtgraph + OpenGL）

## 依赖

- Python 3.9+
- PyQt5
- pyqtgraph
- openpyxl
- numpy
- PyOpenGL

## 许可

待定
