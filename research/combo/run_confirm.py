# -*- coding: utf-8 -*-
"""research/combo/run_confirm.py — CLI：第二关 swing trade-PnL 确认（组合规则短名单）。

短名单由第一关（run_sweep）行为去重结果锁定：基线(2/3,3) + N≥30 均分最优 + N≥20 均分最优
+ N≥20 夏普最优（身份去重，通常 3 条）。对每条跑真实 swing trade-PnL —— 尊重 backtest 引擎
原生的 3 档/日节奏（09:30/11:00/14:30 逐档 decide），与第一关"每日一票 +5交易日"是两种时态，
排名不要求一致：第一关测判断质量、第二关测是否真赚钱。

回归护栏（每次必跑，不过则拒写）：decide=rules.decide(2/3,3) 的回测必须与 decide=None（默认
2/3 口径）在 stats/daily/trades/ticks 上逐项 == —— 证明接缝零漂移、默认路径未被改动。

用法：
  python -m research.combo.run_confirm                # 短名单 long-only → 写 combo_confirm.{md,csv}
  python -m research.combo.run_confirm --allow-short  # 另加"看空也开空"双向对照（指数期货式）
  python -m research.combo.run_confirm --check        # 先跑回归护栏 + 确定性，再写

产物（research/combo/reports/）：
  combo_confirm.md       PnL 对照表 + 口径/逐条读数
  combo_confirm.csv      短名单 PnL 机器可读（allow_short 两态）
"""
import argparse
import csv
import os

from .. import poll as pollmod
from ..backtest.backtest import run as bt_run
from . import REPORTS_DIR, ensure_reports
from . import rules
from . import sweep as sweepmod

COLS = ["rank", "rule", "q", "k", "basis", "mode", "total_return", "annualized",
        "buyhold_return", "excess", "sharpe", "mdd", "n_roundtrips", "win_rate",
        "in_market_days", "avg_hold_days", "n_trades", "quality_n", "quality_avg"]

MODE_WORDS = {"long": "仅做多", "both": "双向(看空开空)"}


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


def write_csv(path, rows, fields):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})


# ---------------- 回归护栏 ----------------

def _guard_default(index):
    """decide=2/3,k3 必须 == decide=None（默认 2/3 口径）：stats/daily/trades/ticks 逐项 ==。"""
    a = bt_run("swing", index=index)
    b = bt_run("swing", index=index, decide=rules.decide(rules.BASELINE_Q, rules.BASELINE_K))
    diffs = []
    if a["stats"] != b["stats"]:
        for k in a["stats"]:
            if a["stats"].get(k) != b["stats"].get(k):
                diffs.append(f"stats[{k}]")
    for part in ("daily", "trades", "ticks"):
        if a[part] != b[part]:
            diffs.append(f"{part}")
    return a, not diffs, diffs


# ---------------- CLI ----------------

def run_picks(index, picks, allow_short):
    """对每条短名单跑 PnL；返回行 dict 列表。"""
    rows = []
    for rank, p in enumerate(picks, 1):
        f, basis = p["f"], p["basis"]
        q, k = f["qk"]
        for mode in ("long", "both") if allow_short else ("long",):
            r = bt_run("swing", index=index, decide=rules.decide(q, k),
                       allow_short=(mode == "both"))
            st = r["stats"]
            rows.append({
                "rank": rank,
                "rule": f"{rules.q_str(q)}/k{k}",
                "q": rules.q_str(q), "k": k, "basis": basis, "mode": mode,
                "total_return": st["total_return"], "annualized": st["annualized"],
                "buyhold_return": st["buyhold_return"], "excess": st["excess_vs_buyhold"],
                "sharpe": st["sharpe"], "mdd": st["max_drawdown"],
                "n_roundtrips": st["n_roundtrips"], "win_rate": st["win_rate"],
                "in_market_days": st["in_market_days"], "avg_hold_days": st["avg_hold_days"],
                "n_trades": st["n_trades"],
                "quality_n": f["m"]["n"],
                "quality_avg": f["m"]["avg"],
                "raw": r,
            })
    return rows


