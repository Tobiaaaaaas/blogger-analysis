# -*- coding: utf-8 -*-
"""research/combo/sweep.py — 全网格扫描 → 行为去重 → 有效规则指标 + 短名单挑选。

42 原始格 (q×k) 各自算 rows/指标；两格行为相同（逐信号日方向向量全同）归并为一"有效族"。
本样本若 k 惰性则 42 格折叠为 7 族（每阈值 q 一族）；若 k 实际起约束则自然分出更多族。
短名单 = 基线 + N≥30 均分最优 + N≥20 均分最优 + N≥20 夏普最优（身份去重），喂给 run_confirm 跑 PnL。
"""
from fractions import Fraction

from . import rules
from . import daygrid
from .engine import rows_for_rule, behavior_key, rule_metrics
from .engine import QUAL_N, SHORTLIST_MIN


def _alias_txt(params):
    """一族在原始 42 格里共享同一行为的全部 (q,k) 的紧凑表述。"""
    by_q = {}
    for q, k in params:
        by_q.setdefault(q, []).append(k)
    parts = []
    for q in sorted(by_q, key=float):
        ks = sorted(by_q[q])
        parts.append(f"q={rules.q_str(q)} · k∈{{{','.join(map(str, ks))}}}")
    return "；".join(parts)


def run_sweep(index=None):
    ctxs, n_clean = daygrid.build_contexts(index)
    half_idx = n_clean // 2
    fam_map = {}                                  # behavior_key → {rows, params:[]}
    for q, k in rules.grid_cells():
        rows = rows_for_rule(ctxs, q, k)
        bk = behavior_key(rows)
        fam = fam_map.get(bk)
        if fam is None:
            fam = fam_map[bk] = {"rows": rows, "params": []}
        fam["params"].append((q, k))

    families = []
    for bk, fam in sorted(fam_map.items(),
                          key=lambda it: (min(float(q) for q, _ in it[1]["params"]),
                                          min(k for _, k in it[1]["params"]))):
        params = sorted(fam["params"], key=lambda z: (float(z[0]), z[1]))
        qs = sorted({q for q, _ in params}, key=float)
        q_repr = qs[0]
        ks = sorted(k for q, k in params if q == q_repr)
        k_repr = 3 if 3 in ks else ks[0]          # 优先 k=3 对齐基线直觉，否则取该族最小 k
        qk = (q_repr, k_repr)                     # 恒为真实格（k_repr∈ks ⇒ (q_repr,k_repr)∈params）
        rows = rows_for_rule(ctxs, q_repr, k_repr)
        m = rule_metrics(rows, half_idx)
        families.append({
            "q_repr": q_repr, "k_repr": k_repr, "qk": qk,
            "params": params, "alias_txt": _alias_txt(params),
            "n_cells": len(params),
            "is_baseline": rules.is_baseline(q_repr, k_repr),
            "m": m,
        })
    for i, f in enumerate(families, 1):
        f["fid"] = i

    return {
        "ctxs": ctxs, "n_clean": n_clean, "half_idx": half_idx,
        "coverage": (ctxs[0].date, ctxs[-1].date) if ctxs else None,
        "families": families,
        "n_cells": len(rules.grid_cells()),
        "n_theo": rules.n_theoretical_distinct(),
    }


def pick_shortlist(families):
    """达标(N≥20)内：基线 + N≥30 均分最优 + N≥20 均分最优 + N≥20 夏普最优，身份去重。"""
    def best(lst, key):
        cand = [f for f in lst if f["m"].get(key) is not None]
        return max(cand, key=lambda f: f["m"][key]) if cand else None

    qualified = [f for f in families if f["m"]["n"] >= QUAL_N]
    base = next((f for f in qualified if f["is_baseline"]), None)
    cand30 = [f for f in qualified if f["m"]["n"] >= SHORTLIST_MIN]

    picks = []
    for f, basis in [
        (base, "基线（现行 live 口径）"),
        (best(cand30, "avg"), "N≥30 平均分最优"),
        (best(qualified, "avg"), "N≥20 平均分最优"),
        (best(qualified, "sharpe"), "N≥20 夏普最优"),
    ]:
        if f is not None and not any(p["f"] is f for p in picks):
            picks.append({"f": f, "basis": basis})
    return picks
