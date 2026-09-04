# -*- coding: utf-8 -*-
"""research/combo/hyst.py — 滞回共识平仓规则（每日一票 · 收盘成交）。

策略规格主档 = docs/hysteresis_consensus_spec.md（活文档），本模块按 §2 状态机实现：
  · 判定节奏：每"干净交易日"一次 14:30 快照 → 当日 15:00 收盘成交；投票 = poll 单条 last
    （swing 剔 spec=long、无 mixed 概念，见 poll.py），分母 e = 当日有波段观点的博主数。
  · 唯一自变量 = **看多比例 ρ = bull/e**（看空比例 = 1−ρ 互补；不引入"支持率"叫法）。
  · 阈值参数化（config.HYST_* 单源，空腿 = 1−多头 镜像派生）：
      开多 ρ>TO_LONG(2/3)；开空 ρ<TO_SHORT(1/3)【both】；
      持多 ρ<TX_LONG(1/2) 且 e>Q_exit → 平多；持空 ρ>TX_SHORT(1/2) 且 e>Q_exit → 平空。
  · **开/平双法定人数、无任何 50% 填充/冻结**：开仓需 e>Q_open、平仓需 e>Q_exit（严格）；
    e 不过对应门槛 → 该日不动作（空仓保持持币 / 持仓走滞回续持，不认退潮翻转）。
  · 恰 50%（2·bull=e，仅偶数 e 日）落在两腿各自的滞回**持**侧：不平不开。
  · long 模式 pos∈{0,1}（看空比例 >2/3 只平多持币、不开空）；both 模式可开空。
  · 整数比较、不用 float（改阈值即同步换分母，见 config.HYST_*）。

时态说明：本模块是**日线收盘到收盘**的净值模拟，不是 backtest.py 的 3 档/日盘中引擎——
决策次数少、开关更少、成交只在收盘；二者本就该逐位不同。参考基准 = 同期买持（ref0→refN）。

复用：ctxs 来自 daygrid.build_contexts（每干净日一次 14:30 poll 快照，全变体共享）；
日线收盘 ref 来自同一 DayContext.ref。指标公式镜像 backtest.compute_stats（total/
ann/sharpe/MDD/逐月），保证口径可比。纯计算、无副作用。
"""
import math
from fractions import Fraction

from .. import config

# 阈值（单源 config；空头 = 1−多头 镜像派生 → 两条腿在各自比例上过同一条线）
TO_LONG = config.HYST_TO_LONG          # 看多开仓线（现 2/3）
TX_LONG = config.HYST_TX_LONG          # 多腿平仓线（现 1/2）
TO_SHORT = Fraction(1, 1) - TO_LONG    # 空头开仓线 = 1−TO_LONG（现 1/3）：ρ<TO_SHORT 开空
TX_SHORT = Fraction(1, 1) - TX_LONG    # 空头平仓线 = 1−TX_LONG（现 1/2）：ρ>TX_SHORT 平空

SIDE_WORD = {"L": "多", "S": "空"}


# ---- 整数比较（看多比例 ρ = bull/e 与阈值 thr 比较；不用 float）----
def _rho_gt(b, e, thr):
    return b * thr.denominator > thr.numerator * e        # b/e > thr 严格


def _rho_lt(b, e, thr):
    return b * thr.denominator < thr.numerator * e        # b/e < thr 严格


def _decide(e, b, pos, mode, q_open, q_exit):
    """核心判定（纯整数）：当日 (e, bull) + 现持仓 → 收盘目标持仓。供 hyst_policy 与边界测试直用。

    平仓门先看持仓腿（防薄日把退潮误当可动作）：e ≤ q_exit → 该日不平、走滞回续持。
    开仓/换向门：e > q_open 才看方向；两门都过且与原仓反向 → 同收盘换向。
    """
    if pos == 1 and e > q_exit and _rho_lt(b, e, TX_LONG):
        pos = 0                        # 持多 ρ<TX_LONG 且 e>Q_exit → 平多
    elif pos == -1 and e > q_exit and _rho_gt(b, e, TX_SHORT):
        pos = 0                        # 持空 ρ>TX_SHORT 且 e>Q_exit → 平空
    if pos in (1, -1):
        return pos                     # 未平（e≤Q_exit，或 ρ 仍 ≥ TX_LONG / ≤ TX_SHORT）→ 续持（含恰 50%）
    if e > q_open:                     # 空仓/刚平 → 开仓门：人数不足则不动作、持币
        if _rho_gt(b, e, TO_LONG):
            return 1                   # ρ>2/3 → 开多
        if mode == "both" and _rho_lt(b, e, TO_SHORT):
            return -1                  # both 且 ρ<1/3（= 看空比例>2/3）→ 开空；long 模式不开空
    return 0


