# -*- coding: utf-8 -*-
"""research/combo/run_hyst.py — CLI：滞回共识平仓规则回测（每日一票 · 收盘成交）。

策略规格主档 = docs/hysteresis_consensus_spec.md（活文档，勿在 README 复述口径）。本跑法按 §1/§2：
  · swing 波段板共识 · 上证指数 · 窗口 w=5 交易日（本 CLI 启动时自动置 config.WINDOW_TRADING_DAYS["swing"]）。
  · 投票 = poll 单条 last（swing 剔 spec=long、无 mixed 概念）；分母 e = 当日有波段观点者（多+空）。
  · 唯一自变量 = 看多比例 ρ = bull/e（看空比例 = 1−ρ 互补；不引入"支持率"叫法）。
  · 开多 ρ>2/3（TO_LONG，严格）；both 开空 ρ<1/3（= 看空比例>2/3）。
  · 平仓 = 持腿比例跌破 1/2 才平：持多 ρ<1/2、持空 ρ>1/2（各需 e>Q_exit）；恰 50% 续持（滞回带）。
  · **开/平双法定人数、无填充/冻结**：Q10 = Q_open=Q_exit=10（e 严格 > 才过门）；fixed = 0/0 无门槛对照。
  · 每干净交易日一次 14:30 快照 → 当日 15:00 收盘成交；模式 long（仅做多）/ both（多空双向，期货式对照）。
比较 Q10 与 fixed 两变体 × long/both 四组。不带"无滞回"对照（用户明确不需要）。

用法：
  python -m research.combo.run_hyst            # 写 combo_hyst.md/.csv + daily.csv + trades.csv + pnl.png
  python -m research.combo.run_hyst --check    # 确定性双跑 + 不变式 + §2 整数边界断言再写

产物（research/combo/reports/）：
  combo_hyst.md          报告（口径/表现表/双门实证/逐笔/逐月/读法警示）
  combo_hyst.csv         变体×模式 指标机器可读（Q10 / fixed × long / both）
  combo_hyst_trades.csv  Q10 · 多空双向 逐笔往返
  combo_hyst_daily.csv   日净值曲线（Q10 的 long/both 两模式 + 持仓态）
  combo_hyst_pnl.png     PnL 图（Q10 / fixed × long / both + 买持）
"""
import argparse
import csv
import os

from .. import config
from .. import poll as pollmod
from . import REPORTS_DIR, ensure_reports
from . import daygrid, hyst

MODES = ("long", "both")
MODE_WORD = {"long": "仅做多", "both": "多空双向"}
NAME = {"long": "long_only", "both": "long_and_short"}
VARIANTS = ("Q10", "fixed")
V_QPAIR = {"Q10": (config.HYST_Q_OPEN, config.HYST_Q_EXIT), "fixed": (0, 0)}
V_WORD = {"Q10": "滞回·开/平双门Q10", "fixed": "滞回·无门槛"}


def _fmt(x, nd=2, pct=False):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    if pct:
        return f"{x * 100:.{nd}f}%"
    return f"{x:.{nd}f}"


def _fmt_sign(x, nd=2, pct=False):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    return f"{'+' if x > 0 else ''}{_fmt(x, nd, pct)}"


def write_csv(path, rows, fields):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})


def build_sims(ctxs):
    """变体 × 模式 → dict[(variant, mode)] = (sim, metrics)。"""
    out = {}
    for var in VARIANTS:
        qo, qe = V_QPAIR[var]
        for mode in MODES:
            sim = hyst.simulate(ctxs, lambda c, pos, m=mode, _qo=qo, _qe=qe:
                                hyst.hyst_policy(c, pos, m, _qo, _qe))
            out[(var, mode)] = (sim, hyst.metrics(sim))
    return out


