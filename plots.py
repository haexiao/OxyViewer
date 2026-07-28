"""pyqtgraph 绘图 — 高性能 GPU 加速版"""
import numpy as np
import pyqtgraph as pg

# ── 配色 ──
C_O2   = (31, 119, 180)        # 蓝色 — 溶氧
C_HL   = (231, 76, 60)         # 红色 — 斜率计算区
C_T    = (255, 127, 14)        # 橙色 — 温度
C_P    = (44, 160, 44)         # 绿色 — 气压
C_REG  = (39, 174, 96)         # 绿色 — 回归线
C_FILL = (231, 76, 60, 35)     # 红半透 — 斜率区域填充

# ── 单位 ──
_SD = {'s': 1, 'm': 60, 'h': 3600}
_XL = {'s': '时间 (秒)', 'm': '时间 (分)', 'h': '时间 (时)'}


def _div(unit): return _SD[unit]
def _xlbl(unit): return _XL[unit]


# ════════════════════════════════════════════════════════
#  多 ViewBox 辅助 (温度/气压用独立右轴)
# ════════════════════════════════════════════════════════

def setup_multi_axes(pw):
    """为 PlotWidget 设置额外的右轴 ViewBox（温度、气压），缓存复用。
    不随 pw.clear() 清除，只在首次创建。"""
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
    """清除绘图项但保留 ViewBox 结构（替代 pw.clear()）。"""
    pi = pw.getPlotItem()
    # 清除主 ViewBox 中的所有曲线/区域
    pi.vb.clear()
    # 清除图例
    if pi.legend is not None:
        pi.legend.scene().removeItem(pi.legend)
        pi.legend = None


# ════════════════════════════════════════════════════════
#  全局预览
# ════════════════════════════════════════════════════════

def render_global(pw, data, cycle_bounds, current_cycle,
                  show_temp, show_press, time_unit,
                  show_points=True, show_lines=True,
                  point_size=2, line_width=1,
                  show_slope_region=True):
    _clear_plot(pw)

    pi = pw.getPlotItem()
    sd = _div(time_unit)
    pi.setLabel('bottom', _xlbl(time_unit))
    pi.setLabel('left', 'O2 (mg/L)')
    pi.showAxis('right')
    pi.getAxis('right').setLabel('')

    tx = np.array(data['time_seconds']) / sd
    oxy = np.array(data['oxygen'])
    temp_arr = np.array(data['temperature'])
    press_arr = np.array(data['pressure'])

    vb_main, vb_temp, vb_press, ax_press = setup_multi_axes(pw)

    # ── 斜率窗口 — 红色半透明区域 ──
    legends = []
    if show_slope_region:
        for cb in cycle_bounds:
            x0 = cb['slope_start_seconds'] / sd
            x1 = cb['slope_end_seconds'] / sd
            if x1 > x0:
                pw.addItem(pg.LinearRegionItem(
                    values=(x0, x1), orientation='vertical',
                    brush=pg.mkBrush(*C_FILL), movable=False))

    # ── 溶氧: 线 ──
    if show_lines:
        c = pw.plot(tx, oxy, pen=pg.mkPen(C_O2, width=line_width),
                    name='O2')
        c.setDownsampling(auto=True, method='peak')
        legends.append(c)

    # ── 溶氧: 散点 (跳 5 采样) ──
    if show_points:
        step = 5
        pw.plot(tx[::step], oxy[::step],
                pen=None, symbol='o', symbolSize=point_size,
                symbolBrush=C_O2, symbolAlpha=120,
                name='_dots')

    # ── 斜率计算区红色散点 ──
    if show_slope_region and show_points:
        mask = np.zeros(len(oxy), dtype=bool)
        for cb in cycle_bounds:
            hs = cb['slope_start_seconds'] / sd
            he = cb['slope_end_seconds'] / sd
            idx_s = int(cb['slope_start_idx'])
            idx_e = int(cb['slope_end_idx'])
            if idx_e > idx_s:
                mask[idx_s:idx_e] = True
        if mask.any():
            pw.plot(tx[mask], oxy[mask],
                    pen=None, symbol='o', symbolSize=point_size + 1,
                    symbolBrush=C_HL, symbolAlpha=160,
                    name='_hl_dots')

    # ── 当前循环黄色高亮 ──
    if 1 <= current_cycle <= len(cycle_bounds):
        cb = cycle_bounds[current_cycle - 1]
        x0 = cb['start_seconds'] / sd
        x1 = min((cb['start_seconds'] + cb['cycle_length']) / sd, tx[-1])
        pw.addItem(pg.LinearRegionItem(
            values=(x0, x1), orientation='vertical',
            brush=pg.mkBrush(255, 255, 0, 20), movable=False))

    # ── 温度 ──
    if show_temp:
        c = pg.PlotCurveItem(tx, temp_arr, pen=pg.mkPen(C_T, width=0.8))
        vb_temp.addItem(c)
        pw.getAxis('right').setLabel('Temp (°C)')
        legends.append(pg.PlotDataItem(name='Temp', pen=C_T))

    # ── 气压 ──
    if show_press:
        c = pg.PlotCurveItem(tx, press_arr, pen=pg.mkPen(C_P, width=0.8))
        vb_press.addItem(c)
        ax_press.setLabel('Press (hPa)')
        ax_press.show()
    else:
        ax_press.hide()

    _add_legend(pw, legends)

    vb_main.enableAutoRange()
    vb_temp.enableAutoRange()
    vb_press.enableAutoRange()


