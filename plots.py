"""pyqtgraph 绘图 — 有状态渲染器（类版）。
GlobalRenderer: 全局视图，持有所有 item 引用，支持属性级更新。
LocalRenderer:  局部视图，同理。
"""
import bisect

import numpy as np
import pyqtgraph as pg
from cycle_analyzer import compute_slope

# ── 配色 ──
C_O2   = (31, 119, 180)
C_HL   = (231, 76, 60)
C_T    = (255, 127, 14)
C_P    = (44, 160, 44)
C_REG  = (39, 174, 96)
C_FILL = (231, 76, 60, 35)
# 被「排除循环」的灰色标记（只在计算窗口内填充，不要边框线）
C_EX      = (150, 150, 150)
C_EX_FILL = (150, 150, 150, 70)

_SD = {'s': 1, 'm': 60, 'h': 3600}
_XL = {'s': '时间 (秒)', 'm': '时间 (分)', 'h': '时间 (时)'}


def _div(unit): return _SD[unit]
def _xlbl(unit): return _XL[unit]


# ════════════════════════════════════════════════════════
#  多 ViewBox 辅助
# ════════════════════════════════════════════════════════

def _setup_multi_axes(pw):
    """缓存复用多 ViewBox 结构。"""
    if hasattr(pw, '_oxy_vb_temp'):
        pw._oxy_vb_temp.clear()
        pw._oxy_vb_press.clear()
        return pw.getPlotItem().vb, pw._oxy_vb_temp, pw._oxy_vb_press, pw._oxy_ax_press

    pi = pw.getPlotItem()
    vb_main = pi.vb

    vb_temp = pg.ViewBox()
    pi.scene().addItem(vb_temp)
    pi.getAxis('right').linkToView(vb_temp)
    vb_temp.setXLink(pi)

    vb_press = pg.ViewBox()
    pi.scene().addItem(vb_press)
    ax_press = pg.AxisItem('right')
    pi.layout.addItem(ax_press, 2, 5)
    ax_press.linkToView(vb_press)
    vb_press.setXLink(pi)

    def _sync():
        r = vb_main.sceneBoundingRect()
        vb_temp.setGeometry(r)
        vb_press.setGeometry(r)
    vb_main.sigResized.connect(_sync)
    _sync()

    pw._oxy_vb_temp = vb_temp
    pw._oxy_vb_press = vb_press
    pw._oxy_ax_press = ax_press
    return vb_main, vb_temp, vb_press, ax_press


def _clear_plot(pw):
    pi = pw.getPlotItem()
    pi.vb.clear()
    if pi.legend is not None:
        pi.legend.scene().removeItem(pi.legend)
        pi.legend = None


def _add_legend(pw, legends):
    if not legends:
        return
    pi = pw.getPlotItem()
    pi.addLegend(offset=(-10, 10))
    for item in legends:
        name = item.name() if hasattr(item, 'name') else ''
        if name:
            pi.legend.addItem(item, name)


# ════════════════════════════════════════════════════════
#  区域边框隐藏（被排除循环只看中间灰色填充，不要外面的框线和黄边）
# ════════════════════════════════════════════════════════

def _hide_region_lines(region):
    """隐藏 LinearRegionItem 的两条边框线（含悬停画笔）。"""
    for ln in region.lines:
        ln.setPen(pg.mkPen(None))
        ln.setHoverPen(pg.mkPen(None))


# ════════════════════════════════════════════════════════
#  全局渲染器
# ════════════════════════════════════════════════════════

