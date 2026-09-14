# PyInstaller runtime hook — 固化本 EXE 的默认计算引擎为 Python (resprpy)。
# 在引导阶段、main.py 之前执行；用 setdefault，用户仍可用环境变量
# OXY_ENGINE 覆盖（例如临时切回 R 引擎）。
import os

os.environ.setdefault('OXY_ENGINE', 'python')
