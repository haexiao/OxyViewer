"""校验三处默认渗透系数是否一致。

默认 k 值在三处出现：
    1. calc_rmr.py 的 K_VALUES          （Python 引擎的回退默认值）
    2. calc_rmr.R  的 k 矩阵            （R 引擎的回退默认值）
    3. templates/chamber.csv            （给用户的模板）

三者必须一致，否则「不选文件」和「选模板文件」会得到不同结果。
改动任一处的默认值后，跑一下本脚本即可确认。

用法:  python packaging/check_defaults.py
"""
import csv
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def from_python_src():
    src = open(os.path.join(ROOT, 'calc_rmr.py'), encoding='utf-8').read()
    m = re.search(r'K_VALUES = \{(.*?)\n\}', src, re.S)
    if not m:
        raise SystemExit('未能在 calc_rmr.py 中定位 K_VALUES')
    return {int(k): float(v) for k, v in
            re.findall(r'(\d+)\s*:\s*([0-9.eE+-]+)', m.group(1))}


def from_r_src():
    src = open(os.path.join(ROOT, 'calc_rmr.R'), encoding='utf-8').read()
    m = re.search(r'k\[, "k_value"\] <- c\((.*?)\)', src, re.S)
    if not m:
        raise SystemExit('未能在 calc_rmr.R 中定位 k 矩阵')
    vals = [float(x.strip()) for x in m.group(1).replace('\n', ' ').split(',')
            if x.strip()]
    return {i + 1: v for i, v in enumerate(vals)}


def from_template():
    path = os.path.join(ROOT, 'templates', 'chamber.csv')
    with open(path, encoding='utf-8-sig', newline='') as f:
        return {int(r['chamber_ID']): float(r['k_values'])
                for r in csv.DictReader(f) if r.get('chamber_ID')}


def main():
    py, r, tpl = from_python_src(), from_r_src(), from_template()
    ok = True
    for name, data in (('calc_rmr.py K_VALUES', py),
                       ('calc_rmr.R  k 矩阵', r),
                       ('templates/chamber.csv', tpl)):
        print(f'  {name:26s} {len(data)} 个通道  样例 k[1]={data.get(1)}')

    if py != r:
        ok = False
        print('  [错误] calc_rmr.py 与 calc_rmr.R 的默认值不一致')
    if py != tpl:
        ok = False
        print('  [错误] 内置默认值与 templates/chamber.csv 不一致')

    print('  一致 ✓' if ok else '  不一致 ✗')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
