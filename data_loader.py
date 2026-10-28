"""数据读取与参数解析"""
import csv
import bisect
from datetime import datetime
from openpyxl import load_workbook

# ── 排除参数文件 ──
# 一行一个通道（chamber_ID 为单个整数），最后一列记录要排除的循环号。
# 排除循环用紧凑写法: "3,6,9-14"（连续 ≥3 个会压缩成区间）。
EXCLUDE_COLUMN = 'exclude_cycles'
EXCLUDE_FIELDS = ['meas_time', 'meas_type', 'rmr_type', 'chamber_ID',
                  'meas_batch', EXCLUDE_COLUMN]

# 区间连接符（用户可能输入的各种横线/波浪线）
_RANGE_SEPS = '-~—–－'


def parse_cycle_spec(text):
    """解析排除循环表达式，返回升序去重的循环号列表。

    支持: "3,6,9-14" / "3 6 9~14" / 中文逗号、顿号 / 区间端点写反。
    无法识别的片段会被忽略（调用方可用返回值是否为空来判断整体非法）。
    """
    if text is None:
        return []
    s = str(text)
    for ch in ('，', '、', '；', ';', ' ', '\t', '，'):
        s = s.replace(ch, ',')
    out = set()
    for part in s.split(','):
        part = part.strip()
        if not part:
            continue
        sep = next((c for c in part if c in _RANGE_SEPS), None)
        if sep is not None:
            a, _, b = part.partition(sep)
            try:
                lo, hi = int(a.strip()), int(b.strip())
            except ValueError:
                continue
            if lo > hi:
                lo, hi = hi, lo
            out.update(range(lo, hi + 1))
        else:
            try:
                out.add(int(part))
            except ValueError:
                continue
    return sorted(v for v in out if v > 0)


def format_cycle_spec(cycles):
    """把循环号集合压缩成 "3,6,9-14" 写法（连续 ≥3 个才用区间）。"""
    nums = sorted({int(c) for c in cycles if int(c) > 0})
    if not nums:
        return ''
    parts = []
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        if j - i >= 2:
            parts.append(f'{nums[i]}-{nums[j]}')
        else:
            parts.extend(str(n) for n in nums[i:j + 1])
        i = j + 1
    return ','.join(parts)


def load_exclude(csv_path):
    """读取排除参数文件。

    Returns:
        list[dict]: 每行 {meas_time, meas_type, rmr_type, chamber_id,
                         meas_batch, exclude_cycles(原文), exclude(list[int])}
    """
    rows = []
    with open(csv_path, 'r', encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            if not str(row.get('meas_time', '')).strip():
                continue
            raw = str(row.get(EXCLUDE_COLUMN) or '').strip()
            rows.append({
                'meas_time': str(row.get('meas_time', '')).strip(),
                'meas_type': str(row.get('meas_type', '')).strip(),
                'rmr_type': str(row.get('rmr_type', '')).strip(),
                'chamber_id': str(row.get('chamber_ID', '')).strip(),
                'meas_batch': str(row.get('meas_batch', '')).strip(),
                'exclude_cycles': raw,
                'exclude': parse_cycle_spec(raw),
            })
    return rows


def build_exclude_rows(params_list):
    """把循环参数（chamber_ID 可能是 "2,3,4"）展开成一行一个通道的排除行。"""
    rows = []
    seen = set()
    for p in params_list:
        cid_str = str(p.get('chamber_id', '')).replace('"', '')
        for token in cid_str.split(','):
            token = token.strip()
            if not token:
                continue
            try:
                cid = int(float(token))
            except ValueError:
                continue
            key = (str(p.get('meas_time', '')), cid)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                'meas_time': p.get('meas_time', ''),
                'meas_type': p.get('meas_type', ''),
                'rmr_type': p.get('rmr_type', ''),
                'chamber_ID': cid,
                'meas_batch': p.get('meas_batch', ''),
                EXCLUDE_COLUMN: '',
            })
    return rows


def write_exclude_csv(csv_path, rows):
    """写出排除参数文件（列顺序固定为 EXCLUDE_FIELDS）。

    行字典的通道号键接受 ``chamber_ID`` 或 ``chamber_id``，其余键缺省写空串，
    这样 ``load_exclude()`` 读出来的行可以直接写回，不会把通道号写成空。
    """
    norm = []
    for r in rows:
        cid = r.get('chamber_ID')
        if cid is None or str(cid).strip() == '':
            cid = r.get('chamber_id', '')
        norm.append({
            'meas_time': r.get('meas_time', ''),
            'meas_type': r.get('meas_type', ''),
            'rmr_type': r.get('rmr_type', ''),
            'chamber_ID': cid,
            'meas_batch': r.get('meas_batch', ''),
            EXCLUDE_COLUMN: r.get(EXCLUDE_COLUMN, ''),
        })
    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=EXCLUDE_FIELDS)
        writer.writeheader()
        writer.writerows(norm)


def generate_exclude_csv(params_csv, exclude_csv):
    """按循环参数文件生成空白的排除参数文件，返回生成的行数。"""
    rows = build_exclude_rows(load_params(params_csv))
    write_exclude_csv(exclude_csv, rows)
    return len(rows)


