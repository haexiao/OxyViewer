#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""calc_rmr.py — OxyViewer Python 计算引擎（resprpy 版）

与 calc_rmr.R 接口完全一致，可由 viewer.py 通过 QProcess 调用：

    用法:
        python calc_rmr.py <data_folder> <params_csv> <meas_time> <channels>

    参数:
        data_folder : 数据文件夹 (如 "X:/Rtools/20260422/20260422 20")
        params_csv  : 参数文件路径 (如 ".../raw/meas_params.csv")
        meas_time   : 实验日期 (如 20260422)
        channels    : 通道号, 逗号分隔 "1,2,3" 或区间 "2-9"

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

# ── 渗透系数（与 calc_rmr.R 完全一致）──────────────────────────────
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


def msg(*args):
    """对应 R 的 message()：输出到 stderr。"""
    print(*args, file=sys.stderr, flush=True)


def parse_channels(ch_str):
    """解析通道串: "1,3,5" 或 "2-9"（与 R 的 grepl("-") 分支一致）。"""
    if "-" in ch_str:
        a, b = ch_str.split("-")[:2]
        return list(range(int(a), int(b) + 1))
    return [int(v) for v in ch_str.split(",") if v.strip() != ""]


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


def run_channel(ch, params, data_folder):
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
        return

    cycles = to_int(mode_row.get("cycles"))
    cycle_length = to_float(mode_row.get("cycle_length"))
    initial = to_float(mode_row.get("initial"))
    cycle_start = to_float(mode_row.get("cycle_start"))
    cycle_time = (to_float(mode_row.get("cycle_time"))
                  - to_float(mode_row.get("cycle_start")) - 5)

    if cycles is None or cycles == 0:
        msg(f"通道 {ch} 参数为空，跳过")
        return

    # ── 循环时间矩阵 ──
    starts = cycle_start + initial + np.arange(cycles) * cycle_length
    ends = starts + cycle_time

    infile = os.path.join(data_folder, f"{ch}.xlsx")
    outfile = f"rmr{ch}.csv"
    if not os.path.exists(infile):
        msg(f"文件不存在: {infile}，跳过")
        return

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
    k_value = K_VALUES[ch]
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

    write_r_csv(outfile, SUMMARY_COLS, col_values)
    msg(f"已保存: {outfile}")


def main(argv):
    global MEAS_TIME
    if len(argv) < 5:
        msg("Usage: python calc_rmr.py <data_folder> <params_csv> "
            "<meas_time> <channels>")
        return 2

    data_folder, params_file, meas_time_str, ch_str = argv[1:5]
    MEAS_TIME = int(meas_time_str)

    if not os.path.exists(params_file):
        msg(f"循环参数文件未找到: {params_file}")
        return 1

    params = read_params(params_file)
    for ch in parse_channels(ch_str):
        run_channel(ch, params, data_folder)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
