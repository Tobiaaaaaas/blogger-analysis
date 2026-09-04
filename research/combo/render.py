# -*- coding: utf-8 -*-
"""research/combo/render.py — md/csv 渲染（复用 quality/run_compare 的排版与格式化口径）。

产物都写 combo/reports/：
  combo_sweep.md            有效族表 + 口径 + 关键读数 + 多重比较警示 + 下一步
  combo_sweep.csv           有效族机器可读（含上下半期均分）
  combo_sweep_grid.csv      42 原始格全披露（含 behavior 族 id），不被去重藏格
"""
import csv
import os

from . import REPORTS_DIR, ensure_reports
from . import rules
from .engine import QUAL_N

COLS = ["rank", "q", "k", "alias", "n", "acc", "avg", "vol", "sharpe",
        "bull_n", "bull_avg", "bear_n", "bear_avg",
        "half1_avg", "half2_avg", "qual", "basis"]
GRID_COLS = ["behavior_group", "q", "k", "same_behavior_cells", "n", "acc", "avg", "vol",
             "sharpe", "bull_n", "bear_n", "qual"]


def _fmt(x, nd=2, pct=False):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    if pct:
        return f"{x * 100:.{nd}f}%"
    return f"{x:.{nd}f}"


def _fmt_sign(x, nd=2, pct=False):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    s = _fmt(x, nd, pct)
    return f"{'+' if x > 0 else ''}{s}"


def _qual_note(m):
    return "达标" if m["n"] >= QUAL_N else f"N={m['n']} < {QUAL_N}"


def write_csv(path, rows, fields):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})


def order_effective(fams):
    """达标(N≥20)按平均分降序（并列按阈值升序）；未达标列尾按阈值升序。md/csv 同一顺序。"""
    def key(f):
        m = f["m"]
        return (0 if m["n"] >= QUAL_N else 1,
                -(m["avg"] if m["avg"] is not None else -9e9),
                float(f["q_repr"]), f["k_repr"])
    return sorted(fams, key=key)


def _table_row(i, f, bold_avg=False):
    m = f["m"]
    mark = " ★" if f["is_baseline"] else ""
    star = f"{rules.q_str(f['q_repr'])}{mark}"
    if bold_avg and m["avg"] is not None:
        avg_txt = f"**{_fmt_sign(m['avg'])}**"
    else:
        avg_txt = _fmt_sign(m["avg"])
    rank = str(i) if i is not None else "—"
    return (f"| {rank} | {star} | {f['k_repr']} | {f['alias_txt']} | {m['n']} | "
            f"{_fmt(m['acc'], 1, pct=True)} | {avg_txt} | {_fmt(m['vol'])} | "
            f"{_fmt_sign(m['sharpe'])} | {m['bull_n']} | {_fmt_sign(m['bull_avg'])} | "
            f"{m['bear_n']} | {_fmt_sign(m['bear_avg'])} | {_fmt_sign(m['half1_avg'])} | "
            f"{_fmt_sign(m['half2_avg'])} | {_qual_note(m)} |")


def _pick_txt(p):
    f, m = p["f"], p["f"]["m"]
    mark = " ★" if f["is_baseline"] else ""
    return (f"q={rules.q_str(f['q_repr'])}{mark}, k={f['k_repr']} — N={m['n']} "
            f"acc {_fmt(m['acc'], 1, pct=True)} 均分 {_fmt_sign(m['avg'])} "
            f"夏普 {_fmt_sign(m['sharpe'])} | 上半 {_fmt_sign(m['half1_avg'])} / "
            f"下半 {_fmt_sign(m['half2_avg'])}")