# ════════════════════════════════════════════════════════
#  局部预览
# ════════════════════════════════════════════════════════

def render_local(pw, data, cycle_bounds, cycle_idx,
                 show_temp, show_press, time_unit,
                 show_points=True, show_lines=True,
                 point_size=3, line_width=1.5,
                 show_trend=True, trend_width=2,
                 show_slope_region=True):
    _clear_plot(pw)

    pi = pw.getPlotItem()

    if cycle_idx < 0 or cycle_idx >= len(cycle_bounds):
        return

    # 当 cycle_bounds 为空时，只显示原始数据(无循环标记)
    if not cycle_bounds:
        sd = _div(time_unit)
        tx = np.array(data['time_seconds']) / sd
        oxy = np.array(data['oxygen'])
        if show_lines:
            pw.plot(tx, oxy, pen=pg.mkPen(C_O2, width=line_width),
                    name='O2')
        if show_points:
            pw.plot(tx, oxy, pen=None, symbol='o', symbolSize=point_size,
                    symbolBrush=C_O2, symbolAlpha=140, name='_dots')
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

    vb_main, vb_temp, vb_press, ax_press = setup_multi_axes(pw)

    # ── 斜率窗口 — 红色半透明区域 ──
    ssi = cb['slope_start_idx'] - si
    sei = min(cb['slope_end_idx'], ei) - si
    legends = []
    if sei > ssi and show_slope_region:
        x0 = (cb['slope_start_seconds'] - cb['start_seconds']) / sd
        x1 = (cb['slope_end_seconds'] - cb['start_seconds']) / sd
        pw.addItem(pg.LinearRegionItem(
            values=(x0, x1), orientation='vertical',
            brush=pg.mkBrush(*C_FILL), movable=False))

    # ── 溶氧 ──
    if show_lines:
        c = pw.plot(tx, oxy, pen=pg.mkPen(C_O2, width=line_width),
                    name='O2')
        c.setDownsampling(auto=True, method='peak')
        legends.append(c)
    if show_points:
        pw.plot(tx, oxy, pen=None, symbol='o', symbolSize=point_size,
                symbolBrush=C_O2, symbolAlpha=140,
                name='_dots')

    # ── 斜率计算区红色散点 ──
    if sei > ssi and show_slope_region and show_points:
        pw.plot(tx[ssi:sei], oxy[ssi:sei],
                pen=None, symbol='o', symbolSize=point_size + 1,
                symbolBrush=C_HL, symbolAlpha=180,
                name='_hl_dots')

    # ── 回归线 ──
    if show_trend:
        from cycle_analyzer import compute_slope
        result = compute_slope(data['time_seconds'], data['oxygen'],
                               cb['slope_start_idx'], cb['slope_end_idx'])
        if result:
            rx = np.array([cb['slope_start_seconds'] - cb['start_seconds'],
                           cb['slope_end_seconds'] - cb['start_seconds']]) / sd
            ry = result['intercept'] + result['slope'] * np.array(
                [cb['slope_start_seconds'], cb['slope_end_seconds']])
            c = pw.plot(rx, ry, pen=pg.mkPen(C_REG, width=trend_width,
                                             style=pg.QtCore.Qt.DashLine),
                        name='Regression')
            legends.append(c)

    pi.setTitle(f'Cycle {cb["cycle_num"]} / {len(cycle_bounds)}')

    # ── 温度 ──
    if show_temp:
        c = pg.PlotCurveItem(tx, temp_arr, pen=pg.mkPen(C_T, width=0.8))
        vb_temp.addItem(c)
        pw.getAxis('right').setLabel('Temp (°C)')
        legends.append(pg.PlotDataItem(name='Temp', pen=C_T))

    # ── 气压 ──
    if show_press:
        c = pg.PlotCurveItem(tx, press_arr, pen=pg.mkPen(C_P, width=0.8))
        vb_press.addItem(c)
        ax_press.setLabel('Press (hPa)')
        ax_press.show()
    else:
        ax_press.hide()

    _add_legend(pw, legends)

    vb_main.enableAutoRange()
    vb_temp.enableAutoRange()
    vb_press.enableAutoRange()


# ════════════════════════════════════════════════════════
#  图例
# ════════════════════════════════════════════════════════

def _clear_legend(pw):
    """清除旧图例。"""
    pi = pw.getPlotItem()
    if pi.legend is not None:
        pi.legend.scene().removeItem(pi.legend)
        pi.legend = None


def _add_legend(pw, legends):
    """添加图例（只添加 legends 列表中的项）。"""
    if not legends:
        return
    pi = pw.getPlotItem()
    pi.addLegend(offset=(-10, 10))
    for item in legends:
        name = item.name() if hasattr(item, 'name') else ''
        if name:
            pi.legend.addItem(item, name)


# ════════════════════════════════════════════════════════
#  数据游标
# ════════════════════════════════════════════════════════

def find_nearest(time_seconds, target_seconds):
    if target_seconds is None:
        return None
    return min(range(len(time_seconds)),
               key=lambda i: abs(time_seconds[i] - target_seconds))
