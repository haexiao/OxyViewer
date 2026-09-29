"""计算引擎判定 — R (respR) 或 Python (resprpy)。

main.py 与 viewer.py 共用同一份判定逻辑，避免两处各维护一份别名列表
（之前两处各写了一个元组，改一处容易漏掉另一处）。

引擎由环境变量 OXY_ENGINE 决定：
    - 未设置时，使用打包版 runtime hook 写入的值（见 packaging/engine_hook_*.py），
      源码运行时默认 'R'
    - 取值不区分大小写，python / py / p / resprpy 都视为 Python 引擎
"""
import os

# 视为「Python 引擎」的取值（小写）
PY_ENGINE_ALIASES = ('python', 'py', 'p', 'resprpy')

# 默认引擎（环境变量未设置且非打包版时）
DEFAULT_ENGINE = 'R'


def current_engine():
    """返回当前 OXY_ENGINE 原始取值。"""
    return os.environ.get('OXY_ENGINE', DEFAULT_ENGINE)


def is_python_engine(value=None):
    """是否为 Python 引擎。value 省略时读取 OXY_ENGINE。"""
    eng = current_engine() if value is None else value
    return str(eng).strip().lower() in PY_ENGINE_ALIASES


def engine_label(value=None):
    """展示用的引擎名。"""
    return 'Python (resprpy)' if is_python_engine(value) else 'R'


def engine_tag(value=None):
    """控制台输出版。"""
    return 'PY' if is_python_engine(value) else 'R'
