# -*- coding: utf-8 -*-
"""research/combo/run_hyst_sweep.py — CLI：滞回策略参数敏感性扫描（窗口 w × 法定人数 Q × 开/平阈值）。

绕基线 **w5 · Q_open=Q_exit=10 · TO_LONG=2/3 · TX_LONG=1/2**（= canonical combo_hyst.md 那套）做**单轴 OAT**
每次只动一个轴 + 少量**跨轴角格**，模式全含 long/both。阈值一律 fractions.Fraction、整数式比较（复用
hyst._decide 的显式 to_long/tx_long 参数化），空腿恒 = 1−多头镜像（spec §1 语义），不动 hyst 默认路径。

样本与 canonical 同源：swing 21 人 · 上证 · 干净日取样（w∈{3,5,7,10} 下日期集不变，右侧漏抽主导端界 →
150 日 2026-01-05→08-17）；w 只改每博主"窗口内最新一条"与 e/bull 计数。每个 w 重建 CorpusIndex +
daygrid.build_contexts 一次，全部 cell 共享同批 ctxs（run_sweep 同款"一份快照多规则"）。

**不改 canonical 产物**：本 CLI 只写 combo_hyst_sweep.md/.csv。

用法：
  python -m research.combo.run_hyst_sweep            # 全轴扫描 → reports/combo_hyst_sweep.{md,csv}
  python -m research.combo.run_hyst_sweep --check    # + 基细胞 vs canonical 校验 + 确定性双跑 + §2 边界断言

产物（research/combo/reports/）：
  combo_hyst_sweep.md   报告（基线/w/Q对称/Q不对称/TO/TX/角格 分块表 + 块内读数 + 读法警示）
  combo_hyst_sweep.csv  46 行（23 cell × long/both）机器可读全量
"""
import argparse
import csv
import os
from fractions import Fraction

from .. import config
from .. import poll as pollmod
from . import REPORTS_DIR, ensure_reports
from . import daygrid, hyst
from . import run_hyst as rh            # 复用 MODES/MODE_WORD/_fmt/_fmt_sign/write_csv

# ---- 取值（均衡集 A；基准 = S0）----
BASE = dict(w=5, to=Fraction(2, 3), tx=Fraction(1, 2), qo=config.HYST_Q_OPEN, qe=config.HYST_Q_EXIT)
WS = [3, 5, 7, 10]
Q_SYM = [(0, 0), (8, 8), (12, 12), (14, 14)]      # (10,10)=基线 S0，已单列
Q_ASYM = [(8, 10), (10, 8), (10, 12), (12, 10)]
TO_VS = [Fraction(3, 5), Fraction(7, 10), Fraction(3, 4)]
TX_VS = [Fraction(2, 5), Fraction(3, 5)]

# 块 = (标题, 固定轴说明, [(行标签, cell_dict), ...])；cell_dict 字段同 BASE 六键
def _cell(**kw):
    c = dict(BASE)
    c.update(kw)
    return c


