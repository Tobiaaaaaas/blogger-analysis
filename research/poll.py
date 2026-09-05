# -*- coding: utf-8 -*-
"""research/poll.py — 逐档表态重建：对给定决策时刻 dt，按快照口径
（交易日窗口 + 窗口内每博主**时间序最新一条板块候选**且目标未过）重建「该板块当时会推什么」的计数。

输出 per-tick：{dt, board, expressed, bull, bear, bull_frac, bear_frac,
               trigger_long, trigger_short, votes:[...], mixed:0, mixed_votes:[]}。
纯函数、可复用、无副作用。

口径（与滞回策略规格 .claude/skills/analyze-blogger/Swing_Timing.md §1 一致，勿擅自改动）：
  窗口 = [n_trading_days_ago(决策日, N[board]) 的 00:00, dt)（wall-clock 内含周末帖）
  候选 = 板块周期归属匹配（short=today/t1；swing=其余含 long）
         且 **swing 剔除 spec=long**（长线/年度目标不算波段观点；short 板 long 天然不匹配）
         且 idx 匹配（默认上证指数）且 pub ∈ 窗口
  有效 = target 为 None（unscored 恒活）或 target >= 决策日（未过期）
  投票 = 每博主窗口内**时间序最新一条**有效候选 → 一票 d（同 pub 无秒级时间，按语料行序取最后一条）
  —— 单条即票，**无 mixed / 无双向组概念**（v14 早期「最新一组双向并存 → 中性」已取消）；
     mixed/mixed_votes 键保留恒空以兼容下游校验读取。
  计数 = expressed=投票人数、bull=看多人数、bear=看空人数（分母 e = 当日有板块观点的博主数）
  触发 = expressed ≥ MIN_EXPRESSED 且 bull/(bull+bear) > 2/3（严格；见 backtest 默认口径）
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

    每博主投票规则（单条 last，口径 .claude/skills/analyze-blogger/Swing_Timing.md §1「波段观点」）：
      · 窗口内候选 = 该板块 spec 且目标未过且 pub ∈ [窗口起点, dt)；
        swing 另剔除 spec=long（长线不算波段观点；short 板 long 天然不匹配板块）；
      · 取 pub **最新的一条**候选（pub 最大；同 pub 无秒级时间 → 语料行序最后一条）→ 一票 d；
      · 窗口内无候选 → 无观点（不进 expressed）。单条即票 → 构造上无 mixed/双向组。
    """
    d = dt.date()
    wstart = _window_start_ts(d, board)
    votes, gaps = [], []
    gap_set = set(index.uncovered(board, d))          # 窗口未被完整捕获 → 不作表态（避免把漏抽当弃权）
    for blogger in config.PANELS[board]:
        if blogger in gap_set:
            gaps.append(blogger)
            continue
        chosen = None                 # 单条 last：最新一条命中即票
        for r in _lookup(index, blogger, dt, wstart):   # pub 最新 → 最旧；同 pub 行序最后先遇到
            if config.board_of_spec(r["spec"]) != board:
                continue              # today/t1→short；其余（含 long）→swing
            if board == "swing" and r["spec"] == "long":
                continue              # 波段投票候选剔除 long（长线/年度目标不是波段观点）
            if r["_target"] is not None and r["_target"] < d:
                continue              # 目标已过（短：目标日≠今/明；波：目标周已过）
            chosen = r
            break
        if chosen is None:
            continue
        votes.append({
            "blogger": blogger, "d": chosen["d"], "spec": chosen["spec"],
            "target": chosen["target"], "target_txt": chosen["target_txt"],
            "pub": chosen["pub"], "summary": chosen["summary"], "cat": chosen["cat"],
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
        "expressed": expressed, "bull": bull, "bear": bear, "mixed": 0,
        "bull_frac": bull_frac, "bear_frac": bear_frac,
        "trigger_long": expressed >= _MIN and bull > config.THRESHOLD * expressed,
        "trigger_short": expressed >= _MIN and bear > config.THRESHOLD * expressed,
        "votes": votes, "mixed_votes": [],
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
