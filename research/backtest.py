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
    """0/1 全仓账本（仿射：nav(px)=base+pos*px）。state ∈ F/L/S：
    L 持多 q 股、S 做空 q 股（q=入市净值*(1-c)/入市价，与多头同名义），F 持币。
    成本 c 每边按当边名义计：开仓扣 c，平仓再扣 c；直接翻转 L↔S = 平旧+开新，扣双边。
    做空按"指数期货式线性收益"计：px 跌 d% → 名义仓赚 d%（非反向杠杆复利），px 涨翻倍 → 名义全损。"""

    def __init__(self, cost):
        self.cost = cost
        self.state = "F"
        self.pos = 0.0     # 多头股数（正）/ 空头股数（负）
        self.base = 1.0    # 初始净值 1.0；nav(px) = base + pos*px

    def nav(self, px):
        return self.base + self.pos * px

    def open_long(self, px):
        q = self.nav(px) * (1 - self.cost) / px
        self.pos = q
        self.base = 0.0
        self.state = "L"

    def open_short(self, px):
        q = self.nav(px) * (1 - self.cost) / px
        self.pos = -q
        self.base = 2 * q * px          # nav(px0) = 2q·px0 − q·px0 = q·px0 = 入市净值*(1-c)
        self.state = "S"

    def close(self, px):
        self.base = self.nav(px) - self.cost * abs(self.pos) * px
        self.pos = 0.0
        self.state = "F"


def clean_days(index, board, start=config.START_DATE, end=config.END_DATE):
    """语料 100% 覆盖的干净决策日（连续区间内逐个判），升序。"""
    out = []
    for d in tc.decision_days(start, end):
        if not index.uncovered(board, d):
            out.append(d)
    return out


def run(board, cost=config.COST_DEFAULT, fill_mode="instant",
        start=config.START_DATE, end=config.END_DATE, index=None, _bars=None,
        allow_short=False):
    """allow_short=False（默认）：看空 >2/3 只平多仓持币（用户锁定口径）；
    allow_short=True：看空 >2/3 改开空仓（对称双向），其余仍持币。"""
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

    for di, day in enumerate(days):
        dstr = day.isoformat()
        for hm in pollmod.tick_times(board):
            dt = _dt.strptime(f"{dstr} {hm}", "%Y-%m-%d %H:%M").replace(tzinfo=BEIJING)
            snap = pollmod.poll_tick(index, board, dt)
            assert snap["clean"], f"{dstr} {hm} {board}: 非干净日进入回测（{snap['gaps']}）"
            px = decision_price(bars, dstr, hm, fill_mode)
            before = book.state
            if snap["trigger_long"]:
                want = "L"
            elif allow_short and snap["trigger_short"]:
                want = "S"
            else:
                want = "F"
            act, parts = "hold", []
            if before != want:
                if before != "F":        # 平掉旧仓（L 卖 / S 买回平空）
                    book.close(px)
                    parts.append("cover" if before == "S" else "sell")
                    open_tr.update(exit_dt=dt, exit_px=px)
                    trades.append(open_tr); open_tr = None
                if want != "F":          # 开新仓（买多 / 开空）
                    if want == "L":
                        book.open_long(px); parts.append("buy")
                    else:
                        book.open_short(px); parts.append("open_short")
                    open_tr = {"side": want, "entry_dt": dt, "entry_px": px,
                               "entry_nav": round(book.nav(px), 6)}
                act = "+".join(parts)
            nav = book.nav(px)
            ticks.append({
                "date": dstr, "time": hm, "dt": dt.isoformat(sep=" "),
                "board": board, "state": book.state, "action": act, "price": px,
                "nav": round(nav, 6), "expressed": snap["expressed"],
                "bull": snap["bull"], "bear": snap["bear"], "mixed": snap["mixed"],
                "bull_frac": round(snap["bull_frac"], 4),
                "trigger": snap["trigger_long"], "trigger_short": snap["trigger_short"],
                "gaps": len(snap["gaps"]),
            })
        # 日终 15:00 盯市（波段末档 14:30 之后仍持币/持仓都按日收标一次）
        close_px = decision_price(bars, dstr, "15:00", "instant")
        nav_close = book.nav(close_px)
        daily_series.append((dstr, nav_close, book.state))
    # 样本末日强制平仓（若有持仓）@ 当日 15:00 收盘
    if book.state != "F":
        px = close_px
        book.close(px)
        open_tr.update(exit_dt=_dt.strptime(f"{d1} 15:00", "%Y-%m-%d %H:%M").replace(tzinfo=BEIJING),
                       exit_px=px)
        trades.append(open_tr); open_tr = None
    final_nav = book.nav(close_px)

    stats = compute_stats(board, d0, d1, days, daily_series, trades, ticks,
                          first_open=daily_all[d0]["开盘"],
                          last_close=d1_close, final_nav=final_nav,
                          allow_short=allow_short)
    return {"board": board, "cost": cost, "fill_mode": fill_mode,
            "allow_short": allow_short,
            "start": d0, "end": d1, "n_days": len(days),
            "stats": stats, "daily": daily_series, "trades": trades, "ticks": ticks}


