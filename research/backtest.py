# -*- coding: utf-8 -*-
"""research/backtest.py — 板块信号 >2/3 逐档跟随回测（状态机 + 净值/指标）。

规则（用户锁定口径，勿擅自放宽）：
  · 板块信号 = poll_tick 的板块快照（交易日窗口 + 每博主最新且目标未过 + mixed 不计数 +
    语料覆盖缺口成员不计——见 poll.py docstring）。
  · 触发 = 表态者(多+空) ≥ MIN_EXPRESSED 且 多/(多+空) > 2/3（严格）→ 持多；否则空仓。
  · 决策频率 = 逐档跟随：short 10 档/日、swing 3 档/日；每档以「当时已发帖」判定。
  · 成交 = 决策时刻价（30 分 bar 起点 = bar open 成交，时段末档 = 该档 close）。
  · 卖 = 平多仓持币（不做空）；仓位 0/1 全仓开关；跌破阈值即平。
  · 费率 --cost 每边（默认 0）。
  · 样本 = 语料 100% 覆盖的干净决策日区间（right-edge 漏抽成员当日整档剔除）。

输出：{board, 统计 dict, daily 序列, trades[], ticks[]}。纯计算、无副作用（写报告在别处）。
"""
import datetime
import math
from datetime import date, datetime as _dt

from . import config
from . import poll as pollmod
from . import trading_cal as tc

BEIJING = config.BEIJING_TZ
_LABEL_NEXT = {"09:30": "10:00", "10:00": "10:30", "10:30": "11:00", "11:00": "11:30",
               "11:30": None, "13:00": "13:30", "13:30": "14:00", "14:00": "14:30",
               "14:30": "15:00", "15:00": None}


def load_intraday():
    """intraday 30 分 bar → {(date_str, label): bar}; 另存每交易日 label 列表。"""
    import json
    idx = {}
    labels = {}
    data = json.load(open(config.INTRADAY_FILE, encoding="utf-8"))["bars"]
    for b in data:
        d, lab = b["time"].split(" ")
        idx[(d, lab)] = b
        labels.setdefault(d, []).append(lab)
    return idx, labels


def load_daily():
    """上证日线 → {date_str: {open, close}}。"""
    import json
    data = json.load(open(config.DAILY_FILE, encoding="utf-8"))[config.IDX_DEFAULT]
    return {r["日期"]: r for r in data}


def bar_price(bars, day, label, field):
    b = bars.get((day, label))
    if b is None:
        raise KeyError(f"缺 intraday bar: {day} {label}")
    return b[field]


def decision_price(bars, day, hm, mode="instant"):
    """决策档位时刻 hm 的成交/盯市价。

    bar.time = 区间**终点**：bar "10:00" 覆盖 09:30–10:00，其 open = 09:30 价。
    · instant（默认）：在决策时刻成交——时刻是 bar 起点 → 用该 bar open
      （09:30/13:00 开盘用下一 bar open；10:00..14:30 → bar[hm+30].open）；
      时刻是时段终点（11:30 午收 / 15:00 日收，无后继 bar）→ 用该档 close。
    · delayed（敏感性对照）：成交拖后 30 分钟（决策档其后那根 bar 的 close）；
      11:30/15:00 无后继 bar → 回落 instant。
    """
    nxt = _LABEL_NEXT.get(hm)
    if mode == "delayed" and nxt is not None:
        return bar_price(bars, day, nxt, "close")
    if hm in ("09:30", "13:00"):
        return bar_price(bars, day, _LABEL_NEXT[hm], "open")
    if nxt is None:                     # 11:30 / 15:00 时段终
        return bar_price(bars, day, hm, "close")
    return bar_price(bars, day, nxt, "open")


def _sharpe(navs):
    """日净值序列的年度化夏普（rf=0）：日均收益/样本波动 * sqrt(252)。"""
    rs = [a / b - 1.0 for b, a in zip(navs[:-1], navs[1:])]
    n = len(rs)
    if n < 2:
        return float("nan")
    mu = sum(rs) / n
    var = sum((x - mu) ** 2 for x in rs) / (n - 1)
    sd = var ** 0.5
    return mu / sd * math.sqrt(252) if sd > 0 else float("nan")


