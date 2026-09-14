@echo off
cd /d "%~dp0"
set PYTHONPATH=

echo ==========================================================
echo   打包 OxyViewer 两个版本
echo     [1] OxyViewer-R.exe       默认 R 计算引擎
echo     [2] OxyViewer-Python.exe  默认 Python 计算引擎
echo ==========================================================
echo.

if not exist "venv\Scripts\python.exe" (
    echo [错误] 未找到 venv，请先运行 run.bat 初始化环境。
    pause
    exit /b 1
)

echo 清理 vc9 DLL ...
del /q "venv\Lib\site-packages\OpenGL\DLLS\*vc9.dll" 2>nul

echo [1/2] 打包 R 版 ...
venv\Scripts\python.exe -m PyInstaller --onefile --windowed --name OxyViewer-R ^
  --hidden-import numpy --collect-all numpy --collect-all pyqtgraph --collect-all resprpy ^
  --runtime-hook engine_hook_r.py ^
  --add-data "venv\Lib\site-packages\PyQt5\Qt5\plugins\platforms;PyQt5/Qt5/plugins/platforms" ^
  --add-data "calc_rmr.R;." --add-data "calc_rmr.py;." ^
  --noconfirm main.py
if errorlevel 1 goto fail

echo.
echo [2/2] 打包 Python 版 ...
venv\Scripts\python.exe -m PyInstaller --onefile --windowed --name OxyViewer-Python ^
  --hidden-import numpy --collect-all numpy --collect-all pyqtgraph --collect-all resprpy ^
  --runtime-hook engine_hook_p.py ^
  --add-data "venv\Lib\site-packages\PyQt5\Qt5\plugins\platforms;PyQt5/Qt5/plugins/platforms" ^
  --add-data "calc_rmr.R;." --add-data "calc_rmr.py;." ^
  --noconfirm main.py
if errorlevel 1 goto fail

echo.
echo 清理构建缓存 ...
rmdir /s /q build 2>nul
del /q OxyViewer-R.spec OxyViewer-Python.spec 2>nul

echo.
echo ==========================================================
echo   完成，输出在 dist\
echo ==========================================================
dir /b dist
pause
exit /b 0

:fail
echo.
echo [错误] 打包失败。
pause
exit /b 1