def build_blocks():
    b = BASE
    blocks = []
    blocks.append(("基线 S0", "w5 · Q(10,10) · TO 2/3 · TX 1/2（= canonical Q10 行）",
                   [("S0", _cell())]))
    blocks.append(("w 窗口轴", "Q(10,10) · TO 2/3 · TX 1/2，只动 w",
                   [(f"w={w}", _cell(w=w)) for w in (3, 7, 10)]))
    blocks.append(("Q 对称法定人数", "w5 · TO 2/3 · TX 1/2，Q_open=Q_exit 同升同降",
                   [(f"Q=({qo},{qe})", _cell(qo=qo, qe=qe)) for qo, qe in Q_SYM]))
    blocks.append(("Q 不对称双门", "w5 · TO 2/3 · TX 1/2，开/平门独立",
                   [(f"Q=({qo},{qe})", _cell(qo=qo, qe=qe)) for qo, qe in Q_ASYM]))
    blocks.append(("TO_LONG 开仓线", "w5 · Q(10,10) · TX 1/2，只动多头开仓线（空头开 = 1−镜像）",
                   [(f"TO={_fq(t)}", _cell(to=t)) for t in TO_VS]))
    blocks.append(("TX_LONG 平仓线", "w5 · Q(10,10) · TO 2/3，只动多头平仓线（空头平 = 1−镜像）",
                   [(f"TX={_fq(t)}", _cell(tx=t)) for t in TX_VS]))
    corners = [
        ("严挑·高Q·严开", dict(qo=12, qe=12, to=Fraction(3, 4))),
        ("宽松·低Q·宽开宽平", dict(qo=8, qe=8, to=Fraction(3, 5), tx=Fraction(2, 5))),
        ("无门槛·宽滞回", dict(qo=0, qe=0, to=Fraction(3, 4), tx=Fraction(2, 5))),
        ("宽窗·高Q", dict(w=10, qo=12, qe=12)),
        ("快窗·无门槛", dict(w=3, qo=0, qe=0)),
        ("窄滞回（近似非滞回）", dict(to=Fraction(7, 10), tx=Fraction(3, 5))),
    ]
    blocks.append(("跨轴角格", "Q10 除非注明；名字后括号 = 与 S0 的全部差异",
                   [(f"{name}（{_diff_desc(kw)}）", _cell(**kw)) for name, kw in corners]))
    return blocks


def _fq(x):
    return f"{x.numerator}/{x.denominator}"


def _diff_desc(kw):
    bits = []
    if "w" in kw:
        bits.append(f"w={kw['w']}")
    if "qo" in kw or "qe" in kw:
        bits.append(f"Q=({kw.get('qo', BASE['qo'])},{kw.get('qe', BASE['qe'])})")
    for k in ("to", "tx"):
        if k in kw:
            bits.append(f"{k.upper()}={_fq(kw[k])}")
    return "·".join(bits) if bits else "基线"


# ---- 运行 ----
def run_all():
    """每个 w 建一份 ctxs；全部 cell × long/both → out[(w,to,tx,qo,qe)][mode] = (sim, m)。"""
    ctxs_by_w = {}
    for w in WS:
        config.WINDOW_TRADING_DAYS["swing"] = w
        index = pollmod.CorpusIndex()
        ctxs, n_clean = daygrid.build_contexts(index)
        ctxs_by_w[w] = ctxs
    # 收集所有唯一 cell（按块序去重）
    cells, seen = [], set()
    for _title, _note, rows in build_blocks():
        for label, c in rows:
            key = (c["w"], c["to"], c["tx"], c["qo"], c["qe"])
            if key not in seen:
                seen.add(key)
                cells.append((label, c))
    out = {}
    for label, c in cells:
        assert c["tx"] < c["to"], f"{label}: TX({c['tx']}) ≥ TO({c['to']}) 非真滞回"
        ctxs = ctxs_by_w[c["w"]]
        sims = {}
        for mode in rh.MODES:
            sim = hyst.simulate(ctxs, lambda cc, pos, m=mode, _c=c:
                                hyst._decide(cc.expressed, cc.bull, pos, m, _c["qo"], _c["qe"],
                                             _c["to"], _c["tx"]))
            sims[mode] = (sim, hyst.metrics(sim))
        out[(c["w"], c["to"], c["tx"], c["qo"], c["qe"])] = sims
    return out, ctxs_by_w


def _canonical_base():
    """读 canonical combo_hyst.csv 的 Q10/fixed × long/both 四行 → 基细胞期望。"""
    path = os.path.join(REPORTS_DIR, "combo_hyst.csv")
    exp = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            exp[(row["variant"], row["mode_word"])] = row
    return exp


def _near(a, b):
    return abs(float(a) - float(b)) < 1e-9


