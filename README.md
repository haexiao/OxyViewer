# OxyViewer v1.1.0

鱼类耗氧率数据可视化分析工具，基于 PyQt5 + pyqtgraph。

## 功能

- 读取 PreSens OXY-10 导出的 xlsx 数据（sheet 6）
- 多通道管理 — 通道类型（Fish / Blank / 特殊）一键切换，自动同步到参数文件
- 循环参数可视化编辑 — 7 列参数，cycles 和 cycle_length 自动计算
- 可折叠面板（通道设置 / 循环参数 / 图像设置），防止窗口缩小时挤压缩
- 全局 + 单循环双视图，游标实时显示时间和溶氧值
- 线性回归计算斜率（R²）
- 温度 / 气压叠加显示（可开关）
- 参数保存到 meas_params.csv（逗号分隔，支持逗号 chamber_ID 列表）
- 便携式 EXE 发布（`run.bat` 启动）

## 环境

```bash
python -m venv venv
venv\Scripts\pip install -r requirements.txt
python main.py
```

或直接双击 `run.bat`。

## 数据格式

| 文件 | 说明 |
|------|------|
| `{date}_{temp}/1~9.xlsx` | 通道数据，sheet 6 含 time/O₂/temp/press |
| `raw/meas_params.csv` | 循环参数，逗号分隔 13 列（含 chamber_ID） |

## 快捷操作

- 打开数据文件 → 加载参数文件 → 点击「加载数据」
- 点击通道按钮切换通道，右侧同步更新
- 修改测量(s) / 冲洗(s) / 总时长(s) → cycles 和周期自动重算
- 「保存到循环参数」按通道号精确定位 CSV 行，不串列
- 「保存到通道设置」支持 Fish↔特殊↔Blank 行级增删

## 许可

MIT
