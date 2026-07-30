"""
博主操作配对收益评估：追踪 explicit_action 入场→出场完整周期

用法:
  python scripts/evaluate_trade_pairs.py --blogger 顺应周期
  python scripts/evaluate_trade_pairs.py --quality  # 仅评估高质量信号
  python scripts/evaluate_trade_pairs.py             # 全部博主
"""

import json
import os
import sys
import argparse
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
MARKET_DIR = os.path.join(DATA_DIR, "market")
SIGNALS_DIR = os.path.join(DATA_DIR, "signals")
MARKET_FILE = os.path.join(MARKET_DIR, "market_data.json")

# 拐点日期 — 用于质量过滤
INFLECTION_DATES = {
    "M1": "2024-09-13", "M2": "2024-10-08", "M3": "2025-04-07",
    "M4": "2025-11-14", "M5": "2026-03-23", "M6": "2026-05-14",
    "M7": "2026-06-25", "M8": "2026-07-20",
    "I1": "2024-10-18", "I2": "2024-11-08", "I5": "2025-01-13",
    "I6": "2025-03-19", "I9": "2025-12-16", "I10": "2026-01-14",
    "I12": "2026-03-03", "I13": "2026-06-08", "I14": "2026-06-23",
}
QUALITY_WINDOW = 5


def is_valid_signal(sig):
    """判断信号是否可用于配对：explicit_action + 非模糊。
    保留 strong 和 moderate，仅排除 directional_vague."""
    if sig.get("specific") == "directional_vague":
        return False
    if sig.get("specific") != "explicit_action":
        return False
    return True


def load_market_data():
    with open(MARKET_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    data = {}
    for index_name, records in raw.items():
        date_map = {}
        for r in records:
            date_map[r["日期"]] = r["收盘"]
        dates = sorted(date_map.keys())
        data[index_name] = {"price": date_map, "dates": dates}
    return data


def load_signals(blogger_filter=None):
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
    """找到 date_str 或之后最近的一个交易日"""
    if date_str in dates_list:
        return dates_list.index(date_str), date_str
    for i, d in enumerate(dates_list):
        if d >= date_str:
            return i, d
    return None, None


def get_price(date_str, idx_data):
    """获取某日的收盘价，若非交易日则取下一交易日"""
    idx, actual_date = find_next_trading_day(date_str, idx_data["dates"])
    if idx is None:
        return None, None
    return actual_date, idx_data["price"][actual_date]


def compute_holding_drawdown(entry_idx, exit_idx, dates, prices, direction):
    """计算持仓期间的最大回撤（bullish=做多）"""
    entry_price = prices[dates[entry_idx]]
    max_adverse = 0.0
    worst_date = dates[entry_idx]

    for i in range(entry_idx, exit_idx + 1):
        price_i = prices[dates[i]]
        if direction == "bullish":
            adverse = (entry_price - price_i) / entry_price
        else:
            adverse = (price_i - entry_price) / entry_price
        if adverse > max_adverse:
            max_adverse = adverse
            worst_date = dates[i]

    return round(-max_adverse * 100, 2), worst_date


def evaluate_pairs(blogger_data, market_data, quality_only=False):
    """配对入场→出场信号，计算完整周期收益"""
    name = blogger_data["blogger"]
    signals = blogger_data["signals"]

    # 筛选 explicit_action 信号
    actions = [s for s in signals if s.get("specific") == "explicit_action"]

    # 信号过滤：仅排除 directional_vague
    if quality_only:
        actions = [s for s in actions if is_valid_signal(s)]

    if not actions:
        # 降级尝试
        fallback_actions = [s for s in signals if s.get("specific") == "directional_clear"]
        if quality_only:
            fallback_actions = [s for s in fallback_actions if is_valid_signal(s)]
        if fallback_actions:
            actions = fallback_actions
            fallback = True
        else:
            return None
    else:
        fallback = False

    if not actions:
        return None

    # 按日期排序
    actions.sort(key=lambda x: x["date"])

    # 配对
    pairs = []
    current_entry = None

    for sig in actions:
        direction = sig["direction"]
        if direction == "bullish" and current_entry is None:
            current_entry = sig  # 开仓
        elif direction == "bearish" and current_entry is not None:
            pairs.append((current_entry, sig))  # 平仓
            current_entry = None
        # 连续 bullish → 保持第一个入场
        # 连续 bearish → 保持空仓

    # 计算每对收益
    index_name = actions[0].get("index", "上证指数")
    if index_name not in market_data:
        index_name = "上证指数"  # fallback

    idx_data = market_data.get(index_name)
    if not idx_data:
        return None

    dates = idx_data["dates"]
    prices = idx_data["price"]

    trade_results = []
    for entry, exit_ in pairs:
        entry_actual_date, entry_price = get_price(entry["date"], idx_data)
        exit_actual_date, exit_price = get_price(exit_["date"], idx_data)

        if entry_price is None or exit_price is None:
            continue

        entry_idx = dates.index(entry_actual_date)
        exit_idx = dates.index(exit_actual_date)
        holding_days = exit_idx - entry_idx

        raw_return = (exit_price - entry_price) / entry_price
        # 做多：涨=盈利
        return_pct = round(raw_return * 100, 2)

        # 持仓期间最大回撤
        max_dd, worst_date = compute_holding_drawdown(entry_idx, exit_idx, dates, prices, "bullish")

        # 年化
        if holding_days > 0:
            annualized = round(((1 + raw_return) ** (252 / holding_days) - 1) * 100, 2)
        else:
            annualized = 0

        trade_results.append({
            "entry_date": entry["date"],
            "entry_actual": entry_actual_date,
            "entry_price": entry_price,
            "entry_evidence": entry.get("evidence", "")[:80],
            "exit_date": exit_["date"],
            "exit_actual": exit_actual_date,
            "exit_price": exit_price,
            "exit_evidence": exit_.get("evidence", "")[:80],
            "holding_days": holding_days,
            "return_pct": return_pct,
            "annualized": annualized,
            "max_drawdown": max_dd,
            "worst_date": worst_date,
            "is_win": return_pct > 0,
        })

    # 当前持仓
    open_position = None
    if current_entry is not None:
        entry_actual_date, entry_price = get_price(current_entry["date"], idx_data)
        latest_date = dates[-1]
        latest_price = prices[latest_date]
        if entry_price:
            floating_return = round((latest_price - entry_price) / entry_price * 100, 2)
            open_position = {
                "entry_date": current_entry["date"],
                "entry_price": entry_price,
                "evidence": current_entry.get("evidence", "")[:80],
                "latest_date": latest_date,
                "latest_price": latest_price,
                "floating_return": floating_return,
            }

    # 汇总
    if not trade_results:
        win_rate = avg_return = avg_hold = profit_factor = 0
        max_gain = max_loss = avg_dd = 0
    else:
        returns = [t["return_pct"] for t in trade_results]
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r <= 0]
        dds = [t["max_drawdown"] for t in trade_results]
        hold_days = [t["holding_days"] for t in trade_results]

        win_rate = round(len(wins) / len(returns) * 100, 1)
        avg_return = round(sum(returns) / len(returns), 2)
        avg_hold = round(sum(hold_days) / len(hold_days), 1)
        max_gain = round(max(returns), 2)
        max_loss = round(min(returns), 2)
        avg_dd = round(sum(dds) / len(dds), 2)
        profit_factor = round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) != 0 else None

    return {
        "blogger": name,
        "fallback": fallback,  # 是否降级使用了 directional_clear
        "total_actions": len(actions),
        "total_trades": len(trade_results),
        "open_position": open_position,
        "summary": {
            "win_rate": win_rate,
            "avg_return": avg_return,
            "avg_holding_days": avg_hold,
            "max_gain": max_gain,
            "max_loss": max_loss,
            "avg_max_drawdown": avg_dd,
            "profit_factor": profit_factor,
        },
        "trades": trade_results,
    }