def hyst_policy(c, pos, mode, q_open, q_exit):
    """滞回目标持仓判定：输入当前持仓 pos(∈{0,±1}) + 当日 14:30 快照 → 收盘目标。

    pos 现持仓；mode ∈ {'long','both'}；q_open/q_exit = 开/平法定人数（e 严格 > 才过门）。
    语义见模块 docstring 与 docs/hysteresis_consensus_spec.md §2。
    """
    return _decide(c.expressed, c.bull, pos, mode, q_open, q_exit)


def assert_policy_edges():
    """§2 整数边界断言表（数据无关、可单测）：返回错误行列表（空 = 全过）。"""
    errs = []
    # (描述, pos, mode, q_open, q_exit, e, bull, 期望目标)
    cases = [
        ("开多 ρ=8/11>2/3", 0, "both", 10, 10, 11, 8, 1),
        ("恰 2/3（3b==2e）不开多", 0, "both", 10, 10, 6, 4, 0),
        ("开多被 Q_open 拦（e=10≤10）", 0, "both", 10, 10, 10, 9, 0),
        ("无 Q（fixed）e=10 可开多", 0, "both", 0, 0, 10, 9, 1),
        ("持多 ρ=5/11<1/2 平多", 1, "both", 10, 10, 11, 5, 0),
        ("持多恰 50%（2b==e，偶数 e）续持", 1, "both", 10, 10, 10, 5, 1),
        ("持多 e≤Q_exit（e=9,b=3）不退潮", 1, "both", 10, 10, 9, 3, 1),
        ("持多 ρ=1/4 → both 平多并开空", 1, "both", 10, 10, 12, 3, -1),
        ("持多 ρ=1/4 → long 只平多持币", 1, "long", 10, 10, 12, 3, 0),
        ("持空 ρ=6/11>1/2 平空", -1, "both", 10, 10, 11, 6, 0),
        ("持空恰 50% 续持", -1, "both", 10, 10, 10, 5, -1),
        ("持空 ρ=3/4 → both 平空并开多", -1, "both", 10, 10, 12, 9, 1),
        ("e=0 持多续持（无人表态不平）", 1, "both", 10, 10, 0, 0, 1),
        ("e=0 空仓不开", 0, "both", 10, 10, 0, 0, 0),
        ("空方 ρ=3/12<1/3 → both 开空", 0, "both", 10, 10, 12, 3, -1),
        ("空方超 2/3 → long 不开空（空仓保持持币）", 0, "long", 10, 10, 12, 3, 0),
    ]
    for desc, pos, mode, qo, qe, e, b, want in cases:
        got = _decide(e, b, pos, mode, qo, qe)
        if got != want:
            errs.append(f"{desc}：期望 {want} 得 {got}（pos={pos} mode={mode} Q=({qo},{qe}) e={e} bull={b}）")
    return errs


def simulate(ctxs, policy):
    """在干净日收盘序列上跑策略 → 净值/持仓/往返统计 dict。

    净值口径：nav 记在每日 15:00 收盘。close_i → close_{i+1} 的涨跌由 close_i 收盘后的
    目标持仓承担；当日 14:30 定调 → 收盘成交，故决策日自己那根 K 线不承担该日涨跌。
    持仓在样本末日收盘若未平 → 视作以末日收盘强制平仓（与 backtest 同语义）。
    """
    n = len(ctxs)
    closes = [c.ref for c in ctxs]
    navs = [1.0] * n
    pos = 0
    pos_after = []                  # 每收盘后的目标持仓（承担下一根 K）
    trades = []                     # 已平仓往返
    open_tr = None
    for i in range(n):
        if i > 0:
            r = closes[i] / closes[i - 1] - 1.0
            navs[i] = navs[i - 1] * (1.0 + pos * r)
        newpos = policy(ctxs[i], pos)
        if newpos != pos:
            if pos in (1, -1) and open_tr is not None:      # 平旧
                open_tr.update(exit_px=closes[i], exit_idx=i,
                               exit_date=ctxs[i].date.isoformat())
                trades.append(open_tr)
                open_tr = None
            if newpos in (1, -1):                            # 开新
                open_tr = {"side": "L" if newpos == 1 else "S",
                           "entry_px": closes[i], "entry_idx": i,
                           "entry_date": ctxs[i].date.isoformat()}
            pos = newpos
        pos_after.append(pos)
    if open_tr is not None:                                 # 样本末强制平仓 @ 末日收盘
        open_tr.update(exit_px=closes[-1], exit_idx=n - 1, exit_date=ctxs[-1].date.isoformat())
        trades.append(open_tr)
    return {"ctxs": ctxs, "closes": closes, "navs": navs,
            "pos_after": pos_after, "trades": trades}