def checks(ctxs, sims):
    ok = True
    msgs = []
    n = len(ctxs)
    for (var, mode), (sim, m) in sims.items():
        if len(sim["navs"]) != n:
            ok, msgs = False, msgs + [f"{(var, mode)} navs 长度 {len(sim['navs'])} != {n}"]
        if not all(x > 0 for x in sim["navs"]):
            ok, msgs = False, msgs + [f"{(var, mode)} nav 非正"]
        if m["n_days"] != n:
            ok, msgs = False, msgs + [f"{(var, mode)} n_days {m['n_days']} != {n}"]
    if len({id(s) for s, _m in sims.values()}) != len(sims):
        ok, msgs = False, msgs + ["sims 非独立实例"]
    return ok, msgs


def _div_runs(pos_a, pos_b):
    """两 pos_after 序列的连续差异段 [(start, end), ...]（逐元素不等即差异）。"""
    runs, in_run, start = [], False, None
    for i, (x, y) in enumerate(zip(pos_a, pos_b)):
        if x != y and not in_run:
            in_run, start = True, i
        elif x == y and in_run:
            runs.append((start, i - 1))
            in_run = False
    if in_run:
        runs.append((start, len(pos_a) - 1))
    return runs


def render_md(ctxs, sims, guard_ok):
    d0, d1 = ctxs[0].date.isoformat(), ctxs[-1].date.isoformat()
    n = len(ctxs)
    e_all = [c.expressed for c in ctxs]
    e_min, e_max = min(e_all), max(e_all)
    half_days = sum(1 for c in ctxs if 2 * c.bull == c.expressed)
    QO, QE = config.HYST_Q_OPEN, config.HYST_Q_EXIT
    L = []
    L.append("# 滞回共识回测：看多比例 ρ>2/3 开多 · ρ<1/3 开空 · 持腿 ρ<1/2 平 · 开/平双门 Q10（每日一票 · 收盘成交）")
    L.append("")
    L.append(f"在 **swing 波段板共识**（上证指数 · 窗口 w={config.HYST_WINDOW} 交易日 · 干净日 "
             f"{d0} → {d1} 共 {n} 日）上按滞回策略主档 "
             f"[docs/hysteresis_consensus_spec.md](../../docs/hysteresis_consensus_spec.md) 回测："
             f"每博主取窗口内**时间序最新一条波段观点**（剔 spec=long、无 mixed），"
             f"看多比例 **ρ = 多方观点/e** 唯一自变量——ρ>2/3（严格）开盘开多、both 且 ρ<1/3 开空，"
             f"此后**持腿 ρ<1/2（多）/ ρ>1/2（空）才平**，中间 ρ∈[1/2, 2/3] 滞回带继续持有。")
    L.append("")
    L.append("## 口径")
    L.append("")
    L.append("- 判定节奏：每干净交易日**一次 14:30 快照**定调 → 当日 **15:00 收盘**成交；"
             "分母 e = 当日有波段观点的博主数 = 多 + 空（单条 last 无 mixed，与 quality/combo 同网格）。")
    L.append(f"- 阈值（config.HYST_* 单源，空腿 = 1−多头镜像）：开多 ρ>2/3；开空 ρ<1/3（both）；"
             f"平多 ρ<1/2 且 e>Q_exit；平空 ρ>1/2 且 e>Q_exit；恰 50%（偶数 e 日 2·bull=e）落两腿持侧、不平不开。")
    L.append(f"- **开/平双法定人数**：Q10 = 开仓 e>Q_open={QO}、平仓 e>Q_exit={QE}（严格，无任何 50% 填充/冻结——"
             f"e 不足对应门 = 该日不动作：空仓持币 / 持仓走滞回续持）。对照列“无门槛”= (0,0) 每日按 ρ 判。")
    L.append("- 模式：**仅做多**（看空比例 >2/3 只平多持币）/ **多空双向**（看空比例 >2/3 开空，"
             "指数期货式线性收益，未计融券费/保证金——仅上限对照）。成本 0、全仓 0/1。")
    L.append("- 净值 = 收盘到收盘；样本末持仓以末日收盘强制平仓。买持基准 = 同期首/末干净日收盘。")
    L.append("- 回归护栏：" + ("✓" if guard_ok else "✗——拒写") + " 确定性双跑 + 结构不变式 + §2 整数边界断言。")
    L.append("")
    L.append("## 表现（Q10 双门 vs 无门槛对照）")
    L.append("")
    L.append("| 规则 | 模式 | 累计收益 | 年化 | 基准买持 | 超额 | 日Sharpe | MDD | 往返(多/空) | 胜率 | 在场K | 平均持仓K |")
    L.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for var in VARIANTS:                      # Q10 在前（锁定口径）
        for mode in MODES:
            _sim, m = sims[(var, mode)]
            bold = var == "Q10"
            L.append(f"| {V_WORD[var]} | {MODE_WORD[mode]} | "
                     f"**{_fmt_sign(m['total_return'],2,pct=True)}** | "
                     f"{_fmt_sign(m['annualized'],1,pct=True)} | {_fmt_sign(m['buyhold_return'],1,pct=True)} | "
                     f"{_fmt_sign(m['excess_vs_buyhold'],2,pct=True)} | {_fmt_sign(m['sharpe'])} | "
                     f"{_fmt(m['max_drawdown'],1,pct=True)} | {m['n_roundtrips']} ({m['n_long_rt']}/{m['n_short_rt']}) | "
                     f"{_fmt(m['win_rate'],1,pct=True)} | {m['in_market_days']} | {_fmt(m['avg_hold_legs'],1)} |")
    L.append("")
    L.append(f"- 基准买持 = 上证 {d0} 收盘 → {d1} 收盘："
             f"{_fmt_sign(sims[('Q10', 'long')][1]['buyhold_return'],2,pct=True)}"
             f"（首日开盘→末日收盘锚定会略不同）。")
    L.append("- 两组都是滞回规则（>2/3 开 · 持腿跌破 1/2 平），唯一区别 = 是否要求 e 严格过 Q_open/Q_exit。")
    L.append("")
    L.append("## 逐行读数")
    L.append("")
    kl = sims[("Q10", "long")][1]
    kb = sims[("Q10", "both")][1]
    fl = sims[("fixed", "long")][1]
    fb = sims[("fixed", "both")][1]
    L.append(f"- **Q10 · 仅做多**：累计 **{_fmt_sign(kl['total_return'],2,pct=True)}**（基准 "
             f"{_fmt_sign(kl['buyhold_return'],1,pct=True)}，超额 {_fmt_sign(kl['excess_vs_buyhold'],2,pct=True)}），"
             f"Sharpe {_fmt_sign(kl['sharpe'])}，MDD {_fmt(kl['max_drawdown'],1,pct=True)}，"
             f"{kl['n_roundtrips']} 往返 · 胜率 {_fmt(kl['win_rate'],1,pct=True)}，在场 {kl['in_market_days']} 根 K，"
             f"平均持仓 {_fmt(kl['avg_hold_legs'],1)} 根 K。")
    L.append("")
    L.append(f"- **Q10 · 多空双向**：累计 **{_fmt_sign(kb['total_return'],2,pct=True)}**（基准 "
             f"{_fmt_sign(kb['buyhold_return'],1,pct=True)}，超额 {_fmt_sign(kb['excess_vs_buyhold'],2,pct=True)}），"
             f"Sharpe {_fmt_sign(kb['sharpe'])}，MDD {_fmt(kb['max_drawdown'],1,pct=True)}，"
             f"{kb['n_roundtrips']} 往返（多 {kb['n_long_rt']} / 空 {kb['n_short_rt']}）· 胜率 "
             f"{_fmt(kb['win_rate'],1,pct=True)}，在场 {kb['in_market_days']} 根 K。")
    L.append("")
    L.append("## 双门实证（Q10 vs 无门槛唯一分叉）")
    L.append("")
    L.append(f"- 全样本 {n} 个干净日，剔 long + 单条 last 后 e 分布 **{e_min}..{e_max}**"
             f"（均值 {sum(e_all) / n:.1f}）；e≤Q_open={QO} 与 e≤Q_exit={QE} 皆拦截的日数 = "
             f"{sum(1 for c in ctxs if c.expressed <= max(QO, QE))}；恰 50%（偶数 e）日数 = {half_days}。")
    for mode in MODES:
        pa_q = sims[("Q10", mode)][0]["pos_after"]
        pa_f = sims[("fixed", mode)][0]["pos_after"]
        runs = _div_runs(pa_q, pa_f)
        L.append(f"- **{MODE_WORD[mode]}**：Q10 与无门槛差异段 "
                 + ("**0 段**——两变体在本样本行为全同，Q 门未真实改变任何动作。"
                    if not runs else f"{len(runs)} 段（下表为每段起始日，分叉日期 = "
                    f"{sum(r[1] - r[0] + 1 for r in runs)} 个干净日）："))
        if runs:
            L.append("")
            L.append("| 段起始 | e | 多/空 | ρ | 此前Q10仓 | Q10→ | 无门槛→ | 拦截归属 |")
            L.append("|---:|---:|---:|---:|---:|---:|---:|---|")
            for (s, _end) in runs:
                c = ctxs[s]
                prev = pa_q[s - 1] if s > 0 else 0
                rho = c.bull / c.expressed if c.expressed else 0.0
                if prev in (1, -1) and c.expressed <= QE:
                    kind = "平仓门拦截（e≤Q_exit，Q10 续持不认退潮）"
                elif prev == 0 and c.expressed <= QO:
                    kind = "开仓门拦截（e≤Q_open，Q10 空仓不开）"
                else:
                    kind = "分叉（两门过但前序差异传导）"
                L.append(f"| {c.date.isoformat()} | {c.expressed} | {c.bull}/{c.bear} | {rho:.0%} | "
                         f"{prev} | {pa_q[s]} | {pa_f[s]} | {kind} |")
            L.append("")
    L.append("- 即：“Q10 与无门槛谁更好”的分量只在这些分叉段上；若差异为空或极薄，属样本内个案、非显著性结论。")
    L.append("")
    L.append("## 多空双向 · 逐笔往返（Q10）")
    L.append("")
    L.append("| 腿 | 开 | 平 | 持有K | 毛收益 |")
    L.append("|---:|---:|---:|---:|---:|")
    sb = sims[("Q10", "both")][0]
    for t in sb["trades"]:
        L.append(f"| {t['side']} | {t['entry_date']} | {t['exit_date']} | {t['exit_idx']-t['entry_idx']} | "
                 f"{_fmt_sign(hyst._signed_move(t),2,pct=True)} |")
    L.append("")
    L.append("## 逐月（Q10）")
    L.append("")
    L.append("| 月 | 仅做多 | 多空双向 |")
    L.append("|---:|---:|---:|")
    for mo in sorted(kl["monthly"]):
        L.append(f"| {mo} | {_fmt_sign(kl['monthly'].get(mo,0),2,pct=True)} | "
                 f"{_fmt_sign(kb['monthly'].get(mo,0),2,pct=True)} |")
    L.append("")
    L.append("## 读法警示")
    L.append("")
    L.append("- Q10 vs 无门槛在同一份样本上比较；Q 门本质是“表态人少时比例不可信 → 当日不动”，"
             "若分叉段极少，谁优谁劣只有那一两笔的分量——换行情可能反转，勿当普适结论。")
    L.append("- 多空双向是理想化建模（指数期货式线性、无融券费/保证金/强平、可随时按收盘开平）。"
             "A 股个股做空门槛与成本远高于此；做空腿对费率敏感，双向结果宜视为“信号方向被双向兑现”的上限估计。")
    L.append("- 滞回原理：多头共识退潮时若仍占多数（ρ 50%~66%）继续持有，跌破 1/2 才平——把多数支持的漂移段"
             "留住（往返少、在场长）。换更碎、假突破多的行情，滞回也可能把失去多数前的亏损单拿更久。")
    L.append("- 每日一票 · 收盘成交：14:30 定调后当日新帖（14:30~15:00）不入快照；"
             "收盘成交假设整仓可在收盘价成交（无冲击/滑点/费率）。")
    L.append("- 本表为 w=5（run_hyst 自动置入）的滞回口径，与 3 档盘中引擎（combo_confirm）及 w=3 的 "
             "quality/backtest 属不同时态/窗口，不逐位对比。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="确定性双跑 + 不变式 + §2 整数边界断言")
    args = ap.parse_args()
    ensure_reports()

    # 滞回验证窗口 w（须在 CorpusIndex / clean_days 前；poll 运行时实时读该 dict，仅影响本进程）
    config.WINDOW_TRADING_DAYS["swing"] = config.HYST_WINDOW
    index = pollmod.CorpusIndex()
    ctxs, n_clean = daygrid.build_contexts(index)

    sims = build_sims(ctxs)
    guard_ok, msgs = checks(ctxs, sims)

    edge_errs = hyst.assert_policy_edges()
    if args.check:
        sims2 = build_sims(ctxs)
        same = all(sims[(var, mode)][0]["navs"] == sims2[(var, mode)][0]["navs"]
                   for var in VARIANTS for mode in MODES)
        guard_ok = guard_ok and same and not edge_errs
        print("[check] 确定性双跑" + (" ✓" if same else " ✗"))
        for msg in msgs:
            print("[check] ✗", msg)
        for e in edge_errs:
            print("[check] ✗ §2 边界断言：", e)
    if not guard_ok:
        print("[hyst] 护栏 ✗ —— 拒写产物")
        return

    # CSV 指标表
    csv_rows = []
    for var in VARIANTS:
        for mode in MODES:
            _sim, m = sims[(var, mode)]
            qo, qe = V_QPAIR[var]
            csv_rows.append({
                "rule": "滞回", "variant": var, "variant_word": V_WORD[var],
                "q_open": qo, "q_exit": qe,
                "mode": NAME[mode], "mode_word": MODE_WORD[mode],
                "total_return": round(m["total_return"], 6), "annualized": round(m["annualized"], 6),
                "buyhold_return": round(m["buyhold_return"], 6),
                "excess": round(m["excess_vs_buyhold"], 6),
                "sharpe": round(m["sharpe"], 4), "bh_sharpe": round(m["bh_sharpe"], 4),
                "mdd": round(m["max_drawdown"], 6), "n_roundtrips": m["n_roundtrips"],
                "n_long_rt": m["n_long_rt"], "n_short_rt": m["n_short_rt"],
                "win_rate": round(m["win_rate"], 4), "in_market_legs": m["in_market_days"],
                "in_market_frac": round(m["in_market"], 4), "avg_hold_legs": round(m["avg_hold_legs"], 2),
                "n_days": m["n_days"],
            })
    write_csv(os.path.join(REPORTS_DIR, "combo_hyst.csv"), csv_rows,
              ["rule", "variant", "variant_word", "q_open", "q_exit", "mode", "mode_word",
               "total_return", "annualized", "buyhold_return", "excess", "sharpe", "bh_sharpe", "mdd",
               "n_roundtrips", "n_long_rt", "n_short_rt", "win_rate", "in_market_legs",
               "in_market_frac", "avg_hold_legs", "n_days"])

    # 逐笔（Q10 双向）
    sb = sims[("Q10", "both")][0]
    tr_rows = [{"side": t["side"], "entry_date": t["entry_date"], "exit_date": t["exit_date"],
                "hold_legs": t["exit_idx"] - t["entry_idx"],
                "move": round(hyst._signed_move(t), 6)} for t in sb["trades"]]
    write_csv(os.path.join(REPORTS_DIR, "combo_hyst_trades.csv"), tr_rows,
              ["side", "entry_date", "exit_date", "hold_legs", "move"])

    # 日净值（Q10）
    sl, sb_ = sims[("Q10", "long")][0], sims[("Q10", "both")][0]
    daily = [{"date": c.date.isoformat(),
              "nav_long": round(a, 6), "pos_long": p,
              "nav_both": round(b, 6), "pos_both": q}
             for c, a, p, b, q in zip(ctxs, sl["navs"], sl["pos_after"],
                                      sb_["navs"], sb_["pos_after"])]
    write_csv(os.path.join(REPORTS_DIR, "combo_hyst_daily.csv"), daily,
              ["date", "nav_long", "pos_long", "nav_both", "pos_both"])

    # PNG
    plot_png(ctxs, sims)

    md = render_md(ctxs, sims, guard_ok)
    with open(os.path.join(REPORTS_DIR, "combo_hyst.md"), "w", encoding="utf-8") as f:
        f.write(md + "\n")

    e_all = [c.expressed for c in ctxs]
    QO, QE = config.HYST_Q_OPEN, config.HYST_Q_EXIT
    print(f"[hyst] 护栏 {'✓' if guard_ok else '✗'} · 干净日 {len(ctxs)}（w={config.HYST_WINDOW}，"
          f"剔 long 后 e {min(e_all)}..{max(e_all)}，e≤Q_open/Q_exit={QO}/{QE} 拦截日 "
          f"{sum(1 for c in ctxs if c.expressed <= max(QO, QE))}，恰50% {sum(1 for c in ctxs if 2*c.bull == c.expressed)} 天）")
    for var in VARIANTS:
        for mode in MODES:
            m = sims[(var, mode)][1]
            print(f"  {V_WORD[var]:<12} {MODE_WORD[mode]:<4} 累计 {m['total_return']*100:+6.2f}%  "
                  f"超额 {m['excess_vs_buyhold']*100:+6.2f}%  sharpe {m['sharpe']:+.2f}  "
                  f"往返 {m['n_roundtrips']}")
    print(f"[hyst] 产物 → {REPORTS_DIR}/combo_hyst.{{md,csv}} + _trades.csv + _daily.csv + _pnl.png")


def plot_png(ctxs, sims):
    import glob
    from matplotlib import font_manager
    for pat in ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Light.ttc",
                "/System/Library/Fonts/Hiragino Sans GB.ttc"):
        if glob.glob(pat):
            try:
                font_manager.fontManager.addfont(pat)
                break
            except Exception:
                pass
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["PingFang SC", "STHeiti", "Hiragino Sans GB", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    dates = [c.date for c in ctxs]
    ref0 = ctxs[0].ref
    bh = [c.ref / ref0 for c in ctxs]
    curves = [
        (sims[("Q10", "both")][0]["navs"], "#cf222e", 2.3, "-", "滞回·Q10 · 多空双向（锁定）"),
        (sims[("Q10", "long")][0]["navs"], "#1a7f37", 1.9, "-", "滞回·Q10 · 仅做多"),
        (sims[("fixed", "both")][0]["navs"], "#e0919a", 1.1, "--", "滞回·无门槛 · 多空双向（对照）"),
        (sims[("fixed", "long")][0]["navs"], "#9cc6a7", 1.0, "--", "滞回·无门槛 · 仅做多"),
    ]
    fig, ax = plt.subplots(figsize=(12, 6.0))
    for nav, col, lw, ls, lab in curves:
        ax.plot(dates, [(x - 1) * 100 for x in nav], lw=lw, ls=ls, color=col, label=lab)
    ax.plot(dates, [(x - 1) * 100 for x in bh], lw=1.1, color="#888", ls=":", label="买持基准")
    ax.axhline(0, color="#aaa", lw=0.7, ls=":")
    ax.set_ylabel("累计收益 (%)")
    ax.set_title(f"滞回共识（看多比例 ρ>2/3 开多 · ρ<1/3 开空 · 持腿 ρ<1/2 平 · 开/平双门 e>10 无填充）"
                 f"每日收盘成交 PnL  --  swing 21 人共识 · 上证指数 · w={config.HYST_WINDOW}\n"
                 "实线 = Q10（e>10 才动作）；虚线 = 无门槛（每日按 ρ 判，对照）")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.3)
    fig.autofmt_xdate(rotation=45)
    import matplotlib.dates as mdates
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    plt.tight_layout()
    out = os.path.join(REPORTS_DIR, "combo_hyst_pnl.png")
    plt.savefig(out, dpi=110)
    print("saved", out)


if __name__ == "__main__":
    main()
