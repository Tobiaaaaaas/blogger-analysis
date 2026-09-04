# -*- coding: utf-8 -*-
"""research/combo/engine.py — 每 (q,k) 规则的每日一票计分行构建 + 聚合 + 半期拆分。

行只在触发日产出（d=+1/−1），故行数 N = 该格的信号日数 = 计分数。
聚合复用 quality/engine.summarize_metrics —— 与综合基线行同一套 acc/avg/vol/sharpe 函数，
基线格 (2/3,3) 应与 quality/engine.signal_rows 的聚合零漂移一致（run_sweep --check 断言）。
"""
from ..quality.engine import summarize_metrics   # 只读行聚合器（acc/avg/vol/sharpe + 多空分腿）

QUAL_N = 20              # 有资格进入"达标"排序的最低计分信号日（组合网格用 20，比成员榜更严）
SHORTLIST_MIN = 30       # "N≥30 均分最优"档（短名单挑选用）


def rows_for_rule(ctxs, q, k):
    """ctxs 上对每个干净日按 (q,k) 定方向 → 计分行列表。

    行键：date | clean_idx | d | expressed | bull | bear | score。
    d：e≥k 且 bull>q×e → +1；对称看空（bear>q×e）→ −1；都不达 → 当日无信号。
    """
    rows = []
    for c in ctxs:
        e, b, be = c.expressed, c.bull, c.bear
        d = None
        if e >= k and b > q * e:
            d = 1
        elif e >= k and be > q * e:
            d = -1
        if d is None:
            continue
        rows.append({"date": c.date.isoformat(), "clean_idx": c.clean_idx,
                     "expressed": e, "bull": b, "bear": be,
                     "d": d, "score": round(d * c.raw_ret * 100.0, 2)})
    return rows


def behavior_key(rows):
    """行为去重指纹：逐 (date, d) 向量（跳过无信号日）。两规则等价 ⟺ 逐信号日方向全同。"""
    return tuple(sorted((r["date"], r["d"]) for r in rows))


def _half_avg(rows, half_idx, which):
    """which='first' 用 clean_idx<half_idx 的计分行均分；'second' 反之。空 → None。"""
    sub = [r["score"] for r in rows if (r["clean_idx"] < half_idx) == (which == "first")]
    return round(sum(sub) / len(sub), 4) if sub else None


def rule_metrics(rows, half_idx):
    """计分行 → 聚合指标 + n_sig/上下半期均分（半界固定干净日序，规则无关）。"""
    m = summarize_metrics(rows)          # n/hit/denom/acc/avg/vol/sharpe + bull_n/avg bear_n/avg
    m["n_sig"] = len(rows)
    m["half1_avg"] = _half_avg(rows, half_idx, "first")
    m["half2_avg"] = _half_avg(rows, half_idx, "second")
    return m