def _signed_move(t):
    g = t["exit_px"] / t["entry_px"] - 1.0
    return g if t["side"] == "L" else -g                   # 空：price 跌为正


def _nav_sharpe(navs):
    rs = [a / b - 1.0 for b, a in zip(navs[:-1], navs[1:])]
    n = len(rs)
    if n < 2:
        return float("nan")
    mu = sum(rs) / n
    var = sum((x - mu) ** 2 for x in rs) / (n - 1)
    sd = var ** 0.5
    return mu / sd * math.sqrt(252) if sd > 0 else float("nan")


def metrics(sim):
    """simulate() 结果 → 指标 dict（镜像 backtest.compute_stats 口径）。"""
    ctxs = sim["ctxs"]
    n = len(ctxs)
    navs = sim["navs"]
    total_ret = navs[-1] - 1.0
    ann = (navs[-1] ** (252 / n) - 1.0) if navs[-1] > 0 else -1.0
    closes = sim["closes"]
    bh_ret = closes[-1] / closes[0] - 1.0
    bh_ann = ((1 + bh_ret) ** (252 / n) - 1.0) if bh_ret > -1 else -1.0
    sharpe = _nav_sharpe(navs)
    bh_sharpe = _nav_sharpe(closes)                 # 买持净值 = closes/ref0（比值同）
    peak, mdd = -1e9, 0.0
    for x in navs:
        peak = max(peak, x)
        mdd = max(mdd, (peak - x) / peak if peak > 0 else 0.0)
    # 在场 = 该收盘后持仓非 0（承担其后一根 K 的仓位）；样本内共 n-1 根 K
    pa = sim["pos_after"]
    expo = [i for i in range(n - 1) if pa[i] != 0]
    long_days = sum(1 for i in expo if pa[i] == 1)
    short_days = len(expo) - long_days
    in_mkt = len(expo) / (n - 1) if n > 1 else 0.0
    closed = sim["trades"]
    wins = [t for t in closed if _signed_move(t) > 0]
    hold_legs = [t["exit_idx"] - t["entry_idx"] for t in closed]
    n_long_rt = sum(1 for t in closed if t["side"] == "L")
    n_short_rt = len(closed) - n_long_rt
    # 逐月净值比（月 = 该日收盘所在月；首日基 1.0，镜像 engine）
    monthly = {}
    prev = 1.0
    for i, c in enumerate(ctxs):
        m = c.date.strftime("%Y-%m")
        monthly[m] = monthly.get(m, 1.0) * (navs[i] / prev)
        prev = navs[i]
    for m in list(monthly):
        monthly[m] = monthly[m] - 1.0
    return {
        "total_return": total_ret, "annualized": ann,
        "buyhold_return": bh_ret, "buyhold_annualized": bh_ann,
        "excess_vs_buyhold": total_ret - bh_ret,
        "max_drawdown": mdd, "sharpe": sharpe, "bh_sharpe": bh_sharpe,
        "n_days": n, "in_market_days": len(expo), "in_market": in_mkt,
        "long_days": long_days, "short_days": short_days,
        "n_roundtrips": len(closed), "n_long_rt": n_long_rt, "n_short_rt": n_short_rt,
        "win_rate": len(wins) / len(closed) if closed else float("nan"),
        "avg_trade_move": (sum(_signed_move(t) for t in closed) / len(closed)) if closed else float("nan"),
        "avg_hold_legs": sum(hold_legs) / len(hold_legs) if hold_legs else float("nan"),
        "monthly": monthly,
    }
