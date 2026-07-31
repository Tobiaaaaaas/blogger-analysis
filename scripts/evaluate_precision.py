"""
博主信号精确率验证：事件研究法（Event Study）

对每个博主的每次方向性判断（信号），计算后续固定窗口的市场表现，
统计胜率、平均收益、反向风险等指标。

用法:
  python scripts/evaluate_precision.py
  python scripts/evaluate_precision.py --blogger 稀豹     # 只看单个博主
  python scripts/evaluate_precision.py --quality           # 仅评估高质量信号（strong+拐点对齐）
"""

import json
import os
import sys
import argparse
from datetime import datetime, timedelta
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
MARKET_DIR = os.path.join(DATA_DIR, "market")
SIGNALS_DIR = os.path.join(DATA_DIR, "signals")
REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports")
MARKET_FILE = os.path.join(MARKET_DIR, "market_data.json")

# 验证窗口：交易日数
WINDOWS = {
    "T+5": 5, "T+10": 10, "T+15": 15, "T+20": 20,
}

# 长窗口（仅用于 long time_horizon 信号）
WINDOWS_LONG = {
    "T+40": 40, "T+60": 60,
}

# 时间维度窗口权重 — 根据信号的 time_horizon 分配各窗口权重
# 权重越高，该窗口的表现越影响综合评分
TIME_HORIZON_WEIGHTS = {
    "intraday":    {"T+5": 1.0, "T+10": 0.3, "T+15": 0.0, "T+20": 0.0},
    "short":       {"T+5": 1.0, "T+10": 0.6, "T+15": 0.2, "T+20": 0.0},
    "medium":      {"T+5": 0.6, "T+10": 1.0, "T+15": 1.0, "T+20": 0.6},
    "long":        {"T+5": 0.2, "T+10": 0.4, "T+15": 0.7, "T+20": 1.0},
    "unspecified": {"T+5": 0.6, "T+10": 0.8, "T+15": 1.0, "T+20": 1.0},
}

# 拐点日期（来自 market_analysis.md）— 用于质量过滤
INFLECTION_DATES = {
    # Major
    "M1": "2024-09-13", "M2": "2024-10-08", "M3": "2025-04-07",
    "M4": "2025-11-14", "M5": "2026-03-23", "M6": "2026-05-14",
    "M7": "2026-06-25", "M8": "2026-07-20",
    # Intermediate
    "I1": "2024-10-18", "I2": "2024-11-08", "I5": "2025-01-13",
    "I6": "2025-03-19", "I9": "2025-12-16", "I10": "2026-01-14",
    "I12": "2026-03-03", "I13": "2026-06-08", "I14": "2026-06-23",
}

QUALITY_WINDOW = 5  # 拐点对齐窗口 ±5 个交易日


def is_valid_signal(sig):
    """判断信号是否可评估：排除模糊观点和描述性语言。
    - directional_vague: 骑墙派，无明确方向
    - descriptive: 描述性语言（有方向词但无预测意图），v7新增
    保留 strong 和 moderate，保留 explicit_action 和 directional_clear。
    所有非模糊、预测性观点都应假设跟随并评估质量。"""
    spec = sig.get("specific", "")
    if spec in ("directional_vague", "descriptive"):
        return False
    return True


def load_market_data():
    """加载大盘数据，返回 {指数名: {date_str: close_price}}"""
    with open(MARKET_FILE, encoding="utf-8") as f:
        raw = json.load(f)

    data = {}
    for index_name, records in raw.items():
        date_map = {}
        for r in records:
            date_map[r["日期"]] = r["收盘"]
        # 构建日期列表用于查找交易日
        dates = sorted(date_map.keys())
        data[index_name] = {
            "price": date_map,
            "dates": dates,
        }
    return data