def render_confirm(rows, guard_ok, allow_short, bh_note):
    L = []
    L.append("# swing trade-PnL 确认：组合规则短名单（第二关）")
    L.append("")
    L.append("第一关（`combo_sweep.md`，每日一票 @14:30 → +5交易日质量评估）筛出的短名单规则，"
             "回到真实 **swing trade-PnL** 看是否真赚钱。回测尊重引擎原生节奏：每交易日 3 档"
             "（09:30/11:00/14:30）逐档以 `decide` 重新判定，30 分 bar open 成交、跌破即平、"
             "样本末日 15:00 强平；**两种时态本就不该逐位对齐**——本表与第一关并列读，"
             "第一关测方向判断质量，第二关测盈亏。")
    L.append("")
    L.append("## 口径")
    L.append("")
    L.append(f"- 决策：短名单每条以 q×k 阈值逐档触发（表述≥k 且 多/空 > q×表述，Fraction 严格大于）。")
    L.append(f"- 模式：默认**仅做多**（看空信号只平多仓持币，对齐 live 口径）；"
             f"{'`--allow-short` 双向对照 = 看空 >q×e 时开空仓。' if allow_short else '（本次未开双向；加 `--allow-short` 可得看空开空对照）'}")
    L.append("- 成本 0、全仓 0/1 开关、instant 成交、卖=平多（不做空）。")
    L.append("- 回归护栏：`decide(2/3,3)` 与 `decide=None`（默认）回测**逐项相等**"
             + (" ✓" if guard_ok else " ✗——本报告拒写！"))
    L.append("")
    L.append("## 短名单 PnL 对照")
    L.append("")
    L.append("| " + " | ".join(["排名", "规则", "入选依据", "模式", "累计收益", "年化", "基准买持",
                             "超额", "Sharpe", "MDD", "往返", "胜率", "在场交易日",
                             "平均持仓日", "一关质量N", "一关均分"]) + " |")
    L.append("|" + "|".join(["---:"] * 16) + "|")
    for r in rows:
        mark = " ★" if rules.is_baseline(rules.Fraction(r["q"]), r["k"]) else ""
        basis = "基线" if "基线" in r["basis"] else r["basis"]
        L.append(f"| {r['rank']} | {r['rule']}{mark} | {basis} | {MODE_WORDS[r['mode']]} | "
                 f"**{_fmt_sign(r['total_return'], 2, pct=True)}** | {_fmt_sign(r['annualized'], 1, pct=True)} | "
                 f"{_fmt_sign(r['buyhold_return'], 1, pct=True)} | {_fmt_sign(r['excess'], 2, pct=True)} | "
                 f"{_fmt_sign(r['sharpe'])} | {_fmt(r['mdd'], 1, pct=True)} | {r['n_roundtrips']} | "
                 f"{_fmt(r['win_rate'], 1, pct=True)} | {_fmt(r['in_market_days'], 0, pct=True)} | "
                 f"{_fmt(r['avg_hold_days'], 0)} | {r['quality_n']} | {_fmt_sign(r['quality_avg'])} |")
    L.append("")
    L.append(f"- 基准买持 = 同区间上证 {bh_note}；超额 = 策略累计 − 买持。")
    L.append("- ★ = 基线格（q=2/3, k=3，现行 live 口径）。")
    L.append("")
    L.append("## 逐条读数（第二关视角）")
    L.append("")
    for r in rows:
        if r["mode"] != "long":
            continue
        L.append(f"- **{r['rule']}**（{r['basis']}）：累计 {_fmt_sign(r['total_return'],2,pct=True)}"
                 f"（基准 {_fmt_sign(r['buyhold_return'],1,pct=True)}，超额 {_fmt_sign(r['excess'],2,pct=True)}），"
                 f"Sharpe {_fmt_sign(r['sharpe'])}，MDD {_fmt(r['mdd'],1,pct=True)}，"
                 f"{r['n_roundtrips']} 往返 · 胜率 {_fmt(r['win_rate'],1,pct=True)}。"
                 f"第一关质量 N={r['quality_n']} 均分 {_fmt_sign(r['quality_avg'])}。")
    L.append("")
    L.append("## 读法警示")
    L.append("")
    L.append("- 短名单是**同一份样本**上第一关扫出来再回测，样本内择优回填有过拟合放大；"
             "若某条规则第一关均分高但 PnL 不赚（或反超），说明“质量分≠钱”，以 PnL 为最终裁判时勿只看一关。")
    L.append("- 双向(看空开空) 按指数期货式线性收益计、未计融券成本与保证金，仅为对照。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-short", action="store_true", help="额外跑看空开空双向对照")
    ap.add_argument("--check", action="store_true", help="回归护栏 + 确定性双跑")
    args = ap.parse_args()
    ensure_reports()

    index = pollmod.CorpusIndex()
    res = sweepmod.run_sweep(index)
    picks = sweepmod.pick_shortlist(res["families"])

    # 回归护栏（必跑；不过拒写）
    base_raw, guard_ok, diffs = _guard_default(index)
    if not guard_ok:
        print(f"[confirm] 回归护栏失败：decide(2/3,3) ≠ decide=None → 差异 {diffs[:10]}；拒写报告。")
        return
    bh_note = (f"累计 {_fmt_sign(base_raw['stats']['buyhold_return'], 1, pct=True)} / "
               f"年化 {_fmt_sign(base_raw['stats']['buyhold_annualized'], 1, pct=True)} / "
               f"Sharpe {_fmt_sign(base_raw['stats']['bh_sharpe'])}")

    rows = run_picks(index, picks, allow_short=args.allow_short)
    if args.check:
        rows2 = run_picks(index, picks, allow_short=args.allow_short)
        same = [(r1["rule"], r1["mode"], r1["total_return"], r1["sharpe"], r1["n_roundtrips"])
                == (r2["rule"], r2["mode"], r2["total_return"], r2["sharpe"], r2["n_roundtrips"])
                for r1, r2 in zip(rows, rows2)]
        print("[check] 确定性双跑" + (" ✓" if all(same) else f" ✗ {sum(not s for s in same)} 条不一致"))

    # csv
    csv_rows = [{"rank": r["rank"], "rule": r["rule"], "q": r["q"], "k": r["k"],
                 "basis": r["basis"], "mode": r["mode"],
                 "total_return": round(r["total_return"], 6), "annualized": round(r["annualized"], 6),
                 "buyhold_return": round(r["buyhold_return"], 6), "excess": round(r["excess"], 6),
                 "sharpe": round(r["sharpe"], 4) if r["sharpe"] is not None else None,
                 "mdd": round(r["mdd"], 6), "n_roundtrips": r["n_roundtrips"],
                 "win_rate": round(r["win_rate"], 4) if r["win_rate"] is not None else None,
                 "in_market_days": round(r["in_market_days"], 4),
                 "avg_hold_days": round(r["avg_hold_days"], 2) if r["avg_hold_days"] is not None else None,
                 "n_trades": r["n_trades"], "quality_n": r["quality_n"],
                 "quality_avg": round(r["quality_avg"], 4) if r["quality_avg"] is not None else None}
                for r in rows]
    write_csv(os.path.join(REPORTS_DIR, "combo_confirm.csv"), csv_rows, COLS)

    md = render_confirm(rows, guard_ok, args.allow_short, bh_note)
    with open(os.path.join(REPORTS_DIR, "combo_confirm.md"), "w", encoding="utf-8") as f:
        f.write(md + "\n")

    print(f"[confirm] 回归护栏 {'✓' if guard_ok else '✗'}（{len(picks)} 条短名单 × "
          f"{'2' if args.allow_short else '1'} 态）")
    for r in csv_rows:
        print(f"  {r['rule']:<10} {MODE_WORDS[r['mode']]:<8} 累计 {r['total_return']*100:+6.2f}% "
              f"超额 {r['excess']*100:+6.2f}% sharpe {r['sharpe']:+.2f}")
    print(f"[confirm] 产物 → {REPORTS_DIR}/combo_confirm.{'md,csv'}")


if __name__ == "__main__":
    main()
