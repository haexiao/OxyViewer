"""OxyViewer — 溶氧数据可视化工具 · 入口 (PyQt5 + pyqtgraph, OpenGL)"""
import sys
import os
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ══ 全局 pyqtgraph 配置 — GPU 加速 (PyInstaller 打包时可能不可用) ══
import pyqtgraph as pg
_use_opengl = True
try:
    from OpenGL import GL
except ImportError:
    _use_opengl = False
pg.setConfigOptions(antialias=True, useOpenGL=_use_opengl)

# ── 修复 Qt DLL 路径 ──
import PyQt5
_qt_dir = os.path.dirname(PyQt5.__file__)
_qt_bin = os.path.join(_qt_dir, 'Qt5', 'bin')
if os.path.isdir(_qt_bin):
    try:
        os.add_dll_directory(_qt_bin)
    except AttributeError:
        pass
_qt_plugins = os.path.join(_qt_dir, 'Qt5', 'plugins')
os.environ['QT_PLUGIN_PATH'] = _qt_plugins
os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = os.path.join(_qt_plugins, 'platforms')


def show_error(title, msg):
    print(f'\n=== {title} ===\n{msg}\n', file=sys.stderr)


def _setup_renv():
    """初始化 R 虚拟环境 (renv) — 首次自动安装。"""
    import subprocess
    project_dir = os.path.dirname(os.path.abspath(__file__))
    renv_lib = os.path.join(project_dir, 'renv', 'library')

    print('─' * 40)
    print('  [R] 环境检查')

    try:
        result = subprocess.run(['Rscript', '--version'], capture_output=True,
                                text=True, timeout=10)
        r_ver = result.stdout.strip() if result.stdout else ''
    except Exception:
        print('─' * 40)
        print('  [R] 未检测到 R — 计算功能不可用')
        print('      下载: https://cran.r-project.org')
        print('─' * 40)
        return

    r_ver_short = r_ver.split()[-1] if r_ver else '?'
    if os.path.isdir(renv_lib) and os.listdir(renv_lib):
        pkg_count = len([n for n in os.listdir(renv_lib)
                        if os.path.isdir(os.path.join(renv_lib, n))])
        print(f'        R {r_ver_short} — renv 已就绪 ({pkg_count} 个包)')
        return

    print(f'        R {r_ver_short} — 首次运行，安装 R 包...')
    print(f'        CRAN 镜像: mirrors.tuna.tsinghua.edu.cn/CRAN')
    print(f'        预计下载 ~30 MB\n')

    # 分步安装以显示进度
    steps = [
        ('安装 renv', 'if(!require("renv", quietly=TRUE)) install.packages("renv")'),
        ('初始化项目', 'renv::init(restart=FALSE)'),
        ('安装 respR/lubridate/readxl',
         'renv::install(c("respR","lubridate","readxl"), prompt=FALSE)'),
    ]

    for i, (label, cmd) in enumerate(steps, 1):
        print(f'  [{i}/3] {label}...')
        try:
            subprocess.run(
                ['Rscript', '-e',
                 f'options(repos=c(CRAN="https://mirrors.tuna.tsinghua.edu.cn/CRAN"));{cmd}'],
                cwd=project_dir, check=True)
        except subprocess.CalledProcessError as e:
            print(f'  [!] {label} 失败')
            break

    # snapshot 可选
    try:
        subprocess.run(
            ['Rscript', '-e',
             'options(repos=c(CRAN="https://mirrors.tuna.tsinghua.edu.cn/CRAN"));'
             'tryCatch(renv::snapshot(prompt=FALSE), error=function(e){})'],
            cwd=project_dir, check=True)
    except Exception:
        pass


def main():
    print('═' * 50)
    print('  OxyViewer — 溶氧数据可视化工具')
    print('═' * 50)

    # ── [1/3] Python 环境 ──
    print()
    print('  [1/3] Python 环境')
    print(f'        解释器: Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')
    venv_dir = os.environ.get('VIRTUAL_ENV', '')
    if not venv_dir and 'venv' in sys.executable:
        venv_dir = os.path.dirname(os.path.dirname(sys.executable))
    if venv_dir:
        print(f'        虚拟环境: {venv_dir}')
    else:
        print('        虚拟环境: 无')

    # 第三方包
    pkgs = []
    try:
        from PyQt5.QtCore import QT_VERSION_STR
        pkgs.append(f'PyQt5 {QT_VERSION_STR}')
    except Exception: pass
    for name in ['pyqtgraph', 'numpy', 'openpyxl']:
        try:
            mod = __import__(name)
            pkgs.append(f'{name} {mod.__version__}')
        except Exception: pass
    if pkgs:
        print(f'        关键包: {", ".join(pkgs)}')

    # ── [2/3] R 环境 ──
    _setup_renv()

    # ── [3/3] 启动界面 ──
    print()
    print('  [3/3] 启动 Qt 界面')
    try:
        from PyQt5 import QtWidgets
        app = QtWidgets.QApplication(sys.argv)
        app.setStyle('Fusion')
        print('        Qt 初始化完成')
    except Exception as e:
        show_error('Qt 初始化失败', traceback.format_exc())
        return 1

    try:
        from viewer import OxyViewer
        window = OxyViewer()
        window.show()
        print('        主窗口已启动\n')
        return app.exec_()
    except Exception as e:
        show_error('启动失败', traceback.format_exc())
        return 1


if __name__ == '__main__':
    sys.exit(main())
