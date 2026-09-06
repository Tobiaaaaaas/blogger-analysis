# -*- coding: utf-8 -*-
"""research/combo/daygrid.py — 全干净日 14:30 快照一次预计算（规则无关），供全部 (q,k) 格共享。

每个干净决策日只 poll 一次（同一份 expressed/bull/bear + 参考/终点收盘），所有候选规则在
同一份上下文上打分 —— 保证 42 格之间唯一差异是 q×k 判定本身，避免每格重复快照引入的偏差。

DayContext.clean_idx = 干净决策日序（从 0 起）；上/下半期按干净日序切半（现 150 日 → 界 75），
与信号日 N（各格自己的触发数）是两个概念。
"""
import datetime
from dataclasses import dataclass

from .. import config
from .. import poll as pollmod
from .. import trading_cal as tc
from ..backtest.backtest import clean_days, load_daily

BOARD = "swing"
SNAP_TICK = "14:30"          # 每日一票定调时刻（与 quality/engine 同口径）
PERIOD = "t5"                # 验证终点 = 决策日后第 5 交易日


@dataclass(frozen=True)
class DayContext:
    date: object             # datetime.date
    clean_idx: int           # 干净决策日序（半期切分依据）
    expressed: int
    bull: int
    bear: int
    ref: float               # 决策日信号标的上证 15:00 收盘（打分参考价/回看）
    ep: object               # 终点 date
    ep_close: float          # 终点日收盘（上证；score = d×raw_ret 评"上证观点对错"）
    raw_ret: float           # ep_close/ref − 1（未签名，乘 d 得 score）
    px: float = None         # 决策日交易标的中证1000 15:00 收盘（Swing_Timing §1 交易/基准用；旧 ctx 缺省回落 None）


def _snapshot(index, day):
    dt = datetime.datetime.strptime(f"{day.isoformat()} {SNAP_TICK}",
                                    "%Y-%m-%d %H:%M").replace(tzinfo=config.BEIJING_TZ)
    snap = pollmod.poll_tick(index, BOARD, dt)
    assert snap["clean"], f"{day} {BOARD}: 非干净日进入取样（{snap.get('gaps')}）"
    return snap


def build_contexts(index=None, start=config.START_DATE, end=config.END_DATE):
    """→ (ctxs, n_clean)。ctxs 每干净日一条（行情可打分才保留；当前全保留）；
    n_clean = 干净决策日总数（半期切界 = n_clean//2）。"""
    index = index or pollmod.CorpusIndex()
    daily = load_daily()                            # 信号标的上证（打分 ref/ep/raw_ret）
    daily_trade = load_daily(config.IDX_TRADE)      # 交易标的中证1000（hyst 净值/成交/买持基准）
    ctxs, n_clean = [], 0
    for day in clean_days(index, BOARD, start, end):
        clean_idx = n_clean
        n_clean += 1
        snap = _snapshot(index, day)
        ds = day.isoformat()
        ref = daily.get(ds, {}).get("收盘")        # 决策日信号标的上证 15:00 日线收盘
        if ref is None:
            continue                                # 防御：决策日无行情
        px = daily_trade.get(ds, {}).get("收盘")   # 决策日交易标的中证1000 收盘（hyst 用）
        if px is None:
            continue                                # 防御：交易标的数据缺口（现两指数同日历不应发生）
        ep = tc.endpoint_of(ds, PERIOD)
        ep_s = ep.isoformat() if ep else None
        epc = daily.get(ep_s, {}).get("收盘") if ep_s else None
        if epc is None:
            continue                                # 终点未到/无行情 → 不可打分（现快照不应发生）
        ctxs.append(DayContext(date=day, clean_idx=clean_idx,
                               expressed=snap["expressed"], bull=snap["bull"], bear=snap["bear"],
                               ref=ref, ep=ep, ep_close=epc, raw_ret=epc / ref - 1.0,
                               px=px))
    return ctxs, n_clean