def checks(out, ctxs_by_w):
    """基细胞 vs canonical、确定性、边界。返回 (ok, msgs)。"""
    ok, msgs = True, []
    exp = _canonical_base()
    base_sims = out[(BASE["w"], BASE["to"], BASE["tx"], BASE["qo"], BASE["qe"])]
    free_sims = out[(BASE["w"], BASE["to"], BASE["tx"], 0, 0)]
    for var, qkey, sims in (("Q10", "S0", base_sims), ("fixed", "free", free_sims)):
        for mode, mw in (("long", "仅做多"), ("both", "多空双向")):
            e = exp.get((var, mw))
            if e is None:
                ok = False
                msgs.append(f"canonical combo_hyst.csv 缺 {var}/{mw} 行")
                continue
            m = sims[mode][1]
            # canonical combo_hyst.csv 按各自精度 round 后写盘，故比对先把 sweep 值 round 到同精度
            dec = {"total_return": 6, "annualized": 6, "buyhold_return": 6, "excess": 6,
                   "sharpe": 4, "mdd": 6, "win_rate": 4, "avg_hold_legs": 2}
            for field, val in [("total_return", m["total_return"]), ("annualized", m["annualized"]),
                               ("buyhold_return", m["buyhold_return"]), ("excess", m["excess_vs_buyhold"]),
                               ("sharpe", m["sharpe"]), ("mdd", m["max_drawdown"]),
                               ("n_roundtrips", m["n_roundtrips"]), ("win_rate", m["win_rate"]),
                               ("avg_hold_legs", m["avg_hold_legs"])]:
                cmp = round(val, dec[field]) if field in dec else val
                if not _near(e[field], cmp):
                    ok = False
                    msgs.append(f"{var}/{mw} {field}: canonical {e[field]} ≠ sweep {val}")
    # 确定性：抽 3 cell 复跑对 navs
    probes = [(5, Fraction(2, 3), Fraction(1, 2), 10, 10),
              (5, Fraction(2, 3), Fraction(1, 2), 8, 10),
              (5, Fraction(3, 4), Fraction(2, 5), 0, 0)]
    for w, to, tx, qo, qe in probes:
        ctxs = ctxs_by_w[w]
        for mode in rh.MODES:
            sim = hyst.simulate(ctxs, lambda cc, pos, m=mode, _qo=qo, _qe=qe, _to=to, _tx=tx:
                                hyst._decide(cc.expressed, cc.bull, pos, m, _qo, _qe, _to, _tx))
            ref = out[(w, to, tx, qo, qe)][mode][0]["navs"]
            if sim["navs"] != ref:
                ok = False
                msgs.append(f"确定性 ✗ cell (w{w},Q({qo},{qe}),{to},{tx}) {mode}")
    return ok, msgs


# ---- 渲染 ----
def _block_tables(out):
    """md 各块表 + 块内读数。返回 (md_lines, rows_meta)。"""
    L = []
    all_rows = []
    bh = None
    for block_title, block_note, rows in build_blocks():
        L.append(f"## {block_title}")
        L.append("")
        L.append(f"> {block_note}")
        L.append("")
        L.append("| 参数 | 模式 | 累计 | 年化 | 超额 | 日Sharpe | MDD | 往返(L/S) | 胜率 | 在场K | 均持K |")
        L.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        per_mode = {"long": [], "both": []}
        for label, c in rows:
            key = (c["w"], c["to"], c["tx"], c["qo"], c["qe"])
            sims = out[key]
            for mode in rh.MODES:
                _sim, m = sims[mode]
                if bh is None:
                    bh = m["buyhold_return"]
                per_mode[mode].append((label, m))
                all_rows.append((block_title, label, c, mode, m))
                L.append(f"| {label} | {rh.MODE_WORD[mode]} | "
                         f"{rh._fmt_sign(m['total_return'],2,pct=True)} | "
                         f"{rh._fmt_sign(m['annualized'],1,pct=True)} | "
                         f"{rh._fmt_sign(m['excess_vs_buyhold'],2,pct=True)} | {rh._fmt_sign(m['sharpe'])} | "
                         f"{rh._fmt(m['max_drawdown'],1,pct=True)} | "
                         f"{m['n_roundtrips']} ({m['n_long_rt']}/{m['n_short_rt']}) | "
                         f"{rh._fmt(m['win_rate'],1,pct=True)} | {m['in_market_days']} | "
                         f"{rh._fmt(m['avg_hold_legs'],1)} |")
        L.append("")
        for mode, mw in (("long", "仅做多"), ("both", "多空双向")):
            items = per_mode[mode]
            if len(items) == 1 and items[0][0] == "S0":
                continue                          # 基线块不写 delta
            bl = max(items, key=lambda t: t[1]["excess_vs_buyhold"])
            sl = max(items, key=lambda t: t[1]["sharpe"])
            wo = min(items, key=lambda t: t[1]["excess_vs_buyhold"])
            L.append(f"- **{mw}**：超额最好 {bl[0]} {rh._fmt_sign(bl[1]['excess_vs_buyhold'],2,pct=True)}、"
                     f"最差 {wo[0]} {rh._fmt_sign(wo[1]['excess_vs_buyhold'],2,pct=True)}；"
                     f"Sharpe 最好 {sl[0]} {rh._fmt_sign(sl[1]['sharpe'])}。")
        L.append("")
    return L, all_rows, bh


