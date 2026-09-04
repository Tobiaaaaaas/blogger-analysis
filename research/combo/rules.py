# -*- coding: utf-8 -*-
"""research/combo/rules.py — 共识规则语义：阈值 q（看多占比，严格大于）× 最低表态数 k。

纯函数、无 I/O。所有阈值比较用 fractions.Fraction 精确计算（绝不用 float 表达 2/3 等），
否则在整数票边界（如 expressed=10 时 2/3×10=6.67 vs 0.7×10=7）会静默错格。

基线格（= 现行口径）：q=2/3, k=3，对称看空。k=1 在语义上退化为"跟单单个最新表态"。
"""
from fractions import Fraction

from .. import config

# 看多占比须严格大于 q（对称：看空同 q 触发）。基线 2/3 在其中。
RATIO_QS = (Fraction(1, 2), Fraction(3, 5), Fraction(2, 3),
            Fraction(7, 10), Fraction(3, 4), Fraction(4, 5), Fraction(9, 10))
# 最低表态者数（多+空）。swing 板触发日实测表态恒 ≥7，故本样本 k 可能惰性（运行时按行为去重判定）。
KS = (1, 2, 3, 4, 5, 7)

BASELINE_Q = Fraction(2, 3)
BASELINE_K = 3

SWING = sorted(config.PANELS["swing"])          # swing 板 21 人（每日一票的投票宇宙）
MAX_EXPRESSED = len(SWING)                      # 21


def min_bull(q, e):
    """严格 bull > q×e 的最小整数票数（q×e 恰好为整数时需再多 1 票）。"""
    return int(q * e) + 1


def grid_cells():
    """42 个原始格（含全等价重复），供全披露 csv。"""
    return [(q, k) for q in RATIO_QS for k in KS]


def canonical_key(q, k):
    """理论判等键：给定 k 下各表态人数的必需票数序列。"""
    return (k, tuple(min_bull(q, e) for e in range(k, MAX_EXPRESSED + 1)))


def n_theoretical_distinct():
    return len({canonical_key(q, k) for q, k in grid_cells()})


def n_cells_total():
    return len(RATIO_QS) * len(KS)


def decide(q, k):
    """返回 snap -> "L"/"S"/"F" 的判定闭包（snap 需带 expressed/bull/bear）。"""
    def f(snap):
        e, b, be = snap["expressed"], snap["bull"], snap["bear"]
        if e >= k and b > q * e:
            return "L"
        if e >= k and be > q * e:
            return "S"
        return "F"
    return f


def is_baseline(q, k):
    return q == BASELINE_Q and k == BASELINE_K


def q_str(q):
    return f"{q.numerator}/{q.denominator}"