def render_sweep(res, picks):
    fams = res["families"]
    ordered = order_effective(fams)
    qualified = [f for f in ordered if f["m"]["n"] >= QUAL_N]
    unqual = [f for f in ordered if f["m"]["n"] < QUAL_N]
    c0, c1 = res["coverage"]
    L = []
    L.append("# swing 板块共识 · 组合规则寻优（阈值 q × 表态规模 k 网格）")
    L.append("")
    L.append("衡量把「看多占比 **>2/3** 且 **表态者≥3**（对称看空）」这一现行口径泛化成 "
             "**阈值 q × 最低表态 k** 网格后，哪种规则的方向性判断质量更好。本表是**第一关**："
             "每日一票 @14:30 → +5 交易日质量评估（博主评价口径，score=d×return×100，衡量判断质量、"
             "**不是** trade-PnL）。挑出的更优规则还要过 **第二关 swing trade-PnL**（`run_confirm`）"
             "才算数——这是探索性景观，不是显著性检验。")
    L.append("")
    L.append("## 口径")
    L.append("")
    L.append(f"- 投票宇宙/快照：swing 板 **{len(rules.SWING)} 人**；每干净交易日 14:30 快照（与 "
             "`quality/engine.signal_rows` 同一网格同一快照）。")
    L.append("- 规则触发 ⟺ 表态者(多+空) e ≥ k 且 看多 b **> q×e**（严格；对称：看空 >q×e → d=−1）；"
             "都不达 → 当日无信号。q、k 用 Fraction 精确比较，绝无浮点错格。")
    L.append("- 打分：参考价 = 决策日上证 15:00 收盘；终点 = 决策日后**第 5 交易日**收盘；"
             "score = d×(ep收盘/参考−1)×100。")
    L.append(f"- 覆盖窗 = {c0.isoformat()} → {c1.isoformat()}（{res['n_clean']} 个干净决策日，"
             "所有格共享）；**N = 各格自己的触发信号日数**（越严越少）。")
    L.append(f"- 网格：q ∈ {', '.join(rules.q_str(q) for q in rules.RATIO_QS)} × "
             f"k ∈ {', '.join(str(k) for k in rules.KS)} = **{res['n_cells']} 原始格**"
             f"（键级全异、无跨格同义 → 同 {res['n_theo']} 族）；在**本样本实际表态分布**上按"
             f"**行为去重**（逐信号日方向向量全同）归并为 **{len(fams)} 族**——本表每族一行。")
    L.append("- **k 惰性（实证）**：本样本每个触发日表态者都 ≥7，k 从不缺票 → 各族 k 全并列"
             "（「k」列即代表 k；「族内并列(k)」列可见全部并列）。换时段若出现表态者不足的日子，"
             "k 会自然起约束、表自动分族。")
    L.append("- 达标资格：**N≥20** 才参与排序与短名单；达标按**平均分降序**，未达标列尾（阈值升序）。")
    L.append(f"- 半期稳健：上/下半期均分按干净日序对半切（界 = 第 {res['half_idx']} 日），各格对"
             "自己那半的计分行平均——N 若只扎堆某一半，两列差距会暴露，别只盯全期均分。")
    L.append("- ⚠️ **多重比较警示**：42 格/多族在同一份表态分布上扫描、高度相关；\"平均分最高\"不能"
             "单独作结论。请按 N、上下半期稳健列交叉读，并看短名单进 PnL 后的结果。")
    L.append("")
    L.append("## 全网格 · 有效规则（行为去重；达标 N≥20 按平均分降序）")
    L.append("")
    L.append("| " + " | ".join(["排名", "阈值 q", "k", "族内并列(k)", "信号日N", "正确率",
                             "**平均分**", "波动率", "夏普", "多N", "多均分", "空N", "空均分",
                             "上半期均分", "下半期均分", "资格"]) + " |")
    L.append("|" + "|".join(["---:"] * 16) + "|")
    for i, f in enumerate(qualified, 1):
        L.append(_table_row(i, f, bold_avg=True))
    if unqual:
        L.append("| — | （以下 N<20 未达资格，仅全列） | | | | | | | | | | | | | | |")
        for f in unqual:
            L.append(_table_row(None, f))
    L.append("")
    L.append("**★ = 基线格（q=2/3, k=3，现行 live 口径）**；「族内并列(k)」= 与代表格共享同一行为的"
             "全部原始格（同一 q 下 k 并列即 k 惰性实证）。")
    L.append("")
    L.append("## 关键读数")
    L.append("")
    for p in picks:
        L.append(f"- **{p['basis']}** → {_pick_txt(p)}")
    L.append("")
    base = next((f for f in fams if f["is_baseline"]), None)
    if base:
        L.append(f"- 基线格与 `quality/engine.signal_rows` 同源零漂移（--check 逐行断言）："
                 f"N={base['m']['n']} / acc {_fmt(base['m']['acc'], 1, pct=True)} / "
                 f"均分 {_fmt_sign(base['m']['avg'])} / 夏普 {_fmt_sign(base['m']['sharpe'])}。")
    L.append("- 阅读提示：阈值越严 → N 越少、样本内均分往往越高。这是\"更少、更挑的信号\"加"
             "多重比较叠加出来的表象，请勿按单调外推成\"越严越好\"——跨时段再看会均值回归。")
    L.append("")
    L.append("## 网格全披露")
    L.append("")
    L.append("`combo_sweep_grid.csv`：42 原始格（含等价重复，附 behavior 族 id）全量机器可读，"
             "不被去重藏格；可自行复查 k 惰性与跨 q 折叠。")
    L.append("")
    L.append("## 下一步")
    L.append("")
    L.append("短名单（上述 3-4 条）→ `python -m research.combo.run_confirm`（swing trade-PnL "
             "确认是否真赚钱，含 `--allow-short` 对照）。")
    return "\n".join(L)