def load_signals(blogger_filter=None):
    """加载所有博主信号，返回 [{blogger, signals: [...]}]"""
    all_signals = []
    for fname in sorted(os.listdir(SIGNALS_DIR)):
        if not fname.endswith(".json"):
            continue
        blogger_name = fname.replace(".json", "")
        if blogger_filter and blogger_name != blogger_filter:
            continue
        fpath = os.path.join(SIGNALS_DIR, fname)
        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)
        all_signals.append(data)
    return all_signals


def find_next_trading_day(date_str, dates_list):
    """找到 date_str 或之后最近的一个交易日，返回 (index, date_str)"""
    if date_str in dates_list:
        idx = dates_list.index(date_str)
        return idx, date_str

    # 日期不在列表中（非交易日），找下一个
    for i, d in enumerate(dates_list):
        if d >= date_str:
            return i, d
    return None, None


def evaluate_signal(signal, market_data, windows):
    """评估单个信号，返回各窗口的前向收益 + 风险指标"""
    index_name = signal.get("index", "上证指数")
    if index_name not in market_data:
        return None

    idx_data = market_data[index_name]
    dates = idx_data["dates"]
    prices = idx_data["price"]

    date_str = signal["date"]
    direction = signal["direction"]  # bullish or bearish

    start_idx, actual_date = find_next_trading_day(date_str, dates)
    if start_idx is None:
        return None

    start_price = prices[actual_date]

    results = {
        "signal_date": date_str,
        "actual_trade_date": actual_date,
        "direction": direction,
        "start_price": start_price,
        "index": index_name,
    }

    for win_name, win_days in windows.items():
        end_idx = start_idx + win_days
        if end_idx >= len(dates):
            results[win_name] = None
            continue

        end_date = dates[end_idx]
        end_price = prices[end_date]
        raw_return = (end_price - start_price) / start_price

        # bullish: 涨了=对, bearish: 跌了=对
        if direction == "bullish":
            forward_return = raw_return
        else:
            forward_return = -raw_return

        # --- 风险指标：窗口内最不利波动 ---
        # bullish（做多）：最不利方向 = 下跌，即"最大回撤"（浮亏）
        # bearish（看空）：最不利方向 = 上涨，即"最大反向波动"（踏空/卖飞）
        # 两者含义不同，后续按方向分别统计
        max_adverse = 0.0  # 正值 = 不利幅度
        worst_date = actual_date
        worst_price = start_price
        recovered = False  # 是否先触及最坏点再恢复到盈利

        for i in range(start_idx, end_idx + 1):
            price_i = prices[dates[i]]
            if direction == "bullish":
                adverse = (start_price - price_i) / start_price  # 下跌=不利
            else:
                adverse = (price_i - start_price) / start_price  # 上涨=不利（踏空）

            if adverse > max_adverse:
                max_adverse = adverse
                worst_date = dates[i]
                worst_price = price_i

        hit_worst_then_recover = (
            max_adverse > 0.01 and
            worst_date < end_date and
            forward_return > 0
        )

        results[win_name] = {
            "end_date": end_date,
            "end_price": end_price,
            "raw_return": round(raw_return * 100, 2),
            "forward_return": round(forward_return * 100, 2),
            "correct": forward_return > 0,
            "max_adverse": round(-max_adverse * 100, 2),  # 负数=不利方向幅度
            "worst_date": worst_date,
            "worst_price": worst_price,
            "hit_worst_then_recover": hit_worst_then_recover,
        }

    return results


