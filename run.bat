@echo off
cd /d "%~dp0"

rem ==================================================================
rem  1. 选择耗氧率计算引擎
rem     跳过询问:  run.bat R   /   run.bat python
rem ==================================================================
if not "%1"=="" set OXY_ENGINE=%1
if /i "%OXY_ENGINE%"=="py" set OXY_ENGINE=python
if "%OXY_ENGINE%"=="" (
    echo ==========================================================
    echo   请选择耗氧率计算引擎
    echo ==========================================================
    echo     [1] R      （respR + renv） 传统引擎，依赖 R 环境
    echo     [2] Python （resprpy）     新引擎，无需 R
    echo ==========================================================
    choice /C 12 /N /M "   输入 1 或 2: "
    if errorlevel 2 (set OXY_ENGINE=python) else (set OXY_ENGINE=R)
)

rem ==================================================================
rem  2. Python 环境（GUI 自身依赖，与计算引擎无关）
rem ==================================================================
if not exist "venv\Scripts\python.exe" (
    echo 创建 Python 虚拟环境...
    python -m venv venv
    if errorlevel 1 (
        pause
        exit /b 1
    )
)

echo 安装/检查 Python 依赖...
"venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet 2>nul

rem ==================================================================
rem  3. 按所选引擎检查对应依赖
rem ==================================================================
if /i "%OXY_ENGINE%"=="python" (
    echo [计算引擎] Python / resprpy
    "venv\Scripts\python.exe" -c "import resprpy" 2>nul
    if errorlevel 1 (
        echo 未找到 resprpy，正在安装...
        "venv\Scripts\python.exe" -m pip install resprpy --quiet
    )
) else (
    set OXY_ENGINE=R
    echo [计算引擎] R / respR
    where Rscript >nul 2>&1
    if errorlevel 1 (
        echo.
        echo [错误] 未找到 Rscript，R 计算引擎不可用。
        echo        请先安装 R，或改用 Python 引擎启动:  run.bat python
        echo.
        pause
        exit /b 1
    )
)

rem ==================================================================
rem  4. 启动
rem ==================================================================
echo 启动 OxyViewer  计算引擎: %OXY_ENGINE%
set PYTHONPATH=
"venv\Scripts\python.exe" "main.py"
if %errorlevel% neq 0 pause
