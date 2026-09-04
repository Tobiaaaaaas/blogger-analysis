# -*- coding: utf-8 -*-
"""research/poll.py — 逐档表态重建：对给定决策时刻 dt，按 v14 简报快照口径
（交易日窗口 + 窗口内每博主最新一条且目标未过）重建「该板块当时会推什么」的计数。

输出 per-tick：{dt, board, expressed, bull, bear, bull_frac, bear_frac,
               trigger_long, trigger_short, votes:[...]}。纯函数、可复用、无副作用。

口径（与 v14 简报一致，勿擅自改动）：
  窗口 = [n_trading_days_ago(决策日, N[board]) 的 00:00, dt)（wall-clock 内含周末帖）
  候选 = 板块周期归属匹配（short=today/t1；swing=其余 scored + unscored=long）
         且 idx 匹配（默认上证指数）且 pub ∈ 窗口
  有效 = target 为 None（long/unscored 恒活）或 target >= 决策日（未过期）
  投票 = 每博主窗口内最新一条（pub 最大）有效候选 → 一票 d
  计数 = expressed=投票人数、bull=看多人数、bear=看空人数
  触发 = expressed ≥ MIN_EXPRESSED 且 bull/(bull+bear) > 2/3（严格）
"""
import bisect
import datetime
from datetime import date, datetime as _dt

from . import config
from . import trading_cal as tc

_MIN = config.MIN_EXPRESSED


class CorpusIndex:
    """一次性加载 research/signals/*.json → 每博主按 pub_ts 升序的信号列表。
    附带语料覆盖元数据（用于判定某决策日窗口是否被 100% 抽取覆盖）：
      ES/LS = 该博主 signals 的最早/最晚 pub 日（= 方向抽取的起/止）
      LP    = data/posts 中该博主的最晚发帖日（= 真实发帖的最晚点，data/posts 全量）

    covered(b, D, board)：窗口 [ws, D) 内博主的表态被语料完整捕获 ⟺
      已入抽取（ES ≤ ws）且 无漏抽取（LP ≤ LS，即抽取追到了最后一帖；或 LS ≥ D，
      即抽取已达决策日，窗口后的帖不影响）。data/posts 缺失 → LP 视为 LS（无漏）。
    注意：方向抽取只记"有方向的帖"；data/posts 中无方向的帖不产生信号属正常，非漏抽。
    """

    def __init__(self):
        import json
        import os
        self.sigs = {}     # blogger → rows（升序）
        self._ts = {}      # blogger → 并行升序 pub_ts 数组（二分用）
        self.ES, self.LS, self.LP = {}, {}, {}
        for blogger in sorted(set(config.PANELS["short"]) | set(config.PANELS["swing"])):
            fp = os.path.join(config.SIGNALS_OUT_DIR, f"{blogger}.json")
            if not os.path.exists(fp):
                continue
            rows = json.load(open(fp, encoding="utf-8"))["signals"]
            rows = [r for r in rows if r["idx"] == config.IDX_DEFAULT]  # idx 口径过滤
            for r in rows:
                r["_ts"] = _parse_pub(r["pub"])
                r["_target"] = date.fromisoformat(r["target"]) if r["target"] else None
            rows.sort(key=lambda r: r["_ts"])
            self.sigs[blogger] = rows
            self._ts[blogger] = [r["_ts"] for r in rows]
            if rows:
                self.ES[blogger] = rows[0]["_ts"].date()
                self.LS[blogger] = rows[-1]["_ts"].date()
            # 真实最晚发帖（data/posts 为 2026 窗口全量爬取；缺失则退化为 LS）
            pf = os.path.join(config.POSTS_DIR, f"{blogger}.json")
            lp = None
            if os.path.exists(pf):
                pd = json.load(open(pf, encoding="utf-8"))
                ps = pd.get("posts", [])
                if ps:
                    lp = max(p.get("publish_date", "")[:10] for p in ps)
            self.LP[blogger] = date.fromisoformat(lp) if lp else self.LS.get(blogger)

    def blogger_names(self, board):
        return config.PANELS[board]

    def uncovered(self, board, d: date):
        """返回某决策日 d 该板块中"窗口存在未被抽取的真实方向帖"的成员名单（空 = 干净日）。

        覆盖语义：语料缺失只可能是**右侧漏抽**——方向抽取止于该博主最近一条信号日 LS，
        而 data/posts 显示其后仍发帖（LP > LS）。此时若其真实帖进入窗口 [ws, d) 即未盖。
          判定：LS < d 且 LP > LS 且 LP ≥ ws → 未盖。
        左侧（该博主进入抽取前的历史帖）不视为漏抽：那是它进入追踪名册之前的时期，
        poll 按其"该时期无信号 → 不表态"自然处理，与当时实盘口径一致（名册分批纳入）。
        保守取向：宁可少计也不把可能的漏抽当弃权；漏抽风险成员的当日整档判不干净。
        """
        ws = tc.n_trading_days_ago(d, config.WINDOW_TRADING_DAYS[board])
        out = []
        for b in config.PANELS[board]:
            ls, lp = self.LS.get(b), self.LP.get(b)
            if ls is None:
                if lp is not None:
                    out.append(b)                       # 有真实帖但无任何方向语料 → 无从判定
                continue
            if lp is None or lp <= ls:
                continue                                # 抽取已追到最后一帖 → 无右侧漏抽
            if ls < d and lp >= ws:
                out.append(b)                           # 抽取止于 LS，其后真实帖可能已入窗口
        return out


