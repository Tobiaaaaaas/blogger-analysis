# -*- coding: utf-8 -*-
"""A 股交易日历：判断某日期是否交易日。

优先用 akshare 的官方交易日历（覆盖全年调休，联网拉一次并缓存到 data/trade_calendar.json）；
akshare 不可用/拉取失败时，回退到内置的 2026 节假日规则（weekday 减去法定假日 + 补上班日）。

2026 国务院放假安排（国办发明电〔2025〕7 号）：
  元旦 1/1-1/3（1/4 补班）· 春节 2/15-2/23（2/14、2/28 补班）· 清明 4/4-4/6
  五一 5/1-5/5（5/9 补班）· 端午 6/19-6/21 · 中秋 9/25-9/27（9/20 补班）
  国庆 10/1-10/7（10/10 补班）
A 股在这些"法定节假日"休市；周末补班日（周末上班）为交易日。
"""
import bisect
import json
import os
from datetime import date, datetime, timedelta

from . import paths

# 2026 法定节假日（休市）区间
HOLIDAYS_2026 = [
    ("2026-01-01", "2026-01-03"),  # 元旦
    ("2026-02-15", "2026-02-23"),  # 春节
    ("2026-04-04", "2026-04-06"),  # 清明
    ("2026-05-01", "2026-05-05"),  # 五一
    ("2026-06-19", "2026-06-21"),  # 端午
    ("2026-09-25", "2026-09-27"),  # 中秋
    ("2026-10-01", "2026-10-07"),  # 国庆
]
# 周末补班（周末上班 → 交易日）
MAKEUP_2026 = {
    "2026-01-04", "2026-02-14", "2026-02-28",
    "2026-05-09", "2026-09-20", "2026-10-10",
}


def _load_calendar():
    """从 akshare 拉官方交易日历并缓存；返回 sorted 的 "YYYY-MM-DD" 列表或 None。"""
    if os.path.exists(paths.CALENDAR_CACHE):
        try:
            return json.load(open(paths.CALENDAR_CACHE, encoding="utf-8"))
        except Exception:
            pass
    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        days = sorted(str(d)[:10] for d in df["trade_date"].tolist())
        json.dump(days, open(paths.CALENDAR_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
        return days
    except Exception as e:
        print(f"  [calendar] akshare 交易日历获取失败，回退内置规则: {e}")
        return None


def is_trading_day(d: date) -> bool:
    days = _load_calendar()
    ds = d.strftime("%Y-%m-%d")
    if days:
        return ds in days
    # 回退：周末补班=交易；weekday 且不在节假日=交易
    if ds in MAKEUP_2026:
        return True
    if d.weekday() >= 5:
        return False
    for start, end in HOLIDAYS_2026:
        if start <= ds <= end:
            return False
    return True


def latest_trading_day(d: date) -> date:
    """d 或之前最近一个交易日。"""
    cur = d
    while not is_trading_day(cur):
        cur -= timedelta(days=1)
    return cur


def next_trading_day(d: date) -> date:
    """d 之后最近一个交易日（跨周末/节假自动顺延）。

    解析博主"明天"等以发帖日为基准的相对词（v12 日期锚定）。
    14 天内找不到（理论上不会）则退回 d+1。
    """
    cur = d + timedelta(days=1)
    for _ in range(14):
        if is_trading_day(cur):
            return cur
        cur += timedelta(days=1)
    return d + timedelta(days=1)


def trading_days(d: date, n: int) -> list:
    """截至 d 最近 n 个交易日（含），升序返回 [date, ...]。

    锚点 = latest_trading_day(d)（d 本身若非交易日则取上一交易日）；
    边界示例：trading_days(2026-09-02, 3) → [08-31, 09-01, 09-02]；
              trading_days(2026-09-05(周六), 3) → [09-02, 09-03, 09-04]。
    """
    anchor = latest_trading_day(d)
    days = _load_calendar()
    if days:
        i = bisect.bisect_right(days, anchor.strftime("%Y-%m-%d")) - 1
        if i >= 0:
            lo = max(0, i - n + 1)
            return [date.fromisoformat(days[j]) for j in range(lo, i + 1)]
    # akshare 缓存不可用 → 用 is_trading_day 回退向前数
    out, cur = [], anchor
    while len(out) < n:
        out.append(cur)
        cur -= timedelta(days=1)
        while not is_trading_day(cur):
            cur -= timedelta(days=1)
    return sorted(out)


def n_trading_days_ago(d: date, n: int) -> date:
    """d 往前数第 n 个交易日那天（v14 交易日窗口起点；n=1 → 前一交易日）。

    d 非交易日先取最近交易日 ≤ d 作参考；只数交易日，跨周末/节假日自动跳过。
    边界示例：n_trading_days_ago(2026-09-07(周一), 1) → 2026-09-04(上周五)；
              n_trading_days_ago(2026-09-07(周一), 3) → 2026-09-02(上周三)。
    """
    anchor = latest_trading_day(d)
    days = _load_calendar()
    if days:
        i = bisect.bisect_right(days, anchor.strftime("%Y-%m-%d")) - 1
        j = i - n
        if j >= 0:
            return date.fromisoformat(days[j])
        return date.fromisoformat(days[0])
    # akshare 缓存不可用 → 用 is_trading_day 回退向前数
    cur = anchor
    for _ in range(n):
        cur -= timedelta(days=1)
        while not is_trading_day(cur):
            cur -= timedelta(days=1)
    return cur
