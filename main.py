"""OxyViewer — 溶氧数据可视化工具 · 入口 (PyQt5 + pyqtgraph, OpenGL)"""
import sys
import os
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ══ 全局 pyqtgraph 配置 — GPU 加速 ══
# 打包版要用 GPU，必须把 GL 依赖凑齐：PyOpenGL、PyQt5.QtOpenGL，以及
# Qt5OpenGL.dll（代码里没有静态引用，PyInstaller 容易漏掉 —— 见
# packaging/build.bat 里的 --hidden-import）。任一项缺失时 pyqtgraph 的
# OpenGL 渲染会「静默失败」：坐标轴正常但所有曲线都不画。所以这里逐项
# 确认，缺了就退回 CPU 渲染，而不是给用户一张空图。
import glob as _glob
import pyqtgraph as pg


def _opengl_ready():
    """确认 OpenGL 渲染所需依赖齐全。"""
    try:
        from OpenGL import GL      # noqa: F401  PyOpenGL
        import PyQt5.QtOpenGL      # noqa: F401  Qt 的 GL 模块
        # pyqtgraph 是用字符串动态导入下面这些「私有」模块来取 GL 函数入口的
        # (OpenGLHelpers.py: importlib.import_module("PyQt5._QOpenGLFunctions_4_1_Core"))，
        # PyInstaller 静态分析扫不到它们，不在这里显式引用就会漏打包，
        # 症状正是「坐标轴正常但所有曲线都不画」。
        from PyQt5 import _QOpenGLFunctions_4_1_Core  # noqa: F401
        from PyQt5 import _QOpenGLFunctions_2_1       # noqa: F401
        from PyQt5 import _QOpenGLFunctions_2_0       # noqa: F401
    except ImportError:
        return False
    if getattr(sys, 'frozen', False):
        base = getattr(sys, '_MEIPASS', '')
        if not _glob.glob(os.path.join(base, '**', 'Qt5OpenGL.dll'), recursive=True):
            return False
    return True


_use_opengl = _opengl_ready()
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


def _resolve_engine():
    """确定计算引擎，优先级：环境变量 OXY_ENGINE > 打包配置 > 默认 R。

    打包时可以带一个 engine_default.txt（内容为 R 或 python）来固化该 EXE
    的计算引擎，这样同一个源码能打出「R 版」和「Python 版」两个 exe。
    解析结果写回环境变量，viewer.py 读取同一变量，保证两处一致。
    """
    eng = os.environ.get('OXY_ENGINE', '').strip()
    if not eng:
        cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'engine_default.txt')
        try:
            with open(cfg, encoding='utf-8') as f:
                eng = f.read().strip()
        except Exception:
            eng = ''
    if not eng:
        eng = 'R'
    os.environ['OXY_ENGINE'] = eng
    return eng


def _engine_is_python():
    """当前选择的是否为 Python (resprpy) 计算引擎。"""
    eng = os.environ.get('OXY_ENGINE', 'R').strip().lower()
    return eng in ('python', 'py', 'p', 'resprpy')


def _setup_pyengine():
    """检查 Python 计算引擎 (resprpy) — 计算依赖。"""
    import subprocess

    print('─' * 40)
    print('  [P] 计算引擎检查 (Python / resprpy)')

    try:
        import resprpy
        print(f'        resprpy {resprpy.__version__} 已就绪')
    except ImportError:
        print('        未安装 resprpy — 正在自动安装...')
        try:
            subprocess.run([sys.executable, '-m', 'pip', 'install', 'resprpy'],
                           capture_output=True, text=True, timeout=300)
            import resprpy
            print(f'        resprpy {resprpy.__version__} 安装完成')
        except Exception as e:
            print(f'  [P] resprpy 安装失败: {e}')
            print('      手动安装: pip install resprpy')
            print('─' * 40)
            return

    print('────────────────────────────────────────')

    # R 环境不需要，若已安装也提示一下未使用
    print('  [R] 使用 Python 引擎 — 跳过 R 环境初始化')


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

    # ── [0/3] 计算引擎 ──
    engine = _resolve_engine()
    print(f'  计算引擎: {engine}')

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
    for name in ['pyqtgraph', 'numpy', 'openpyxl'] + (['resprpy'] if _engine_is_python() else []):
        try:
            mod = __import__(name)
            pkgs.append(f'{name} {mod.__version__}')
        except Exception: pass
    if pkgs:
        print(f'        关键包: {", ".join(pkgs)}')

    # ── [2/3] 计算引擎依赖 ──
    if _engine_is_python():
        _setup_pyengine()
    else:
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


def _run_frozen_engine(script, script_args):
    """打包版 (PyInstaller) 专用：用 EXE 自身执行 Python 计算引擎脚本。

    onefile 打包后 sys.executable 指向 OxyViewer.exe 而不是 python.exe，
    直接调用会重新启动 GUI，因此这里拦截 --run-py-engine 参数，
    在当前进程里执行目标脚本（与 `python calc_rmr.py ...` 等价）。
    """
    sys.argv = [script] + list(script_args)
    with open(script, encoding='utf-8') as f:
        code = compile(f.read(), script, 'exec')
    exec(code, {'__name__': '__main__', '__file__': script})


def _gl_test():
    """命令行自检：确认 OpenGL 上下文能否真正建立（打包版排查用）。"""
    from PyQt5 import QtWidgets
    from PyQt5.QtWidgets import QOpenGLWidget

    app = QtWidgets.QApplication(sys.argv)
    w = QOpenGLWidget()
    w.resize(64, 64)
    w.show()
    app.processEvents()
    ctx = w.context()
    ok = bool(ctx and ctx.isValid())
    print('pyqtgraph useOpenGL      =', _use_opengl)
    print('QOpenGLWidget 上下文可用 =', ok)
    if ctx:
        fmt = ctx.format()
        print('GL 版本 = %d.%d' % (fmt.majorVersion(), fmt.minorVersion()))
    return 0 if ok else 1


if __name__ == '__main__':
    if '--gl-test' in sys.argv:
        sys.exit(_gl_test())
    if len(sys.argv) > 1 and sys.argv[1] == '--run-py-engine':
        _run_frozen_engine(sys.argv[2], sys.argv[3:])
        sys.exit(0)
    sys.exit(main())