def print_pair_report(result):
    """打印配对交易报告"""
    print(f"\n{'='*60}")
    tag = " ⚠️降级" if result["fallback"] else ""
    print(f"  {result['blogger']} — 配对交易收益{tag}")
    print(f"{'='*60}")
    print(f"  操作型信号: {result['total_actions']} → 配成 {result['total_trades']} 笔完整交易")

    if not result["trades"]:
        print("  无完整配对待仓")
        if result["open_position"]:
            op = result["open_position"]
            print(f"  当前持仓: {op['entry_date']} 入场 @{op['entry_price']:.0f}, 浮动 {op['floating_return']:+.2f}%")
        return

    s = result["summary"]
    print(f"  胜率: {s['win_rate']}% | 平均收益: {s['avg_return']:+.2f}% | 盈亏比: {s['profit_factor']}")
    print(f"  平均持仓: {s['avg_holding_days']}天 | 最大盈利: {s['max_gain']:+.2f}% | 最大亏损: {s['max_loss']:+.2f}%")
    print(f"  平均最大回撤: {s['avg_max_drawdown']:+.2f}%")

    print(f"\n  交易明细:")
    print(f"  {'入场日':<12} {'入场@':>6} {'出场日':<12} {'出场@':>6} {'持仓天':>6} {'收益':>8} {'最大回撤':>8}")
    print(f"  {'-'*60}")
    for t in result["trades"]:
        print(f"  {t['entry_date']:<12} {t['entry_price']:>6.0f} {t['exit_date']:<12} {t['exit_price']:>6.0f} {t['holding_days']:>6} {t['return_pct']:>7.2f}% {t['max_drawdown']:>7.2f}%")

    if result.get("open_position"):
        op = result["open_position"]
        print(f"\n  📍 当前持仓: {op['entry_date']} 入场 @{op['entry_price']:.0f}, 至{op['latest_date']} @{op['latest_price']:.0f} 浮动 {op['floating_return']:+.2f}%")
        print(f"     {op['evidence']}")


def main():
    parser = argparse.ArgumentParser(description="博主配对待仓收益评估")
    parser.add_argument("--blogger", help="只分析指定博主")
    parser.add_argument("--quality", action="store_true", help="仅评估高质量信号（strong+explicit_action+拐点对齐）")
    args = parser.parse_args()

    print("加载大盘数据...")
    market_data = load_market_data()

    print("加载信号文件...")
    all_bloggers = load_signals(args.blogger)

    for blogger_data in all_bloggers:
        result = evaluate_pairs(blogger_data, market_data, quality_only=args.quality)
        if result:
            print_pair_report(result)
        else:
            print(f"\n  {blogger_data['blogger']}: 无可配对的操作型信号")


if __name__ == "__main__":
    main()