class _Book:
    """0/1 全仓账本：flat → cash=nav；long → shares=nav/(1-c)/px。"""

    def __init__(self, cost):
        self.cost = cost
        self.state = "F"                 # F flat / L long
        self.shares = 0.0
        self.cash = 1.0                  # 初始 1.0

    def nav(self, px):
        return self.cash if self.state == "F" else self.shares * px

    def buy(self, px):
        self.shares = self.cash * (1 - self.cost) / px
        self.cash = 0.0
        self.state = "L"

    def sell(self, px):
        self.cash = self.shares * px * (1 - self.cost)
        self.shares = 0.0
        self.state = "F"


def clean_days(index, board, start=config.START_DATE, end=config.END_DATE):
    """语料 100% 覆盖的干净决策日（连续区间内逐个判），升序。"""
    out = []
    for d in tc.decision_days(start, end):
        if not index.uncovered(board, d):
            out.append(d)
    return out


def run(board, cost=config.COST_DEFAULT, fill_mode="instant",
        start=config.START_DATE, end=config.END_DATE, index=None, _bars=None):
    index = index or pollmod.CorpusIndex()
    days = clean_days(index, board, start, end)
    if not days:
        raise ValueError(f"{board}: 干净决策日为空（语料覆盖不足）")
    bars, _lbl = (_bars if _bars is not None else load_intraday())
    daily_all = load_daily()
    d0, d1 = days[0].isoformat(), days[-1].isoformat()
    d1_close = daily_all[d1]["收盘"]

    book = _Book(cost)
    ticks, trades = [], []
    daily_series = []                    # (date, day-end nav @15:00, state_at_end)
    open_tr = None                       # 未平仓记录
    long_ticks = long_days = 0

    for di, day in enumerate(days):
        dstr = day.isoformat()
        day_open = daily_all[dstr]["开盘"] if not di else None   # 仅首日用
        first = (di == 0)
        for hm in pollmod.tick_times(board):
            dt = _dt.strptime(f"{dstr} {hm}", "%Y-%m-%d %H:%M").replace(tzinfo=BEIJING)
            snap = pollmod.poll_tick(index, board, dt)
            assert snap["clean"], f"{dstr} {hm} {board}: 非干净日进入回测（{snap['gaps']}）"
            px = decision_price(bars, dstr, hm, fill_mode)
            before = book.state
            act = "hold"
            want_long = snap["trigger_long"]
            if before == "F" and want_long:
                book.buy(px); act = "buy"
                open_tr = {"entry_dt": dt, "entry_px": px, "entry_nav": book.nav(px)}
            elif before == "L" and not want_long:
                book.sell(px); act = "sell"
                open_tr.update(exit_dt=dt, exit_px=px)
                trades.append(open_tr); open_tr = None
            nav = book.nav(px)
            long_ticks += (book.state == "L")
            ticks.append({
                "date": dstr, "time": hm, "dt": dt.isoformat(sep=" "),
                "board": board, "state": book.state, "action": act, "price": px,
                "nav": round(nav, 6), "expressed": snap["expressed"],
                "bull": snap["bull"], "bear": snap["bear"], "mixed": snap["mixed"],
                "bull_frac": round(snap["bull_frac"], 4),
                "trigger": snap["trigger_long"], "gaps": len(snap["gaps"]),
            })
        # 日终 15:00 盯市（波段末档 14:30 之后仍持币/持仓都按日收标一次）
        close_px = decision_price(bars, dstr, "15:00", "instant")
        nav_close = book.nav(close_px)
        daily_series.append((dstr, nav_close, book.state))
        if book.state == "L":
            long_days += 1
    # 样本末日强制平仓（若有持仓）@ 当日 15:00 收盘
    if book.state == "L":
        px = close_px
        book.sell(px)
        open_tr.update(exit_dt=_dt.strptime(f"{d1} 15:00", "%Y-%m-%d %H:%M").replace(tzinfo=BEIJING),
                       exit_px=px)
        trades.append(open_tr); open_tr = None
    final_nav = book.nav(close_px)

    stats = compute_stats(board, d0, d1, days, daily_series, trades, ticks,
                          long_days, long_ticks, first_open=daily_all[d0]["开盘"],
                          last_close=d1_close, final_nav=final_nav)
    return {"board": board, "cost": cost, "fill_mode": fill_mode,
            "start": d0, "end": d1, "n_days": len(days),
            "stats": stats, "daily": daily_series, "trades": trades, "ticks": ticks}