def write_sweep_reports(res, picks):
    ensure_reports()
    rows = []
    for i, f in enumerate(order_effective(res["families"]), 1):
        m = f["m"]
        basis = next((p["basis"] for p in picks if p["f"] is f), "")
        rows.append({"rank": i, "q": rules.q_str(f["q_repr"]), "k": f["k_repr"],
                     "alias": f["alias_txt"], "n": m["n"],
                     "acc": round(m["acc"], 4), "avg": round(m["avg"], 4) if m["avg"] is not None else None,
                     "vol": round(m["vol"], 4) if m["vol"] is not None else None,
                     "sharpe": round(m["sharpe"], 4) if m["sharpe"] is not None else None,
                     "bull_n": m["bull_n"], "bull_avg": round(m["bull_avg"], 4) if m["bull_avg"] is not None else None,
                     "bear_n": m["bear_n"], "bear_avg": round(m["bear_avg"], 4) if m["bear_avg"] is not None else None,
                     "half1_avg": m["half1_avg"], "half2_avg": m["half2_avg"],
                     "qual": _qual_note(m), "basis": basis})
    write_csv(os.path.join(REPORTS_DIR, "combo_sweep.csv"), rows, COLS)

    grid_rows = []                              # 42 原始格全披露
    for f in res["families"]:
        for q, k in f["params"]:
            m = f["m"]
            grid_rows.append({"behavior_group": f["fid"], "q": rules.q_str(q), "k": k,
                              "same_behavior_cells": f["n_cells"], "n": m["n"],
                              "acc": round(m["acc"], 4), "avg": round(m["avg"], 4),
                              "vol": round(m["vol"], 4) if m["vol"] is not None else None,
                              "sharpe": round(m["sharpe"], 4) if m["sharpe"] is not None else None,
                              "bull_n": m["bull_n"], "bear_n": m["bear_n"], "qual": _qual_note(m)})
    write_csv(os.path.join(REPORTS_DIR, "combo_sweep_grid.csv"), grid_rows, GRID_COLS)

    md = render_sweep(res, picks)
    with open(os.path.join(REPORTS_DIR, "combo_sweep.md"), "w", encoding="utf-8") as f:
        f.write(md + "\n")