def _parse_pub(pub):
    return _dt.strptime(pub, "%Y-%m-%d %H:%M").replace(tzinfo=config.BEIJING_TZ)


def _window_start_ts(decision_date: date, board: str):
    """窗口起点 = 决策日往前数 N 个交易日那天的北京时 00:00。"""
    start_day = tc.n_trading_days_ago(decision_date, config.WINDOW_TRADING_DAYS[board])
    return datetime.datetime.combine(start_day, datetime.time(0, 0)).replace(tzinfo=config.BEIJING_TZ)


def _lookup(index: CorpusIndex, blogger, dt, wstart):
    """取该博主 pub ∈ [wstart, dt) 的信号，从最新（pub 最大）往旧 yield。"""
    rows = index.sigs.get(blogger)
    if not rows:
        return
    i = bisect.bisect_left(index._ts[blogger], dt) - 1   # 严格 < dt
    while i >= 0 and index._ts[blogger][i] >= wstart:
        yield rows[i]
        i -= 1


def poll_tick(index: CorpusIndex, board: str, dt):
    """dt: tz-aware 北京时决策时刻。返回该时刻板块快照 dict。

    每博主投票规则（与 v14 逐条一致）：
      · 窗口内候选 = 该板块 spec + 目标未过（long/unscored 恒活）且 pub ∈ [窗口起点, dt)；
      · 取 pub 最新一组；该组同板同向 → 一票 d；该组同板**双向并存** → 该博主对板块
        立场不明确（如"今天涨/明天跌"、"收上 X 看涨否则跌"），计 mixed（中性），
        不入 expressed / 不多空力量 —— 对齐实时卡"中性不计入多空力量"。
    """
    d = dt.date()
    wstart = _window_start_ts(d, board)
    votes, mixed, gaps = [], [], []
    gap_set = set(index.uncovered(board, d))          # 窗口未被完整捕获 → 不作表态（避免把漏抽当弃权）
    for blogger in config.PANELS[board]:
        if blogger in gap_set:
            gaps.append(blogger)
            continue
        group = []                    # pub 最新一组的有效候选
        for r in _lookup(index, blogger, dt, wstart):
            if config.board_of_spec(r["spec"]) != board:
                continue
            if r["_target"] is not None and r["_target"] < d:
                continue            # 目标已过（短：目标日≠今/明；波：目标周已过）
            if not group:
                group = [r]
            elif r["pub"] == group[0]["pub"]:
                group.append(r)
            else:
                break                # 已越过最新 pub → 不再有同组
        if not group:
            continue
        ds = {r["d"] for r in group}
        if len(ds) > 1:
            mixed.append({
                "blogger": blogger, "d": list(ds), "spec": "/".join(sorted({r["spec"] for r in group})),
                "target": group[0]["target"], "target_txt": group[0]["target_txt"],
                "pub": group[0]["pub"], "summary": group[0]["summary"], "cat": "/".join(sorted({r["cat"] for r in group})),
            })
            continue
        r = group[0]
        votes.append({
            "blogger": blogger, "d": r["d"], "spec": r["spec"],
            "target": r["target"], "target_txt": r["target_txt"],
            "pub": r["pub"], "summary": r["summary"], "cat": r["cat"],
        })
    bull = sum(1 for v in votes if v["d"] == 1)
    bear = sum(1 for v in votes if v["d"] == -1)
    expressed = bull + bear
    bull_frac = (bull / expressed) if expressed else 0.0
    bear_frac = (bear / expressed) if expressed else 0.0
    return {
        "dt": dt, "date": d.isoformat(), "time": dt.strftime("%H:%M"),
        "board": board,
        "clean": not gaps, "gaps": gaps,
        "expressed": expressed, "bull": bull, "bear": bear, "mixed": len(mixed),
        "bull_frac": bull_frac, "bear_frac": bear_frac,
        "trigger_long": expressed >= _MIN and bull > config.THRESHOLD * expressed,
        "trigger_short": expressed >= _MIN and bear > config.THRESHOLD * expressed,
        "votes": votes, "mixed_votes": mixed,
    }


# ---- 决策网格：决策日的档位时刻序列（short 10 档 / swing 3 档）----
def tick_times(board):
    return config.GRID_TICKS[board]


def decision_datetimes(board, start_date, end_date):
    """[start,end] 区间内所有决策时刻（交易日 + 板块档位），北京时 tz-aware，升序。"""
    out = []
    for d in tc.decision_days(start_date, end_date):
        for hm in tick_times(board):
            hh, mm = int(hm[:2]), int(hm[3:5])
            out.append(datetime.datetime.combine(d, datetime.time(hh, mm)).replace(tzinfo=config.BEIJING_TZ))
    return out
