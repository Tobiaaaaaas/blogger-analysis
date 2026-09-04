# -*- coding: utf-8 -*-
"""research/run_backtest.py — 回测 CLI。

用法：
  python -m research.run_backtest --board short|swing|both [--cost 0.0005] [--fill instant|delayed]
输出：research/reports/{board}_report.md + {board}_ticks.csv + {board}_trades.csv（--fill delayed 加后缀）。

不含任何外部调用/密钥；语料与行情均离线。
"""
import argparse
import csv
import os
from collections import OrderedDict

from . import config
from . import backtest as bt
from . import poll as pollmod
from . import trading_cal as tc


def _fmt_pct(x, nd=2):
    return "—" if x is None or x != x else f"{x * 100:.{nd}f}%"


def _fmt_num(x, nd=2):
    """非百分比量（如夏普）纯数字格式化。"""
    return "—" if x is None or x != x else f"{x:.{nd}f}"


def write_csv(path, rows, fields):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})


def _bh_monthly(start, end):
    """基准逐月收益：样本区间内上证日收盘；首段基 = 区间首交易日开盘。"""
    import json
    data = json.load(open(config.DAILY_FILE, encoding="utf-8"))[config.IDX_DEFAULT]
    days = [r["日期"] for r in data if start <= r["日期"] <= end]
    by_d = {r["日期"]: r for r in data}
    first_open = by_d[days[0]]["开盘"]
    m_end = OrderedDict()
    for ds in days:
        m_end[ds[:7]] = by_d[ds]["收盘"]
    out = OrderedDict()
    prev = first_open
    for m, c in m_end.items():
        out[m] = c / prev - 1.0
        prev = c
    return out


