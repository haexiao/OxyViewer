@echo off
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo 创建 Python 虚拟环境...
    python -m venv venv
    if errorlevel 1 pause && exit /b 1
)

echo 安装 Python 依赖...
"venv\Scripts\pip" install -r requirements.txt --quiet 2>nul

echo 启动 OxyViewer...
set PYTHONPATH=
"venv\Scripts\python.exe" "main.py"
if %errorlevel% neq 0 pause
