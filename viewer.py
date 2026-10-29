"""OxyViewer — 溶氧数据可视化工具 · 主窗口 (PyQt5 + pyqtgraph)"""
import bisect
import glob
import os
import re
import sys
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets, QtGui

from data_loader import (load_xlsx, load_params, compute_cycle_boundaries,
                         load_exclude, parse_cycle_spec, format_cycle_spec,
                         generate_exclude_csv, EXCLUDE_COLUMN, EXCLUDE_FIELDS)
import engine as engine_mod
from cycle_analyzer import compute_slope
from plots import GlobalRenderer, LocalRenderer, find_nearest

# ── 样式 ──────────────────────────────────────────────
# 状态提示标签样式（统一维护，避免各处字面量不一致）
ST_OK   = 'font-size: 8pt; color: #4caf50; padding-left: 2px;'
ST_ERR  = 'font-size: 8pt; color: #e74c3c; padding-left: 2px;'
ST_HINT = 'font-size: 8pt; color: #888; padding-left: 2px;'

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
        self._active_channel = 1          # 当前选中的通道
        # 通道类型: {channel_num: 'fish'|'blank'|'special'}
        self._channel_types = {}
        self._channel_enabled = {}        # {channel_num: bool}
        self._channel_buttons = {}        # {channel_num: QPushButton}
        self._channel_rows = {}           # {channel_num: QWidget}
        # 排除循环
        self._exclude_rows = None         # 排除参数文件内容 (list[dict])
        self._exclude_cycles = {}         # {channel_num: set[int]} 被排除的循环号
        self._exclude_edits = {}          # {channel_num: QLineEdit}

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

        self._splitter = splitter
        # 记住上次拖动的左右宽度（用户调好后不用每次重调）
        saved_sizes = self._settings.value('splitter_sizes')
        restored = False
        if isinstance(saved_sizes, (list, tuple)) and len(saved_sizes) == 2:
            try:
                sizes = [int(v) for v in saved_sizes]
                if all(v > 0 for v in sizes):
                    splitter.setSizes(sizes)
                    restored = True
            except (TypeError, ValueError):
                restored = False
        if not restored:
            splitter.setSizes([300, 1000])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

    def closeEvent(self, event):
        """退出前记住左右分栏宽度。"""
        try:
            self._settings.setValue('splitter_sizes', self._splitter.sizes())
            self._settings.sync()
        except Exception:                       # noqa: BLE001
            pass
        super().closeEvent(event)

        # 设置应用图标
        logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logo.png')
        if os.path.isfile(logo_path):
            self.setWindowIcon(QtGui.QIcon(logo_path))

    def _build_left_panel(self, parent):
        """左侧控制面板 — 新布局：数据文件夹/参数文件/加载数据/数据日期/通道设置/循环参数/图像设置"""
        panel = QtWidgets.QWidget()
        # 宽度由 QSplitter 控制；给个下限，避免缩太窄把控件挤没
        panel.setMinimumWidth(268)

        # 顶层用 QScrollArea 防止窗口缩小时挤压
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        # 需要时才出横向滚动条：之前强制关掉，内容比面板宽时控件会被直接裁掉
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
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

        def _sub(title):
            """二级条目标签：比 _section 轻（不加粗、无下边框）。"""
            lbl = QtWidgets.QLabel(title)
            lbl.setStyleSheet(
                'font-size: 9pt; color: #444; '
                'padding-left: 2px; margin-top: 3px;')
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
        # 参数文件（含二级条目：循环参数文件 / 渗透系数文件）
        # ════════════════════════════════════════════════
        _section('参数文件')

        # ── 循环参数文件 ──
        _sub('循环参数文件')
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
            ST_OK)
        vbox.addWidget(self._params_status)

        # ── 渗透系数文件 ──
        _sub('渗透系数文件')
        row_k = QtWidgets.QHBoxLayout()
        self._k_file_edit = QtWidgets.QLineEdit()
        self._k_file_edit.setPlaceholderText('chamber.csv')
        btn_k = QtWidgets.QPushButton('浏览')
        btn_k.setFixedWidth(48)
        btn_k.clicked.connect(self._browse_kfile)
        btn_k_open = QtWidgets.QPushButton('打开')
        btn_k_open.setFixedWidth(48)
        btn_k_open.clicked.connect(self._open_kfile_folder)
        row_k.addWidget(self._k_file_edit)
        row_k.addWidget(btn_k)
        row_k.addWidget(btn_k_open)
        vbox.addLayout(row_k)

        self._k_status = QtWidgets.QLabel('未选择时使用内置默认值')
        self._k_status.setStyleSheet(
            ST_HINT)
        vbox.addWidget(self._k_status)

        # ── 排除参数文件 ──
        _sub('排除参数文件')
        row_ex = QtWidgets.QHBoxLayout()
        self._exclude_file_edit = QtWidgets.QLineEdit()
        self._exclude_file_edit.setPlaceholderText('meas_exclude.csv')
        self._exclude_file_edit.returnPressed.connect(self._on_exclude_path_changed)
        btn_ex = QtWidgets.QPushButton('浏览')
        btn_ex.setFixedWidth(48)
        btn_ex.clicked.connect(self._browse_exclude)
        btn_ex_open = QtWidgets.QPushButton('打开')
        btn_ex_open.setFixedWidth(48)
        btn_ex_open.clicked.connect(self._open_exclude_folder)
        row_ex.addWidget(self._exclude_file_edit)
        row_ex.addWidget(btn_ex)
        row_ex.addWidget(btn_ex_open)
        vbox.addLayout(row_ex)

        self._exclude_status = QtWidgets.QLabel('未选择时不排除任何循环')
        self._exclude_status.setStyleSheet(
            ST_HINT)
        vbox.addWidget(self._exclude_status)

        self._gen_exclude_btn = QtWidgets.QPushButton('生成排除参数')
        self._gen_exclude_btn.clicked.connect(self._generate_exclude)
        self._gen_exclude_btn.setMinimumHeight(24)
        vbox.addWidget(self._gen_exclude_btn)

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
        self._date_edit.editingFinished.connect(self._on_date_changed)
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
        lbl.setStyleSheet(ST_HINT)
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
        self._rmr_status.setStyleSheet(ST_OK)
        _rmr_cl.addWidget(self._rmr_status)

        _sep()

        # ════════════════════════════════════════════════
        # 通道设置 (可折叠)
        # ════════════════════════════════════════════════
        _ch_container, _ch_cl = _fold_section('通道设置')

        row_range = QtWidgets.QHBoxLayout()
        row_range.addWidget(QtWidgets.QLabel('通道范围：'))
        self._ch_from = QtWidgets.QLineEdit('1')
        self._ch_from.setFixedWidth(34)
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

        self._save_exclude_btn = QtWidgets.QPushButton('保存到排除参数')
        self._save_exclude_btn.clicked.connect(self._save_exclude_settings)
        self._save_exclude_btn.setMinimumHeight(28)
        _ch_cl.addWidget(self._save_exclude_btn)

        self._exclude_hint = QtWidgets.QLabel(
            '被排除的循环仍保留在结果中，只把代谢数据（斜率/截距/R²/速率）留空')
        self._exclude_hint.setStyleSheet(ST_HINT)
        _ch_cl.addWidget(self._exclude_hint)

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
        self._save_warn.setStyleSheet(ST_ERR)
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

        # 长提示文字改成「可换行 + 不参与最小宽度计算」：
        # 否则一句提示会把左侧面板需要的宽度撑到 400+ px，
        # 面板显示不全或被迫出横向滚动条（曲线区域被挤窄）。
        for lbl in inner.findChildren(QtWidgets.QLabel):
            if len(lbl.text()) >= 10:
                lbl.setWordWrap(True)
                lbl.setSizePolicy(QtWidgets.QSizePolicy.Ignored,
                                  QtWidgets.QSizePolicy.Preferred)

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

        # 渗透系数文件（未设置时保持为空 = 使用内置默认值）
        self._k_file_edit.returnPressed.connect(self._on_kfile_changed)
        saved_k = self._settings.value('k_file', '')
        if saved_k:
            if os.path.isfile(saved_k):
                self._k_file_edit.setText(saved_k)
                self._load_kfile(saved_k)
            else:
                # 上次的文件被移动/删除：清掉记录，避免每次启动都提示
                self._settings.remove('k_file')
                self._k_status.setText('上次的渗透系数文件已不存在，已清除该记录')
                self._k_status.setStyleSheet(ST_HINT)
                print(f'  [UI] 渗透系数文件已不存在，已清除记录: {saved_k}')

        # 排除参数文件（未设置时不排除任何循环）
        saved_ex = self._settings.value('exclude_file', '')
        if saved_ex:
            if os.path.isfile(saved_ex):
                self._exclude_file_edit.setText(saved_ex)
                self._load_exclude_file(saved_ex)
            else:
                # 上次的文件被移动/删除：清掉记录，避免每次启动都提示
                self._settings.remove('exclude_file')
                self._exclude_loaded_path = None
                self._exclude_status.setText('上次的排除参数文件已不存在，已清除该记录')
                self._exclude_status.setStyleSheet(ST_HINT)
                print(f'  [UI] 排除参数文件已不存在，已清除记录: {saved_ex}')
        # 没有记录时，自动找参数文件夹里的 meas_ex*.csv（用户可能改过名）
        self._autodetect_exclude_file()

    # ════════════════════════════════════════════════════
    #  通道行构建
    # ════════════════════════════════════════════════════

    def _rebuild_channel_rows(self, ch_from, ch_to):
        """重新构建通道行 (ch_from ~ ch_to)。"""
        # 清除旧行
        for w in self._channel_rows.values():
            w.deleteLater()
        self._channel_rows.clear()
        self._channel_buttons.clear()
        self._exclude_edits.clear()

        for ch in range(ch_from, ch_to + 1):
            wrap = QtWidgets.QWidget()
            wvbox = QtWidgets.QVBoxLayout(wrap)
            wvbox.setContentsMargins(2, 1, 2, 1)
            wvbox.setSpacing(0)

            row = QtWidgets.QWidget()
            hbox = QtWidgets.QHBoxLayout(row)
            hbox.setContentsMargins(0, 0, 0, 0)
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
            btn.clicked.connect(lambda _checked, c=ch: self._on_channel_button_clicked(c))
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
            rb_f.toggled.connect(lambda _checked, c=ch: self._on_ch_type_changed(c, 'fish'))
            rb_b.toggled.connect(lambda _checked, c=ch: self._on_ch_type_changed(c, 'blank'))
            rb_s.toggled.connect(lambda _checked, c=ch: self._on_ch_type_changed(c, 'special'))

            hbox.addWidget(rb_f)
            hbox.addWidget(rb_b)
            hbox.addWidget(rb_s)
            hbox.addStretch()
            wvbox.addWidget(row)

            # ── 排除循环 (该通道不参与计算的循环号) ──
            ex_row = QtWidgets.QHBoxLayout()
            ex_row.setContentsMargins(0, 0, 0, 0)
            ex_row.setSpacing(2)
            ex_row.addSpacing(22)
            ex_row.addWidget(QtWidgets.QLabel('排除：'))
            ed = QtWidgets.QLineEdit()
            ed.setPlaceholderText('如 3,6,9-14')
            ed.setMinimumWidth(90)
            ed.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            ed.setText(format_cycle_spec(self._exclude_cycles.get(ch, ())))
            # 输入即刷新图像；回车/失焦时再做规范化与非法输入提示
            ed.textChanged.connect(lambda _t, c=ch: self._on_exclude_typed(c))
            ed.editingFinished.connect(
                lambda c=ch: self._on_exclude_edited(c))
            self._exclude_edits[ch] = ed
            ex_row.addWidget(ed, 1)
            wvbox.addLayout(ex_row)

            self._channel_rows[ch] = wrap
            self._ch_list_layout.addWidget(wrap)

        self._update_channel_button_styles()

    def _channel_range(self):
        """安全解析「通道范围」两个输入框，返回 (from, to)。

        输入非法（空/非数字/负数）时回退到 1..9，避免后续 int() 抛异常；
        结果保证 1 <= from <= to <= 99。
        """
        def _val(edit, default):
            try:
                return int(str(edit.text()).strip())
            except (TypeError, ValueError):
                return default
        f = _val(self._ch_from, 1)
        t = _val(self._ch_to, 9)
        f = max(1, min(f, 99))
        t = max(1, min(t, 99))
        if t < f:
            f, t = t, f
        return f, t

    def _refresh_channel_rows(self):
        """通道范围变更时重建行。"""
        self._rebuild_channel_rows(*self._channel_range())

    def _update_channel_button_styles(self):
        """更新通道按钮样式：选中蓝色，未选中浅灰。"""
        for _ch, btn in self._channel_buttons.items():
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
        print(f'  [UI] 切换通道: {self._active_channel} → {ch}')
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
        # 只允许「该日期 CSV 里已有的通道」∪「界面上当前显示的通道」，
        # 否则 _channel_types 里默认 fish 的 10~19 会被写进参数文件（凭空多出通道）
        date_channels = set()
        for row in rows:
            if row.get('meas_time', '').strip() != date_str:
                continue
            for token in str(row.get('chamber_ID', '')).replace('"', '').split(','):
                token = token.strip()
                if not token:
                    continue
                try:
                    date_channels.add(int(float(token)))
                except ValueError:
                    continue
        allowed = date_channels | set(self._exclude_edits.keys())

        fish_chs   = [c for c in sorted(self._channel_types)
                      if c in allowed and self._channel_types[c] == 'fish'
                      and self._channel_enabled.get(c, True)]
        blank_chs  = [c for c in sorted(self._channel_types)
                      if c in allowed and self._channel_types[c] == 'blank'
                      and self._channel_enabled.get(c, True)]
        special_chs = [c for c in sorted(self._channel_types)
                       if c in allowed and self._channel_types[c] == 'special'
                       and self._channel_enabled.get(c, True)]

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
                # 更新 special_rows 中的索引
                for i in range(len(special_rows)):
                    sp = special_rows[i]
                    if sp[0] > idx:
                        special_rows[i] = (sp[0] - 1, sp[1], sp[2])
                if blank_idx is not None and blank_idx > idx:
                    blank_idx -= 1
                if fish_general_idx is not None and fish_general_idx > idx:
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

        # 写盘后重新读入，避免内存里还是旧参数（否则下次自动匹配会用旧类型/旧 chamber）
        self._load_params_file(csv_path)
        self._load_channel_types_from_params()
        # 只刷新输入框，不要从排除文件重读（会冲掉尚未保存的排除输入）
        self._fill_exclude_boxes()

        self._params_status.setText('通道设置已保存！')
        QtWidgets.QMessageBox.information(self, '成功', '通道设置已保存到参数文件。')
        print('  [UI] 通道设置已保存')

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
        # 重置为 fish
        for ch in range(1, 20):
            self._channel_types[ch] = 'fish'

        for p in self._params_list:
            if p['meas_time'] != date_str:
                continue
            chamber = str(p.get('chamber_id', '')).replace('"', '')
            ids = []
            for token in chamber.split(','):
                token = token.strip()
                if not token:
                    continue
                try:
                    ids.append(int(float(token)))
                except ValueError:
                    continue
            if not ids:
                continue
            if p['rmr_type'] == 'blank':
                ctype = 'blank'
            elif len(ids) == 1 and ids[0] != 1:
                # 单个仓的 fish 行 = 该通道有独立参数（特殊）
                ctype = 'special'
            else:
                ctype = 'fish'
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
            'Time: -- | -- s | O₂: -- | Temp: -- | Press: -- | Cycle: --')
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
            self._autodetect_exclude_file()

    def _open_params_folder(self):
        path = self._params_file_edit.text().strip()
        if path:
            folder = os.path.dirname(path)
            if os.path.isdir(folder):
                os.startfile(folder)
            else:
                QtWidgets.QMessageBox.warning(self, '提示', '文件夹不存在。')
        else:
            QtWidgets.QMessageBox.warning(self, '提示', '请先选择循环参数文件。')

    # ══════════ 渗透系数文件 ══════════

    def _browse_kfile(self):
        start = self._k_file_edit.text() or self._settings.value('k_file', '')
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, '选择渗透系数文件', start,
            'CSV (*.csv);;All (*.*)')
        if path:
            self._k_file_edit.setText(path)
            self._settings.setValue('k_file', path)
            self._load_kfile(path)

    def _open_kfile_folder(self):
        path = self._k_file_edit.text().strip()
        if path:
            folder = os.path.dirname(path)
            if os.path.isdir(folder):
                os.startfile(folder)
            else:
                QtWidgets.QMessageBox.warning(self, '提示', '文件夹不存在。')
        else:
            QtWidgets.QMessageBox.warning(self, '提示', '请先选择渗透系数文件。')

    def _load_kfile(self, path):
        """校验并显示渗透系数表（chamber.csv: chamber_ID,k_values）。

        实际计算时由 calc_rmr.R / calc_rmr.py 读取该文件，这里只做校验和提示。
        """
        import csv as _csv
        try:
            with open(path, encoding='utf-8-sig', newline='') as f:
                rows = list(_csv.DictReader(f))
            ks = {}
            for r in rows:
                cid = str(r.get('chamber_ID') or r.get('channel') or '').strip()
                kv = str(r.get('k_values') or r.get('k_value') or '').strip()
                if cid and kv:
                    ks[int(float(cid))] = float(kv)
        except Exception as e:
            self._k_status.setText(f'读取失败：{e}')
            self._k_status.setStyleSheet(
                ST_ERR)
            return
        if ks:
            self._k_status.setText(
                f'已加载 {len(ks)} 个通道（{min(ks)}–{max(ks)}）')
            self._k_status.setStyleSheet(
                ST_OK)
        else:
            self._k_status.setText('文件为空或列名不符（应为 chamber_ID,k_values）')
            self._k_status.setStyleSheet(
                ST_ERR)

    def _on_kfile_changed(self):
        path = self._k_file_edit.text().strip()
        if not path:
            return
        path = os.path.abspath(path)          # 统一存绝对路径，避免下次启动按不同 cwd 找不到
        self._k_file_edit.setText(path)
        self._settings.setValue('k_file', path)
        if os.path.isfile(path):
            self._load_kfile(path)

    # ══════════ 排除参数文件 ══════════

    def _autodetect_exclude_file(self):
        """在循环参数文件所在文件夹里自动找排除参数文件（支持改名）。

        用户可能把默认的 meas_exclude.csv 改成别的名字（如 meas_ex.csv），
        这里按 meas_ex*.csv 通配查找；恰好一个才自动选用，避免选错。
        """
        if self._exclude_file_edit.text().strip():
            return
        params_csv = self._params_file_edit.text().strip()
        if not params_csv:
            return
        folder = os.path.dirname(os.path.abspath(params_csv))
        found = sorted(glob.glob(os.path.join(folder, 'meas_ex*.csv')))
        if len(found) != 1:
            return
        self._exclude_file_edit.setText(found[0])
        self._settings.setValue('exclude_file', found[0])
        self._load_exclude_file(found[0])
        print(f'  [UI] 自动识别排除参数文件: {os.path.basename(found[0])}')

    def _browse_exclude(self):
        start = (self._exclude_file_edit.text()
                 or self._settings.value('exclude_file', '')
                 or os.path.dirname(self._params_file_edit.text().strip()))
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, '选择排除参数文件', start, 'CSV (*.csv);;All (*.*)')
        if path:
            self._exclude_file_edit.setText(path)
            self._settings.setValue('exclude_file', path)
            self._load_exclude_file(path)

    def _open_exclude_folder(self):
        path = self._exclude_file_edit.text().strip()
        if path:
            folder = os.path.dirname(path)
            if os.path.isdir(folder):
                os.startfile(folder)
            else:
                QtWidgets.QMessageBox.warning(self, '提示', '文件夹不存在。')
        else:
            QtWidgets.QMessageBox.warning(self, '提示', '请先选择排除参数文件。')

    def _on_exclude_path_changed(self):
        path = self._exclude_file_edit.text().strip()
        if not path:
            self._exclude_rows = None
            self._exclude_cycles = {}
            self._exclude_loaded_path = None
            self._sync_exclude_boxes()
            if self._data is not None and self._cycle_bounds is not None:
                self._redraw()
            return
        path = os.path.abspath(path)          # 统一存绝对路径
        self._exclude_file_edit.setText(path)
        self._settings.setValue('exclude_file', path)
        if os.path.isfile(path):
            self._load_exclude_file(path)
        else:
            self._exclude_loaded_path = None
            self._exclude_status.setText('文件不存在')
            self._exclude_status.setStyleSheet(ST_ERR)

    def _load_exclude_file(self, path, redraw=True):
        """读取排除参数文件，校验列名，同步到各通道输入框。

        Args:
            redraw: 读完后是否重绘图像。选择/切换文件时为 True（排除内容变了）；
                保存后重新读取时为 False（排除设置早已通过 set_excluded 实时生效，
                重绘只会白白重建上万点的主曲线）。
        """
        import csv as _csv
        try:
            with open(path, 'r', encoding='utf-8-sig', newline='') as f:
                fields = list(_csv.DictReader(f).fieldnames or [])
        except Exception as e:
            self._exclude_rows = None
            self._exclude_status.setText(f'读取失败：{e}')
            self._exclude_status.setStyleSheet(ST_ERR)
            return

        missing = [c for c in ('meas_time', 'chamber_ID', EXCLUDE_COLUMN)
                   if c not in fields]
        if missing:
            self._exclude_rows = None
            self._exclude_status.setText(f'缺少列：{", ".join(missing)}')
            self._exclude_status.setStyleSheet(ST_ERR)
            QtWidgets.QMessageBox.critical(
                self, '排除参数文件格式错误',
                f'{os.path.basename(path)} 缺少以下列:\n{", ".join(missing)}\n\n'
                f'可点击「生成排除参数」重建该文件，或手动补上列名 '
                f'({", ".join(EXCLUDE_FIELDS)})。')
            return

        try:
            self._exclude_rows = load_exclude(path)
        except Exception as e:
            self._exclude_rows = None
            self._exclude_status.setText(f'读取失败：{e}')
            self._exclude_status.setStyleSheet(ST_ERR)
            return

        self._exclude_loaded_path = path
        self._sync_exclude_boxes()
        if redraw and self._data is not None and self._cycle_bounds is not None:
            self._redraw()
        print(f'  [UI] 加载排除参数: {os.path.basename(path)} '
              f'({len(self._exclude_rows)} 行)')

    def _sync_exclude_boxes(self):
        """把排除文件内容按当前数据日期同步到 _exclude_cycles 与输入框。"""
        self._exclude_cycles = {}
        date_str = self._date_edit.text().strip()
        # 没有日期时不做跨日期合并（否则会把所有日期的排除混在一起）
        for r in (self._exclude_rows or [] if date_str else []):
            if r['meas_time'] != date_str:
                continue
            for token in str(r['chamber_id']).replace('"', '').split(','):
                token = token.strip()
                if not token:
                    continue
                try:
                    cid = int(float(token))
                except ValueError:
                    continue
                bucket = self._exclude_cycles.setdefault(cid, set())
                if r['exclude']:
                    bucket.update(r['exclude'])
        self._exclude_synced_date = date_str
        self._fill_exclude_boxes()

    def _fill_exclude_boxes(self):
        """用内存里的 _exclude_cycles 刷新各通道输入框（不读盘）。"""
        self._exclude_syncing = True
        try:
            for ch, ed in self._exclude_edits.items():
                ed.setText(format_cycle_spec(self._exclude_cycles.get(ch, ())))
        finally:
            self._exclude_syncing = False
        self._update_exclude_status()

    def _on_date_changed(self):
        """数据日期变化：按新日期重新取排除设置并刷新图像。"""
        if self._exclude_rows:
            self._sync_exclude_boxes()
        if self._data is not None and self._cycle_bounds is not None:
            self._redraw()

    def _update_exclude_status(self):
        if not self._exclude_rows:
            self._exclude_status.setText('未选择时不排除任何循环')
            self._exclude_status.setStyleSheet(ST_HINT)
            return
        n_ex = sum(1 for r in (self._exclude_rows or []) if r['exclude'])
        self._exclude_status.setText(
            f'已加载 {len(self._exclude_rows)} 行，{n_ex} 行有排除')
        self._exclude_status.setStyleSheet(ST_OK)

    def _on_exclude_typed(self, ch):
        """输入过程中实时解析并刷新图像（不写回文本，避免打断输入）。"""
        if getattr(self, '_exclude_syncing', False):
            return
        ed = self._exclude_edits.get(ch)
        if ed is None:
            return
        text = ed.text().strip()
        cycles = parse_cycle_spec(text)
        if text and not cycles:
            return          # 可能是输入到一半（如 "9-"），保持原状态
        if self._exclude_cycles.get(ch, set()) == set(cycles):
            return
        self._exclude_cycles[ch] = set(cycles)
        self._update_exclude_status()
        self._apply_exclusion_to_plot(ch)

    def _apply_exclusion_to_plot(self, ch):
        """属性级更新被排除循环的灰色标记（不重建整个视图）。"""
        if ch != self._active_channel:
            return
        if self._data is None or self._cycle_bounds is None:
            return
        excluded = set(self._exclude_cycles.get(ch, ()))
        self._global_renderer.set_excluded(excluded)
        self._local_renderer.set_excluded(self._current_cycle in excluded)
        self._update_slope_info()

    def _on_exclude_edited(self, ch):
        """排除输入框编辑完成：规范化文本，非法输入给出明确提示。"""
        ed = self._exclude_edits.get(ch)
        if ed is None or getattr(self, '_exclude_syncing', False):
            return
        text = ed.text().strip()
        if text and not parse_cycle_spec(text):
            self._exclude_status.setText(
                '无法识别的排除写法，请用 3,6,9-14 这样的格式')
            self._exclude_status.setStyleSheet(ST_ERR)
            return
        cycles = parse_cycle_spec(text)
        norm = format_cycle_spec(cycles)
        if ed.text() != norm:
            ed.setText(norm)
        self._exclude_cycles[ch] = set(cycles)
        self._update_exclude_status()
        self._apply_exclusion_to_plot(ch)

    def _generate_exclude(self):
        """在循环参数文件所在文件夹生成空白的排除参数文件。"""
        params_csv = self._params_file_edit.text().strip()
        if not params_csv or not os.path.isfile(params_csv):
            QtWidgets.QMessageBox.warning(
                self, '提示', '请先选择有效的循环参数文件。')
            return
        folder = os.path.dirname(os.path.abspath(params_csv))
        # 目标文件：优先沿用「排除参数文件」框里已有的文件名（用户可能改过名，
        # 例如 meas_ex.csv），其次沿用该文件夹里已有的 meas_ex*.csv，
        # 都没有时才用默认名 meas_exclude.csv。
        cur = self._exclude_file_edit.text().strip()
        if cur:
            target = os.path.abspath(
                os.path.join(os.path.dirname(cur) or folder,
                             os.path.basename(cur)))
        else:
            found = sorted(glob.glob(os.path.join(folder, 'meas_ex*.csv')))
            target = found[0] if found else os.path.join(folder,
                                                         'meas_exclude.csv')
        if os.path.isfile(target):
            reply = QtWidgets.QMessageBox.question(
                self, '文件已存在',
                f'{target}\n\n该文件已存在，是否覆盖？（原有排除设置会丢失）',
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
            if reply != QtWidgets.QMessageBox.Yes:
                # 不覆盖：直接载入已有文件，方便继续编辑
                self._exclude_file_edit.setText(target)
                self._settings.setValue('exclude_file', target)
                self._load_exclude_file(target)
                return
        try:
            n = generate_exclude_csv(params_csv, target)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, '生成失败', f'{e}')
            return
        self._exclude_file_edit.setText(target)
        self._settings.setValue('exclude_file', target)
        self._load_exclude_file(target)
        print(f'  [UI] 生成排除参数: {os.path.basename(target)} ({n} 行)')
        QtWidgets.QMessageBox.information(
            self, '完成',
            f'已生成排除参数文件（一个通道一行，共 {n} 行）\n{target}\n\n'
            '在「通道设置」里给通道填排除循环，再点「保存到排除参数」。')

    def _save_exclude_settings(self):
        """把各通道输入框里的排除循环写入排除参数文件。"""
        import csv as _csv
        csv_path = self._exclude_file_edit.text().strip()
        if not csv_path or not os.path.isfile(csv_path):
            QtWidgets.QMessageBox.warning(
                self, '提示', '请先选择或生成排除参数文件。')
            return
        date_str = self._date_edit.text().strip()
        if not date_str:
            QtWidgets.QMessageBox.warning(self, '提示', '数据日期不能为空。')
            return

        with open(csv_path, 'r', encoding='utf-8-sig', newline='') as f:
            reader = _csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            rows = [dict(r) for r in reader]
        missing = [c for c in ('meas_time', 'chamber_ID', EXCLUDE_COLUMN)
                   if c not in fieldnames]
        if missing:
            QtWidgets.QMessageBox.critical(
                self, '排除参数文件格式错误',
                f'缺少以下列:\n{", ".join(missing)}\n\n'
                '请点击「生成排除参数」重建该文件。')
            return

        # 收集界面上的排除设置
        wanted = {}
        for ch, ed in self._exclude_edits.items():
            text = ed.text().strip()
            if text and not parse_cycle_spec(text):
                QtWidgets.QMessageBox.warning(
                    self, '提示',
                    f'通道 {ch} 的排除格式无法识别：{text}\n'
                    '请用 3,6,9-14 这样的写法。')
                return
            wanted[ch] = format_cycle_spec(parse_cycle_spec(text))

        updated = 0
        unmatched = []
        for ch, spec in wanted.items():
            hit = False
            for row in rows:
                if str(row.get('meas_time', '')).strip() != date_str:
                    continue
                ids = [x.strip() for x in
                       str(row.get('chamber_ID', '')).replace('"', '').split(',')
                       if x.strip()]
                if str(ch) in ids:
                    row[EXCLUDE_COLUMN] = spec
                    hit = True
                    updated += 1
                    break
            if not hit:
                unmatched.append(ch)

        with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
            writer = _csv.DictWriter(f, fieldnames=fieldnames,
                                     extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)

        # 重新载入文件内容。
        # 排除设置在输入时已经实时生效（set_excluded 增量刷新），保存只是写文件，
        # 图像状态并没有变化，这里不需要重绘（否则每次保存都要重建上万点的曲线）。
        self._load_exclude_file(csv_path, redraw=False)
        if unmatched and self._data is not None and self._cycle_bounds is not None:
            # 有通道没能写进文件：内存里的排除值已按文件回滚，图像要跟着回滚
            self._redraw()

        msg = f'已保存 {updated} 个通道的排除设置。'
        if unmatched:
            msg += (f'\n\n以下通道在该日期没有对应行（未保存）：'
                    f'{",".join(str(c) for c in unmatched)}\n'
                    '可先点「生成排除参数」补齐通道行。')
        self._exclude_status.setText(f'排除参数已保存（{updated} 个通道）')
        self._exclude_status.setStyleSheet(ST_OK)
        QtWidgets.QMessageBox.information(self, '成功', msg)
        print(f'  [UI] 排除参数已保存: {updated} 个通道')

    def _on_params_changed(self):
        if self._data is None:
            return
        # 空/非数字输入按 0 处理（手动填写时常见），不要静默放弃更新
        new_vals = (self._int_entry(self._p_cycles),
                    self._int_entry(self._p_initial),
                    self._int_entry(self._p_cycle_length),
                    self._int_entry(self._p_cycle_start),
                    self._int_entry(self._p_cycle_time),
                    self._int_entry(self._p_flush_time),
                    self._int_entry(self._p_all_time))

        # 值未变化则跳过
        if self._params:
            old_vals = (self._params['cycles'], self._params.get('initial', 0),
                        self._params['cycle_length'], self._params['cycle_start'],
                        self._params['cycle_time'], self._params.get('flush_time', 0),
                        self._params.get('all_time', 0))
            if new_vals == old_vals:
                return
        else:
            # 该日期/通道在参数文件里没有匹配行：用界面上的值建一份临时参数，
            # 先让图像按新参数预览（用户随后可「保存到循环参数」补写进 CSV）
            self._params = {
                'meas_time': self._date_edit.text().strip(),
                'meas_type': '', 'rmr_type': '', 'chamber_id': '',
                'meas_batch': '', 'temperature': '',
            }

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
        # 参数微调时只挪动斜率窗口（主曲线不重建，差一个数量级）；
        # 循环数量发生变化时增量更新不适用，退回全量重绘。
        if not self._global_renderer.set_bounds(self._cycle_bounds):
            self._redraw()
        else:
            self._redraw_cycle()

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
                self, '错误', f'文件不存在:\n{filepath}')
            return

        # 自动提取日期
        m = re.match(r'(\d{8})', os.path.basename(folder))
        if m and not self._date_edit.text().strip():
            self._date_edit.setText(m.group(1))

        self._progress.setVisible(True)
        QtWidgets.QApplication.processEvents()

        try:
            self._data = load_xlsx(filepath)
            print(f'  [UI] 加载数据: {os.path.basename(filepath)} ({self._data["nrows"]} 行)')
        except Exception as e:
            # 读取失败：清空旧数据，别让上一个通道的曲线留在图上
            self._data = None
            self._cycle_bounds = []
            self._params = None
            self._update_nav()
            QtWidgets.QMessageBox.critical(
                self, '错误', f'读取 xlsx 失败:\n{e}')
            return
        finally:
            self._progress.setVisible(False)

        # 更新通道可用性 (基于文件是否存在，但不改范围)
        existing = self._detect_channels(folder)
        ch_from, ch_to = self._channel_range()
        for ch in range(max(1, ch_from - 1), max(ch_to + 2, 12)):
            self._channel_enabled[ch] = ch in existing

        params_path = self._params_file_edit.text().strip()
        if params_path and os.path.isfile(params_path):
            self._load_params_file(params_path)
        self._load_channel_types_from_params()

        # 排除参数（首次或换了文件才读盘；其余情况沿用内存中的设置，
        # 避免切换通道时丢掉尚未保存的排除输入）
        ex_path = self._exclude_file_edit.text().strip()
        if ex_path and os.path.isfile(ex_path):
            if ex_path != getattr(self, '_exclude_loaded_path', None):
                self._load_exclude_file(ex_path)
        # 未选择排除文件时，保留内存中的排除设置（可能尚未保存）
        # 日期变了则按新日期重新取排除设置
        if self._exclude_rows and \
                getattr(self, '_exclude_synced_date', None) != self._date_edit.text().strip():
            self._sync_exclude_boxes()

        self._rebuild_channel_rows(ch_from, ch_to)
        self._fill_exclude_boxes()
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
            cids = []
            for token in str(p.get('chamber_id', '')).split(','):
                token = token.strip()
                if not token:
                    continue
                try:
                    cids.append(int(float(token)))
                except ValueError:
                    continue
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
            # 该日期/通道在参数文件里没有行：清掉上一通道的参数，
            # 避免界面显示别的通道的参数、图像也按它来画
            self._params = None
            for ed in (self._p_cycles, self._p_initial, self._p_cycle_length,
                       self._p_cycle_start, self._p_cycle_time,
                       self._p_flush_time, self._p_all_time):
                ed.clear()
            self._cycle_bounds = []
            self._current_cycle = 1
            self._update_nav()
            if self._data is not None:
                self._redraw()
            self._params_status.setText('该通道无参数，可手动填写后保存')

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
        ct = self._int_entry(self._p_cycle_time)
        ft = self._int_entry(self._p_flush_time)
        at = self._int_entry(self._p_all_time)
        cl = ct + ft
        cy = int((at + ft) / cl) if cl > 0 else 0
        self._p_cycle_length.setText(str(cl))
        self._p_cycles.setText(str(cy))
        if self._params:
            self._params['cycle_length'] = cl
            self._params['cycles'] = cy

    def _int_entry(self, edit, default=0):
        """读取数字输入框，空/非法值返回 default（避免手动填写时闪退）。"""
        try:
            return int(str(edit.text()).strip())
        except (TypeError, ValueError):
            return default

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
            row_chs = {int(x.strip()) for x in row_ch.split(',') if x.strip()}
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

        # 写盘后重新读入，保持内存与文件一致
        self._load_params_file(csv_path)
        self._load_channel_types_from_params()

        self._params_status.setText('参数已保存！')
        QtWidgets.QMessageBox.information(self, '成功', '循环参数已保存到文件。')
        print('  [UI] 循环参数已保存')

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
        excluded = set(self._exclude_cycles.get(self._active_channel, ()))

        self._global_renderer.full_render(self._data, self._cycle_bounds,
                          self._current_cycle, show_temp, show_press, unit,
                          show_points=show_points, show_lines=show_lines,
                          point_size=pt_size, line_width=ln_width,
                          show_slope_region=show_slope,
                          excluded_cycles=excluded)

        idx = self._current_cycle - 1
        self._local_renderer.full_render(self._data, self._cycle_bounds,
                         idx, show_temp, show_press, unit,
                         show_points=show_points, show_lines=show_lines,
                         point_size=pt_size, line_width=ln_width,
                         show_trend=show_trend, trend_width=trend_w,
                         show_slope_region=show_slope,
                         is_excluded=(self._current_cycle in excluded))

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
        excluded = set(self._exclude_cycles.get(self._active_channel, ()))
        is_ex = self._current_cycle in excluded
        mark = '  |  【已排除，不参与计算】' if is_ex else ''
        head = (f'Cycle {self._current_cycle}/{len(self._cycle_bounds)}'
                + ('（已排除）' if is_ex else '') + '  |  ')
        if result:
            s = (head + f'n = {result["n_points"]}  |  '
                 f'slope = {result["slope"]:.6f} mgO₂/L·s  |  '
                 f'hourly = {result["hourly_rate"]:.6f} mgO₂/L·h  |  '
                 f'intercept = {result["intercept"]:.4f}  |  '
                 f'R² = {result["r_squared"]:.4f}{mark}')
            self._slope_label.setText(s)
        else:
            self._slope_label.setText(f'{head}数据不足，无法计算斜率{mark}')

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
        excluded = set(self._exclude_cycles.get(self._active_channel, ()))

        self._global_renderer.switch_cycle(self._current_cycle)
        self._local_renderer.full_render(self._data, self._cycle_bounds,
                         idx, show_temp, show_press, unit,
                         show_points=show_points, show_lines=show_lines,
                         point_size=pt_size, line_width=ln_width,
                         show_trend=show_trend, trend_width=trend_w,
                         show_slope_region=show_slope,
                         is_excluded=(self._current_cycle in excluded))
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

    def _cycle_at_time(self, t_s):
        """返回 t_s（秒）落在哪个循环里（1-based）；不在任何循环内返回 None。"""
        bounds = self._cycle_bounds or []
        if not bounds or t_s is None:
            return None
        starts = [cb['start_seconds'] for cb in bounds]
        i = bisect.bisect_right(starts, t_s) - 1
        if i < 0:
            return None
        cb = bounds[i]
        if t_s < cb['start_seconds'] + cb['cycle_length']:
            return cb['cycle_num']
        return None

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

        oxy = self._data['oxygen'][idx]
        tmp = self._data['temperature'][idx]
        prs = self._data['pressure'][idx]
        t_s = self._data['time_seconds'][idx]
        hh = int(t_s // 3600)
        mm = int((t_s % 3600) // 60)
        ss = int(t_s % 60)
        rel_time = f'{hh:02d}:{mm:02d}:{ss:02d}'
        # 鼠标所指位置对应的循环号（排除的循环额外标注）
        cycle_no = self._cycle_at_time(t_s)
        if cycle_no is None:
            cycle_txt = '—'
        else:
            excluded = set(self._exclude_cycles.get(self._active_channel, ()))
            cycle_txt = f'{cycle_no} / {len(self._cycle_bounds)}'
            if cycle_no in excluded:
                cycle_txt += '（已排除）'
        self._cursor_label.setText(
            f'Time: {rel_time}  |  {t_s:.0f} s  |  '
            f'O₂: {oxy:.3f} mg/L  |  '
            f'Temp: {tmp:.2f} °C  |  '
            f'Press: {prs:.0f} hPa  |  '
            f'Cycle: {cycle_txt}')

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
        py_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'calc_rmr.py')
        date_str = self._date_edit.text() or os.path.basename(data_folder)[:8]
        ch_str = ','.join(str(c) for c in channels)
        # 渗透系数文件（留空 = 由脚本使用内置默认 k 值）
        k_file = self._k_file_edit.text().strip()
        if k_file and not os.path.isfile(k_file):
            QtWidgets.QMessageBox.warning(
                self, '警告',
                f'渗透系数文件不存在：\n{k_file}\n\n'
                f'请重新选择，或清空该项以使用内置默认值。')
            return
        # 排除参数文件（留空 = 不排除任何循环）
        exclude_csv = self._exclude_file_edit.text().strip()
        if exclude_csv and not os.path.isfile(exclude_csv):
            QtWidgets.QMessageBox.warning(
                self, '警告',
                f'排除参数文件不存在：\n{exclude_csv}\n\n'
                f'请重新选择，或清空该项以不排除任何循环。')
            return
        # ── 计算引擎：环境变量 OXY_ENGINE = R(默认) | python ──
        use_py = engine_mod.is_python_engine()
        engine_name = engine_mod.engine_label()
        msg_parts = [
            f'即将调用 {engine_name} 计算:',
            f'  数据文件夹: {data_folder}',
            f'  参数文件:   {params_csv}',
            f'  渗透系数:   {k_file or "（内置默认值）"}',
            f'  排除参数:   {exclude_csv or "（不排除任何循环）"}',
            f'  实验日期:   {date_str}',
            f'  通道:       {ch_str}',
            f'  导出到:     {export_folder}',
            '', '是否继续？',
        ]
        reply = QtWidgets.QMessageBox.question(
            self, f'确认 {engine_name} 计算', '\n'.join(msg_parts),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if reply != QtWidgets.QMessageBox.Yes:
            return

        print(f'  [UI] 开始 {engine_name} 计算: 通道 {ch_str}')
        self._rmr_success_msg = success_msg
        self._rmr_engine_name = engine_name
        self._rmr_engine_tag = engine_mod.engine_tag()
        self._rmr_progress.setVisible(True)
        self._rmr_status.setText(f'正在计算通道 {ch_str}...')

        # ── QProcess 异步调用，不阻塞 UI ──
        project_root = os.path.dirname(os.path.abspath(__file__))
        self._rmr_process = QtCore.QProcess(self)
        self._rmr_process.setWorkingDirectory(export_folder)
        # 设置环境变量 (兼容 PyQt5 < 5.15.3)
        proc_env = QtCore.QProcessEnvironment.systemEnvironment()
        if not use_py:                      # R 引擎才需要 renv 环境
            r_libs_path = os.path.join(project_root, 'renv', 'library')
            proc_env.insert('RENV_PROJECT', project_root)
            proc_env.insert('R_LIBS', r_libs_path)
        self._rmr_process.setProcessEnvironment(proc_env)

        self._rmr_process.finished.connect(self._on_rmr_finished)
        self._rmr_process.errorOccurred.connect(self._on_rmr_error)
        self._rmr_process.readyReadStandardError.connect(self._on_rmr_output)

        if use_py:
            py_args = [py_script, data_folder, params_csv, date_str, ch_str,
                       k_file, exclude_csv]
            if getattr(sys, 'frozen', False):
                # 打包版：sys.executable 是 OxyViewer.exe，用其内嵌入口执行脚本
                self._rmr_process.start(sys.executable, ['--run-py-engine'] + py_args)
            else:
                self._rmr_process.start(sys.executable, py_args)
        else:
            self._rmr_process.start('Rscript',
                [r_script, data_folder, params_csv, date_str, ch_str,
                 k_file, exclude_csv])

    def _on_rmr_output(self):
        data = bytes(self._rmr_process.readAllStandardError()).decode('utf-8', errors='replace')
        tag = getattr(self, '_rmr_engine_tag', 'R')
        for line in data.strip().split('\n'):
            line = line.strip()
            if line:
                print(f'  [{tag}] {line}')

    def _on_rmr_error(self, error):
        self._rmr_progress.setVisible(False)
        engine_name = getattr(self, '_rmr_engine_name', 'R')
        if error == QtCore.QProcess.FailedToStart:
            if getattr(self, '_rmr_engine_tag', 'R') == 'PY':
                self._rmr_status.setText('Python 计算引擎启动失败，请检查 resprpy 是否已安装。')
            else:
                self._rmr_status.setText('Rscript 未找到，请确认已安装 R。')
        else:
            self._rmr_status.setText(f'{engine_name} 计算异常 (错误码 {error})')

    def _on_rmr_finished(self, exit_code, exit_status):
        self._rmr_progress.setVisible(False)
        engine_name = getattr(self, '_rmr_engine_name', 'R')
        if exit_status == QtCore.QProcess.NormalExit and exit_code == 0:
            status = self._rmr_success_msg or '计算完成！'
            self._rmr_status.setText(status)
            print(f'  [UI] {engine_name} 计算完成: {status}')
        else:
            err = bytes(self._rmr_process.readAllStandardError()).decode('utf-8', errors='replace')
            self._rmr_status.setText('计算失败，请查看输出。')
            QtWidgets.QMessageBox.critical(self, f'{engine_name} 错误',
                f'计算脚本返回值 {exit_code}\n\n{err[:500]}')


# ════════════════════════════════════════════════════════
#  _UNITS helper (needed by _on_cursor)
# ════════════════════════════════════════════════════════
_UNITS = {'s': 1, 'm': 60, 'h': 3600}