def write_sweep_csv(all_rows, bh):
    path = os.path.join(REPORTS_DIR, "combo_hyst_sweep.csv")
    rows = []
    for block_title, label, c, mode, m in all_rows:
        rows.append({
            "block": block_title, "label": label, "w": c["w"],
            "to_long": _fq(c["to"]), "tx_long": _fq(c["tx"]),
            "q_open": c["qo"], "q_exit": c["qe"],
            "mode": rh.NAME[mode], "mode_word": rh.MODE_WORD[mode],
            "total_return": round(m["total_return"], 6), "annualized": round(m["annualized"], 6),
            "buyhold_return": round(m["buyhold_return"], 6), "excess": round(m["excess_vs_buyhold"], 6),
            "sharpe": round(m["sharpe"], 4), "bh_sharpe": round(m["bh_sharpe"], 4),
            "mdd": round(m["max_drawdown"], 6), "n_roundtrips": m["n_roundtrips"],
            "n_long_rt": m["n_long_rt"], "n_short_rt": m["n_short_rt"],
            "win_rate": round(m["win_rate"], 4) if m["win_rate"] == m["win_rate"] else "",
            "in_market_legs": m["in_market_days"], "in_market_frac": round(m["in_market"], 4),
            "avg_hold_legs": round(m["avg_hold_legs"], 2), "n_days": m["n_days"],
        })
    rh.write_csv(path, rows,
                 ["block", "label", "w", "to_long", "tx_long", "q_open", "q_exit",
                  "mode", "mode_word", "total_return", "annualized", "buyhold_return", "excess",
                  "sharpe", "bh_sharpe", "mdd", "n_roundtrips", "n_long_rt", "n_short_rt",
                  "win_rate", "in_market_legs", "in_market_frac", "avg_hold_legs", "n_days"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="基细胞 vs canonical + 确定性双跑 + §2 边界断言")
    args = ap.parse_args()
    ensure_reports()

    out, ctxs_by_w = run_all()

    # w 侧 e 分布（验证 150 不变、e 随窗宽上移）
    for w in WS:
        e_all = [c.expressed for c in ctxs_by_w[w]]
        n = len(e_all)
        print(f"[sweep] w={w}: 干净日 {n}，e {min(e_all)}..{max(e_all)}（均值 {sum(e_all) / n:.1f}）")

    edge_errs = hyst.assert_policy_edges()
    ok, msgs = True, []
    if args.check:
        ok, msgs = checks(out, ctxs_by_w)
        print("[sweep] 基细胞 vs canonical" + (" ✓" if ok else " ✗"))
        for msg in msgs:
            print("[sweep] ✗", msg)
        if edge_errs:
            ok = False
            for e in edge_errs:
                print("[sweep] ✗ §2 边界断言：", e)
    if not ok:
        print("[sweep] 护栏 ✗ —— 拒写产物")
        return

    L, all_rows, bh = _block_tables(out)
    md = render_header(L, ctxs_by_w, bh)
    with open(os.path.join(REPORTS_DIR, "combo_hyst_sweep.md"), "w", encoding="utf-8") as f:
        f.write(md + "\n")
    write_sweep_csv(all_rows, bh)
    n_rows = len(all_rows)
    print(f"[sweep] 产物 → {REPORTS_DIR}/combo_hyst_sweep.{{md,csv}}（{n_rows} 行 = {n_rows // 2} cell × long/both）")


def render_header(body, ctxs_by_w, bh):
    b = BASE
    n = len(ctxs_by_w[BASE["w"]])
    d0, d1 = ctxs_by_w[BASE["w"]][0].date.isoformat(), ctxs_by_w[BASE["w"]][-1].date.isoformat()
    e5 = [c.expressed for c in ctxs_by_w[5]]
    L = []
    L.append("# 滞回策略参数敏感性：窗口 w × 法定人数 Q × 开/平阈值（单轴 OAT + 角格）")
    L.append("")
    L.append(f"在 swing 波段板共识（上证指数 · 干净日 {d0} → {d1} 共 {n} 日，w∈{{3,5,7,10}} 下日期集不变）上，"
             f"绕基线 **w5 · Q_open=Q_exit=10 · TO_LONG=2/3 · TX_LONG=1/2**（= canonical combo_hyst.md 那套）"
             f"做单轴 OAT + 精选跨轴角格。口径同 "
             f"[Swing_Timing.md](../../../.claude/skills/analyze-blogger/Swing_Timing.md)：每博主窗口内时间序最新一条"
             f"波段观点（剔 spec=long、无 mixed），ρ=bull/e 唯一自变量；14:30 快照 → 15:00 收盘成交；long/both；"
             f"0 成本全仓 0/1；净值收盘→收盘、末日强平。空腿恒镜像（TO_SHORT=1−TO_LONG、TX_SHORT=1−TX_LONG）。")
    L.append("")
    L.append("## 口径与读法")
    L.append("")
    L.append(f"- 样本 = 150 个干净决策日（{d0}→{d1}）；买持基准恒 **{rh._fmt_sign(bh,2,pct=True)}**（随样本固定），"
             f"故下表只列“超额”（累计−买持），不再逐行列基准。")
    L.append(f"- w=5 下 e 分布 **{min(e5)}..{max(e5)}**（均值 {sum(e5) / len(e5):.1f}），其余 w 见 w 轴块内注。"
             f"所有比较严格不等（Fraction 整数式），恰阈值不触发。")
    L.append("- 每块表后一行“块内读数”（最好/最差格），仅供 OAT 单轴内对比；**跨轴/整表择优是多重点比较，"
             "样本内越挑越好看的叠加表象、跨时段大概率均值回归，勿据此改 live 口径**。")
    L.append(f"- 本扫描不改 canonical combo_hyst.*（Q10/fixed 基细胞与 canonical 逐格校验一致后才写本产物）。")
    L.append("")
    warnings = [
        "- 单一样本（150 日、一段行情）+ 23 cell × 2 模式 ≈ 46 行同时比较 → **多重比较风险**："
        "'最优格'的领先量只有 1~2 笔往返的分量，换行情可能反转。",
        "- Q 门本质 = '表态人少时比例不可信 → 当日不动'：Q 抬升减少动作次数、增加滞回持有，有利有弊视行情。",
        "- 阈值越严（TO 大 / Q 大）→ 动作越少、单笔越挑 —— 与 repo 42 格扫描同一规律，属择优回填表象，非普适信号。",
        "- 多空双向为指数期货式线性、未计融券费/保证金，仅为上限对照；做空腿对费率敏感。",
        "- 本表 w 各列在不同窗口宽度下比（w 改变每博主'窗口内最新一条'及 e 分布），其余轴全部在 w5 同批 ctxs 上比。",
    ]
    return "\n".join(L) + "\n\n" + "\n".join(body) + "\n\n## 读法警示\n\n" + "\n".join(warnings)


if __name__ == "__main__":
    main()
