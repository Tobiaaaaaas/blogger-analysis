# -*- coding: utf-8 -*-
"""research/quality/engine.py — 综合波段信号·每日一票打分（博主评价口径）。

口径（用户 AskUserQuestion 逐项锁定，勿改）：
  · 只评 swing 波段板，不看超短。
  · 每日一票：每"干净交易日"（语料 100% 覆盖，= backtest.clean_days 同网格）取 14:30 快照
    定调 —— 表态者(多+空)≥3 且 看多占比 >2/3 → d=+1；对称地 看空>2/3 → d=−1；都不达 → 当日无信号。
  · 参考价 = 决策日上证 15:00 收盘（14:30 定调、收盘成交——用户选定口径）。
  · 验证终点 = 决策日后第 5 个交易日（t5）收盘；score = d×(ep收盘/参考价−1)×100。
逐信号行键对齐 run_direction.calc 打分行（含 d/score 供聚合直接复用）。

聚合走 research/quality/_run_direction 的同源 acc/avg/vol/sharpe，与 30 博主行同一套函数，
本模块的 summarize_metrics 即全单元共用的行→指标聚合器。
"""
import datetime

from .. import config
from .. import poll as pollmod
from .. import trading_cal as tc
from ..backtest.backtest import clean_days, load_daily
from . import _run_direction as dr

BEIJING = config.BEIJING_TZ
BOARD = "swing"
SNAP_TICK = "14:30"
PERIOD_TEXT = "+5交易日"          # 综合信号固定验证周期（展示用）
STRONG_SHARE = 0.80              # 强度代理线：方向占比 ≥0.80 → 强（≥4/5），否则 中

_DAILY = None                    # {date_str: {"收盘": ...}} 惰性缓存


def _daily():
    global _DAILY
    if _DAILY is None:
        _DAILY = load_daily()
    return _DAILY


def _snap_at(index, day, hm):
    dt = datetime.datetime.strptime(f"{day.isoformat()} {hm}",
                                    "%Y-%m-%d %H:%M").replace(tzinfo=BEIJING)
    snap = pollmod.poll_tick(index, BOARD, dt)
    assert snap["clean"], f"{day} {BOARD}: 非干净日进入取样（{snap.get('gaps')}）"
    return snap


def signal_rows(index=None, start=config.START_DATE, end=config.END_DATE):
    """干净日逐日 14:30 采样 → 综合信号行列表（有 score 的为计分信号；待验证不入聚合）。

    行键（对齐 run_direction 打分行，聚合只需 d/score）：
      date | content | direction(±1) | dir_word | strength | period | idx | ref | ep
      | epc | ret | score | note
    """
    index = index or pollmod.CorpusIndex()
    daily = _daily()
    rows = []
    for day in clean_days(index, BOARD, start, end):
        snap = _snap_at(index, day, SNAP_TICK)
        if snap["trigger_long"]:
            d = 1
        elif snap["trigger_short"]:
            d = -1
        else:
            continue                       # 该日无 2/3 表态 → 无信号
        ds = day.isoformat()
        ref = daily.get(ds, {}).get("收盘")      # 决策日 15:00 日线收盘
        if ref is None:
            continue                       # 防御：决策日无行情（干净日不应发生）
        ep = tc.endpoint_of(ds, "t5")
        ep_s = ep.isoformat() if ep else None
        epc = daily.get(ep_s, {}).get("收盘") if ep_s else None
        note = "待验证" if epc is None else ""
        ret = None
        score = None
        if epc is not None:
            ret = epc / ref - 1.0
            score = round(d * ret * 100.0, 2)
        expressed = snap["expressed"]
        bull, bear = snap["bull"], snap["bear"]
        share = (bull if d == 1 else bear) / expressed if expressed else 0.0
        rows.append({
            "date": ds,
            "content": f"看多{bull}/看空{bear}·表态{expressed}人（方向占比{share:.0%}）",
            "direction": d,
            "dir_word": "↑ 看多" if d == 1 else "↓ 看空",
            "strength": "强" if share >= STRONG_SHARE else "中",   # 占比代理，非博主自述强度
            "period": PERIOD_TEXT,
            "idx": config.IDX_DEFAULT,
            "ref": round(ref, 2),
            "ep": ep_s,
            "epc": round(epc, 2) if epc is not None else None,
            "ret": ret,
            "score": score,
            "note": note,
            "d": d,
        })
    return rows


def summarize_metrics(rows):
    """行列表 → 聚合指标（全单元共用；仅取有 score 的计分行）。

    命中=score>0；score=0 计"平"不计入分子分母。返回 n/hit/denom/acc/avg/vol/sharpe +
    看多·看空各自 N 与均分。vol/sharpe 语义 = run_direction.vol_of/sharpe_of（同一函数）。
    """
    scored = [r for r in rows if r.get("score") is not None]
    n = len(scored)
    hit, denom, _pct = dr.acc_of(scored) if scored else (0, 0, 0.0)
    acc = _pct / 100.0 if denom else 0.0        # 转成分数（acc_of 第三元素是 %，与 avg/vol 统一为小数）
    avg = dr.avg_of(scored)
    vol = dr.vol_of(scored)
    sharpe = dr.sharpe_of(scored)
    bull = [r for r in scored if r["d"] == 1]
    bear = [r for r in scored if r["d"] == -1]
    return {
        "n": n, "hit": hit, "denom": denom, "acc": acc,
        "avg": avg, "vol": vol, "sharpe": sharpe,
        "bull_n": len(bull), "bull_avg": dr.avg_of(bull),
        "bear_n": len(bear), "bear_avg": dr.avg_of(bear),
    }
