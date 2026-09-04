# -*- coding: utf-8 -*-
"""research 交易日历工具：复用以简报缓存的官方交易日历（1990→2026-12-31）。

与父仓库 eval/run_direction.py 的 endpoint_of 语义对齐（tN = 信号日之后第 N 个交易日、
week = 本周最后交易日、nweek/nweek_first = 下周最后/第一个、month/nmonth = 当月/下月最后、
d: 顺延）。简报缓存按天全量覆盖到 2026 年末，供语料把 spec 解析成绝对目标日；
若缓存缺失则回退到简报 calendar 的内置 2026 规则。
"""
import os
import datetime
from datetime import date, timedelta

from briefing.scripts import paths as _paths
from briefing.scripts import calendar as _cal


def _load_days():
    """sorted ["YYYY-MM-DD", ...]，来自简报缓存 trade_calendar.json；缺失回退内置规则。"""
    p = _paths.CALENDAR_CACHE
    if os.path.exists(p):
        try:
            import json
            days = json.load(open(p, encoding="utf-8"))
            if days:
                return sorted(days)
        except Exception:
            pass
    # 回退：简报 calendar 内置 2026 规则（用于 2026 决策窗；缓存正常时应永不命中）
    out = []
    d = date(2024, 6, 1)
    end = date(2026, 12, 31)
    while d <= end:
        if _cal.is_trading_day(d):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return sorted(out)


DAYS = _load_days()          # sorted "YYYY-MM-DD"
DAY_SET = set(DAYS)
_DAY_DATE = [datetime.datetime.strptime(d, "%Y-%m-%d").date() for d in DAYS]


def is_trading_day(d) -> bool:
    s = d.isoformat() if isinstance(d, date) else str(d)[:10]
    return s in DAY_SET


def next_trading_day(d) -> date:
    """d 之后最近一个交易日（不含 d）。"""
    s = d.isoformat() if isinstance(d, date) else str(d)[:10]
    i = _bisect_right(DAYS, s)
    return _DAY_DATE[i] if i < len(DAYS) else None


def prev_trading_day(d) -> date:
    """d 之前最近一个交易日（不含 d）。"""
    s = d.isoformat() if isinstance(d, date) else str(d)[:10]
    i = _bisect_left(DAYS, s) - 1
    return _DAY_DATE[i] if i >= 0 else None


def n_trading_days_ago(d: date, n: int) -> date:
    """d 往前数第 n 个交易日那天（n=1 → 前一交易日）；d 非交易日先取最近交易日 ≤ d。"""
    s = d.isoformat()
    i = _bisect_right(DAYS, s) - 1      # ≤ d 的最近交易日下标
    j = i - n
    if j < 0:
        return _DAY_DATE[0]
    return _DAY_DATE[j]


def _nth_from(d: date, n: int) -> date:
    """d 之后第 n 个交易日（d 不计；n=1 → 下一交易日）。"""
    cur = d
    for _ in range(n):
        nxt = next_trading_day(cur)
        if nxt is None:
            return None
        cur = nxt
    return cur


def _week_bounds(d: date) -> tuple:
    """d 所在 ISO 周的 [该周最早交易日, 该周最晚交易日]（可能 None，若整周无交易日）。"""
    y, w, _ = d.isocalendar()
    inw = [dt for dt in _DAY_DATE if dt.isocalendar()[:2] == (y, w)]
    return (inw[0], inw[-1]) if inw else (None, None)


def endpoint_of(pub_date, spec):
    """spec → 绝对目标日（验证终点，语义与 run_direction.endpoint_of 对齐）。

    pub_date: "YYYY-MM-DD" 或 date。返回 date 或 None（long / 无法推算 / 超日历）。
    """
    d = pub_date if isinstance(pub_date, date) else date.fromisoformat(str(pub_date)[:10])
    if spec == "long":
        return None
    if spec == "today":
        # 信号日当天（非交易日顺延下一交易日；但非交易日"今天"信号在语料构建时即剔除）
        return d if d.isoformat() in DAY_SET else next_trading_day(d)
    if spec.startswith("t") and spec[1:].isdigit():
        return _nth_from(d, int(spec[1:]))
    if spec == "week":
        base = d if d.isoformat() in DAY_SET else next_trading_day(d)
        if base is None:
            return None
        _, last = _week_bounds(base)
        return last
    if spec in ("nweek", "nweek_first"):
        nd = d + timedelta(days=7)
        first, last = _week_bounds(nd)
        return first if spec == "nweek_first" else last
    if spec == "month":
        mdays = [dt for dt in _DAY_DATE if dt.strftime("%Y-%m") == d.strftime("%Y-%m")]
        return mdays[-1] if mdays else None
    if spec == "nmonth":
        y, m = d.year, d.month
        m += 1
        if m > 12:
            y, m = y + 1, 1
        mdays = [dt for dt in _DAY_DATE if (dt.year, dt.month) == (y, m)]
        return mdays[-1] if mdays else None
    if spec.startswith("d:"):
        tgt = date.fromisoformat(spec[2:])
        return tgt if tgt.isoformat() in DAY_SET else next_trading_day(tgt)
    return None


def _bisect_right(a, x):
    lo, hi = 0, len(a)
    while lo < hi:
        mid = (lo + hi) // 2
        if x < a[mid]:
            hi = mid
        else:
            lo = mid + 1
    return lo


def _bisect_left(a, x):
    lo, hi = 0, len(a)
    while lo < hi:
        mid = (lo + hi) // 2
        if a[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


def decision_days(start_date, end_date):
    """决策交易日序列（含 start/end 边界）——只用缓存里真实存在的交易日。"""
    s, e = start_date.isoformat() if isinstance(start_date, date) else str(start_date)[:10], \
           end_date.isoformat() if isinstance(end_date, date) else str(end_date)[:10]
    out = []
    i = _bisect_left(DAYS, s)
    while i < len(DAYS) and DAYS[i] <= e:
        out.append(_DAY_DATE[i])
        i += 1
    return out