def compute_stats(board, d0, d1, days, daily_series, trades, ticks,
                  long_days, long_ticks, first_open, last_close, final_nav):
    n_days = len(days)
    # 日净值（含末日强平后的最终值）
    navs = [round(n, 6) for _d, n, _s in daily_series]
    navs[-1] = round(final_nav, 6)
    total_ret = final_nav - 1.0
    ann = (final_nav ** (252 / max(n_days, 1)) - 1.0) if final_nav > 0 else -1.0
    # buy & hold 基准：首交易日开盘买入 → 末交易日收盘
    bh_ret = last_close / first_open - 1.0
    bh_ann = ((1 + bh_ret) ** (252 / max(n_days, 1)) - 1.0) if bh_ret > -1 else -1.0
    # 夏普（日净值，rf=0，年化 252）：策略 vs 买持（同一决策日网格的上证日收盘）
    sharpe = _sharpe(navs)
    day_set = {d.isoformat() for d in days}
    closes = [load_daily()[k]["收盘"] for k in sorted(k for k in load_daily() if k in day_set)]  # 按日期升序
    bh_sharpe = _sharpe(closes) if len(closes) >= 2 else float("nan")
    # 最大回撤（日净值序列）
    peak, mdd = -1e9, 0.0
    for n in navs:
        peak = max(peak, n)
        mdd = max(mdd, (peak - n) / peak if peak > 0 else 0.0)
    # 交易统计
    entries = list(trades)
    closed = [t for t in trades if "exit_px" in t]
    wins = [t for t in closed if t["exit_px"] > t["entry_px"]]
    hold_days = [(t["exit_dt"].date() - t["entry_dt"].date()).days for t in closed]
    # 触发/表态分布（仅干净档位）
    n_tick = len(ticks)
    trig = sum(1 for t in ticks if t["trigger"])
    ex3 = sum(1 for t in ticks if t["expressed"] >= config.MIN_EXPRESSED)
    avg_expr = sum(t["expressed"] for t in ticks) / n_tick if n_tick else 0
    avg_bull_trig = (sum(t["bull_frac"] for t in ticks if t["trigger"]) / trig) if trig else 0
    # 逐月收益（首日基 = 初始净值 1.0）
    monthly = {}
    prev = 1.0
    for i, (dstr, _n, _s) in enumerate(daily_series):
        m = dstr[:7]
        r = navs[i] / prev
        prev = navs[i]
        monthly[m] = monthly.get(m, 1.0) * r
    for m in monthly:
        monthly[m] = monthly[m] - 1.0
    return {
        "total_return": total_ret, "annualized": ann,
        "buyhold_return": bh_ret, "buyhold_annualized": bh_ann,
        "excess_vs_buyhold": total_ret - bh_ret,
        "max_drawdown": mdd,
        "sharpe": sharpe, "bh_sharpe": bh_sharpe,
        "n_days": n_days, "long_days": long_days, "in_market_days": long_days / n_days,
        "n_ticks": n_tick, "long_ticks": long_ticks, "in_market_ticks": long_ticks / n_tick,
        "n_trades": len(entries), "n_roundtrips": len(closed),
        "win_rate": len(wins) / len(closed) if closed else float("nan"),
        "avg_trade_move": (sum(t["exit_px"] / t["entry_px"] - 1 for t in closed) / len(closed)) if closed else float("nan"),
        "avg_hold_days": sum(hold_days) / len(hold_days) if hold_days else float("nan"),
        "trigger_ticks": trig, "trig_per_day": trig / max(n_days, 1),
        "expressed_ge3_ticks": ex3,
        "avg_expressed": avg_expr,
        "avg_bull_frac_on_trig": avg_bull_trig,
        "monthly": monthly,
    }
