"""循环分析与线性回归"""
import numpy as np


def compute_slope(time_seconds, oxygen, start_idx, end_idx):
    """对指定区间进行线性回归，计算耗氧率。

    Args:
        time_seconds: list[float] — 完整时间序列 (秒)
        oxygen: list[float] — 完整溶氧序列
        start_idx: int — 区间起始索引 (含)
        end_idx: int — 区间结束索引 (不含)

    Returns:
        dict or None:
            slope:        mgO₂/L·s (负数表示耗氧)
            intercept:    mgO₂/L
            r_squared:    R²
            n_points:     数据点数
            hourly_rate:  mgO₂/L·h
    """
    if end_idx - start_idx < 3:
        return None

    t = np.array(time_seconds[start_idx:end_idx], dtype=float)
    o2 = np.array(oxygen[start_idx:end_idx], dtype=float)

    # O2 = b0 + b1 * t
    A = np.column_stack([np.ones_like(t), t])
    result = np.linalg.lstsq(A, o2, rcond=None)
    b0, b1 = result[0]

    o2_pred = b0 + b1 * t
    ss_res = np.sum((o2 - o2_pred) ** 2)
    ss_tot = np.sum((o2 - np.mean(o2)) ** 2)
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0

    return {
        'slope': b1,
        'intercept': b0,
        'r_squared': r_squared,
        'n_points': end_idx - start_idx,
        'hourly_rate': b1 * 3600,
    }