class GlobalRenderer:
    def __init__(self, pw):
        self.pw = pw
        self.o2_line = None
        self.o2_points = None
        self.hl_points = None
        self.excluded_points = None
        self.slope_regions = []
        self._region_by_cycle = {}
        self.cycle_highlight = None
        self.temp_line = None
        self.press_line = None
        self._data = None
        self._cycle_bounds = None
        self._time_unit = 's'
        self._stored_cycle = 1
        self._excluded = set()      # 被排除的循环号集合 (1-based)
        # 允许 set_excluded() 在不重建视图的情况下更新灰色标记
        self._point_size = 2
        self._show_points = True
        self._show_slope = True
        self._ex_legend_added = False

    def _slope_masks(self, n):
        """返回 (正常斜率窗口掩码, 被排除循环的斜率窗口掩码)。"""
        mask = np.zeros(n, dtype=bool)
        mask_ex = np.zeros(n, dtype=bool)
        for cb in self._cycle_bounds or []:
            s = int(cb['slope_start_idx'])
            e = min(int(cb['slope_end_idx']), n)
            if e <= s:
                continue
            if cb['cycle_num'] in self._excluded:
                mask_ex[s:e] = True
            else:
                mask[s:e] = True
        return mask, mask_ex

    def full_render(self, data, cycle_bounds, current_cycle,
                    show_temp, show_press, time_unit,
                    show_points=True, show_lines=True,
                    point_size=2, line_width=1,
                    show_slope_region=True, excluded_cycles=()):
        _clear_plot(self.pw)
        self._data = data
        self._cycle_bounds = cycle_bounds
        self._time_unit = time_unit
        self._stored_cycle = current_cycle
        self._excluded = {int(c) for c in (excluded_cycles or ())}
        self._point_size = point_size
        self._show_points = show_points
        self._show_slope = show_slope_region
        self._ex_legend_added = False

        pi = self.pw.getPlotItem()
        sd = _div(time_unit)
        pi.setLabel('bottom', _xlbl(time_unit))
        pi.setLabel('left', 'O2 (mg/L)')
        pi.showAxis('right')
        pi.getAxis('right').setLabel('')

        tx = np.array(data['time_seconds']) / sd
        oxy = np.array(data['oxygen'])
        temp_arr = np.array(data['temperature'])
        press_arr = np.array(data['pressure'])

        vb_main, vb_temp, vb_press, ax_press = _setup_multi_axes(self.pw)

        legends = []
        self.slope_regions = []
        self._region_by_cycle = {}
        if show_slope_region:
            for cb in cycle_bounds:
                is_ex = cb['cycle_num'] in self._excluded
                x0 = cb['slope_start_seconds'] / sd
                x1 = cb['slope_end_seconds'] / sd
                if x1 > x0:
                    r = pg.LinearRegionItem(
                        values=(x0, x1), orientation='vertical',
                        brush=pg.mkBrush(*(C_EX_FILL if is_ex else C_FILL)),
                        movable=False)
                    # 只有填充，不要边框线 / 悬停线
                    _hide_region_lines(r)
                    self.pw.addItem(r)
                    self.slope_regions.append(r)
                    self._region_by_cycle[cb['cycle_num']] = r

        if show_lines:
            self.o2_line = self.pw.plot(tx, oxy, pen=pg.mkPen(C_O2, width=line_width), name='O2')
            self.o2_line.setDownsampling(auto=True, method='peak')
            legends.append(self.o2_line)
        else:
            self.o2_line = None

        if show_points:
            self.o2_points = pg.ScatterPlotItem(tx, oxy, size=point_size,
                                                brush=pg.mkBrush(*C_O2, 120),
                                                pxMode=True, name='_dots')
            self.pw.addItem(self.o2_points)
        else:
            self.o2_points = None

        if show_slope_region and show_points:
            mask, mask_ex = self._slope_masks(len(oxy))
            if mask.any():
                self.hl_points = pg.ScatterPlotItem(tx[mask], oxy[mask],
                                                    size=point_size + 1,
                                                    brush=pg.mkBrush(*C_HL, 160),
                                                    pxMode=True, name='_hl_dots')
                self.pw.addItem(self.hl_points)
            else:
                self.hl_points = None
            if mask_ex.any():
                self.excluded_points = pg.ScatterPlotItem(
                    tx[mask_ex], oxy[mask_ex], size=point_size + 1,
                    brush=pg.mkBrush(*C_EX, 170), pxMode=True, name='已排除循环')
                self.pw.addItem(self.excluded_points)
            else:
                self.excluded_points = None
        else:
            self.hl_points = None
            self.excluded_points = None

        self._update_highlight(current_cycle, sd, tx)

        self.temp_line = pg.PlotCurveItem(tx, temp_arr, pen=pg.mkPen(C_T, width=0.8))
        vb_temp.addItem(self.temp_line)
        self.temp_line.setVisible(show_temp)
        if show_temp:
            self.pw.getAxis('right').setLabel('Temp (°C)')
            legends.append(pg.PlotDataItem(name='Temp', pen=C_T))

        self.press_line = pg.PlotCurveItem(tx, press_arr, pen=pg.mkPen(C_P, width=0.8))
        vb_press.addItem(self.press_line)
        self.press_line.setVisible(show_press)
        if show_press:
            ax_press.setLabel('Press (hPa)')
            ax_press.show()
        else:
            ax_press.hide()

        _add_legend(self.pw, legends)
        self._sync_excluded_legend()
        vb_main.enableAutoRange()
        vb_temp.enableAutoRange()
        vb_press.enableAutoRange()

    def _sync_excluded_legend(self):
        """按当前是否有排除循环，增删图例里的「已排除循环」示例。"""
        pi = self.pw.getPlotItem()
        if pi.legend is None:
            return
        name = '已排除循环'
        has = bool(self._excluded)
        if has and not self._ex_legend_added:
            sample = pg.ScatterPlotItem([], [], size=9,
                                        brush=pg.mkBrush(*C_EX, 200), pxMode=True)
            pi.legend.addItem(sample, name)
            self._ex_legend_added = True
        elif not has and self._ex_legend_added:
            pi.legend.removeItem(name)
            self._ex_legend_added = False

    def set_excluded(self, excluded_cycles):
        """只更新「被排除循环」的灰色标记，不重建整个视图（实时响应用）。"""
        excluded = {int(c) for c in (excluded_cycles or ())}
        if excluded == self._excluded:
            return
        self._excluded = excluded
        if self._data is None or not self._cycle_bounds:
            return

        sd = _div(self._time_unit)
        tx = np.array(self._data['time_seconds']) / sd
        oxy = np.array(self._data['oxygen'])

        # 1) 斜率区间换色：只在计算窗口内灰/粉切换，任何情况下都不画边框线
        for cb in self._cycle_bounds:
            region = self._region_by_cycle.get(cb['cycle_num'])
            if region is None:
                continue
            color = C_EX_FILL if cb['cycle_num'] in excluded else C_FILL
            region.setBrush(pg.mkBrush(*color))
            _hide_region_lines(region)

        # 3) 灰色数据点 / 红色斜率点重新分配
        if self._show_slope and self._show_points:
            mask, mask_ex = self._slope_masks(len(oxy))
            if mask_ex.any():
                if self.excluded_points is None:
                    self.excluded_points = pg.ScatterPlotItem(
                        tx[mask_ex], oxy[mask_ex], size=self._point_size + 1,
                        brush=pg.mkBrush(*C_EX, 170), pxMode=True,
                        name='已排除循环')
                    self.pw.addItem(self.excluded_points)
                else:
                    self.excluded_points.setData(tx[mask_ex], oxy[mask_ex])
            elif self.excluded_points is not None:
                if self.excluded_points.scene() is not None:
                    self.pw.removeItem(self.excluded_points)
                self.excluded_points = None
            if self.hl_points is not None and mask.any():
                self.hl_points.setData(tx[mask], oxy[mask])

        # 4) 当前循环高亮带（被排除时转灰）
        self._update_highlight(self._stored_cycle, sd, tx)
        self._sync_excluded_legend()

    def _update_highlight(self, current_cycle, sd, tx):
        if self.cycle_highlight is not None:
            if self.cycle_highlight.scene() is not None:
                self.pw.removeItem(self.cycle_highlight)
            self.cycle_highlight = None
        idx = current_cycle - 1
        if self._cycle_bounds and 0 <= idx < len(self._cycle_bounds):
            cb = self._cycle_bounds[idx]
            # 当前循环被排除时不再叠一层高亮框：灰色填充本身就是「已排除」标记，
            # 叠上去只会多出一圈边框线
            if cb['cycle_num'] in self._excluded:
                return
            x0 = cb['start_seconds'] / sd
            x1 = min((cb['start_seconds'] + cb['cycle_length']) / sd, tx[-1])
            self.cycle_highlight = pg.LinearRegionItem(
                values=(x0, x1), orientation='vertical',
                brush=pg.mkBrush(255, 255, 0, 20), movable=False)
            # 当前所选循环保留带边框的样式（其余斜率窗口仍是纯填充）
            self.pw.addItem(self.cycle_highlight)

    def switch_cycle(self, current_cycle):
        if self._data is None or not self._cycle_bounds:
            return
        self._stored_cycle = current_cycle
        sd = _div(self._time_unit)
        tx = np.array(self._data['time_seconds']) / sd
        self._update_highlight(current_cycle, sd, tx)

    def toggle_temp(self, show):
        if self.temp_line:
            self.temp_line.setVisible(show)

    def toggle_press(self, show):
        if self.press_line:
            self.press_line.setVisible(show)
        # 同步坐标轴
        self._show_press_axis(show)

    def toggle_points(self, show):
        # 记住开关状态：实时排除更新新建灰点时要沿用当前可见性
        self._show_points = show
        if self.o2_points:
            self.o2_points.setVisible(show)
        if self.hl_points:
            self.hl_points.setVisible(show)
        if self.excluded_points:
            self.excluded_points.setVisible(show)

    def toggle_lines(self, show):
        if self.o2_line:
            self.o2_line.setVisible(show)

    def set_point_size(self, size):
        if self.o2_points:
            self.o2_points.setSize(size)
        if self.hl_points:
            self.hl_points.setSize(size + 1)
        if self.excluded_points:
            self.excluded_points.setSize(size + 1)

    def set_line_width(self, width):
        if self.o2_line:
            self.o2_line.setPen(pg.mkPen(C_O2, width=width))

    def toggle_slope_region(self, show):
        self._show_slope = show
        for r in self.slope_regions:
            r.setVisible(show)
        if self.hl_points:
            self.hl_points.setVisible(show)
        if self.excluded_points:
            self.excluded_points.setVisible(show)

    def _show_press_axis(self, show):
        """显示/隐藏气压右轴。"""
        if hasattr(self.pw, '_oxy_ax_press'):
            ax = self.pw._oxy_ax_press
            if show:
                ax.show()
                ax.setLabel('Press (hPa)')
            else:
                ax.hide()

    def set_time_unit(self, time_unit):
        if self._data is None:
            return
        self._time_unit = time_unit
        sd = _div(time_unit)
        tx = np.array(self._data['time_seconds']) / sd
        oxy = np.array(self._data['oxygen'])
        temp_arr = np.array(self._data['temperature'])
        press_arr = np.array(self._data['pressure'])

        self.pw.getPlotItem().setLabel('bottom', _xlbl(time_unit))

        if self.o2_line:
            self.o2_line.setData(tx, oxy)
        if self.o2_points:
            self.o2_points.setData(tx, oxy)
        if self._cycle_bounds:
            mask, mask_ex = self._slope_masks(len(oxy))
            if self.hl_points and mask.any():
                self.hl_points.setData(tx[mask], oxy[mask])
            if self.excluded_points and mask_ex.any():
                self.excluded_points.setData(tx[mask_ex], oxy[mask_ex])
        if self.temp_line:
            self.temp_line.setData(tx, temp_arr)
        if self.press_line:
            self.press_line.setData(tx, press_arr)

        for cb in (self._cycle_bounds or []):
            num = cb['cycle_num']
            x0 = cb['slope_start_seconds'] / sd
            x1 = cb['slope_end_seconds'] / sd
            region = self._region_by_cycle.get(num)
            if region is not None and x1 > x0:
                region.setRegion((x0, x1))

        self._update_highlight(self._stored_cycle, sd, tx)


