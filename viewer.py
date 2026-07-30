"""OxyViewer — 溶氧数据可视化工具 · 主窗口 (PyQt5 + pyqtgraph)"""
import os
import re
import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets, QtGui

from data_loader import load_xlsx, load_params, compute_cycle_boundaries
from cycle_analyzer import compute_slope
from plots import GlobalRenderer, LocalRenderer, find_nearest

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
        self._params = None              # 当前选中的循环参数行
        self._cycle_bounds = None
        self._current_cycle = 1
        self._show_points = True
        self._show_lines = True
        self._active_channel = 1          # 当前选中的通道
        # 通道类型: {channel_num: 'fish'|'blank'|'special'}
        self._channel_types = {}
        self._channel_enabled = {}        # {channel_num: bool}
        self._channel_buttons = {}        # {channel_num: QPushButton}
        self._channel_rows = {}           # {channel_num: QWidget}

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

        splitter.setSizes([290, 1000])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        # 设置应用图标
        logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logo.png')
        if os.path.isfile(logo_path):
            self.setWindowIcon(QtGui.QIcon(logo_path))

    def _build_left_panel(self, parent):
        """左侧控制面板 — 新布局：数据文件夹/参数文件/加载数据/数据日期/通道设置/循环参数/图像设置"""
        panel = QtWidgets.QWidget()
        # 宽度由 QSplitter 控制

        # 顶层用 QScrollArea 防止窗口缩小时挤压
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet('QScrollArea { border: none; }')

        inner = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(inner)
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
            line.setStyleSheet('color: #ddd; margin: 2px 0;')
            vbox.addWidget(line)

        def _small(text, color='#888'):
            lbl = QtWidgets.QLabel(text)
            lbl.setStyleSheet(f'font-size: 8pt; color: {color}; padding-left: 2px;')
            return lbl

        # ── 可折叠 section ──
        self._collapsed = {}  # {title: bool}

        def _fold_section(title):
            """创建可折叠区域，返回 (add_to_vbox, content_layout)。"""
            container = QtWidgets.QWidget()
            cvbox = QtWidgets.QVBoxLayout(container)
            cvbox.setContentsMargins(0, 0, 0, 0)
            cvbox.setSpacing(2)

            # 标题行
            header = QtWidgets.QWidget()
            hbox = QtWidgets.QHBoxLayout(header)
            hbox.setContentsMargins(0, 2, 0, 2)
            lbl = QtWidgets.QLabel(title)
            lbl.setStyleSheet(
                'font-weight: bold; color: #000; '
                'border-bottom: 1px solid #ccc; padding-bottom: 2px;')
            btn = QtWidgets.QPushButton('▼')
            btn.setFixedSize(18, 18)
            btn.setFlat(True)
            btn.setStyleSheet('font-size: 10px; padding: 0;')
            hbox.addWidget(lbl)
            hbox.addStretch()
            hbox.addWidget(btn)
            cvbox.addWidget(header)

            # 内容区
            content = QtWidgets.QWidget()
            content_layout = QtWidgets.QVBoxLayout(content)
            content_layout.setContentsMargins(0, 0, 0, 0)
            content_layout.setSpacing(4)
            cvbox.addWidget(content)

            self._collapsed[title] = False

            def toggle():
                collapsed = self._collapsed[title]
                collapsed = not collapsed
                self._collapsed[title] = collapsed
                content.setVisible(not collapsed)
                btn.setText('▶' if collapsed else '▼')

            btn.clicked.connect(toggle)

            vbox.addWidget(container)
            return container, content_layout

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
        btn_open = QtWidgets.QPushButton('打开')
        btn_open.setFixedWidth(48)
        btn_open.clicked.connect(self._open_data_dir)
        row1.addWidget(self._data_dir_edit)
        row1.addWidget(btn)
        row1.addWidget(btn_open)
        vbox.addLayout(row1)

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
        btn_p_open = QtWidgets.QPushButton('打开')
        btn_p_open.setFixedWidth(48)
        btn_p_open.clicked.connect(self._open_params_folder)
        row2.addWidget(self._params_file_edit)
        row2.addWidget(btn_p)
        row2.addWidget(btn_p_open)
        vbox.addLayout(row2)

        self._params_status = QtWidgets.QLabel('')
        self._params_status.setStyleSheet(
            'font-size: 8pt; color: #4caf50; padding-left: 2px;')
        vbox.addWidget(self._params_status)

        _sep()

        # ════════════════════════════════════════════════
        # 加载数据
        # ════════════════════════════════════════════════
        self._load_btn = QtWidgets.QPushButton('加载数据')
        self._load_btn.clicked.connect(self._on_load)
        self._load_btn.setMinimumHeight(36)
        self._load_btn.setStyleSheet(
            'QPushButton { font-weight: bold; font-size: 11pt; '
            'background-color: #3498db; color: white; border-radius: 4px; }'
            'QPushButton:hover { background-color: #2980b9; }')
        vbox.addWidget(self._load_btn)

        # ── 数据日期 ──
        row_date = QtWidgets.QHBoxLayout()
        row_date.addWidget(QtWidgets.QLabel('数据日期：'))
        self._date_edit = QtWidgets.QLineEdit()
        self._date_edit.setFixedWidth(80)
        self._date_edit.setPlaceholderText('自动获取')
        row_date.addWidget(self._date_edit)
        row_date.addStretch()
        vbox.addLayout(row_date)

        self._progress = QtWidgets.QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        self._progress.setFixedHeight(6)
        vbox.addWidget(self._progress)

        _sep()

        # ════════════════════════════════════════════════
        # 数据计算 (可折叠)
        # ════════════════════════════════════════════════
        _rmr_container, _rmr_cl = _fold_section('数据计算')

        # 导出文件名
        row_fn = QtWidgets.QHBoxLayout()
        row_fn.addWidget(QtWidgets.QLabel('导出文件名'))
        self._rmr_fname = QtWidgets.QLineEdit('rmr')
        self._rmr_fname.setFixedWidth(50)
        row_fn.addWidget(self._rmr_fname)
        row_fn.addWidget(QtWidgets.QLabel('.csv'))
        row_fn.addStretch()
        _rmr_cl.addLayout(row_fn)
        lbl = QtWidgets.QLabel('格式: rmr{通道号}.csv (如 rmr1.csv)')
        lbl.setStyleSheet('font-size: 8pt; color: #888; padding-left: 2px;')
        _rmr_cl.addWidget(lbl)

        # 导出文件夹
        row_fd = QtWidgets.QHBoxLayout()
        row_fd.addWidget(QtWidgets.QLabel('导出到'))
        self._rmr_folder_combo = QtWidgets.QComboBox()
        self._rmr_folder_combo.addItems(['数据文件夹', '自定义'])
        self._rmr_folder_combo.currentIndexChanged.connect(self._rmr_on_folder_change)
        row_fd.addWidget(self._rmr_folder_combo)
        _rmr_cl.addLayout(row_fd)

        # 数据文件夹路径 (只读)
        self._rmr_data_dir = QtWidgets.QLineEdit()
        self._rmr_data_dir.setReadOnly(True)
        self._rmr_data_dir.setStyleSheet('background: #f0f0f0;')
        _rmr_cl.addWidget(self._rmr_data_dir)

        # 自定义文件夹行
        rmr_custom = QtWidgets.QWidget()
        self._rmr_custom_widget = rmr_custom
        rmr_custom.setVisible(False)
        rmr_layout = QtWidgets.QHBoxLayout(rmr_custom)
        rmr_layout.setContentsMargins(0, 0, 0, 0)
        self._rmr_custom_edit = QtWidgets.QLineEdit()
        self._rmr_custom_edit.setPlaceholderText('选择自定义文件夹...')
        rmr_layout.addWidget(self._rmr_custom_edit)
        btn_browse = QtWidgets.QPushButton('浏览')
        btn_browse.setFixedWidth(48)
        btn_browse.clicked.connect(self._browse_rmr_dir)
        rmr_layout.addWidget(btn_browse)
        btn_open = QtWidgets.QPushButton('打开')
        btn_open.setFixedWidth(48)
        btn_open.clicked.connect(self._open_rmr_dir)
        rmr_layout.addWidget(btn_open)
        _rmr_cl.addWidget(rmr_custom)

        # 按钮
        row_btn = QtWidgets.QHBoxLayout()
        btn_cur = QtWidgets.QPushButton('计算当前通道')
        btn_cur.clicked.connect(self._rmr_calc_current)
        row_btn.addWidget(btn_cur)
        btn_all = QtWidgets.QPushButton('计算所有通道')
        btn_all.clicked.connect(self._rmr_calc_all)
        row_btn.addWidget(btn_all)
        _rmr_cl.addLayout(row_btn)

        # 进度条
        self._rmr_progress = QtWidgets.QProgressBar()
        self._rmr_progress.setRange(0, 0)
        self._rmr_progress.setTextVisible(False)
        self._rmr_progress.setVisible(False)
        self._rmr_progress.setFixedHeight(4)
        _rmr_cl.addWidget(self._rmr_progress)

        # 状态
        self._rmr_status = QtWidgets.QLabel('')
        self._rmr_status.setStyleSheet('font-size: 8pt; color: #4caf50; padding-left: 2px;')
        _rmr_cl.addWidget(self._rmr_status)

        _sep()

        # ════════════════════════════════════════════════
        # 通道设置 (可折叠)
        # ════════════════════════════════════════════════
        _ch_container, _ch_cl = _fold_section('通道设置')

        row_range = QtWidgets.QHBoxLayout()
        row_range.addWidget(QtWidgets.QLabel('通道范围：'))
        self._ch_from = QtWidgets.QLineEdit('1')
        self._ch_from.setFixedWidth(36)
        self._ch_from.returnPressed.connect(self._refresh_channel_rows)
        row_range.addWidget(self._ch_from)
        row_range.addWidget(QtWidgets.QLabel('~'))
        self._ch_to = QtWidgets.QLineEdit('9')
        self._ch_to.setFixedWidth(36)
        self._ch_to.returnPressed.connect(self._refresh_channel_rows)
        row_range.addWidget(self._ch_to)
        row_range.addStretch()
        _ch_cl.addLayout(row_range)

        # 通道列表容器
        self._ch_list = QtWidgets.QWidget()
        self._ch_list_layout = QtWidgets.QVBoxLayout(self._ch_list)
        self._ch_list_layout.setContentsMargins(0, 0, 0, 0)
        self._ch_list_layout.setSpacing(1)
        _ch_cl.addWidget(self._ch_list)

        self._rebuild_channel_rows(1, 9)

        self._save_ch_btn = QtWidgets.QPushButton('保存到通道设置')
        self._save_ch_btn.clicked.connect(self._save_channel_settings)
        self._save_ch_btn.setMinimumHeight(28)
        _ch_cl.addWidget(self._save_ch_btn)

        _sep()

        # ════════════════════════════════════════════════
        # 循环参数 (可折叠)
        # ════════════════════════════════════════════════
        _cp_container, _cp_cl = _fold_section('循环参数')
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(2)

        self._p_cycles = QtWidgets.QLineEdit()
        self._p_initial = QtWidgets.QLineEdit()
        self._p_cycle_length = QtWidgets.QLineEdit()
        self._p_cycle_start = QtWidgets.QLineEdit()
        self._p_cycle_time = QtWidgets.QLineEdit()
        self._p_flush_time = QtWidgets.QLineEdit()
        self._p_all_time = QtWidgets.QLineEdit()

        row_data = [
            (0, '循环数：', self._p_cycles, 'cycles'),
            (1, '起始偏移(s)：', self._p_initial, 'initial'),
            (2, '周期(s)：', self._p_cycle_length, 'cycle_length'),
            (3, '斜率起点(s)：', self._p_cycle_start, 'cycle_start'),
            (4, '测量(s)：', self._p_cycle_time, 'cycle_time'),
            (5, '冲洗(s)：', self._p_flush_time, 'flush_time'),
            (6, '总时长(s)：', self._p_all_time, 'all_time'),
        ]
        for r, label, w, hint in row_data:
            w.setFixedWidth(72)
            w.returnPressed.connect(self._on_params_changed)
            grid.addWidget(QtWidgets.QLabel(label), r, 0)
            grid.addWidget(w, r, 1)
            grid.addWidget(_small(hint), r, 2)

        self._p_cycles.setReadOnly(True)
        self._p_cycles.setStyleSheet('background-color: #e8e8e8; color: #555;')
        self._p_cycle_length.setReadOnly(True)
        self._p_cycle_length.setStyleSheet('background-color: #e8e8e8; color: #555;')
        _cp_cl.addLayout(grid)

        self._save_btn = QtWidgets.QPushButton('保存到循环参数')
        self._save_btn.clicked.connect(self._save_params_to_csv)
        self._save_btn.setMinimumHeight(28)
        _cp_cl.addWidget(self._save_btn)

        self._save_warn = QtWidgets.QLabel('保存前务必备份原参数！')
        self._save_warn.setStyleSheet('font-size: 8pt; color: #e74c3c; padding-left: 2px;')
        _cp_cl.addWidget(self._save_warn)

        _sep()

        # ════════════════════════════════════════════════
        # 图像设置 (可折叠)
        # ════════════════════════════════════════════════
        _img_container, _img_cl = _fold_section('图像设置')

        row_disp = QtWidgets.QHBoxLayout()
        row_disp.addWidget(QtWidgets.QLabel('数据显示：'))
        self._cb_temp = QtWidgets.QCheckBox('温度')
        self._cb_temp.toggled.connect(self._on_display_change)
        self._cb_press = QtWidgets.QCheckBox('气压')
        self._cb_press.toggled.connect(self._on_display_change)
        row_disp.addWidget(self._cb_temp)
        row_disp.addWidget(self._cb_press)
        row_disp.addStretch()
        _img_cl.addLayout(row_disp)

        _img_cl.addWidget(QtWidgets.QLabel('数据展示：'))

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
        _img_cl.addLayout(row_pt)

        row_ln = QtWidgets.QHBoxLayout()
        self._cb_lines = QtWidgets.QCheckBox('数据线')
        self._cb_lines.setChecked(True)
        self._cb_lines.toggled.connect(self._on_display_change)
        row_ln.addWidget(self._cb_lines)
        row_ln.addWidget(QtWidgets.QLabel(' 宽度：'))
        self._combo_ln_width = QtWidgets.QComboBox()
        self._combo_ln_width.addItems(['0.5', '1', '1.5', '2', '2.5', '3'])
        self._combo_ln_width.setCurrentText('1')
        self._combo_ln_width.setFixedWidth(48)
        self._combo_ln_width.currentTextChanged.connect(self._on_display_change)
        row_ln.addWidget(self._combo_ln_width)
        row_ln.addStretch()
        _img_cl.addLayout(row_ln)

        row_trend = QtWidgets.QHBoxLayout()
        self._cb_trend = QtWidgets.QCheckBox('趋势线')
        self._cb_trend.setChecked(True)
        self._cb_trend.toggled.connect(self._on_display_change)
        row_trend.addWidget(self._cb_trend)
        row_trend.addWidget(QtWidgets.QLabel(' 宽度：'))
        self._combo_trend_width = QtWidgets.QComboBox()
        self._combo_trend_width.addItems(['1', '1.5', '2', '2.5', '3'])
        self._combo_trend_width.setCurrentText('2')
        self._combo_trend_width.setFixedWidth(48)
        self._combo_trend_width.currentTextChanged.connect(self._on_display_change)
        row_trend.addWidget(self._combo_trend_width)
        row_trend.addStretch()
        _img_cl.addLayout(row_trend)

        row_slope = QtWidgets.QHBoxLayout()
        self._cb_slope_region = QtWidgets.QCheckBox('斜率计算区')
        self._cb_slope_region.setChecked(True)
        self._cb_slope_region.toggled.connect(self._on_display_change)
        row_slope.addWidget(self._cb_slope_region)
        row_slope.addStretch()
        _img_cl.addLayout(row_slope)

        row_time = QtWidgets.QHBoxLayout()
        row_time.addWidget(QtWidgets.QLabel('时间格式：'))
        self._rb_s = QtWidgets.QRadioButton('秒')
        self._rb_m = QtWidgets.QRadioButton('分')
        self._rb_h = QtWidgets.QRadioButton('时')
        self._rb_s.setChecked(True)
        self._time_group = QtWidgets.QButtonGroup(panel)
        for rb in (self._rb_s, self._rb_m, self._rb_h):
            rb.toggled.connect(self._on_display_change)
            self._time_group.addButton(rb)
            row_time.addWidget(rb)
        row_time.addStretch()
        _img_cl.addLayout(row_time)

        vbox.addStretch()

        # 装入 QScrollArea 防止缩小时挤压
        scroll.setWidget(inner)
        panel_layout = QtWidgets.QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.addWidget(scroll)
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

    # ════════════════════════════════════════════════════
    #  通道行构建
    # ════════════════════════════════════════════════════

    def _rebuild_channel_rows(self, ch_from, ch_to):
        """重新构建通道行 (ch_from ~ ch_to)。"""
        # 清除旧行
        for w in self._channel_rows.values():
            w.setParent(None)
        self._channel_rows.clear()
        self._channel_buttons.clear()

        for ch in range(ch_from, ch_to + 1):
            row = QtWidgets.QWidget()
            hbox = QtWidgets.QHBoxLayout(row)
            hbox.setContentsMargins(2, 1, 2, 1)
            hbox.setSpacing(2)

            # 勾选框
            cb = QtWidgets.QCheckBox()
            cb.setChecked(self._channel_enabled.get(ch, True))
            cb.toggled.connect(lambda checked, c=ch: self._on_ch_enabled(c, checked))
            hbox.addWidget(cb)

            # 通道按钮 (选中时有蓝色高亮)
            btn = QtWidgets.QPushButton(str(ch))
            btn.setFixedSize(28, 22)
            btn.setCheckable(True)
            btn.setChecked(ch == self._active_channel)
            btn.clicked.connect(lambda checked, c=ch: self._on_channel_button_clicked(c))
            self._channel_buttons[ch] = btn
            hbox.addWidget(btn)

            # Fish / Blank / 特殊 radio buttons
            group = QtWidgets.QButtonGroup(row)
            rb_f = QtWidgets.QRadioButton('Fish')
            rb_b = QtWidgets.QRadioButton('Blank')
            rb_s = QtWidgets.QRadioButton('特殊')
            group.addButton(rb_f)
            group.addButton(rb_b)
            group.addButton(rb_s)
            # 根据类型设置默认选中
            ctype = self._channel_types.get(ch, 'fish')
            {'fish': rb_f, 'blank': rb_b, 'special': rb_s}[ctype].setChecked(True)
            # 信号
            rb_f.toggled.connect(lambda checked, c=ch: self._on_ch_type_changed(c, 'fish'))
            rb_b.toggled.connect(lambda checked, c=ch: self._on_ch_type_changed(c, 'blank'))
            rb_s.toggled.connect(lambda checked, c=ch: self._on_ch_type_changed(c, 'special'))

            hbox.addWidget(rb_f)
            hbox.addWidget(rb_b)
            hbox.addWidget(rb_s)
            hbox.addStretch()

            self._channel_rows[ch] = row
            self._ch_list_layout.addWidget(row)

        self._update_channel_button_styles()

    def _refresh_channel_rows(self):
        """通道范围变更时重建行。"""
        try:
            f = int(self._ch_from.text())
            t = int(self._ch_to.text())
            f = max(1, min(f, 99))
            t = max(f, min(t, 99))
        except ValueError:
            return
        self._rebuild_channel_rows(f, t)

    def _update_channel_button_styles(self):
        """更新通道按钮样式：选中蓝色，未选中浅灰。"""
        for ch, btn in self._channel_buttons.items():
            if btn.isChecked():
                btn.setStyleSheet(
                    'font-weight: bold; font-size: 9pt; '
                    'background-color: #3498db; color: white; border: 1px solid #2980b9;')
            else:
                btn.setStyleSheet(
                    'font-size: 9pt; background-color: #e0e0e0; color: #333; border: 1px solid #ccc;')

    def _on_channel_button_clicked(self, ch):
        """点击通道按钮：切换活动通道并加载数据。"""
        if self._active_channel == ch:
            return
        self._active_channel = ch
        self._update_channel_button_styles()
        # 取消其他按钮的选中
        for c, btn in self._channel_buttons.items():
            btn.setChecked(c == ch)
        # 重新加载该通道数据
        self._on_load()

    def _on_ch_enabled(self, ch, checked):
        """勾选/取消通道。"""
        self._channel_enabled[ch] = checked

    def _on_ch_type_changed(self, ch, ctype):
        """通道类型变更。"""
        if ctype == 'special':
            self._channel_types[ch] = 'special'
        else:
            self._channel_types[ch] = ctype

    def _save_channel_settings(self):
        """保存通道设置到 CSV —— 支持 fish↔特殊↔blank 的行级增删。

        规则:
        - fish + 逗号分隔 chamber_ID → 通用鱼行
        - fish + 单个 chamber_ID → 特殊行(独立参数)
        - blank + 单个 chamber_ID → 空白行
        - 从 fish 改为特殊 → 新建行(复制原 fish 行参数), 从 fish chamber_ID 移除
        - 从 特殊 改为 fish → 删除特殊行, 加入 fish chamber_ID
        - fish ↔ blank → 移动通道号
        """
        csv_path = self._params_file_edit.text().strip()
        if not csv_path or not os.path.isfile(csv_path):
            QtWidgets.QMessageBox.warning(self, '提示', '请先选择有效的参数文件。')
            return

        date_str = self._date_edit.text().strip()
        if not date_str:
            QtWidgets.QMessageBox.warning(self, '提示', '数据日期不能为空。')
            return

        import csv
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames)
            # 检查必要列
            required = ['chamber_ID', 'rmr_type', 'meas_time', 'cycles', 'cycle_length']
            missing = [c for c in required if c not in fieldnames]
            if missing:
                QtWidgets.QMessageBox.critical(self, '参数文件格式错误',
                    f'meas_params.csv 缺少以下列:\n{", ".join(missing)}\n\n'
                    '请确认文件格式正确后再操作。')
                return
            rows = [dict(r) for r in reader]

        # ── 当前通道分类 ──
        fish_chs   = [c for c in sorted(self._channel_types)
                      if self._channel_types[c] == 'fish' and self._channel_enabled.get(c, True)]
        blank_chs  = [c for c in sorted(self._channel_types)
                      if self._channel_types[c] == 'blank' and self._channel_enabled.get(c, True)]
        special_chs = [c for c in sorted(self._channel_types)
                       if self._channel_types[c] == 'special' and self._channel_enabled.get(c, True)]

        # ── 查找/分类该日期的行 ──
        fish_general_row = None
        fish_general_idx = None
        blank_row = None
        blank_idx = None
        special_rows = []   # [(idx, row, chamber_id_str)]

        for i, row in enumerate(rows):
            if row.get('meas_time', '').strip() != date_str:
                continue
            rmr = row.get('rmr_type', '').strip()
            chamber_str = row.get('chamber_ID', '').strip().replace('"', '')
            ch_ids = [x.strip() for x in chamber_str.split(',') if x.strip()]

            if rmr == 'fish' and (',' in row.get('chamber_ID', '') or not chamber_str or len(ch_ids) > 1):
                fish_general_row = row
                fish_general_idx = i
            elif rmr == 'fish' and len(ch_ids) == 1:
                special_rows.append((i, row, chamber_str))
            elif rmr == 'blank':
                blank_row = row
                blank_idx = i

        # ── 处理特殊通道 (新建 / 删除) ──
        csv_special_chs = {int(s[2]): s for s in special_rows}
        new_special = [c for c in special_chs if c not in csv_special_chs]
        removed_special = [c for c in csv_special_chs if c not in special_chs]

        # 反向删除 (避免索引变化)
        for ch in sorted(csv_special_chs.keys(), reverse=True):
            if ch in removed_special:
                idx, row, _ = csv_special_chs[ch]
                del rows[idx]
                for sp in special_rows:
                    if sp[0] > idx:
                        sp = (sp[0] - 1, sp[1], sp[2])
                if blank_idx and blank_idx > idx:
                    blank_idx -= 1
                if fish_general_idx and fish_general_idx > idx:
                    fish_general_idx -= 1
                if ch not in fish_chs:
                    fish_chs.append(ch)

        # 新建特殊行
        for ch in new_special:
            if fish_general_row is None:
                QtWidgets.QMessageBox.warning(self, '提示',
                    f'无法创建特殊通道 ch{ch}：该日期无通用鱼行可作为模板。')
                continue
            new_row = dict(fish_general_row)
            new_row['chamber_ID'] = str(ch)
            insert_at = fish_general_idx + 1 if fish_general_idx is not None else 1
            rows.insert(insert_at, new_row)
            if blank_idx is not None and blank_idx >= insert_at:
                blank_idx += 1

        fish_chs = sorted(set(fish_chs))
        blank_chs = sorted(set(blank_chs))

        if fish_general_row:
            fish_general_row['chamber_ID'] = ','.join(str(c) for c in fish_chs) if fish_chs else ''
        if blank_row:
            blank_row['chamber_ID'] = ','.join(str(c) for c in blank_chs) if blank_chs else ''

        with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        self._params_status.setText('通道设置已保存！')
        QtWidgets.QMessageBox.information(self, '成功', '通道设置已保存到参数文件。')

    def _detect_channels(self, folder):
        """扫描文件夹, 返回存在的通道号列表。"""
        existing = []
        for ch in range(1, 100):
            if os.path.isfile(os.path.join(folder, f'{ch}.xlsx')):
                existing.append(ch)
            elif ch > 15:
                break
        return existing

    def _load_channel_types_from_params(self):
        """从 meas_params.csv 的 chamber_ID 列解析通道类型。"""
        if not self._params_list:
            return
        date_str = self._date_edit.text().strip()
        if not date_str:
            return
        # 重置
        for ch in range(1, 10):
            self._channel_types[ch] = 'fish'
        self._channel_types[1] = 'blank'

        for p in self._params_list:
            if p['meas_time'] != date_str:
                continue
            chamber = str(p.get('chamber_id', '')).strip()
            if not chamber:
                continue
            ids = [int(x.strip()) for x in chamber.split(',') if x.strip()]
            ctype = 'fish' if p['rmr_type'] == 'fish' else 'blank'
            if p['rmr_type'] == 'fish' and (not ids or ids == list(range(2, 10))):
                ctype = 'fish'
            elif p['rmr_type'] == 'blank':
                ctype = 'blank'
            # 判断是否是特殊 (单个仓且不是 1)
            if p['rmr_type'] == 'fish' and len(ids) == 1 and ids[0] != 1:
                ctype = 'special'
            for cid in ids:
                if 1 <= cid <= 20:
                    self._channel_types[cid] = ctype

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
        self._global_renderer = GlobalRenderer(self._pw_global)

        # Tab 2: 局部预览
        self._pw_local = pg.PlotWidget()
        self._pw_local.setBackground('w')
        self._pw_local.showGrid(x=True, y=True, alpha=0.3)
        self._proxy_l = pg.SignalProxy(
            self._pw_local.scene().sigMouseMoved, rateLimit=30,
            slot=lambda e: self._on_cursor(e, self._pw_local))
        self._tabs.addTab(self._pw_local, '局部预览')
        self._local_renderer = LocalRenderer(self._pw_local)

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
            # 自动提取日期 (前 8 位数字)
            m = re.match(r'(\d{8})', os.path.basename(path))
            if m:
                self._date_edit.setText(m.group(1))
            self._try_auto_match_params()

    def _open_data_dir(self):
        path = self._data_dir_edit.text().strip()
        if path and os.path.isdir(path):
            os.startfile(path)
        else:
            QtWidgets.QMessageBox.warning(self, '提示', '文件夹不存在或为空。')

    def _browse_params(self):
        start = self._params_file_edit.text() or self._settings.value('params_file', '')
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, '选择循环参数文件', start,
            'CSV (*.csv);;All (*.*)')
        if path:
            self._params_file_edit.setText(path)
            self._settings.setValue('params_file', path)
            self._load_params_file(path)

    def _open_params_folder(self):
        path = self._params_file_edit.text().strip()
        if path:
            folder = os.path.dirname(path)
            if os.path.isdir(folder):
                os.startfile(folder)
            else:
                QtWidgets.QMessageBox.warning(self, '提示', '文件夹不存在。')
        else:
            QtWidgets.QMessageBox.warning(self, '提示', '请先选择参数文件。')

    def _on_params_changed(self):
        if self._data is None:
            return
        try:
            new_vals = (
                int(self._p_cycles.text()),
                int(self._p_initial.text()),
                int(self._p_cycle_length.text()),
                int(self._p_cycle_start.text()),
                int(self._p_cycle_time.text()),
                int(self._p_flush_time.text()),
                int(self._p_all_time.text()),
            )
        except (ValueError, TypeError):
            return

        # 值未变化则跳过
        if self._params:
            old_vals = (self._params['cycles'], self._params.get('initial', 0),
                        self._params['cycle_length'], self._params['cycle_start'],
                        self._params['cycle_time'], self._params.get('flush_time', 0),
                        self._params.get('all_time', 0))
            if new_vals == old_vals:
                return

        self._params['cycles'] = new_vals[0]
        self._params['initial'] = new_vals[1]
        self._params['cycle_length'] = new_vals[2]
        self._params['cycle_start'] = new_vals[3]
        self._params['cycle_time'] = new_vals[4]
        self._params['flush_time'] = new_vals[5]
        self._params['all_time'] = new_vals[6]
        self._recalc_derived_params()
        self._cycle_bounds = compute_cycle_boundaries(
            self._data['time_seconds'], self._params)
        n = len(self._cycle_bounds)
        self._current_cycle = min(self._current_cycle, n) if n else 1
        self._update_nav()
        self._redraw()

    def _on_display_change(self):
        if self._data is None:
            return
        sender = self.sender()
        # 属性级更新 (不重建全部)
        if sender is self._cb_temp:
            show = self._cb_temp.isChecked()
            self._global_renderer.toggle_temp(show)
            self._local_renderer.toggle_temp(show)
            return
        if sender is self._cb_press:
            show = self._cb_press.isChecked()
            self._global_renderer.toggle_press(show)
            self._local_renderer.toggle_press(show)
            return
        if sender is self._cb_points:
            show = self._cb_points.isChecked()
            self._global_renderer.toggle_points(show)
            self._local_renderer.toggle_points(show)
            return
        if sender is self._cb_lines:
            show = self._cb_lines.isChecked()
            self._global_renderer.toggle_lines(show)
            self._local_renderer.toggle_lines(show)
            return
        if sender is self._cb_trend:
            show = self._cb_trend.isChecked()
            self._local_renderer.toggle_trend(show)
            return
        if sender is self._cb_slope_region:
            show = self._cb_slope_region.isChecked()
            self._global_renderer.toggle_slope_region(show)
            self._local_renderer.toggle_slope_region(show)
            return
        if sender is self._combo_pt_size:
            self._global_renderer.set_point_size(float(self._combo_pt_size.currentText()))
            self._local_renderer.set_point_size(float(self._combo_pt_size.currentText()))
            return
        if sender is self._combo_ln_width:
            self._global_renderer.set_line_width(float(self._combo_ln_width.currentText()))
            self._local_renderer.set_line_width(float(self._combo_ln_width.currentText()))
            return
        if sender is self._combo_trend_width:
            self._local_renderer.set_trend_width(float(self._combo_trend_width.currentText()))
            return
        # 时间格式切换: 无需全量重建
        if sender in (self._rb_s, self._rb_m, self._rb_h):
            if self._rb_s.isChecked(): unit = 's'
            elif self._rb_m.isChecked(): unit = 'm'
            else: unit = 'h'
            self._global_renderer.set_time_unit(unit)
            self._local_renderer.set_time_unit(unit)
            return
        self._redraw()

    def _on_load(self):
        folder = self._data_dir_edit.text().strip()
        if not folder:
            QtWidgets.QMessageBox.critical(self, '错误', '请先选择数据文件夹')
            return
        channel = self._active_channel
        filepath = os.path.join(folder, f'{channel}.xlsx')
        if not os.path.isfile(filepath):
            QtWidgets.QMessageBox.critical(
                self, '错误', f'文件不存在:\\n{filepath}')
            return

        # 自动提取日期
        m = re.match(r'(\d{8})', os.path.basename(folder))
        if m and not self._date_edit.text().strip():
            self._date_edit.setText(m.group(1))

        self._progress.setVisible(True)
        QtWidgets.QApplication.processEvents()

        try:
            self._data = load_xlsx(filepath)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, '错误', f'读取 xlsx 失败:\\n{e}')
            return
        finally:
            self._progress.setVisible(False)

        # 更新通道可用性 (基于文件是否存在，但不改范围)
        existing = self._detect_channels(folder)
        ch_from = int(self._ch_from.text() or 1)
        ch_to = int(self._ch_to.text() or 9)
        for ch in range(max(1, ch_from - 1), max(ch_to + 2, 12)):
            self._channel_enabled[ch] = ch in existing

        params_path = self._params_file_edit.text().strip()
        if params_path and os.path.isfile(params_path):
            self._load_params_file(params_path)
        self._load_channel_types_from_params()
        self._rebuild_channel_rows(ch_from, ch_to)
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
        self._rmr_data_dir.setText(folder)

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

        date_str = self._date_edit.text().strip()
        if not date_str:
            m = re.match(r'(\d{8})', os.path.basename(
                self._data_dir_edit.text().strip()))
            if m:
                date_str = m.group(1)
                self._date_edit.setText(date_str)
        ch = self._active_channel
        meas_type = self._channel_types.get(ch, 'fish')
        if meas_type == 'special':
            meas_type = 'fish'  # special 行在 CSV 中 rmr_type 仍是 fish

        # 优先匹配包含当前通道的 chamber_ID
        matched = None
        for p in self._params_list:
            if p['meas_time'] != date_str or p['rmr_type'] != meas_type:
                continue
            cids = [int(x) for x in p.get('chamber_id', '').split(',') if x.strip()]
            if ch in cids:
                matched = p
                break
        # 回退到第一个日期+类型匹配的行
        if matched is None:
            for p in self._params_list:
                if p['meas_time'] == date_str and p['rmr_type'] == meas_type:
                    matched = p
                    break
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
        self._p_initial.setText(str(params.get('initial', 0)))
        self._p_cycle_length.setText(str(params['cycle_length']))
        self._p_cycle_start.setText(str(params['cycle_start']))
        self._p_cycle_time.setText(str(params['cycle_time']))
        self._p_flush_time.setText(str(params.get('flush_time', 0)))
        self._p_all_time.setText(str(params.get('all_time', 0)))
        self._recalc_derived_params()

    def _recalc_derived_params(self):
        """根据 cycle_time + flush_time + all_time 自动计算 cycle_length 和 cycles。"""
        try:
            ct = int(self._p_cycle_time.text() or '0')
            ft = int(self._p_flush_time.text() or '0')
            at = int(self._p_all_time.text() or '0')
        except ValueError:
            return
        cl = ct + ft
        cy = int((at + ft) / cl) if cl > 0 else 0
        self._p_cycle_length.setText(str(cl))
        self._p_cycles.setText(str(cy))
        if self._params:
            self._params['cycle_length'] = cl
            self._params['cycles'] = cy

    def _save_params_to_csv(self):
        """将当前循环参数保存回 meas_params.csv。"""
        if not self._params:
            QtWidgets.QMessageBox.warning(self, '提示', '没有可保存的参数，请先加载数据。')
            return
        csv_path = self._params_file_edit.text().strip()
        if not csv_path or not os.path.isfile(csv_path):
            QtWidgets.QMessageBox.warning(self, '提示', '参数文件路径无效。')
            return

        import csv
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames)
            # 检查必要列
            required = ['chamber_ID', 'rmr_type', 'meas_time', 'cycles', 'cycle_length']
            missing = [c for c in required if c not in fieldnames]
            if missing:
                QtWidgets.QMessageBox.critical(self, '参数文件格式错误',
                    f'meas_params.csv 缺少以下列:\n{", ".join(missing)}\n\n'
                    '请确认文件格式正确后再操作。')
                return
            rows = [dict(r) for r in reader]

        date_str = self._date_edit.text().strip()
        if not date_str:
            QtWidgets.QMessageBox.warning(self, '提示', '数据日期不能为空。')
            return
        ch = self._active_channel
        ch_type = self._channel_types.get(ch, 'fish')
        csv_rmr = 'fish' if ch_type in ('fish', 'special') else 'blank'

        updated = False
        for row in rows:
            if row.get('meas_time', '').strip() != date_str:
                continue
            if row.get('rmr_type', '').strip() != csv_rmr:
                continue
            row_ch = row.get('chamber_ID', '').strip().replace('"', '')
            row_chs = set(int(x.strip()) for x in row_ch.split(',') if x.strip())
            if ch not in row_chs:
                continue
            row['cycles'] = self._p_cycles.text()
            row['initial'] = self._p_initial.text()
            row['cycle_length'] = self._p_cycle_length.text()
            row['cycle_start'] = self._p_cycle_start.text()
            row['cycle_time'] = self._p_cycle_time.text()
            row['flush_time'] = self._p_flush_time.text()
            row['all_time'] = self._p_all_time.text()
            updated = True
            break

        if not updated:
            QtWidgets.QMessageBox.warning(self, '提示',
                f'在参数文件中未找到 {date_str} / ch{ch} / {csv_rmr} 的匹配行。')
            return

        with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        self._params['cycles'] = int(self._p_cycles.text())
        self._params['initial'] = int(self._p_initial.text())
        self._params['cycle_length'] = int(self._p_cycle_length.text())
        self._params['cycle_start'] = int(self._p_cycle_start.text())
        self._params['cycle_time'] = int(self._p_cycle_time.text())
        self._params['flush_time'] = int(self._p_flush_time.text())
        self._params['all_time'] = int(self._p_all_time.text())

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
        if self._data is None:
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

        self._global_renderer.full_render(self._data, self._cycle_bounds,
                          self._current_cycle, show_temp, show_press, unit,
                          show_points=show_points, show_lines=show_lines,
                          point_size=pt_size, line_width=ln_width,
                          show_slope_region=show_slope)

        idx = self._current_cycle - 1
        self._local_renderer.full_render(self._data, self._cycle_bounds,
                         idx, show_temp, show_press, unit,
                         show_points=show_points, show_lines=show_lines,
                         point_size=pt_size, line_width=ln_width,
                         show_trend=show_trend, trend_width=trend_w,
                         show_slope_region=show_slope)

        self._update_slope_info()

    def _update_slope_info(self):
        if self._data is None or not self._cycle_bounds:
            self._slope_label.setText('参数为空，请填写循环参数')
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

    def _redraw_cycle(self):
        """快速切换循环：只更新高亮 + 局部视图。"""
        if self._data is None or not self._cycle_bounds:
            return
        idx = self._current_cycle - 1
        if idx < 0 or idx >= len(self._cycle_bounds):
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

        self._global_renderer.switch_cycle(self._current_cycle)
        self._local_renderer.full_render(self._data, self._cycle_bounds,
                         idx, show_temp, show_press, unit,
                         show_points=show_points, show_lines=show_lines,
                         point_size=pt_size, line_width=ln_width,
                         show_trend=show_trend, trend_width=trend_w,
                         show_slope_region=show_slope)
        self._update_slope_info()

    def _cycle_step(self, delta):
        if not self._cycle_bounds:
            return
        n = len(self._cycle_bounds)
        new_val = self._current_cycle + delta
        if 1 <= new_val <= n:
            self._current_cycle = new_val
            self._cycle_slider.setValue(new_val)
            self._cycle_label.setText(f'{self._current_cycle} / {n}')
            self._redraw_cycle()

    def _on_slider_move(self, val):
        if self._cycle_bounds and 1 <= val <= len(self._cycle_bounds):
            self._current_cycle = val
            self._cycle_label.setText(
                f'{self._current_cycle} / {len(self._cycle_bounds)}')
            self._redraw_cycle()

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

    # ════════════════════════════════════════════════════
    #  RMR 数据计算
    # ════════════════════════════════════════════════════

    def _rmr_on_folder_change(self, idx):
        self._rmr_data_dir.setVisible(idx == 0)
        self._rmr_custom_widget.setVisible(idx == 1)

    def _rmr_get_export_folder(self):
        if self._rmr_folder_combo.currentIndex() == 0:
            return self._rmr_data_dir.text() or self._data_dir_edit.text()
        return self._rmr_custom_edit.text()

    def _browse_rmr_dir(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, '选择导出文件夹')
        if path:
            self._rmr_custom_edit.setText(path)

    def _open_rmr_dir(self):
        folder = self._rmr_get_export_folder()
        if folder and os.path.isdir(folder):
            os.startfile(folder)

    def _rmr_calc_current(self):
        if hasattr(self, '_active_channel'):
            self._rmr_calc_channels([self._active_channel],
                                     f'当前通道 {self._active_channel} 已保存！')

    def _rmr_calc_all(self):
        channels = [ch for ch, enabled in self._channel_enabled.items()
                    if enabled and self._channel_types.get(ch) in ('fish', 'blank', 'special')]
        if not channels:
            QtWidgets.QMessageBox.warning(self, '提示', '没有可计算的通道（请先加载数据）。')
            return
        saved_str = ','.join(str(c) for c in sorted(channels))
        self._rmr_calc_channels(channels, f'所有通道 {saved_str} 已保存！')

    def _rmr_calc_channels(self, channels, success_msg=None):
        if self._data is None:
            QtWidgets.QMessageBox.warning(self, '提示', '请先加载数据。')
            return
        data_folder = self._data_dir_edit.text()
        params_csv = self._params_file_edit.text() or os.path.join(
            os.path.dirname(data_folder), 'raw', 'meas_params.csv')
        export_folder = self._rmr_get_export_folder()

        # 验证参数文件格式
        if not os.path.isfile(params_csv):
            QtWidgets.QMessageBox.critical(self, '错误',
                f'参数文件不存在:\n{params_csv}')
            return
        try:
            import csv as _csv
            with open(params_csv, 'r', encoding='utf-8-sig') as _f:
                _fields = _csv.DictReader(_f).fieldnames or []
            _required = ['chamber_ID', 'rmr_type', 'meas_time', 'cycles', 'cycle_length']
            _missing = [c for c in _required if c not in _fields]
            if _missing:
                QtWidgets.QMessageBox.critical(self, '参数文件格式错误',
                    f'{os.path.basename(params_csv)} 缺少以下列:\n'
                    f'{", ".join(_missing)}\n\n请确认文件格式正确后再计算。')
                return
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, '错误',
                f'读取参数文件失败:\n{e}')
            return

        if not os.path.isdir(export_folder):
            QtWidgets.QMessageBox.warning(self, '警告', f'导出文件夹不存在:\n{export_folder}')
            return
        r_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'calc_rmr.R')
        date_str = self._date_edit.text() or os.path.basename(data_folder)[:8]
        ch_str = ','.join(str(c) for c in channels)
        msg_parts = [
            '即将调用 R 计算:',
            f'  数据文件夹: {data_folder}',
            f'  参数文件:   {params_csv}',
            f'  实验日期:   {date_str}',
            f'  通道:       {ch_str}',
            f'  导出到:     {export_folder}',
            '', '是否继续？',
        ]
        reply = QtWidgets.QMessageBox.question(
            self, '确认 R 计算', '\n'.join(msg_parts),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if reply != QtWidgets.QMessageBox.Yes:
            return
        import subprocess
        self._rmr_progress.setVisible(True)
        self._rmr_status.setText(f'正在计算通道 {ch_str}...')
        QtWidgets.QApplication.processEvents()

        # renv 项目路径
        project_root = os.path.dirname(os.path.abspath(__file__))
        env = os.environ.copy()
        env['RENV_PROJECT'] = project_root
        try:
            result = subprocess.run(
                ['Rscript', r_script, data_folder, params_csv, date_str, ch_str],
                capture_output=True, text=True, timeout=300,
                cwd=export_folder, env=env)
            if result.returncode == 0:
                self._rmr_progress.setVisible(False)
                self._rmr_status.setText(success_msg or '计算完成！')
            else:
                self._rmr_progress.setVisible(False)
                self._rmr_status.setText('计算失败，请查看 R 输出。')
                QtWidgets.QMessageBox.critical(self, 'R 错误',
                    f'R 脚本返回值 {result.returncode}\n\n{result.stderr[:500]}')
        except FileNotFoundError:
            self._rmr_progress.setVisible(False)
            self._rmr_status.setText('Rscript 未找到，请确认已安装 R。')
        except subprocess.TimeoutExpired:
            self._rmr_progress.setVisible(False)
            self._rmr_status.setText('计算超时。')
        except Exception as e:
            self._rmr_progress.setVisible(False)
            self._rmr_status.setText('计算异常。')
            QtWidgets.QMessageBox.critical(self, '错误', str(e))


# ════════════════════════════════════════════════════════
#  _UNITS helper (needed by _on_cursor)
# ════════════════════════════════════════════════════════
_UNITS = {'s': 1, 'm': 60, 'h': 3600}