def _to_datetime(value):
    """把时间列的值统一成 datetime 对象。

    多数设备导出的 xlsx 里 Date 是真正的日期时间单元格，但有些经过二次处理
    （合并、裁剪、重新导出）的文件会把它变成文本，如 "2026/05/07 09:00:00"。
    两种都接受，避免后续计算时间差时崩溃。
    """
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ('%Y/%m/%d %H:%M:%S.%f', '%Y/%m/%d %H:%M:%S',
                '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S',
                '%Y/%m/%d %H:%M', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f'无法解析时间列内容: {value!r}')


def load_xlsx(filepath):
    """读取 xlsx 文件第 6 个 sheet 的溶氧/温度/气压数据。

    Returns:
        dict: {timestamps, time_seconds, oxygen, temperature, pressure, nrows}
    """
    wb = load_workbook(filepath, read_only=True, data_only=True)
    try:
        ws = wb[wb.sheetnames[5]]
        # 有些设备/工具导出或处理过的 xlsx，工作表里的 <dimension> 元数据是错的
        # （实际有上万行，却声明成 A1）。read_only 模式依赖 dimension 决定读取范围，
        # 于是只读到第一行，被误判成“sheet 为空”。清掉 dimension 让它扫描整张表。
        ws.reset_dimensions()

        timestamps = []
        oxygen = []
        temperature = []
        pressure = []

        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == 0:
                continue
            if row[1] is None or row[6] is None:
                continue
            timestamps.append(_to_datetime(row[1]))
            oxygen.append(float(row[6]))
            temperature.append(float(row[8]))
            pressure.append(float(row[10]))

        if not timestamps:
            raise ValueError('xlsx contains no data rows (sheet 6 is empty)')

        base = timestamps[0]
        time_seconds = [(t - base).total_seconds() for t in timestamps]

        return {
            'timestamps': timestamps,
            'time_seconds': time_seconds,
            'oxygen': oxygen,
            'temperature': temperature,
            'pressure': pressure,
            'nrows': len(timestamps),
        }
    finally:
        wb.close()


def load_params(csv_path):
    """读取 meas_params.csv。

    Args:
        csv_path: CSV 文件路径

    Returns:
        list[dict]: 每行一个参数字典
    """
    params_list = []
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get('meas_time', '').strip():
                continue
            p = {
                'meas_time': row['meas_time'].strip(),
                'meas_type': row.get('meas_type', '').strip(),
                'rmr_type': row.get('rmr_type', '').strip(),
                'chamber_id': row.get('chamber_ID', '').strip(),
                'meas_batch': row.get('meas_batch', '').strip(),
                'temperature': row.get('temperature', '').strip(),
                'cycles': int(row.get('cycles', 0) or 0),
                'initial': int(row.get('initial', 0) or 0),
                'cycle_length': int(row.get('cycle_length', 0) or 0),
                'cycle_start': int(row.get('cycle_start', 0) or 0),
                'cycle_time': int(row.get('cycle_time', 0) or 0),
                'flush_time': int(row.get('flush_time', 0) or 0),
                'all_time': int(row.get('all_time', 0) or 0),
            }
            params_list.append(p)
    return params_list


def compute_cycle_boundaries(time_seconds, params):
    """根据循环参数计算每个循环在 time_seconds 数组中的索引边界。

    cycle_time 是斜率窗口终点 (距周期起点的秒数)，不是窗口时长。

    Returns:
        list[dict]: cycle_num, start_idx, end_idx,
            slope_start_idx, slope_end_idx,
            start_seconds, cycle_length,
            slope_start_seconds, slope_end_seconds
    """
    cycles = params['cycles']
    cycle_length = params['cycle_length']
    slope_offset = params['cycle_start']   # 斜率窗口起点 (距周期起点的秒数)
    slope_end = params['cycle_time']       # 斜率窗口终点 (距周期起点的秒数, 非时长!)

    # 参数不完整时直接返回空，避免周期为 0 时算出无数个重合区间
    if not cycle_length or cycle_length <= 0:
        return []
    if not cycles or cycles <= 0:
        return []

    n = len(time_seconds)
    bounds = []

    for i in range(cycles):
        cyc_start_s = i * cycle_length
        cyc_end_s = (i + 1) * cycle_length
        slp_start_s = cyc_start_s + slope_offset
        slp_end_s = cyc_start_s + slope_end     # ← 关键: cycle_time 是终点, 非时长

        a = bisect.bisect_left(time_seconds, cyc_start_s)
        b = bisect.bisect_left(time_seconds, cyc_end_s)
        sa = bisect.bisect_left(time_seconds, slp_start_s)
        sb = bisect.bisect_left(time_seconds, slp_end_s)

        if a >= n:
            break

        bounds.append({
            'cycle_num': i + 1,
            'start_idx': a,
            'end_idx': min(b, n),
            'slope_start_idx': sa,
            'slope_end_idx': min(sb, n),
            'start_seconds': cyc_start_s,
            'cycle_length': cycle_length,
            'slope_start_seconds': slp_start_s,
            'slope_end_seconds': slp_end_s,
        })

    return bounds
