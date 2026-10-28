#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""calc_rmr.py — OxyViewer Python 计算引擎（resprpy 版）

与 calc_rmr.R 接口完全一致，可由 viewer.py 通过 QProcess 调用：

    用法:
        python calc_rmr.py <data_folder> <params_csv> <meas_time> <channels> [chamber_csv] [exclude_csv]

    参数:
        data_folder : 数据文件夹 (如 "X:/Rtools/20260422/20260422 20")
        params_csv  : 参数文件路径 (如 ".../raw/meas_params.csv")
        meas_time   : 实验日期 (如 20260422)
        channels    : 通道号, 逗号分隔 "1,2,3" 或区间 "2-9"
        chamber_csv : (可选) 渗透系数表 chamber.csv
        exclude_csv : (可选) 排除参数文件 meas_exclude.csv，
                      exclude_cycles 形如 "3,6,9-14"，这些循环不参与计算

    输出:
        rmr{N}.csv 写到当前工作目录（与 R 版一致，由调用方设定 cwd）

对照 calc_rmr.R 的实现要点:
    - read_excel(sheet=6)  -> openpyxl 第 6 个工作表, 取第 2、7 列
    - lubridate 解析 + format("%y/%m/%d %H:%M:%S") -> strftime 同样格式
    - respR::format_time   -> resprpy.format_time (秒差 + 1 起点)
    - 渗透系数校正         -> oxy = O2 - k * (max_oxy - O2)
    - inspect + calc_rate  -> resprpy 同函数, 多区间 by="time"
    - write.table          -> 复刻 R 的输出格式(列名带引号 / 15 位有效数字 / CRLF)
