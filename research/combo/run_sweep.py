# -*- coding: utf-8 -*-
"""research/combo/run_sweep.py — CLI：第一关 组合规则寻优全网格扫描（每日一票 · +5交易日质量评估）。

用法：
  python -m research.combo.run_sweep            # 扫描 → 写 combo/reports/ 3 产物
  python -m research.combo.run_sweep --check    # 先跑内联校验（基线同源/边界/确定性/去重自洽）再写

产物（research/combo/reports/，UTF-8；csv 用 utf-8-sig）：
  combo_sweep.md            有效规则族同表 + 口径 + 关键读数 + 多重比较警示
  combo_sweep.csv           有效族机器可读
  combo_sweep_grid.csv      42 原始格全披露（behavior 族 id）

仅 Mac 本地离线跑，无外部调用、无密钥；不碰 config.THRESHOLD/MIN_EXPRESSED（网格比较全走 Fraction）。
"""
import argparse

from .. import config
from .. import poll as pollmod
from . import REPORTS_DIR, ensure_reports
from . import rules
from .daygrid import build_contexts
from .engine import rows_for_rule
from . import sweep as sweepmod
from . import render


# ---------------- 内联校验（--check） ----------------

def _run_checks(index):
    errs = []
    # ① min_bull 边界（Fraction 精确；e=3,q=2/3 ⇒ 需 3 票等边界用例）
    cases = [
        ((rules.Fraction(2, 3), 3), 3),   # 2/3×3=2 → 严格 >2 需 3 票
        ((rules.Fraction(2, 3), 6), 5),   # 2/3×6=4 → 需 5 票
        ((rules.Fraction(2, 3), 21), 15), # 2/3×21=14 → 需 15 票
        ((rules.Fraction(1, 2), 3), 2),   # 1/2×3=1.5 → 需 2 票
        ((rules.Fraction(7, 10), 10), 8), # 7/10×10=7 → 需 8 票
        ((rules.Fraction(9, 10), 10), 10),# 9/10×10=9 → 需 10 票
        ((rules.Fraction(3, 4), 21), 16), # 3/4×21=15.75 → 需 16 票
    ]
    for (q, e), want in cases:
        got = rules.min_bull(q, e)
        if got != want:
            errs.append(f"min_bull({q},{e}) = {got} ≠ {want}")

    # ② 基线格同源零漂移：rows_for_rule(2/3,3) == quality.engine.signal_rows 逐行(date,d,score)
    ctxs, n_clean = build_contexts(index)
    from ..quality import engine as qe
    base_rows = rows_for_rule(ctxs, rules.BASELINE_Q, rules.BASELINE_K)
    sig_rows = qe.signal_rows(index)
    base_map = {(r["date"], r["d"]): r["score"] for r in base_rows}
    if len(base_map) != len(base_rows):
        errs.append("基线 rows 内 date/d 重复（不应发生）")
    for r in sig_rows:
        key = (r["date"], r["d"])
        if key not in base_map:
            errs.append(f"基线缺 quality 综合信号行 {r['date']} d={r['d']}")
        elif base_map[key] != r["score"]:
            errs.append(f"基线 score 漂移 {r['date']} d={r['d']}: combo {base_map[key]} ≠ quality {r['score']}")
    for (date_s, d) in base_map:
        if not any(r["date"] == date_s and r["d"] == d for r in sig_rows):
            errs.append(f"combo 基线多出 quality 无的信号行 {date_s} d={d}")
    from .engine import rule_metrics
    base_m = rule_metrics(base_rows, n_clean // 2)
    sig_m = qe.summarize_metrics(sig_rows)
    for k in ("n", "acc", "avg", "vol", "sharpe", "bull_n", "bull_avg", "bear_n", "bear_avg"):
        a, b = base_m[k], sig_m[k]
        if a is None and b is None:
            continue
        if a is None or b is None or abs(a - b) > 1e-9:
            errs.append(f"基线指标 {k} 漂移: combo {a} ≠ quality {b}")

    # ③ 确定性：同 index 二次运行，逐族 rows/指标一致
    res1 = sweepmod.run_sweep(index)
    res2 = sweepmod.run_sweep(index)
    for f1, f2 in zip(res1["families"], res2["families"]):
        if (f1["qk"], f1["alias_txt"], f1["m"]) != (f2["qk"], f2["alias_txt"], f2["m"]):
            errs.append(f"确定性失败：族 {f1['qk']} 两次扫描不一致")
            break

    # ④ 去重自洽：各族 params 行为同指纹；原始格无丢失/无重复
    n_params = sum(f["n_cells"] for f in res1["families"])
    if n_params != rules.n_cells_total():
        errs.append(f"去重自洽失败：各族原始格和 {n_params} ≠ {rules.n_cells_total()}")

    print(f"[check] min_bull 边界 {len(cases)} / 基线同源 N={sig_m['n']}（vs combo N={base_m['n']}）/ "
          f"确定性 / 去重自洽" + (" ✓" if not errs else f" ✗ {len(errs)} 处异常"))
    for e in errs[:20]:
        print("   -", e)
    return not errs


# ---------------- CLI ----------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="先跑内联校验（基线同源/边界/确定性）再写报告")
    args = ap.parse_args()
    ensure_reports()

    index = pollmod.CorpusIndex()
    res = sweepmod.run_sweep(index)
    picks = sweepmod.pick_shortlist(res["families"])

    ok = True
    if args.check:
        ok = _run_checks(index)
        if not ok:
            print("校验未通过，不写报告。")
            return

    render.write_sweep_reports(res, picks)

    # 控制台摘要
    c0, c1 = res["coverage"]
    print(f"[combo] 网格 {res['n_cells']} 原始格 → 理论 {res['n_theo']} → 行为去重 "
          f"{len(res['families'])} 族（覆盖 {c0.isoformat()}→{c1.isoformat()} {res['n_clean']} 日）")
    fams = sorted(res["families"], key=lambda f: (float(f["q_repr"]), f["k_repr"]))
    for f in fams:
        m = f["m"]
        mark = " ★基线" if f["is_baseline"] else ""
        print(f"  q={rules.q_str(f['q_repr']):>4}, k={f['k_repr']}  族{len(f['params'])}格 N={m['n']:<3} "
              f"acc {m['acc']*100:.1f}% 均分 {m['avg']:+.3f} sharpe "
              f"{(m['sharpe'] if m['sharpe'] is not None else 0):+.2f}{mark}")
    print("[combo] 短名单（→ run_confirm）：")
    for p in picks:
        f = p["f"]
        print(f"  - [{p['basis']}] q={rules.q_str(f['q_repr'])}, k={f['k_repr']} "
              f"(N={f['m']['n']}, avg {f['m']['avg']:+.3f})")
    print(f"[combo] 产物 → {REPORTS_DIR}/combo_sweep.{'md,csv'} + combo_sweep_grid.csv")


if __name__ == "__main__":
    main()
