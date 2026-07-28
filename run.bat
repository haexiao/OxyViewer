@echo off
cd /d "%~dp0"
set PYTHONPATH=

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] venv not found. Run: python -m venv venv
    echo Then: venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

echo Starting OxyViewer...
"venv\Scripts\python.exe" "main.py"
if %errorlevel% neq 0 pause