def compute_stats(board, d0, d1, days, daily_series, trades, ticks,
                  first_open, last_close, final_nav, allow_short=False):
    def _signed_mv(t):
        """往返的方向性毛收益：多 = 出/入−1；空 = 1−出/入（price 跌则正）。"""
        g = t["exit_px"] / t["entry_px"] - 1.0
        return g if t.get("side", "L") == "L" else -g

    n_days = len(days)
    navs = [round(n, 6) for _d, n, _s in daily_series]
    navs[-1] = round(final_nav, 6)
    total_ret = final_nav - 1.0
    ann = (final_nav ** (252 / max(n_days, 1)) - 1.0) if final_nav > 0 else -1.0
    bh_ret = last_close / first_open - 1.0
    bh_ann = ((1 + bh_ret) ** (252 / max(n_days, 1)) - 1.0) if bh_ret > -1 else -1.0
    sharpe = _sharpe(navs)
    day_set = {d.isoformat() for d in days}
    closes = [load_daily()[k]["收盘"] for k in sorted(k for k in load_daily() if k in day_set)]  # 按日期升序
    bh_sharpe = _sharpe(closes) if len(closes) >= 2 else float("nan")
    peak, mdd = -1e9, 0.0
    for n in navs:
        peak = max(peak, n)
        mdd = max(mdd, (peak - n) / peak if peak > 0 else 0.0)
    # 状态/交易统计（L 多、S 空、F 持币）
    long_days = sum(1 for _d, _n, st in daily_series if st == "L")
    short_days = sum(1 for _d, _n, st in daily_series if st == "S")
    n_tick = len(ticks)
    long_ticks = sum(1 for t in ticks if t["state"] == "L")
    short_ticks = sum(1 for t in ticks if t["state"] == "S")
    closed = [t for t in trades if "exit_px" in t]
    wins = [t for t in closed if _signed_mv(t) > 0]
    hold_days = [(t["exit_dt"].date() - t["entry_dt"].date()).days for t in closed]
    n_long_rt = sum(1 for t in closed if t.get("side", "L") == "L")
    n_short_rt = sum(1 for t in closed if t.get("side", "L") == "S")
    # 触发/表态分布（仅干净档位）
    trig = sum(1 for t in ticks if t["trigger"])
    trig_s = sum(1 for t in ticks if t.get("trigger_short"))
    ex3 = sum(1 for t in ticks if t["expressed"] >= config.MIN_EXPRESSED)
    avg_expr = sum(t["expressed"] for t in ticks) / n_tick if n_tick else 0
    avg_bull_trig = (sum(t["bull_frac"] for t in ticks if t["trigger"]) / trig) if trig else 0
    avg_bear_trig = (sum(1 - t["bull_frac"] for t in ticks if t.get("trigger_short")) / trig_s) if trig_s else 0
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
        "n_days": n_days, "long_days": long_days, "short_days": short_days,
        "in_market_days": (long_days + short_days) / n_days,
        "n_ticks": n_tick, "long_ticks": long_ticks, "short_ticks": short_ticks,
        "in_market_ticks": (long_ticks + short_ticks) / n_tick,
        "n_trades": len(trades), "n_roundtrips": len(closed),
        "n_long_rt": n_long_rt, "n_short_rt": n_short_rt,
        "win_rate": len(wins) / len(closed) if closed else float("nan"),
        "avg_trade_move": (sum(_signed_mv(t) for t in closed) / len(closed)) if closed else float("nan"),
        "avg_hold_days": sum(hold_days) / len(hold_days) if hold_days else float("nan"),
        "trigger_ticks": trig, "trigger_short_ticks": trig_s,
        "trig_per_day": trig / max(n_days, 1),
        "trig_short_per_day": trig_s / max(n_days, 1),
        "expressed_ge3_ticks": ex3,
        "avg_expressed": avg_expr,
        "avg_bull_frac_on_trig": avg_bull_trig,
        "avg_bear_frac_on_trig_s": avg_bear_trig,
        "allow_short": allow_short,
        "monthly": monthly,
    }
