# -*- coding: utf-8 -*-
"""research/quality/_run_direction.py — 博主评价引擎的唯一同源复用点。

惰性 import scripts/eval/run_direction（模块级仅读行情缓存，无副作用；写盘全在
generate()/__main__ 内）。统一在此转发本单元需要的函数，避免第二份实现漂移。
不 import comparison_all —— 它模块级直接 open().write 榜单文件，只可作规范参考。

聚合口径（与 run_direction §6 / analyze-blogger SKILL 逐条对齐）：
  命中 = score>0；score=0 计"平"，不计入分子分母；
  平均分 = mean(score)；波动率 = 样本标准差（n-1）；夏普 = 均分/波动率（vol=0 或 n<2 → None）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ENGINE = None


def _load():
    global _ENGINE
    if _ENGINE is None:
        eval_dir = os.path.join(ROOT, "scripts", "eval")
        for _p in (ROOT, eval_dir):
            if _p not in sys.path:
                sys.path.insert(0, _p)
        import run_direction  # noqa: PLC0415
        _ENGINE = run_direction
    return _ENGINE


def calc(sig):
    return _load().calc(sig)


def bucket_of(r):
    return _load().bucket_of(r)


def acc_of(rs):
    return _load().acc_of(rs)


def avg_of(rs):
    return _load().avg_of(rs)


def vol_of(rs):
    return _load().vol_of(rs)


def sharpe_of(rs):
    return _load().sharpe_of(rs)
