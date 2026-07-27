"""OxyViewer — 溶氧数据可视化工具 · 入口 (PyQt5 + pyqtgraph, OpenGL)"""
import sys
import os
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ══ 全局 pyqtgraph 配置 — GPU 加速 ══
import pyqtgraph as pg
pg.setConfigOptions(antialias=True, useOpenGL=True)

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
    try:
        from PyQt5.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, f'OxyViewer — {title}', msg)
    except Exception:
        try:
            import tkinter.messagebox as mb
            mb.showerror(f'OxyViewer — {title}', msg)
        except Exception:
            pass


def main():
    try:
        from PyQt5 import QtWidgets
        app = QtWidgets.QApplication(sys.argv)
        app.setStyle('Fusion')
    except Exception as e:
        show_error('Qt 初始化失败', traceback.format_exc())
        return 1

    try:
        from viewer import OxyViewer
        window = OxyViewer()
        window.show()
        return app.exec_()
    except Exception as e:
        show_error('启动失败', traceback.format_exc())
        return 1


if __name__ == '__main__':
    sys.exit(main())
