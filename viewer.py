"""OxyViewer — 溶氧数据可视化工具 · 主窗口 (PyQt5 + pyqtgraph)"""
import os
import re
import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets, QtGui

from data_loader import load_xlsx, load_params, match_params, compute_cycle_boundaries
from cycle_analyzer import compute_slope
from plots import render_global, render_local, find_nearest

# ── 默认路径 (首次启动为空，之后通过 QSettings 记忆) ──
DEFAULT_DATA_DIR = ''
DEFAULT_PARAMS = ''

# ── 样式 ──────────────────────────────────────────────
STYLE = """
QMainWindow { background: #f5f5f5; }
QGroupBox { font-weight: bold; margin-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
"""


class OxyViewer(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('OxyViewer — 溶氧数据可视化')
        self.resize(1300, 820)
        self.setMinimumSize(1024, 680)
        self.setStyleSheet(STYLE)

        # 持久化设置 (记忆上次使用的文件夹和参数文件)
        self._settings = QtCore.QSettings('OxyViewer', 'OxyViewer')

        # ── 状态变量 ──
        self._data = None
        self._params_list = None
        self._params = None
        self._cycle_bounds = None
        self._current_cycle = 1
        self._meas_type = 'fish'
        self._show_points = True
        self._show_lines = True

        self._build_ui()

    # ════════════════════════════════════════════════════
    #  UI 构建
    # ════════════════════════════════════════════════════

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        # 主水平分割器
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        layout = QtWidgets.QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        self._build_left_panel(splitter)
        self._build_right_panel(splitter)

        splitter.setSizes([280, 1000])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

    def _build_left_panel(self, parent):
        """左侧控制面板 — 按用户要求的布局。"""
        panel = QtWidgets.QWidget()
        panel.setFixedWidth(270)
        vbox = QtWidgets.QVBoxLayout(panel)
        vbox.setContentsMargins(10, 8, 10, 8)
        vbox.setSpacing(5)

        def _section(title):
            lbl = QtWidgets.QLabel(title)
            lbl.setStyleSheet(
                'font-weight: bold; color: #000; '
                'border-bottom: 1px solid #ccc; padding-bottom: 2px; margin-top: 6px;')
            vbox.addWidget(lbl)

        def _sep():
            line = QtWidgets.QFrame()
            line.setFrameShape(QtWidgets.QFrame.HLine)
            line.setFrameShadow(QtWidgets.QFrame.Sunken)
            line.setStyleSheet('color: #ddd;')
            vbox.addWidget(line)

        def _small(text, color='#888'):
            lbl = QtWidgets.QLabel(text)
            lbl.setStyleSheet(f'font-size: 8pt; color: {color}; padding-left: 2px;')
            return lbl

        # ════════════════════════════════════════════════
        # 数据文件夹
        # ════════════════════════════════════════════════
        _section('数据文件夹')
        row1 = QtWidgets.QHBoxLayout()
        self._data_dir_edit = QtWidgets.QLineEdit()
        self._data_dir_edit.setPlaceholderText('选择文件夹...')
        btn = QtWidgets.QPushButton('浏览')
        btn.setFixedWidth(48)
        btn.clicked.connect(self._browse_data_dir)
        row1.addWidget(self._data_dir_edit)
        row1.addWidget(btn)
        vbox.addLayout(row1)

        row_ch = QtWidgets.QHBoxLayout()
        row_ch.addWidget(QtWidgets.QLabel('通道：'))
        self._ch_combo = QtWidgets.QComboBox()
        self._ch_combo.addItems([str(i) for i in range(1, 10)])
        self._ch_combo.setFixedWidth(56)
        self._ch_combo.currentTextChanged.connect(self._on_channel_change)
        row_ch.addWidget(self._ch_combo)
        row_ch.addStretch()
        vbox.addLayout(row_ch)

        _sep()

        # ════════════════════════════════════════════════
        # 参数文件
        # ════════════════════════════════════════════════
        _section('参数文件')
        row2 = QtWidgets.QHBoxLayout()
        self._params_file_edit = QtWidgets.QLineEdit()
        self._params_file_edit.setPlaceholderText('meas_params.csv')
        btn_p = QtWidgets.QPushButton('浏览')
        btn_p.setFixedWidth(48)
        btn_p.clicked.connect(self._browse_params)
        row2.addWidget(self._params_file_edit)
        row2.addWidget(btn_p)
        vbox.addLayout(row2)

        row_type = QtWidgets.QHBoxLayout()
        row_type.addWidget(QtWidgets.QLabel('测量类型：'))
        self._rb_fish = QtWidgets.QRadioButton('Fish')
        self._rb_blank = QtWidgets.QRadioButton('Blank')
        self._rb_fish.setChecked(True)
        self._rb_fish.toggled.connect(self._on_type_change)
        # 独立 ButtonGroup — 避免和时间格式 radio 互斥
        self._type_group = QtWidgets.QButtonGroup(panel)
        self._type_group.addButton(self._rb_fish)
        self._type_group.addButton(self._rb_blank)
        row_type.addWidget(self._rb_fish)
        row_type.addWidget(self._rb_blank)
        row_type.addStretch()
        vbox.addLayout(row_type)

        self._params_status = QtWidgets.QLabel('')
        self._params_status.setStyleSheet(
            'font-size: 8pt; color: #4caf50; padding-left: 2px;')
        vbox.addWidget(self._params_status)

        _sep()

        # ════════════════════════════════════════════════
        # 循环参数
        # ════════════════════════════════════════════════
        _section('循环参数')
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(2)

        self._p_cycles = QtWidgets.QLineEdit()
        self._p_cycle_length = QtWidgets.QLineEdit()
        self._p_cycle_start = QtWidgets.QLineEdit()
        self._p_cycle_time = QtWidgets.QLineEdit()

        row_data = [
            (0, '总循环次数：', self._p_cycles, 'cycles'),
            (1, '循环总时间(s)：', self._p_cycle_length, 'cycle_length'),
            (2, '计算斜率起点(s)：', self._p_cycle_start, 'cycle_start'),
            (3, '循环结束时间(s)：', self._p_cycle_time, 'cycle_time'),
        ]
        for r, label, w, hint in row_data:
            w.setFixedWidth(72)
            w.returnPressed.connect(self._on_params_changed)
            grid.addWidget(QtWidgets.QLabel(label), r, 0)
            grid.addWidget(w, r, 1)
            grid.addWidget(_small(hint), r, 2)
        vbox.addLayout(grid)

        self._save_btn = QtWidgets.QPushButton('保存到循环参数')
        self._save_btn.clicked.connect(self._save_params_to_csv)
        self._save_btn.setMinimumHeight(28)
        vbox.addWidget(self._save_btn)

        self._save_warn = QtWidgets.QLabel('保存前务必备份原参数！')
        self._save_warn.setStyleSheet(
            'font-size: 8pt; color: #e74c3c; padding-left: 2px;')
        vbox.addWidget(self._save_warn)

        _sep()

        # ════════════════════════════════════════════════
        # 图像设置
        # ════════════════════════════════════════════════
        _section('图像设置')

        # 数据显示 (Temp/Press only, O2 always on)
        row_disp = QtWidgets.QHBoxLayout()
        row_disp.addWidget(QtWidgets.QLabel('数据显示：'))
        self._cb_temp = QtWidgets.QCheckBox('温度')
        self._cb_temp.toggled.connect(self._on_display_change)
        self._cb_press = QtWidgets.QCheckBox('气压')
        self._cb_press.toggled.connect(self._on_display_change)
        row_disp.addWidget(self._cb_temp)
        row_disp.addWidget(self._cb_press)
        row_disp.addStretch()
        vbox.addLayout(row_disp)

        # 数据展示 (点大小 / 线粗细)
        row_style_label = QtWidgets.QLabel('数据展示：')
        vbox.addWidget(row_style_label)

        # 数据点 + 大小
        row_pt = QtWidgets.QHBoxLayout()
        self._cb_points = QtWidgets.QCheckBox('数据点')
        self._cb_points.setChecked(True)
        self._cb_points.toggled.connect(self._on_display_change)
        row_pt.addWidget(self._cb_points)
        row_pt.addWidget(QtWidgets.QLabel(' 大小：'))
        self._combo_pt_size = QtWidgets.QComboBox()
        self._combo_pt_size.addItems(['1', '2', '3', '4', '5', '6'])
        self._combo_pt_size.setCurrentText('2')
        self._combo_pt_size.setFixedWidth(48)
        self._combo_pt_size.currentTextChanged.connect(self._on_display_change)
        row_pt.addWidget(self._combo_pt_size)
        row_pt.addStretch()
        vbox.addLayout(row_pt)

        # 数据线 + 粗细
        row_ln = QtWidgets.QHBoxLayout()
        self._cb_lines = QtWidgets.QCheckBox('数据线')
        self._cb_lines.setChecked(True)
        self._cb_lines.toggled.connect(self._on_display_change)
        row_ln.addWidget(self._cb_lines)
        row_ln.addWidget(QtWidgets.QLabel(' 粗细：'))
        self._combo_ln_width = QtWidgets.QComboBox()
        self._combo_ln_width.addItems(['0.5', '1', '1.5', '2', '2.5', '3'])
        self._combo_ln_width.setCurrentText('1')
        self._combo_ln_width.setFixedWidth(48)
        self._combo_ln_width.currentTextChanged.connect(self._on_display_change)
        row_ln.addWidget(self._combo_ln_width)
        row_ln.addStretch()
        vbox.addLayout(row_ln)

        # 趋势线 + 粗细
        row_trend = QtWidgets.QHBoxLayout()
        self._cb_trend = QtWidgets.QCheckBox('趋势线')
        self._cb_trend.setChecked(True)
        self._cb_trend.toggled.connect(self._on_display_change)
        row_trend.addWidget(self._cb_trend)
        row_trend.addWidget(QtWidgets.QLabel(' 粗细：'))
        self._combo_trend_width = QtWidgets.QComboBox()
        self._combo_trend_width.addItems(['1', '1.5', '2', '2.5', '3'])
        self._combo_trend_width.setCurrentText('2')
        self._combo_trend_width.setFixedWidth(48)
        self._combo_trend_width.currentTextChanged.connect(self._on_display_change)
        row_trend.addWidget(self._combo_trend_width)
        row_trend.addStretch()
        vbox.addLayout(row_trend)

        # 斜率计算区
        row_slope = QtWidgets.QHBoxLayout()
        self._cb_slope_region = QtWidgets.QCheckBox('斜率计算区')
        self._cb_slope_region.setChecked(True)
        self._cb_slope_region.toggled.connect(self._on_display_change)
        row_slope.addWidget(self._cb_slope_region)
        row_slope.addStretch()
        vbox.addLayout(row_slope)

        # 时间格式
        row_time = QtWidgets.QHBoxLayout()
        row_time.addWidget(QtWidgets.QLabel('时间格式：'))
        self._rb_s = QtWidgets.QRadioButton('秒')
        self._rb_m = QtWidgets.QRadioButton('分')
        self._rb_h = QtWidgets.QRadioButton('时')
        self._rb_s.setChecked(True)
        # 独立 ButtonGroup — 避免和测量类型 radio 互斥
        self._time_group = QtWidgets.QButtonGroup(panel)
        for rb in (self._rb_s, self._rb_m, self._rb_h):
            rb.toggled.connect(self._on_display_change)
            self._time_group.addButton(rb)
            row_time.addWidget(rb)
        row_time.addStretch()
        vbox.addLayout(row_time)

        # ════════════════════════════════════════════════
        # 加载按钮
        # ════════════════════════════════════════════════
        vbox.addSpacing(8)
        self._load_btn = QtWidgets.QPushButton('加载数据')
        self._load_btn.clicked.connect(self._on_load)
        self._load_btn.setMinimumHeight(36)
        self._load_btn.setStyleSheet(
            'QPushButton { font-weight: bold; font-size: 11pt; '
            'background-color: #3498db; color: white; border-radius: 4px; }'
            'QPushButton:hover { background-color: #2980b9; }')
        vbox.addWidget(self._load_btn)

        self._progress = QtWidgets.QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        self._progress.setFixedHeight(6)
        vbox.addWidget(self._progress)

        vbox.addStretch()
        parent.addWidget(panel)

        # 加载上次使用的路径
        saved_dir = self._settings.value('data_dir', '')
        if saved_dir:
            self._data_dir_edit.setText(saved_dir)
        saved_params = self._settings.value('params_file', '')
        if saved_params:
            self._params_file_edit.setText(saved_params)
            if os.path.isfile(saved_params):
                self._load_params_file(saved_params)

    def _build_right_panel(self, parent):
        """右侧：顶部导航 + 数据游标 + 双标签图 + 斜率结果"""
        panel = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(panel)
        vbox.setContentsMargins(0, 4, 0, 0)
        vbox.setSpacing(2)

        # ── 顶部: 循环导航 ──
        nav = QtWidgets.QHBoxLayout()

        self._btn_prev = QtWidgets.QPushButton('◀ 上一循环')
        self._btn_prev.clicked.connect(lambda: self._cycle_step(-1))
        nav.addWidget(self._btn_prev)

        self._cycle_label = QtWidgets.QLabel('1 / 1')
        self._cycle_label.setAlignment(QtCore.Qt.AlignCenter)
        self._cycle_label.setFixedWidth(80)
        nav.addWidget(self._cycle_label)

        self._cycle_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._cycle_slider.setRange(1, 1)
        self._cycle_slider.setValue(1)
        self._cycle_slider.valueChanged.connect(self._on_slider_move)
        nav.addWidget(self._cycle_slider)

        self._btn_next = QtWidgets.QPushButton('下一循环 ▶')
        self._btn_next.clicked.connect(lambda: self._cycle_step(1))
        nav.addWidget(self._btn_next)

        nav_widget = QtWidgets.QWidget()
        nav_widget.setLayout(nav)
        vbox.addWidget(nav_widget)

        # ── 数据游标 ──
        self._cursor_label = QtWidgets.QLabel(
            'Time: -- | -- s | O₂: -- | Temp: -- | Press: --')
        self._cursor_label.setStyleSheet(
            'font-family: Consolas; font-size: 10pt; padding: 2px 6px;')
        vbox.addWidget(self._cursor_label)

        # ── 斜率结果 ──
        self._slope_label = QtWidgets.QLabel('')
        self._slope_label.setStyleSheet(
            'font-family: Consolas; font-size: 9pt; padding: 2px 6px; color: #555;')
        vbox.addWidget(self._slope_label)

        # ── 双标签页 ──
        self._tabs = QtWidgets.QTabWidget()

        # Tab 1: 全局预览
        self._pw_global = pg.PlotWidget()
        self._pw_global.setBackground('w')
        self._pw_global.showGrid(x=True, y=True, alpha=0.3)
        self._proxy_g = pg.SignalProxy(
            self._pw_global.scene().sigMouseMoved, rateLimit=30,
            slot=lambda e: self._on_cursor(e, self._pw_global))
        self._tabs.addTab(self._pw_global, '全局预览')

        # Tab 2: 局部预览
        self._pw_local = pg.PlotWidget()
        self._pw_local.setBackground('w')
        self._pw_local.showGrid(x=True, y=True, alpha=0.3)
        self._proxy_l = pg.SignalProxy(
            self._pw_local.scene().sigMouseMoved, rateLimit=30,
            slot=lambda e: self._on_cursor(e, self._pw_local))
        self._tabs.addTab(self._pw_local, '局部预览')

        vbox.addWidget(self._tabs, stretch=1)

        parent.addWidget(panel)

    # ════════════════════════════════════════════════════
    #  事件处理
    # ════════════════════════════════════════════════════

    def _browse_data_dir(self):
        start = self._data_dir_edit.text() or self._settings.value('data_dir', '')
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, '选择数据文件夹', start)
        if path:
            self._data_dir_edit.setText(path)
            self._settings.setValue('data_dir', path)
            self._try_auto_match_params()

    def _browse_params(self):
        start = self._params_file_edit.text() or self._settings.value('params_file', '')
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, '选择循环参数文件', start,
            'CSV (*.csv);;All (*.*)')
        if path:
            self._params_file_edit.setText(path)
            self._settings.setValue('params_file', path)
            self._load_params_file(path)

    def _on_channel_change(self):
        if self._data:
            self._on_load()

    def _on_type_change(self):
        self._try_auto_match_params()

    def _on_params_changed(self):
        if self._data is None:
            return
        try:
            new_vals = (
                int(self._p_cycles.text()),
                int(self._p_cycle_length.text()),
                int(self._p_cycle_start.text()),
                int(self._p_cycle_time.text()),
            )
        except (ValueError, TypeError):
            return

        # 值未变化则跳过 (避免失去焦点时误触发)
        if self._params:
            old_vals = (self._params['cycles'], self._params['cycle_length'],
                        self._params['cycle_start'], self._params['cycle_time'])
            if new_vals == old_vals:
                return

        self._params['cycles'] = new_vals[0]
        self._params['cycle_length'] = new_vals[1]
        self._params['cycle_start'] = new_vals[2]
        self._params['cycle_time'] = new_vals[3]
        self._cycle_bounds = compute_cycle_boundaries(
            self._data['time_seconds'], self._params)
        n = len(self._cycle_bounds)
        self._current_cycle = min(self._current_cycle, n) if n else 1
        self._update_nav()
        self._redraw()

    def _on_display_change(self):
        if self._data:
            self._redraw()

    def _on_load(self):
        folder = self._data_dir_edit.text().strip()
        if not folder:
            QtWidgets.QMessageBox.critical(self, '错误', '请先选择数据文件夹')
            return
        channel = self._ch_combo.currentText().strip()
        filepath = os.path.join(folder, f'{channel}.xlsx')
        if not os.path.isfile(filepath):
            QtWidgets.QMessageBox.critical(
                self, '错误', f'文件不存在:\n{filepath}')
            return

        self._progress.setVisible(True)
        QtWidgets.QApplication.processEvents()

        try:
            self._data = load_xlsx(filepath)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, '错误', f'读取 xlsx 失败:\n{e}')
            return
        finally:
            self._progress.setVisible(False)

        params_path = self._params_file_edit.text().strip()
        if params_path and os.path.isfile(params_path):
            self._load_params_file(params_path)
        self._try_auto_match_params()

        if self._params:
            self._cycle_bounds = compute_cycle_boundaries(
                self._data['time_seconds'], self._params)
        else:
            self._cycle_bounds = []

        self._current_cycle = 1
        self._update_nav()
        self._redraw()
        self._update_slope_info()

    def _load_params_file(self, path):
        try:
            self._params_list = load_params(path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, '错误', f'读取参数文件失败:\n{e}')
            self._params_list = None

    def _try_auto_match_params(self):
        if not self._params_list:
            p = self._params_file_edit.text().strip()
            if p and os.path.isfile(p):
                self._load_params_file(p)
            if not self._params_list:
                return

        folder = self._data_dir_edit.text().strip()
        meas_type = 'fish' if self._rb_fish.isChecked() else 'blank'
        matched = match_params(self._params_list, folder, meas_type)
        if matched:
            self._params = matched
            self._fill_param_entries(matched)
            if self._data:
                self._cycle_bounds = compute_cycle_boundaries(
                    self._data['time_seconds'], matched)
                self._current_cycle = 1
                self._update_nav()
                self._redraw()
                self._update_slope_info()
            self._params_status.setText('参数文件已加载！')
        else:
            self._params_status.setText('')

    def _fill_param_entries(self, params):
        self._p_cycles.setText(str(params['cycles']))
        self._p_cycle_length.setText(str(params['cycle_length']))
        self._p_cycle_start.setText(str(params['cycle_start']))
        self._p_cycle_time.setText(str(params['cycle_time']))

    def _save_params_to_csv(self):
        """将当前循环参数保存回 meas_params.csv。"""
        if not self._params:
            QtWidgets.QMessageBox.warning(self, '提示', '没有可保存的参数，请先加载数据。')
            return
        csv_path = self._params_file_edit.text().strip()
        if not csv_path or not os.path.isfile(csv_path):
            QtWidgets.QMessageBox.warning(self, '提示', '参数文件路径无效。')
            return

        # 读取所有行
        import csv
        rows = []
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            header = next(reader)
            rows.append(header)
            for row in reader:
                rows.append(row)

        # 找到匹配行并更新
        folder = self._data_dir_edit.text().strip()
        meas_type = 'fish' if self._rb_fish.isChecked() else 'blank'
        m = re.match(r'(\d{8})', os.path.basename(folder))
        if not m:
            QtWidgets.QMessageBox.warning(self, '提示', '无法从文件夹名提取日期。')
            return
        date_str = m.group(1)

        updated = False
        for row in rows[1:]:
            if len(row) < 12:
                continue
            if row[0].strip() == date_str and row[2].strip() == meas_type:
                try:
                    row[5] = self._p_cycles.text()
                    row[7] = self._p_cycle_length.text()
                    row[8] = self._p_cycle_start.text()
                    row[9] = self._p_cycle_time.text()
                    updated = True
                except IndexError:
                    pass
                break

        if not updated:
            QtWidgets.QMessageBox.warning(self, '提示',
                f'在参数文件中未找到 {date_str} / {meas_type} 的匹配行。')
            return

        # 写回
        with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerows(rows)

        self._params['cycles'] = int(self._p_cycles.text())
        self._params['cycle_length'] = int(self._p_cycle_length.text())
        self._params['cycle_start'] = int(self._p_cycle_start.text())
        self._params['cycle_time'] = int(self._p_cycle_time.text())

        self._params_status.setText('参数已保存！')
        QtWidgets.QMessageBox.information(self, '成功', '循环参数已保存到文件。')

    # ════════════════════════════════════════════════════
    #  绘图
    # ════════════════════════════════════════════════════

    def _time_unit(self):
        if self._rb_m.isChecked():
            return 'm'
        if self._rb_h.isChecked():
            return 'h'
        return 's'

    def _redraw(self):
        if self._data is None or not self._cycle_bounds:
            return

        unit = self._time_unit()
        show_temp = self._cb_temp.isChecked()
        show_press = self._cb_press.isChecked()
        show_points = self._cb_points.isChecked()
        show_lines = self._cb_lines.isChecked()
        pt_size = float(self._combo_pt_size.currentText())
        ln_width = float(self._combo_ln_width.currentText())
        show_trend = self._cb_trend.isChecked()
        trend_w = float(self._combo_trend_width.currentText())
        show_slope = self._cb_slope_region.isChecked()

        render_global(self._pw_global, self._data, self._cycle_bounds,
                      self._current_cycle, show_temp, show_press, unit,
                      show_points=show_points, show_lines=show_lines,
                      point_size=pt_size, line_width=ln_width,
                      show_slope_region=show_slope)

        idx = self._current_cycle - 1
        render_local(self._pw_local, self._data, self._cycle_bounds,
                     idx, show_temp, show_press, unit,
                     show_points=show_points, show_lines=show_lines,
                     point_size=pt_size, line_width=ln_width,
                     show_trend=show_trend, trend_width=trend_w,
                     show_slope_region=show_slope)

        self._update_slope_info()

    def _update_slope_info(self):
        if self._data is None or not self._cycle_bounds:
            self._slope_label.setText('')
            return
        idx = self._current_cycle - 1
        if idx < 0 or idx >= len(self._cycle_bounds):
            self._slope_label.setText('')
            return
        cb = self._cycle_bounds[idx]
        result = compute_slope(self._data['time_seconds'],
                               self._data['oxygen'],
                               cb['slope_start_idx'],
                               cb['slope_end_idx'])
        if result:
            s = (f'n = {result["n_points"]}  |  '
                 f'slope = {result["slope"]:.6f} mgO₂/L·s  |  '
                 f'hourly = {result["hourly_rate"]:.6f} mgO₂/L·h  |  '
                 f'intercept = {result["intercept"]:.4f}  |  '
                 f'R² = {result["r_squared"]:.4f}')
            self._slope_label.setText(s)
        else:
            self._slope_label.setText('数据不足，无法计算斜率')

    # ════════════════════════════════════════════════════
    #  循环导航
    # ════════════════════════════════════════════════════

    def _update_nav(self):
        if not self._cycle_bounds:
            self._cycle_slider.setRange(1, 1)
            self._cycle_label.setText('0 / 0')
            return
        n = len(self._cycle_bounds)
        self._cycle_slider.setRange(1, n)
        self._cycle_slider.setValue(self._current_cycle)
        self._cycle_label.setText(f'{self._current_cycle} / {n}')

    def _cycle_step(self, delta):
        if not self._cycle_bounds:
            return
        n = len(self._cycle_bounds)
        new_val = self._current_cycle + delta
        if 1 <= new_val <= n:
            self._current_cycle = new_val
            self._cycle_slider.setValue(new_val)
            self._cycle_label.setText(f'{self._current_cycle} / {n}')
            self._redraw()

    def _on_slider_move(self, val):
        if self._cycle_bounds and 1 <= val <= len(self._cycle_bounds):
            self._current_cycle = val
            self._cycle_label.setText(
                f'{self._current_cycle} / {len(self._cycle_bounds)}')
            self._redraw()

    # ════════════════════════════════════════════════════
    #  数据游标
    # ════════════════════════════════════════════════════

    def _on_cursor(self, proxy_ev, pw):
        if self._data is None:
            return
        pos = proxy_ev[0]  # SignalProxy 把事件包在元组里
        vb = pw.getPlotItem().vb
        if not vb.sceneBoundingRect().contains(pos):
            return
        mouse_point = vb.mapSceneToView(pos)
        tgt = mouse_point.x()

        div = _UNITS[self._time_unit()]
        tgt_s = tgt * div
        idx = find_nearest(self._data['time_seconds'], tgt_s)
        if idx is None:
            return

        ts = self._data['timestamps'][idx]
        oxy = self._data['oxygen'][idx]
        tmp = self._data['temperature'][idx]
        prs = self._data['pressure'][idx]
        t_s = self._data['time_seconds'][idx]
        hh = int(t_s // 3600)
        mm = int((t_s % 3600) // 60)
        ss = int(t_s % 60)
        rel_time = f'{hh:02d}:{mm:02d}:{ss:02d}'
        self._cursor_label.setText(
            f'Time: {rel_time}  |  {t_s:.0f} s  |  '
            f'O₂: {oxy:.3f} mg/L  |  '
            f'Temp: {tmp:.2f} °C  |  '
            f'Press: {prs:.0f} hPa')


# ════════════════════════════════════════════════════════
#  _UNITS helper (needed by _on_cursor)
# ════════════════════════════════════════════════════════
_UNITS = {'s': 1, 'm': 60, 'h': 3600}