# ════════════════════════════════════════════════════════
#  局部渲染器
# ════════════════════════════════════════════════════════

class LocalRenderer:
    def __init__(self, pw):
        self.pw = pw
        self.o2_line = None
        self.o2_points = None
        self.hl_points = None
        self.trend_line = None
        self.slope_region = None
        self.temp_line = None
        self.press_line = None
        self._data = None
        self._cycle_bounds = None
        self._time_unit = 's'
        self._stored_idx = 0
        self._is_excluded = False
        # 允许 set_excluded() 就地更新灰色样式（不重建视图）
        self._point_size = 3
        self._trend_width = 2
        self._show_points = True
        self._show_slope = True

    def full_render(self, data, cycle_bounds, cycle_idx,
                    show_temp, show_press, time_unit,
                    show_points=True, show_lines=True,
                    point_size=3, line_width=1.5,
                    show_trend=True, trend_width=2,
                    show_slope_region=True, is_excluded=False):
        _clear_plot(self.pw)
        self._data = data
        self._cycle_bounds = cycle_bounds
        self._time_unit = time_unit
        self._stored_idx = cycle_idx
        self._is_excluded = bool(is_excluded)
        self._point_size = point_size
        self._trend_width = trend_width
        self._show_points = show_points
        self._show_slope = show_slope_region

        pi = self.pw.getPlotItem()

        # 先处理「没有循环参数」的情况：此时局部视图也应显示原始曲线，
        # 不能因为 cycle_idx(0) >= len([]) 而直接 return（会得到空白图）
        if not cycle_bounds:
            sd = _div(time_unit)
            tx = np.array(data['time_seconds']) / sd
            oxy = np.array(data['oxygen'])
            if show_lines:
                self.o2_line = self.pw.plot(tx, oxy, pen=pg.mkPen(C_O2, width=line_width), name='O2')
            else:
                self.o2_line = None
            if show_points:
                self.o2_points = pg.ScatterPlotItem(tx, oxy, size=point_size,
                                                    brush=pg.mkBrush(*C_O2, 140),
                                                    pxMode=True, name='_dots')
                self.pw.addItem(self.o2_points)
            else:
                self.o2_points = None
            pi.setLabel('bottom', _xlbl(time_unit))
            pi.setLabel('left', 'O2 (mg/L)')
            return

        if cycle_idx < 0 or cycle_idx >= len(cycle_bounds):
            return

        sd = _div(time_unit)
        cb = cycle_bounds[cycle_idx]
        si, ei = cb['start_idx'], cb['end_idx']

        t_raw = np.array(data['time_seconds'][si:ei])
        tx = (t_raw - cb['start_seconds']) / sd
        oxy = np.array(data['oxygen'][si:ei])
        temp_arr = np.array(data['temperature'][si:ei])
        press_arr = np.array(data['pressure'][si:ei])

        pi.setLabel('bottom', _xlbl(time_unit))
        pi.setLabel('left', 'O2 (mg/L)')
        pi.showAxis('right')
        pi.getAxis('right').setLabel('')

        vb_main, vb_temp, vb_press, ax_press = _setup_multi_axes(self.pw)

        legends = []
        ssi = cb['slope_start_idx'] - si
        sei = min(cb['slope_end_idx'], ei) - si
        self.slope_region = None
        if sei > ssi and show_slope_region:
            x0 = (cb['slope_start_seconds'] - cb['start_seconds']) / sd
            x1 = (cb['slope_end_seconds'] - cb['start_seconds']) / sd
            self.slope_region = pg.LinearRegionItem(
                values=(x0, x1), orientation='vertical',
                brush=pg.mkBrush(*(C_EX_FILL if is_excluded else C_FILL)),
                movable=False)
            # 局部视图只显示当前所选循环，保留带边框的样式
            self.pw.addItem(self.slope_region)

        if show_lines:
            self.o2_line = self.pw.plot(tx, oxy, pen=pg.mkPen(C_O2, width=line_width), name='O2')
            self.o2_line.setDownsampling(auto=True, method='peak')
            legends.append(self.o2_line)
        else:
            self.o2_line = None

        if show_points:
            self.o2_points = pg.ScatterPlotItem(tx, oxy, size=point_size,
                                                brush=pg.mkBrush(*C_O2, 140),
                                                pxMode=True, name='_dots')
            self.pw.addItem(self.o2_points)
        else:
            self.o2_points = None

        if sei > ssi and show_slope_region and show_points:
            hl_rgb = C_EX if is_excluded else C_HL
            self.hl_points = pg.ScatterPlotItem(tx[ssi:sei], oxy[ssi:sei],
                                                size=point_size + 1,
                                                brush=pg.mkBrush(*hl_rgb, 180),
                                                pxMode=True, name='_hl_dots')
            self.pw.addItem(self.hl_points)
        else:
            self.hl_points = None

        if show_trend:
            result = compute_slope(data['time_seconds'], data['oxygen'],
                                   cb['slope_start_idx'], cb['slope_end_idx'])
            if result:
                rx = np.array([cb['slope_start_seconds'] - cb['start_seconds'],
                               cb['slope_end_seconds'] - cb['start_seconds']]) / sd
                ry = result['intercept'] + result['slope'] * np.array(
                    [cb['slope_start_seconds'], cb['slope_end_seconds']])
                # 被排除的循环用灰色虚线画趋势线，与正常循环区分
                pen = pg.mkPen(C_EX if is_excluded else C_REG, width=trend_width,
                               style=pg.QtCore.Qt.DashLine)
                name = 'Regression (已排除)' if is_excluded else 'Regression'
                self.trend_line = self.pw.plot(rx, ry, pen=pen, name=name)
                legends.append(self.trend_line)
        else:
            self.trend_line = None

        title = f'Cycle {cb["cycle_num"]} / {len(cycle_bounds)}'
        if is_excluded:
            title += '  （已排除，不参与计算）'
        pi.setTitle(title, color=(120, 120, 120) if is_excluded else (0, 0, 0))

        self.temp_line = pg.PlotCurveItem(tx, temp_arr, pen=pg.mkPen(C_T, width=0.8))
        vb_temp.addItem(self.temp_line)
        self.temp_line.setVisible(show_temp)
        if show_temp:
            self.pw.getAxis('right').setLabel('Temp (°C)')
            legends.append(pg.PlotDataItem(name='Temp', pen=C_T))

        self.press_line = pg.PlotCurveItem(tx, press_arr, pen=pg.mkPen(C_P, width=0.8))
        vb_press.addItem(self.press_line)
        self.press_line.setVisible(show_press)
        if show_press:
            ax_press.setLabel('Press (hPa)')
            ax_press.show()
        else:
            ax_press.hide()

        _add_legend(self.pw, legends)
        vb_main.enableAutoRange()
        vb_temp.enableAutoRange()
        vb_press.enableAutoRange()

    def toggle_temp(self, show):
        if self.temp_line:
            self.temp_line.setVisible(show)

    def toggle_press(self, show):
        if self.press_line:
            self.press_line.setVisible(show)
        # 同步坐标轴
        self._show_press_axis(show)

    def toggle_points(self, show):
        self._show_points = show
        if self.o2_points:
            self.o2_points.setVisible(show)
        if self.hl_points:
            self.hl_points.setVisible(show)

    def toggle_lines(self, show):
        if self.o2_line:
            self.o2_line.setVisible(show)

    def set_point_size(self, size):
        if self.o2_points:
            self.o2_points.setSize(size)
        if self.hl_points:
            self.hl_points.setSize(size + 1)

    def set_line_width(self, width):
        if self.o2_line:
            self.o2_line.setPen(pg.mkPen(C_O2, width=width))

    def toggle_trend(self, show):
        if self.trend_line:
            self.trend_line.setVisible(show)

    def set_trend_width(self, width):
        self._trend_width = width
        if self.trend_line:
            # 被排除的循环保持灰色，别被宽度调节改回绿色
            self.trend_line.setPen(pg.mkPen(
                C_EX if self._is_excluded else C_REG, width=width,
                style=pg.QtCore.Qt.DashLine))

    def toggle_slope_region(self, show):
        self._show_slope = show
        if self.slope_region:
            self.slope_region.setVisible(show)
        if self.hl_points:
            self.hl_points.setVisible(show)

    def set_excluded(self, is_excluded):
        """就地切换「已排除」样式（灰化斜率区/点/趋势线/标题），不重建视图。"""
        is_excluded = bool(is_excluded)
        if is_excluded == self._is_excluded:
            return
        self._is_excluded = is_excluded
        cb = None
        if self._cycle_bounds and 0 <= self._stored_idx < len(self._cycle_bounds):
            cb = self._cycle_bounds[self._stored_idx]

        if self.slope_region is not None:
            self.slope_region.setBrush(
                pg.mkBrush(*(C_EX_FILL if is_excluded else C_FILL)))
        # 局部视图只展示当前所选循环，边框线保留（与全局视图里的高亮框一致）
        if self.hl_points is not None:
            rgb = C_EX if is_excluded else C_HL
            self.hl_points.setBrush(pg.mkBrush(*rgb, 180))
        if self.trend_line is not None:
            self.trend_line.setPen(pg.mkPen(
                C_EX if is_excluded else C_REG, width=self._trend_width,
                style=pg.QtCore.Qt.DashLine))
        if cb is not None:
            title = f'Cycle {cb["cycle_num"]} / {len(self._cycle_bounds)}'
            if is_excluded:
                title += '  （已排除，不参与计算）'
            self.pw.getPlotItem().setTitle(
                title, color=(120, 120, 120) if is_excluded else (0, 0, 0))

    def _show_press_axis(self, show):
        """显示/隐藏气压右轴。"""
        if hasattr(self.pw, '_oxy_ax_press'):
            ax = self.pw._oxy_ax_press
            if show:
                ax.show()
                ax.setLabel('Press (hPa)')
            else:
                ax.hide()

    def set_time_unit(self, time_unit):
        if self._data is None or not self._cycle_bounds:
            return
        self._time_unit = time_unit
        sd = _div(time_unit)
        self.pw.getPlotItem().setLabel('bottom', _xlbl(time_unit))
        if self.o2_line:
            cb = self._cycle_bounds[self._stored_idx]
            si = cb['start_idx']
            t_raw = np.array(self._data['time_seconds'][si:cb['end_idx']])
            tx = (t_raw - cb['start_seconds']) / sd
            oxy = np.array(self._data['oxygen'][si:cb['end_idx']])
            if self.o2_line:
                self.o2_line.setData(tx, oxy)
            if self.o2_points:
                self.o2_points.setData(tx, oxy)
            if self.hl_points:
                ssi = cb['slope_start_idx'] - si
                sei = min(cb['slope_end_idx'], cb['end_idx']) - si
                if sei > ssi:
                    self.hl_points.setData(tx[ssi:sei], oxy[ssi:sei])
            if self.slope_region:
                x0 = (cb['slope_start_seconds'] - cb['start_seconds']) / sd
                x1 = (cb['slope_end_seconds'] - cb['start_seconds']) / sd
                self.slope_region.setRegion((x0, x1))
            if self.temp_line:
                temp_arr = np.array(self._data['temperature'][si:cb['end_idx']])
                self.temp_line.setData(tx, temp_arr)
            if self.press_line:
                press_arr = np.array(self._data['pressure'][si:cb['end_idx']])
                self.press_line.setData(tx, press_arr)
            if self.trend_line:
                result = compute_slope(self._data['time_seconds'], self._data['oxygen'],
                                       cb['slope_start_idx'], cb['slope_end_idx'])
                if result:
                    rx = np.array([cb['slope_start_seconds'] - cb['start_seconds'],
                                   cb['slope_end_seconds'] - cb['start_seconds']]) / sd
                    ry = result['intercept'] + result['slope'] * np.array(
                        [cb['slope_start_seconds'], cb['slope_end_seconds']])
                    self.trend_line.setData(rx, ry)


def find_nearest(time_seconds, target_seconds):
    """在升序时间序列中找最接近 target 的下标（二分查找，O(log n)）。

    游标移动是高频操作，早先的 min(...) 全表扫描在 1 万点时每次都要遍历，
    改成二分后只比较相邻两个点。
    """
    if target_seconds is None or len(time_seconds) == 0:
        return None
    i = bisect.bisect_left(time_seconds, target_seconds)
    if i <= 0:
        return 0
    if i >= len(time_seconds):
        return len(time_seconds) - 1
    return i if abs(time_seconds[i] - target_seconds) < abs(
        time_seconds[i - 1] - target_seconds) else i - 1
