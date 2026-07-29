"""pyqtgraph 绘图 — 有状态渲染器（类版）。
GlobalRenderer: 全局视图，持有所有 item 引用，支持属性级更新。
LocalRenderer:  局部视图，同理。
"""
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
#  全局渲染器
# ════════════════════════════════════════════════════════

class GlobalRenderer:
    def __init__(self, pw):
        self.pw = pw
        self.o2_line = None
        self.o2_points = None
        self.hl_points = None
        self.slope_regions = []
        self.cycle_highlight = None
        self.temp_line = None
        self.press_line = None
        self._data = None
        self._cycle_bounds = None
        self._time_unit = 's'
        self._stored_cycle = 1

    def full_render(self, data, cycle_bounds, current_cycle,
                    show_temp, show_press, time_unit,
                    show_points=True, show_lines=True,
                    point_size=2, line_width=1,
                    show_slope_region=True):
        _clear_plot(self.pw)
        self._data = data
        self._cycle_bounds = cycle_bounds
        self._time_unit = time_unit
        self._stored_cycle = current_cycle

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
        if show_slope_region:
            for cb in cycle_bounds:
                x0 = cb['slope_start_seconds'] / sd
                x1 = cb['slope_end_seconds'] / sd
                if x1 > x0:
                    r = pg.LinearRegionItem(
                        values=(x0, x1), orientation='vertical',
                        brush=pg.mkBrush(*C_FILL), movable=False)
                    self.pw.addItem(r)
                    self.slope_regions.append(r)

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
            mask = np.zeros(len(oxy), dtype=bool)
            for cb in cycle_bounds:
                idx_s = int(cb['slope_start_idx'])
                idx_e = int(cb['slope_end_idx'])
                if idx_e > idx_s:
                    mask[idx_s:idx_e] = True
            if mask.any():
                self.hl_points = pg.ScatterPlotItem(tx[mask], oxy[mask],
                                                    size=point_size + 1,
                                                    brush=pg.mkBrush(*C_HL, 160),
                                                    pxMode=True, name='_hl_dots')
                self.pw.addItem(self.hl_points)
            else:
                self.hl_points = None
        else:
            self.hl_points = None

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
        vb_main.enableAutoRange()
        vb_temp.enableAutoRange()
        vb_press.enableAutoRange()

    def _update_highlight(self, current_cycle, sd, tx):
        if self.cycle_highlight is not None:
            if self.cycle_highlight.scene() is not None:
                self.pw.removeItem(self.cycle_highlight)
            self.cycle_highlight = None
        idx = current_cycle - 1
        if self._cycle_bounds and 0 <= idx < len(self._cycle_bounds):
            cb = self._cycle_bounds[idx]
            x0 = cb['start_seconds'] / sd
            x1 = min((cb['start_seconds'] + cb['cycle_length']) / sd, tx[-1])
            self.cycle_highlight = pg.LinearRegionItem(
                values=(x0, x1), orientation='vertical',
                brush=pg.mkBrush(255, 255, 0, 20), movable=False)
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

    def toggle_slope_region(self, show):
        for r in self.slope_regions:
            r.setVisible(show)
        if self.hl_points:
            self.hl_points.setVisible(show)

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
        if self.hl_points and self._cycle_bounds:
            mask = np.zeros(len(oxy), dtype=bool)
            for cb in self._cycle_bounds:
                s = int(cb['slope_start_idx']); e = int(cb['slope_end_idx'])
                if e > s: mask[s:e] = True
            if mask.any():
                self.hl_points.setData(tx[mask], oxy[mask])
        if self.temp_line:
            self.temp_line.setData(tx, temp_arr)
        if self.press_line:
            self.press_line.setData(tx, press_arr)

        for i, cb in enumerate(self._cycle_bounds):
            if i < len(self.slope_regions):
                x0 = cb['slope_start_seconds'] / sd
                x1 = cb['slope_end_seconds'] / sd
                if x1 > x0:
                    self.slope_regions[i].setRegion((x0, x1))

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

    def full_render(self, data, cycle_bounds, cycle_idx,
                    show_temp, show_press, time_unit,
                    show_points=True, show_lines=True,
                    point_size=3, line_width=1.5,
                    show_trend=True, trend_width=2,
                    show_slope_region=True):
        _clear_plot(self.pw)
        self._data = data
        self._cycle_bounds = cycle_bounds
        self._time_unit = time_unit
        self._stored_idx = cycle_idx

        pi = self.pw.getPlotItem()

        if cycle_idx < 0 or cycle_idx >= len(cycle_bounds):
            return

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
                brush=pg.mkBrush(*C_FILL), movable=False)
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
            self.hl_points = pg.ScatterPlotItem(tx[ssi:sei], oxy[ssi:sei],
                                                size=point_size + 1,
                                                brush=pg.mkBrush(*C_HL, 180),
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
                self.trend_line = self.pw.plot(rx, ry,
                                               pen=pg.mkPen(C_REG, width=trend_width,
                                                            style=pg.QtCore.Qt.DashLine),
                                               name='Regression')
                legends.append(self.trend_line)
        else:
            self.trend_line = None

        pi.setTitle(f'Cycle {cb["cycle_num"]} / {len(cycle_bounds)}')

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
        if self.trend_line:
            self.trend_line.setPen(pg.mkPen(C_REG, width=width,
                                            style=pg.QtCore.Qt.DashLine))

    def toggle_slope_region(self, show):
        if self.slope_region:
            self.slope_region.setVisible(show)
        if self.hl_points:
            self.hl_points.setVisible(show)

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
    if target_seconds is None:
        return None
    return min(range(len(time_seconds)),
               key=lambda i: abs(time_seconds[i] - target_seconds))
