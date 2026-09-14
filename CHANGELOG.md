# 更新日志

OxyViewer 所有值得注意的变更都记录在此文件。

## [v1.3.1] - 2026-09-14

### 变更

- 发布拆分为两个固定引擎的独立版本，下载后无需再选择：
  - **`OxyViewer-R.exe`** —— 默认使用 R 计算引擎
  - **`OxyViewer-Python.exe`** —— 默认使用 Python 计算引擎（**无需安装 R**）
- 新增 `build.bat`：一键打包上述两个版本
- 两个版本通过 PyInstaller runtime hook（`engine_hook_r.py` / `engine_hook_p.py`）固化默认引擎，
  用户仍可用环境变量 `OXY_ENGINE` 临时覆盖；源码运行行为不变

## [v1.3.0] - 2026-09-14

### 新增

- **双计算引擎**：启动时选择用 R 还是 Python 计算耗氧率
  - `calc_rmr.py`：基于 [resprpy](https://pypi.org/project/resprpy/) 的 Python 计算引擎，接口与 `calc_rmr.R` 完全一致
  - `run.bat`：启动菜单选择引擎，并**按所选引擎检查对应依赖**；`run.bat R` / `run.bat python` 可带参数跳过询问
  - **选 Python 引擎时无需安装 R**，也不初始化 renv（不必等 R 装包）
- 启动自检改为 `[1/3] [2/3] [3/3]` 三步提示，并根据引擎显示对应依赖版本

### 变更

- `main.py`：R 环境初始化改为按引擎执行（`_setup_pyengine()` / `_setup_renv()`）
- `viewer.py`：计算调用统一到单一分发点，由环境变量 `OXY_ENGINE` 决定调用 Rscript 还是 Python
- `requirements.txt`：新增 `resprpy>=0.1.2`
- 兼容 PyQt5 < 5.15.3：`setEnvironment` → `setProcessEnvironment`

### 修复

- 特殊通道索引 bug（`_save_channel_settings` 中元组修改未存回列表）
- 清除死代码、内存泄漏，去掉硬编码 blank 通道判断

### 说明

- 两个引擎结果一致：9 通道实测数据上最大相对差 `1.2e-13`（约 76% 的数值逐位相同），差异来自浮点运算路径
- 不设置 `OXY_ENGINE` 时默认使用 R 引擎，行为与 v1.2.0 完全一致

## [v1.2.0] - 2026-07-29

### 新增

- 数据计算模块：respR R 脚本（`calc_rmr.R`），一键计算耗氧率
- 左侧数据计算面板（可折叠）：文件名 / 文件夹 / 计算按钮 / 进度条
- renv R 虚拟环境：首次运行自动安装（TUNA 清华镜像）
- 启动控制台详情：Python 版本、venv 路径、包版本、R 检测
- 应用图标 `logo.png`

### 变更

- `csv.DictReader` / `csv.DictWriter`：全部按列名匹配，防止串列
- 属性级渲染优化：温度 / 气压 / 点 / 线 / 趋势 / 斜率 O(1) 切换
- `_redraw_cycle` 快速循环切换
- 移除 `panel.setFixedWidth`，改用 QSplitter 控制宽度

## [v1.1.0] - 2026-07-28

### 新增

- 通道类型管理（Fish / Blank / 特殊通道）
- 可折叠面板
- 7 列循环参数自动计算
- 保存精确定位、ScrollArea 防挤压

## [v1.0.0] - 2026-07-27

OxyViewer 首个发布版本。

### 功能

- PreSens OXY-10 SMA 溶氧数据读取
- 全局时间序列预览（溶氧 / 温度 / 气压）
- 单循环放大视图 + 线性回归趋势线
- GPU 加速渲染
