"""OxyViewer 渲染自检：对比 GL 开/关时曲线能否正常绘制。

用法（源码）: venv/Scripts/python.exe packaging/self_test.py [--no-gl]
用法（打包）: dist/OxyViewer-R.exe --run-py-engine packaging/self_test.py [--no-gl]
"""
import os
import sys
import tempfile

use_gl = '--no-gl' not in sys.argv

import pyqtgraph as pg
from PyQt5 import QtWidgets

pg.setConfigOptions(antialias=True, useOpenGL=use_gl)

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

w = pg.PlotWidget()
w.resize(600, 400)

x = list(range(100))
y = [i * 0.5 for i in range(100)]
curve = w.plot(x, y, pen='b')
scatter = pg.ScatterPlotItem(x=x, y=y, size=6)
w.addItem(scatter)

w.show()
app.processEvents()

vb = w.getViewBox()

print('frozen            =', getattr(sys, 'frozen', False))
print('useOpenGL         =', use_gl)
print('曲线 isVisible    =', curve.isVisible())
arr = curve.getData()[0]
print('曲线数据点数      =', 0 if arr is None else len(arr))
print('离散点 isVisible  =', scatter.isVisible())
print('viewRange         =', [[round(v, 2) for v in r] for r in vb.viewRange()])
print('场景 items 数     =', len(w.scene().items()))
print('renderer 类型     =', type(w.getPlotItem().vb).__name__)

shot = os.path.join(tempfile.gettempdir(), 'oxy_selftest_%s.png' % ('gl' if use_gl else 'nogl'))
ok = w.grab().save(shot)
print('截图保存          =', ok, shot, os.path.getsize(shot) if os.path.exists(shot) else 0)