"""
import csv
import os
import sys

import numpy as np
import openpyxl

import resprpy as rp

# ── 渗透系数 ──
# 内置默认值：仅在未提供 chamber.csv 时使用（保证脚本可独立运行、
# 也不破坏「不选文件」的旧用法）。必须与 calc_rmr.R 的 k 矩阵、
# templates/chamber.csv 保持一致，改完跑 packaging/check_defaults.py 校验。
K_VALUES = {
    1: 0.0006223,
    2: 0.000317161,
    3: 0.001055724,
    4: 0.000671915,
    5: 0.000537423,
    6: 0.001256691,
    7: 0.000743536,
    8: 0.000743536,
    9: 0.000743536,
}

# calc_rate summary 的导出列（对应 R 的 summary[, 2:13]，即去掉 rep 列）
SUMMARY_COLS = ["rank", "intercept_b0", "slope_b1", "rsq", "row", "endrow",
                "time", "endtime", "oxy", "endoxy", "rate.2pt", "rate"]

# 被排除循环要清空的「代谢结果」列（rank/row/time/oxy 等时间相关列一律保留）
METAB_COLS = ["intercept_b0", "slope_b1", "rsq", "rate.2pt", "rate"]


def load_k_values(path):
    """读取渗透系数表（chamber.csv: chamber_ID,k_values）。

    返回 {channel: k} 字典；未提供、文件不存在或没有可用行时返回 None，
    调用方据此回退到内置默认值 K_VALUES。
    """
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        ks = {}
        for r in rows:
            cid = str(r.get("chamber_ID") or r.get("channel") or "").strip()
            kv = str(r.get("k_values") or r.get("k_value") or "").strip()
            if cid and kv:
                ks[int(float(cid))] = float(kv)
        return ks or None
    except Exception as e:                       # noqa: BLE001
        msg(f"  警告: 渗透系数文件读取失败 ({e})，改用内置默认值")
        return None


def msg(*args):
    """对应 R 的 message()：输出到 stderr。"""
    print(*args, file=sys.stderr, flush=True)


def parse_channels(ch_str):
    """解析通道串，支持混写: "1,3,5" / "2-9" / "1,3-5,8"。

    早先的实现只要含 "-" 就把整串当区间（"1,3-5" → int("1,3") 崩），
    这里逐段解析，非法片段忽略（与 R 版一致）。
    """
    out = []
    for part in str(ch_str).replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                lo, hi = int(a.strip()), int(b.strip())
            except ValueError:
                continue
            if lo > hi:
                lo, hi = hi, lo
            out.extend(range(lo, hi + 1))
        else:
            try:
                out.append(int(part))
            except ValueError:
                continue
    return sorted(set(c for c in out if c > 0))


_RANGE_SEPS = "-~—–－"


def parse_cycle_spec(text):
    """解析排除循环表达式 "3,6,9-14" -> [3, 6, 9, 10, 11, 12, 13, 14]。

    与 calc_rmr.R 的 parse_cycle_spec() 行为一致:
    支持中文逗号/顿号、空格，区间连接符可用 - ~ — – －。
    """
    if text is None:
        return []
    s = str(text)
    for ch in ('，', '、', '；', ';', ' ', '\t'):
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


def load_exclude_map(path):
    """读取排除参数文件，返回 {(meas_time, chamber): set(排除循环号)}。"""
    m = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if "exclude_cycles" not in (reader.fieldnames or []):
            raise ValueError(f"排除参数文件缺少 exclude_cycles 列: {path}")
        for r in reader:
            mt = str(r.get("meas_time") or "").strip()
            if not mt:
                continue
            cycles = set(parse_cycle_spec(r.get("exclude_cycles")))
            for token in str(r.get("chamber_ID") or "").replace('"', "").split(","):
                token = token.strip()
                if not token:
                    continue
                try:
                    cid = int(float(token))
                except ValueError:
                    continue
                m.setdefault((mt, cid), set()).update(cycles)
    return m


def read_params(path):
    """读取循环参数 CSV（UTF-8-BOM，与 R 的 fileEncoding="UTF-8-BOM" 一致）。"""
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def chamber_matches(params_row, ch):
    """chamber_ID 匹配：字段形如 "2,3,4" 或 1，需去引号后按逗号拆分。"""
    cid = str(params_row["chamber_ID"]).replace('"', "")
    ids = [int(v) for v in cid.split(",") if v.strip() != ""]
    return ch in ids


def to_int(v):
    """R 的 as.integer() 语义：空/NA -> None。"""
    if v is None or str(v).strip() == "" or str(v).upper() == "NA":
        return None
    return int(float(v))


def to_float(v):
    if v is None or str(v).strip() == "" or str(v).upper() == "NA":
        return np.nan
    return float(v)


def read_xlsx_channel(path):
    """读第 6 个工作表的第 2、7 列（Date, Oxygen），跳过表头行。

    对应 R: df <- read_excel(path, sheet = 6); data <- df[, c(2, 7)]
    """
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb.worksheets[5]                      # 第 6 个 sheet（1-based 6）
    # 与 data_loader.load_xlsx 同理：部分文件的 <dimension> 元数据是错的
    # （实际有上万行却声明成 A1），read_only 模式据此只读到第一行。
    # 清掉 dimension 让 openpyxl 按实际内容扫描整张表。
    ws.reset_dimensions()
    dates, oxy = [], []
    for row in ws.iter_rows(min_row=2, values_only=True):  # 跳过表头
        dates.append(row[1])                   # 第 2 列
        oxy.append(row[6])                     # 第 7 列
    wb.close()
    return dates, np.array([np.nan if v is None else float(v) for v in oxy])


def fmt_dates(dates):
    """datetime -> "%y/%m/%d %H:%M:%S"。

    对应 R:
        dtm <- parse_date_time(unlist(data$Date), "ymdHMS")
        data$Date <- format(dtm, "%y/%m/%d %H:%M:%S")
    注意 R 的 format() 丢亚秒，这里同样只保留到秒。
    """
    out = []
    for d in dates:
        if d is None:
            out.append("")
        elif hasattr(d, "strftime"):
            out.append(d.strftime("%y/%m/%d %H:%M:%S"))
        else:                                   # 已是字符串的兜底
            out.append(str(d).strip())
    return out


def r_num(v):
    """复刻 R write.table 的数字格式：15 位有效数字，NA -> "NA"。"""
    if v is None:
        return "NA"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if np.isnan(f):
        return "NA"
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return f"{f:.15g}"


def write_r_csv(path, cols, col_values):
    """复刻 R write.table(s, file, sep=",", row.names=FALSE, col.names=TRUE)。

    - 列名带双引号
    - 数字 15 位有效数字（as.character 行为）
    - 换行 CRLF
    """
    n = len(col_values[cols[0]])
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(",".join(f'"{c}"' for c in cols) + "\r\n")
        for i in range(n):
            row = []
            for c in cols:
                v = np.asarray(col_values[c])[i]
                row.append(r_num(v))
            f.write(",".join(row) + "\r\n")


def run_channel(ch, params, data_folder, k_map=None, exclude_map=None):
    """处理单个通道：匹配参数 -> 读数据 -> 校正 -> 计算 -> 导出。"""
    # ── 匹配 meas_time + chamber_ID 的参数行 ──
    mode_row = None
    for p in params:
        if to_int(p.get("meas_time")) != MEAS_TIME:
            continue
        if chamber_matches(p, ch):
            mode_row = p
            break
    if mode_row is None:
        msg(f"通道 {ch} 无匹配参数，跳过")
        return False

    cycles = to_int(mode_row.get("cycles"))
    cycle_length = to_float(mode_row.get("cycle_length"))
    initial = to_float(mode_row.get("initial"))
    cycle_start = to_float(mode_row.get("cycle_start"))
    cycle_time = (to_float(mode_row.get("cycle_time"))
                  - to_float(mode_row.get("cycle_start")) - 5)

    if cycles is None or cycles == 0:
        msg(f"通道 {ch} 参数为空，跳过")
        return False
    if cycle_time is None or np.isnan(cycle_time) or cycle_time <= 0:
        msg(f"通道 {ch} 的斜率窗口时长无效（{cycle_time}），跳过")
        return False
    if cycle_length is None or np.isnan(cycle_length) or cycle_length <= 0:
        msg(f"通道 {ch} 的周期无效（{cycle_length}），跳过")
        return False

    # ── 循环时间矩阵 ──
    starts = cycle_start + initial + np.arange(cycles) * cycle_length
    ends = starts + cycle_time

    # ── 排除循环 ──
    # 保留全部循环（rank / time / oxy 等时间相关列不变），只把被排除循环的
    # 代谢结果列（斜率、截距、R²、两点法速率、速率）置为空（NA）。
    excluded = sorted((exclude_map or {}).get((str(MEAS_TIME), ch), set()))
    if excluded:
        msg(f"  排除循环: {','.join(str(v) for v in excluded)}"
            f"（{len(excluded)} 个，保留行、代谢数据置空）")

    infile = os.path.join(data_folder, f"{ch}.xlsx")
    outfile = f"rmr{ch}.csv"
    if not os.path.exists(infile):
        msg(f"文件不存在: {infile}，跳过")
        return False

    # ── 读数据 ──
    dates_raw, o2 = read_xlsx_channel(infile)
    date_strs = fmt_dates(dates_raw)

    # ── 时间列（respR::format_time, 秒差 + 1）──
    data2 = np.column_stack([date_strs, o2]).astype(object)
    _, tsec = rp.format_time(data2, time=1, format="ymdHMS")

    # ── 渗透系数校正 ──
    # R: max_oxy <- mean(df$Oxygen[order(df$Oxygen, decreasing=TRUE)[1:min(30,nrow)]], na.rm=TRUE)
    valid = o2[~np.isnan(o2)]
    top = np.sort(valid)[::-1][:min(30, valid.size)]
    max_oxy = float(np.mean(top))
    # 通道号不在内置表里（如 >9）时退回 0，别让 KeyError 中断整个批量计算
    k_value = (k_map or {}).get(ch, K_VALUES.get(ch, 0.0))
    oxy = o2 - k_value * (max_oxy - o2)

    # data 4 列: Date, Oxygen, time, oxy  ->  inspect(time=3, oxygen=4)
    data4 = np.column_stack([date_strs, o2, tsec, oxy]).astype(object)

    # ── 计算耗氧率 ──
    dataint = rp.inspect(data4, time=3, oxygen=4, plot=False)
    rates = rp.calc_rate(dataint, from_=starts.tolist(), to=ends.tolist(),
                         by="time", plot=False)

    # ── 导出 summary[, 2:13]，rate 转 mgO2/L/h ──
    s = rates.summary
    col_values = {c: np.asarray(s[c]) for c in SUMMARY_COLS}
    col_values["rate"] = col_values["rate"] * 3600

    # 被排除循环：行保留，只把代谢结果列清空（对应 R 写出的 NA）
    if excluded:
        n_rows = len(col_values["rank"])
        idx = [c - 1 for c in excluded if 1 <= c <= n_rows]
        for col in METAB_COLS:
            arr = np.asarray(col_values[col], dtype=float).copy()
            if idx:
                arr[idx] = np.nan
            col_values[col] = arr

    write_r_csv(outfile, SUMMARY_COLS, col_values)
    msg(f"已保存: {outfile}")
    return True


def main(argv):
    global MEAS_TIME
    if len(argv) < 5:
        msg("Usage: python calc_rmr.py <data_folder> <params_csv> "
            "<meas_time> <channels> [chamber_csv] [exclude_csv]")
        return 2

    data_folder, params_file, meas_time_str, ch_str = argv[1:5]
    k_file = argv[5] if len(argv) > 5 else ""        # 可选：渗透系数表
    exclude_file = argv[6] if len(argv) > 6 else ""  # 可选：排除参数文件
    MEAS_TIME = int(meas_time_str)

    if not os.path.exists(params_file):
        msg(f"循环参数文件未找到: {params_file}")
        return 1

    k_map = load_k_values(k_file)
    if k_map:
        msg(f"  渗透系数: 读取 {os.path.basename(k_file)} ({len(k_map)} 个通道)")
    else:
        msg("  渗透系数: 使用内置默认值")

    exclude_map = None
    if exclude_file:
        if not os.path.exists(exclude_file):
            msg(f"排除参数文件未找到: {exclude_file}")
            return 1
        try:
            exclude_map = load_exclude_map(exclude_file)
        except Exception as e:                    # noqa: BLE001
            msg(f"排除参数文件读取失败: {e}")
            return 1
        msg(f"  排除参数: {os.path.basename(exclude_file)}")
    else:
        msg("  排除参数: 未提供（不排除任何循环）")

    params = read_params(params_file)
    n_ok = n_fail = 0
    failed = []
    for ch in parse_channels(ch_str):
        # 单个通道出错（数据不足、时间段超出数据范围等）只跳过该通道，
        # 不要中断整批计算
        try:
            if run_channel(ch, params, data_folder, k_map, exclude_map):
                n_ok += 1
        except Exception as e:                    # noqa: BLE001
            n_fail += 1
            failed.append(ch)
            msg(f"通道 {ch} 计算失败: {e}（已跳过）")
    summary = f"完成: 成功 {n_ok} 个通道"
    if n_fail:
        summary += f"，失败 {n_fail} 个（{','.join(str(c) for c in failed)}）"
    msg(summary)
    return 0 if (n_ok > 0 or n_fail == 0) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