def build_report(res):
    s = res["stats"]; b = res["board"]; bw = config.BOARD_WORD[b]
    ticks_txt = f"{config.GRID_TICKS['short'][0]}~{config.GRID_TICKS['short'][-1]} 十档" if b == "short" \
        else "/".join(config.GRID_TICKS['swing']) + " 三档"
    L = []
    L.append(f"# {bw}板块（{b}）信号 >2/3 逐档跟随回测")
    L.append("")
    L.append(f"- 样本：{res['start']} → {res['end']}（{res['n_days']} 交易日；语料 100% 覆盖干净日，"
             f"回看窗口 {config.WINDOW_TRADING_DAYS[b]} 个交易日）")
    L.append(f"- 触发：窗口内表态者(多+空) ≥{config.MIN_EXPRESSED} 且 看多占比 >2/3 → 持多；跌破阈值/占比不够 → 空仓"
             f"（卖出=平多仓持币，不做空）。每档复查，{ticks_txt}。")
    L.append(f"- 成交：决策档 30 分 bar open（{res['fill_mode']} 模式）；费率每边 {res['cost']}。"
             f"目标未过才计票；同一博主同板双向并存按 mixed(中性) 不计数；语料覆盖缺口成员不计数。")
    L.append("")
    L.append("## 净值概览（基准 = 同期买入持有上证）")
    L.append("")
    L.append("| 指标 | 策略 | 基准 |")
    L.append("|---|---:|---:|")
    L.append(f"| 累计收益 | {_fmt_pct(s['total_return'])} | {_fmt_pct(s['buyhold_return'])} |")
    L.append(f"| 年化(252日) | {_fmt_pct(s['annualized'])} | {_fmt_pct(s['buyhold_annualized'])} |")
    L.append(f"| 超额 | {_fmt_pct(s['excess_vs_buyhold'])} | — |")
    L.append(f"| 夏普(日,252) | {_fmt_num(s['sharpe'])} | {_fmt_num(s['bh_sharpe'])} |")
    L.append(f"| 最大回撤(日净值) | {_fmt_pct(s['max_drawdown'])} | — |")
    L.append("")
    L.append("## 仓位与交易")
    L.append("")
    L.append(f"- 在市天数：{s['long_days']}/{s['n_days']}（{_fmt_pct(s['in_market_days'],1)}）；"
             f"在市档位占比 {_fmt_pct(s['in_market_ticks'],1)}")
    L.append(f"- 触发持多档位 {s['trigger_ticks']}/{s['n_ticks']}（{_fmt_pct(s['trigger_ticks'] / max(s['n_ticks'],1), 1)}），"
             f"日均 {s['trig_per_day']:.2f} 档；触发时平均多头占比 {s['avg_bull_frac_on_trig']:.2f}")
    L.append(f"- 完整往返 {s['n_roundtrips']} 次（总开仓 {s['n_trades']}），胜率(出场价>入场价) {_fmt_pct(s['win_rate'])}，"
             f"平均单次涨跌 {_fmt_pct(s['avg_trade_move'])}，平均持仓 {s['avg_hold_days']:.1f} 交易日")
    L.append(f"- 表态均值 {s['avg_expressed']:.1f} 人；表态 ≥3 档位 {s['expressed_ge3_ticks']}/{s['n_ticks']}。")
    L.append("")
    L.append("## 逐月收益")
    L.append("")
    L.append("| 月份 | 策略 | 基准 |")
    L.append("|---|---:|---:|")
    bh_m = _bh_monthly(res["start"], res["end"])
    for m in sorted(s["monthly"]):
        L.append(f"| {m} | {_fmt_pct(s['monthly'][m])} | {_fmt_pct(bh_m.get(m))} |")
    L.append("")
    L.append("## 完整往返交易明细")
    L.append("")
    L.append("| # | 入场 | 出场 | 持仓(交易日) | 指数变动 |")
    L.append("|---|---:|---:|---:|---:|")
    for i, t in enumerate(res["trades"], 1):
        if "exit_px" not in t:
            continue
        hold = (t["exit_dt"].date() - t["entry_dt"].date()).days
        mv = t["exit_px"] / t["entry_px"] - 1
        L.append(f"| {i} | {t['entry_dt'].strftime('%Y-%m-%d %H:%M')} | "
                 f"{t['exit_dt'].strftime('%Y-%m-%d %H:%M')} | {hold} | {_fmt_pct(mv)} |")
    if not any("exit_px" in t for t in res["trades"]):
        L.append("（无完整往返）")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", default="both", choices=["short", "swing", "both"])
    ap.add_argument("--cost", type=float, default=config.COST_DEFAULT)
    ap.add_argument("--fill", default="instant", choices=["instant", "delayed"])
    ap.add_argument("--start", default=config.START_DATE)
    ap.add_argument("--end", default=config.END_DATE)
    args = ap.parse_args()

    config.ensure_dirs()
    idx = pollmod.CorpusIndex()
    boards = ["short", "swing"] if args.board == "both" else [args.board]
    for b in boards:
        res = bt.run(b, cost=args.cost, fill_mode=args.fill, start=args.start, end=args.end, index=idx)
        sfx = "" if args.fill == "instant" else f"_{args.fill}"
        with open(os.path.join(config.REPORTS_DIR, f"{b}_report{sfx}.md"), "w", encoding="utf-8") as f:
            f.write(build_report(res))
        write_csv(os.path.join(config.REPORTS_DIR, f"{b}_ticks{sfx}.csv"), res["ticks"],
                  ["dt", "board", "state", "action", "price", "nav", "expressed", "bull",
                   "bear", "mixed", "bull_frac", "trigger", "gaps"])
        write_csv(os.path.join(config.REPORTS_DIR, f"{b}_trades{sfx}.csv"), res["trades"],
                  ["entry_dt", "entry_px", "exit_dt", "exit_px"])
        st = res["stats"]
        print(f"[{b}] {res['start']}→{res['end']} {res['n_days']}日 | "
              f"策略 {st['total_return']*100:+.2f}%  基准 {st['buyhold_return']*100:+.2f}% "
              f"(超额 {st['excess_vs_buyhold']*100:+.2f}%) | 触发{st['trigger_ticks']}档 "
              f"往返{st['n_roundtrips']} 胜率{st['win_rate']*100:.0f}% MDD {st['max_drawdown']*100:.1f}%")

if __name__ == "__main__":
    main()