def aggregate_blogger(blogger_data, market_data, windows, quality_only=False):
    """汇总单个博主的信号验证结果。
    如果 quality_only=True，仅评估 strong + 拐点对齐的高质量信号。
    输出按看多/看空分块，聚焦反向风险。"""
    name = blogger_data["blogger"]
    signals = blogger_data["signals"]

    # 信号过滤：排除 directional_vague（模糊观点）和 descriptive（描述性）
    if quality_only:
        quality_signals = []
        excluded_vague = 0
        excluded_descriptive = 0
        for s in signals:
            spec = s.get("specific", "")
            if spec == "directional_vague":
                excluded_vague += 1
            elif spec == "descriptive":
                excluded_descriptive += 1
            else:
                quality_signals.append(s)
        skipped = excluded_vague + excluded_descriptive
    else:
        quality_signals = signals
        skipped = 0
        excluded_vague = 0
        excluded_descriptive = 0

    results = []
    for sig in quality_signals:
        r = evaluate_signal(sig, market_data, windows)
        if r:
            # For long-horizon signals, also evaluate extended windows (T+40, T+60)
            if sig.get("time_horizon") == "long":
                lr = evaluate_signal(sig, market_data, WINDOWS_LONG)
                if lr:
                    for wn in WINDOWS_LONG:
                        r[wn] = lr.get(wn)
            # 附加元信息
            r["_strength"] = sig.get("strength", "")
            r["_specific"] = sig.get("specific", "")
            r["_evidence"] = sig.get("evidence", "")
            r["_time_horizon"] = sig.get("time_horizon", "unspecified")
            r["_predictive"] = sig.get("predictive", True)
            results.append(r)

    if not results:
        return None

    # 按方向分组（核心变更：看多/看空各自独立统计）
    bull_results = [r for r in results if r["direction"] == "bullish"]
    bear_results = [r for r in results if r["direction"] == "bearish"]

    def dimension_stats(sig_results, direction_label):
        """对一组同方向信号做完整统计"""
        if not sig_results:
            return None

        # 找可用主窗口
        main_window = "T+20"
        valid = [r for r in sig_results if r.get(main_window) is not None]
        if not valid:
            main_window = "T+10"; valid = [r for r in sig_results if r.get(main_window) is not None]
        if not valid:
            main_window = "T+5"; valid = [r for r in sig_results if r.get(main_window) is not None]
        if not valid:
            return None

        fwd_returns = [r[main_window]["forward_return"] for r in valid]
        total = len(valid)
        wins = [fr for fr in fwd_returns if fr > 0]
        losses = [fr for fr in fwd_returns if fr <= 0]
        correct = len(wins)

        # 各窗口胜率 + 收益
        win_rates = {}
        avg_returns = {}
        for wn in windows:
            wv = [r for r in sig_results if r.get(wn) is not None]
            if wv:
                wc = sum(1 for r in wv if r[wn]["correct"])
                wr = sum(r[wn]["forward_return"] for r in wv) / len(wv)
                win_rates[wn] = round(wc / len(wv) * 100, 1)
                avg_returns[wn] = round(wr, 2)

        # --- 总体统计（strong+moderate 等权）---
        risk = {}
        for wn in windows:
            wv = [r for r in sig_results if r.get(wn) is not None]
            if not wv:
                continue
            advs = [r[wn]["max_adverse"] for r in wv]
            avg_adv = sum(advs) / len(advs)
            avg_ret = sum(r[wn]["forward_return"] for r in wv) / len(wv)
            big_adv = [a for a in advs if a < -3]
            recoveries = [r for r in wv if r[wn]["hit_worst_then_recover"]]
            # 加权：strong=2, moderate=1
            weights = [2 if r.get("_strength") == "strong" else 1 for r in wv]
            total_w = sum(weights)
            w_adv = sum(a * w for a, w in zip(advs, weights)) / total_w if total_w > 0 else 0
            w_ret = sum(r[wn]["forward_return"] * w for r, w in zip(wv, weights)) / total_w if total_w > 0 else 0
            risk[wn] = {
                "avg_adverse": round(avg_adv, 2),
                "worst_adverse": round(min(advs), 2),
                "pct_adverse_gt3": round(len(big_adv) / len(advs) * 100, 1),
                "return_adverse_ratio": round(avg_ret / abs(avg_adv), 2) if avg_adv != 0 else None,
                "pct_recover": round(len(recoveries) / len(wv) * 100, 1) if wv else 0,
                "weighted_avg_adverse": round(w_adv, 2),
                "weighted_avg_return": round(w_ret, 2),
            }

        # 严重失误
        severe = [r for r in valid if r[main_window]["forward_return"] < -5]

        # 盈亏比
        pf = round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) != 0 else None

        base = {
            "direction": direction_label,
            "total": total,
            "total_strong": sum(1 for r in valid if r.get("_strength") == "strong"),
            "total_moderate": sum(1 for r in valid if r.get("_strength") == "moderate"),
            "main_window": main_window,
            "win_rate": round(correct / total * 100, 1) if total > 0 else 0,
            "avg_return": round(sum(fwd_returns) / len(fwd_returns), 2),
            "median_return": round(sorted(fwd_returns)[len(fwd_returns) // 2], 2),
            "max_gain": round(max(fwd_returns), 2),
            "max_loss": round(min(fwd_returns), 2),
            "profit_factor": pf,
            "severe_errors": len(severe),
            "win_rates_by_window": win_rates,
            "avg_returns_by_window": avg_returns,
            "risk_by_window": risk,
            "severe_details": [
                {"date": r["signal_date"], "evidence": r.get("_evidence", "")[:80],
                 "forward_return": r[main_window]["forward_return"],
                 "max_adverse": r[main_window]["max_adverse"]}
                for r in severe
            ],
            "all_signals": sig_results,
        }

        # --- strong-only 统计 ---
        strong_only = [r for r in sig_results if r.get("_strength") == "strong"]
        if strong_only and len(strong_only) < len(sig_results):
            s_valid = [r for r in strong_only if r.get(main_window) is not None]
            if s_valid:
                s_fwd = [r[main_window]["forward_return"] for r in s_valid]
                s_win_rates = {}
                s_avg_rets = {}
                s_risk = {}
                for wn in windows:
                    sw = [r for r in strong_only if r.get(wn) is not None]
                    if sw:
                        s_win_rates[wn] = round(sum(1 for r in sw if r[wn]["correct"]) / len(sw) * 100, 1)
                        s_avg_rets[wn] = round(sum(r[wn]["forward_return"] for r in sw) / len(sw), 2)
                        s_advs = [r[wn]["max_adverse"] for r in sw]
                        s_big = [a for a in s_advs if a < -3]
                        s_avg_adv = sum(s_advs) / len(s_advs)
                        s_avg_ret = sum(r[wn]["forward_return"] for r in sw) / len(sw)
                        s_risk[wn] = {
                            "avg_adverse": round(s_avg_adv, 2),
                            "worst_adverse": round(min(s_advs), 2),
                            "pct_adverse_gt3": round(len(s_big) / len(s_advs) * 100, 1),
                            "return_adverse_ratio": round(s_avg_ret / abs(s_avg_adv), 2) if s_avg_adv != 0 else None,
                        }
                base["strong_only"] = {
                    "total": len(strong_only),
                    "valid": len(s_valid),
                    "win_rate": round(sum(1 for f in s_fwd if f > 0) / len(s_fwd) * 100, 1) if s_fwd else 0,
                    "avg_return": round(sum(s_fwd) / len(s_fwd), 2) if s_fwd else 0,
                    "win_rates_by_window": s_win_rates,
                    "avg_returns_by_window": s_avg_rets,
                    "risk_by_window": s_risk,
                }
        else:
            base["strong_only"] = None

        # --- v7: Time horizon analysis ---
        # Time horizon distribution
        horizon_dist = {"intraday": 0, "short": 0, "medium": 0, "long": 0, "unspecified": 0}
        for r in sig_results:
            h = r.get("_time_horizon", "unspecified")
            if h in horizon_dist:
                horizon_dist[h] += 1
        base["time_horizon_distribution"] = horizon_dist

        # Horizon-weighted composite win rate
        weighted_wins = 0.0
        weighted_total = 0.0
        for r in sig_results:
            h = r.get("_time_horizon", "unspecified")
            weights = TIME_HORIZON_WEIGHTS.get(h, TIME_HORIZON_WEIGHTS["unspecified"])
            for wn in WINDOWS:
                w = weights.get(wn, 0)
                if w > 0 and r.get(wn) is not None:
                    weighted_total += w
                    if r[wn]["correct"]:
                        weighted_wins += w
        if weighted_total > 0:
            base["horizon_weighted_win_rate"] = round(weighted_wins / weighted_total * 100, 1)
        else:
            base["horizon_weighted_win_rate"] = None

        # Horizon-weighted avg return
        weighted_ret = 0.0
        weighted_ret_total = 0.0
        for r in sig_results:
            h = r.get("_time_horizon", "unspecified")
            weights = TIME_HORIZON_WEIGHTS.get(h, TIME_HORIZON_WEIGHTS["unspecified"])
            for wn in WINDOWS:
                w = weights.get(wn, 0)
                if w > 0 and r.get(wn) is not None:
                    weighted_ret_total += w
                    weighted_ret += r[wn]["forward_return"] * w
        if weighted_ret_total > 0:
            base["horizon_weighted_avg_return"] = round(weighted_ret / weighted_ret_total, 2)
        else:
            base["horizon_weighted_avg_return"] = None

        # Optimal window identification — which window has the best win rate
        best_window = None
        best_wr = 0
        for wn in WINDOWS:
            wr = base["win_rates_by_window"].get(wn, 0)
            if wr > best_wr:
                best_wr = wr
                best_window = wn
        base["optimal_window"] = best_window
        base["optimal_window_win_rate"] = best_wr

        # Decay pattern: "rising" (T+5→T+20 increasing), "falling" (decreasing), "hump" (peak in middle), "flat"
        wrs = [base["win_rates_by_window"].get(wn, 0) for wn in WINDOWS]
        if wrs[0] < wrs[1] < wrs[2] < wrs[3]:
            base["decay_pattern"] = "rising"  # 长线型
        elif wrs[0] > wrs[1] > wrs[2] > wrs[3]:
            base["decay_pattern"] = "falling"  # 短线型
        elif max(wrs) == wrs[1] or max(wrs) == wrs[2]:
            base["decay_pattern"] = "hump"  # 波段型
        else:
            base["decay_pattern"] = "mixed"

        # --- v7: Long-window evaluation (T+40, T+60) for long-horizon signals only ---
        long_signals = [r for r in sig_results if r.get("_time_horizon") == "long"]
        if long_signals and len(long_signals) >= 5:  # Need at least 5 for meaningful stats
            long_window_stats = {}
            for wn, wn_days in WINDOWS_LONG.items():
                wv = [r for r in long_signals if r.get(wn) is not None]
                if wv:
                    wc = sum(1 for r in wv if r[wn]["correct"])
                    wr = round(wc / len(wv) * 100, 1)
                    ar = round(sum(r[wn]["forward_return"] for r in wv) / len(wv), 2)
                    advs = [r[wn]["max_adverse"] for r in wv]
                    long_window_stats[wn] = {
                        "count": len(wv),
                        "win_rate": wr,
                        "avg_return": ar,
                        "avg_adverse": round(sum(advs) / len(advs), 2),
                        "worst_adverse": round(min(advs), 2),
                    }
            if long_window_stats:
                base["long_window_stats"] = {
                    "signal_count": len(long_signals),
                    "windows": long_window_stats,
                }
            else:
                base["long_window_stats"] = None
        else:
            base["long_window_stats"] = None

        return base

    bull_stats = dimension_stats(bull_results, "bullish")
    bear_stats = dimension_stats(bear_results, "bearish")

    return {
        "blogger": name,
        "quality_mode": quality_only,
        "total_signals_input": len(signals),
        "quality_signals_used": len(results),
        "quality_skipped": skipped,
        "excluded_vague": excluded_vague,
        "excluded_descriptive": excluded_descriptive,
        "bullish": bull_stats,
        "bearish": bear_stats,
        "all_results": results,
    }


def print_dimension(stat, windows, label, risk_label):
    """打印单个维度（看多或看空）的完整报告"""
    if not stat:
        print(f"\n  📊 {label}: 无有效信号")
        return

    n_strong = stat.get("total_strong", 0)
    n_mod = stat.get("total_moderate", 0)
    print(f"\n  {'─'*50}")
    print(f"  📊 {label}（总体{stat['total']}条: strong×{n_strong} moderate×{n_mod}, 主窗口={stat['main_window']}）")
    print(f"  {'─'*50}")
    print(f"  胜率: {stat['win_rate']}% | 平均收益: {stat['avg_return']:+.2f}% | 中位收益: {stat['median_return']:+.2f}%")
    print(f"  最大盈利: {stat['max_gain']:+.2f}% | 最大亏损: {stat['max_loss']:+.2f}%")
    pf = f"{stat['profit_factor']}" if stat['profit_factor'] else "N/A"
    print(f"  盈亏比: {pf} | 严重失误(<-5%): {stat['severe_errors']}次")

    # 各窗口胜率 + 收益
    print(f"\n  {'窗口':<6} {'胜率':>8} {'均收益':>8}")
    for wn in windows:
        wr = stat["win_rates_by_window"].get(wn)
        ar = stat["avg_returns_by_window"].get(wn)
        if wr is not None:
            print(f"  {wn:<6} {wr:>7}% {ar:>7}%")

    # 反向风险
    risk = stat.get("risk_by_window", {})
    if risk:
        print(f"\n  🛡️ 反向风险（总体）— {risk_label}:")
        print(f"  {'窗口':<6} {'均不利':>8} {'最坏':>8} {'不利>3%':>8} {'收益/不利比':>10}")
        for wn in windows:
            r = risk.get(wn)
            if r:
                rdr = f"{r['return_adverse_ratio']}" if r['return_adverse_ratio'] is not None else "N/A"
                print(f"  {wn:<6} {r['avg_adverse']:>7}% {r['worst_adverse']:>7}% {r['pct_adverse_gt3']:>7}% {rdr:>10}")

    # strong-only
    so = stat.get("strong_only")
    if so:
        print(f"\n  ⭐ 仅 strong 信号（{so['total']}条, 有效{so['valid']}条）:")
        print(f"  胜率: {so['win_rate']}% | 平均收益: {so['avg_return']:+.2f}%")
        print(f"  {'窗口':<6} {'胜率':>8} {'均收益':>8}")
        for wn in windows:
            wr = so["win_rates_by_window"].get(wn)
            ar = so["avg_returns_by_window"].get(wn)
            if wr is not None:
                print(f"  {wn:<6} {wr:>7}% {ar:>7}%")
        sr = so.get("risk_by_window", {})
        if sr:
            print(f"  {'窗口':<6} {'均不利':>8} {'最坏':>8} {'不利>3%':>8}")
            for wn in windows:
                r = sr.get(wn)
                if r:
                    print(f"  {wn:<6} {r['avg_adverse']:>7}% {r['worst_adverse']:>7}% {r['pct_adverse_gt3']:>7}%")

    # v7: Time horizon distribution
    hdist = stat.get("time_horizon_distribution", {})
    if hdist:
        total_h = sum(hdist.values())
        if total_h > 0:
            print(f"\n  ⏱️ 时间维度分布 (v7):")
            parts = []
            for h in ["intraday", "short", "medium", "long", "unspecified"]:
                c = hdist.get(h, 0)
                if c > 0:
                    parts.append(f"{h}={c}({c*100//total_h}%)")
            print(f"  {' | '.join(parts)}")
            hwr = stat.get("horizon_weighted_win_rate")
            hret = stat.get("horizon_weighted_avg_return")
            if hwr is not None:
                print(f"  时间加权胜率: {hwr}% | 时间加权均收益: {hret:+.2f}%")
            opt_win = stat.get("optimal_window")
            opt_wr = stat.get("optimal_window_win_rate")
            decay = stat.get("decay_pattern", "?")
            if opt_win:
                print(f"  最优窗口: {opt_win} ({opt_wr}%) | 衰减模式: {decay}")

    # v7: Long window stats
    lws = stat.get("long_window_stats")
    if lws:
        print(f"\n  🔭 长线信号扩展窗口（{lws['signal_count']}条 long-horizon 信号）:")
        for wn, ws in lws.get("windows", {}).items():
            print(f"    {wn}: 胜率{ws['win_rate']}% 均收益{ws['avg_return']:+.2f}% 均不利{ws['avg_adverse']}% 最坏{ws['worst_adverse']}%")

    # 严重失误明细
    if stat["severe_details"]:
        print(f"\n  ⚠️ 严重失误:")
        for err in stat["severe_details"]:
            print(f"    [{err['date']}] 收益{err['forward_return']:+.2f}% 不利{err['max_adverse']}% | {err['evidence'][:60]}...")


def print_blogger_report(agg, windows):
    """打印单个博主的详细报告——看多/看空分块"""
    qtag = " [仅高质量信号]" if agg.get("quality_mode") else ""
    print(f"\n{'='*60}")
    print(f"  {agg['blogger']}{qtag}")
    excl_parts = []
    if agg.get("excluded_vague", 0):
        excl_parts.append(f"vague={agg['excluded_vague']}")
    if agg.get("excluded_descriptive", 0):
        excl_parts.append(f"descriptive={agg['excluded_descriptive']}")
    excl_str = f"（过滤: {', '.join(excl_parts)}）" if excl_parts else ""
    print(f"  输入{agg['total_signals_input']}条信号 → 使用{agg['quality_signals_used']}条{excl_str}")
    print(f"{'='*60}")

    # 看多维度
    print_dimension(agg.get("bullish"), windows, "看多 / 抄底能力", "最大回撤（买入后最惨浮亏）")

    # 看空维度
    print_dimension(agg.get("bearish"), windows, "看空 / 逃顶能力", "最大反向波动（卖出后最大踏空）")


def generate_comparison_md(all_aggs, windows):
    """生成对比报告 Markdown"""
    lines = [
        "# 博主信号精确率对比",
        "",
        f"> 评估时间：{datetime.now().strftime('%Y-%m-%d')}",
        "> 方法：事件研究法 — 对每个方向性信号，验证后续固定窗口的指数表现",
        "> 主验证窗口：各博主统一使用可用最长窗口",
        "",
        "## 核心指标对比",
        "",
        "| 博主 | 信号数 | 主窗口 | 胜率 | 平均收益 | 中位收益 | 最大盈利 | 最大亏损 | 盈亏比 | 严重失误 |",
        "|------|:------:|:------:|:----:|:--------:|:--------:|:--------:|:--------:|:------:|:--------:|",
    ]

    for agg in sorted(all_aggs, key=lambda a: a["stats"]["avg_return"], reverse=True):
        s = agg["stats"]
        pf = f"{s['profit_factor']:.1f}" if s['profit_factor'] else "N/A"
        lines.append(
            f"| {agg['blogger']} | {agg['valid_signals']} | {agg['main_window']} | "
            f"{s['win_rate']}% | {s['avg_return']}% | {s['median_return']}% | "
            f"{s['max_gain']}% | {s['max_loss']}% | {pf} | {s['severe_errors']} |"
        )

    lines += [
        "",
        "> **平均收益**：所有信号 forward_return 的均值。正值=博主判断平均赚钱，负值=平均亏钱。",
        "> **盈亏比**：正确信号平均收益 / |错误信号平均亏损|。>1 说明对了赚的比错了亏的多。",
        "> **严重失误**：forward_return < -5% 的信号数。每次都是让跟随者大亏的判断。",
        "",
        "## 各窗口胜率对比",
        "",
    ]
    header = "| 博主 |"
    sep = "|------|"
    for wn in windows:
        header += f" {wn} |"
        sep += ":----:|"
    lines.append(header)
    lines.append(sep)

    for agg in all_aggs:
        row = f"| {agg['blogger']} |"
        for wn in windows:
            wr = agg["win_rates_by_window"].get(wn)
            if wr is not None:
                row += f" {wr}% |"
            else:
                row += " N/A |"
        lines.append(row)

    lines += [
        "",
        "## 方向拆分",
        "",
        "| 博主 | 看多信号 | 看多胜率 | 看多均收益 | 看空信号 | 看空胜率 | 看空均收益 |",
        "|------|:--------:|:--------:|:----------:|:--------:|:--------:|:----------:|",
    ]

    for agg in all_aggs:
        b = agg["by_direction"]["bullish"]
        br = agg["by_direction"]["bearish"]
        b_str = f"| {agg['blogger']} |"
        if b:
            b_str += f" {b['count']} | {b['win_rate']}% | {b['avg_return']}% |"
        else:
            b_str += " - | - | - |"
        if br:
            b_str += f" {br['count']} | {br['win_rate']}% | {br['avg_return']}% |"
        else:
            b_str += " - | - | - |"
        lines.append(b_str)

    lines += [
        "",
        "## 稳定性指标",
        "",
        "| 博主 | 最长连胜 | 最长连败 | 窗口衰减(T+1→T+20) |",
        "|------|:--------:|:--------:|:-------------------:|",
    ]

    for agg in all_aggs:
        s = agg["stats"]
        wr1 = agg["win_rates_by_window"].get("T+1", 0)
        wr20 = agg["win_rates_by_window"].get("T+20", 0)
        decay = f"{wr1}%→{wr20}%"
        lines.append(
            f"| {agg['blogger']} | {s['max_win_streak']} | {s['max_lose_streak']} | {decay} |"
        )

    lines += [
        "",
        "## 综合解读",
        "",
        "### 如何理解这些指标",
        "",
        "- **胜率 > 50%**：博主的判断比抛硬币强",
        "- **胜率 ≈ 50%**：博主的判断和抛硬币差不多（无预测能力）",
        "- **胜率 < 50% 且平均收益为正**：判断方向经常错但对的几次赚得多（止损严、让利润跑）",
        "- **胜率 > 50% 但平均收益为负**：方向经常对但错的几次亏得大（不止损、死扛）",
        "- **盈亏比 < 1**：错了亏的比对了赚的多——跟随者长期亏钱",
        "- **窗口衰减上行**（T+20胜率 > T+1胜率）：博主是中线判断型，短线噪音大",
        "- **窗口衰减下行**（T+20胜率 < T+1胜率）：博主短线有效，中线可能只是猜对方向",
        "",
    ]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="博主信号精确率验证")
    parser.add_argument("--blogger", help="只分析指定博主")
    parser.add_argument("--quality", action="store_true", help="仅评估高质量信号（strong+拐点对齐）")
    args = parser.parse_args()

    print("加载大盘数据...")
    market_data = load_market_data()
    print(f"  已加载指数: {', '.join(market_data.keys())}")

    print("加载信号文件...")
    all_bloggers = load_signals(args.blogger)
    print(f"  已加载 {len(all_bloggers)} 位博主")

    windows = WINDOWS

    for blogger_data in all_bloggers:
        agg = aggregate_blogger(blogger_data, market_data, windows, quality_only=args.quality)
        if agg:
            print_blogger_report(agg, windows)
        else:
            print(f"\n  {blogger_data['blogger']}: 无有效信号")


if __name__ == "__main__":
    main()
