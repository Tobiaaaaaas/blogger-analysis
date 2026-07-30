"""
博主信号精确率验证：事件研究法（Event Study）

对每个博主的每次方向性判断（信号），计算后续固定窗口的市场表现，
统计胜率、平均收益、盈亏比等指标。

用法:
  python scripts/evaluate_precision.py
  python scripts/evaluate_precision.py --window T+20  # 指定主验证窗口
  python scripts/evaluate_precision.py --blogger 稀豹  # 只看单个博主
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
    "T+1": 1,
    "T+3": 3,
    "T+5": 5,
    "T+10": 10,
    "T+20": 20,
}


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
    """评估单个信号，返回各窗口的前向收益"""
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
            results[win_name] = None  # 数据不足
            continue

        end_date = dates[end_idx]
        end_price = prices[end_date]
        raw_return = (end_price - start_price) / start_price

        # bullish: 涨了=对, bearish: 跌了=对
        if direction == "bullish":
            forward_return = raw_return
        else:
            forward_return = -raw_return

        results[win_name] = {
            "end_date": end_date,
            "end_price": end_price,
            "raw_return": round(raw_return * 100, 2),
            "forward_return": round(forward_return * 100, 2),
            "correct": forward_return > 0,
        }

    return results


def aggregate_blogger(blogger_data, market_data, windows):
    """汇总单个博主的信号验证结果"""
    name = blogger_data["blogger"]
    signals = blogger_data["signals"]

    results = []
    for sig in signals:
        r = evaluate_signal(sig, market_data, windows)
        if r:
            results.append(r)

    if not results:
        return None

    # 按主窗口（T+20）统计
    main_window = "T+20"
    valid = [r for r in results if r.get(main_window) is not None]
    if not valid:
        main_window = "T+10"
        valid = [r for r in results if r.get(main_window) is not None]
    if not valid:
        main_window = "T+5"
        valid = [r for r in results if r.get(main_window) is not None]

    forward_returns = [r[main_window]["forward_return"] for r in valid]
    correct_count = sum(1 for fr in forward_returns if fr > 0)
    total = len(valid)

    pos_returns = [fr for fr in forward_returns if fr > 0]
    neg_returns = [fr for fr in forward_returns if fr <= 0]

    # 各窗口胜率
    win_rates = {}
    for wn in windows:
        w_valid = [r for r in results if r.get(wn) is not None]
        if w_valid:
            w_correct = sum(1 for r in w_valid if r[wn]["correct"])
            win_rates[wn] = round(w_correct / len(w_valid) * 100, 1)

    # 按方向拆分
    bullish_results = [r for r in valid if r["direction"] == "bullish"]
    bearish_results = [r for r in valid if r["direction"] == "bearish"]

    def sub_stats(sub_results):
        if not sub_results:
            return None
        frs = [r[main_window]["forward_return"] for r in sub_results]
        return {
            "count": len(frs),
            "win_rate": round(sum(1 for f in frs if f > 0) / len(frs) * 100, 1),
            "avg_return": round(sum(frs) / len(frs), 2),
        }

    # 按强度拆分
    strong_results = [r for r in valid if r.get("_strength") == "strong"]
    moderate_results = [r for r in valid if r.get("_strength") == "moderate"]

    # 补充 strength 信息到 results
    for r in results:
        for sig in signals:
            if sig["date"] == r["signal_date"] and sig.get("evidence") == r.get("_evidence"):
                r["_strength"] = sig.get("strength", "moderate")
                r["_evidence"] = sig.get("evidence", "")
                r["_specific"] = sig.get("specific", "")
                break
        # 简单匹配 date
        for sig in signals:
            if sig["date"] == r["signal_date"]:
                r["_strength"] = sig.get("strength", "moderate")
                r["_evidence"] = sig.get("evidence", "")
                r["_specific"] = sig.get("specific", "")
                break

    strong_valid = [r for r in valid if r.get("_strength") == "strong"]
    moderate_valid = [r for r in valid if r.get("_strength") == "moderate"]

    # 连续正确/错误
    correct_seq = [1 if r[main_window]["correct"] else 0 for r in valid]
    max_win_streak = 0
    max_lose_streak = 0
    current_win = 0
    current_lose = 0
    for c in correct_seq:
        if c:
            current_win += 1
            current_lose = 0
            max_win_streak = max(max_win_streak, current_win)
        else:
            current_lose += 1
            current_win = 0
            max_lose_streak = max(max_lose_streak, current_lose)

    # 严重失误（forward_return < -5%）
    severe_errors = [r for r in valid if r[main_window]["forward_return"] < -5]

    return {
        "blogger": name,
        "total_signals": len(results),
        "valid_signals": total,
        "main_window": main_window,
        "stats": {
            "win_rate": round(correct_count / total * 100, 1) if total > 0 else 0,
            "avg_return": round(sum(forward_returns) / len(forward_returns), 2),
            "median_return": round(sorted(forward_returns)[len(forward_returns) // 2], 2),
            "max_gain": round(max(forward_returns), 2),
            "max_loss": round(min(forward_returns), 2),
            "profit_factor": round(sum(pos_returns) / abs(sum(neg_returns)), 2) if neg_returns and sum(neg_returns) != 0 else None,
            "severe_errors": len(severe_errors),
            "max_win_streak": max_win_streak,
            "max_lose_streak": max_lose_streak,
        },
        "win_rates_by_window": win_rates,
        "by_direction": {
            "bullish": sub_stats(bullish_results),
            "bearish": sub_stats(bearish_results),
        },
        "by_strength": {
            "strong": sub_stats(strong_valid),
            "moderate": sub_stats(moderate_valid),
        },
        "severe_error_details": [
            {
                "date": r["signal_date"],
                "direction": r["direction"],
                "evidence": r.get("_evidence", ""),
                "forward_return": r[main_window]["forward_return"],
            }
            for r in severe_errors
        ],
        "all_results": results,
    }


def print_blogger_report(agg, windows):
    """打印单个博主的详细报告"""
    s = agg["stats"]
    print(f"\n{'='*60}")
    print(f"  {agg['blogger']}")
    print(f"{'='*60}")
    print(f"  信号总数: {agg['total_signals']} | 有效: {agg['valid_signals']} | 主窗口: {agg['main_window']}")
    print(f"  胜率: {s['win_rate']}% | 平均收益: {s['avg_return']}% | 中位收益: {s['median_return']}%")
    print(f"  最大盈利: {s['max_gain']}% | 最大亏损: {s['max_loss']}%")
    if s['profit_factor']:
        print(f"  盈亏比: {s['profit_factor']} | 严重失误(<-5%): {s['severe_errors']}次")
    print(f"  最长连胜: {s['max_win_streak']} | 最长连败: {s['max_lose_streak']}")

    print(f"\n  各窗口胜率:")
    for wn in windows:
        wr = agg["win_rates_by_window"].get(wn, "N/A")
        print(f"    {wn}: {wr}%")

    print(f"\n  按方向:")
    for d, st in agg["by_direction"].items():
        if st:
            print(f"    {d}: {st['count']}次 胜率{st['win_rate']}% 均收益{st['avg_return']}%")

    print(f"\n  按强度:")
    for d, st in agg["by_strength"].items():
        if st:
            print(f"    {d}: {st['count']}次 胜率{st['win_rate']}% 均收益{st['avg_return']}%")

    if agg["severe_error_details"]:
        print(f"\n  ⚠️ 严重失误（<-5%）:")
        for err in agg["severe_error_details"]:
            print(f"    [{err['date']}] {err['direction']}: {err['evidence'][:60]}... → {err['forward_return']}%")


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
    parser.add_argument("--output", default=None, help="输出文件路径")
    args = parser.parse_args()

    print("加载大盘数据...")
    market_data = load_market_data()
    print(f"  已加载指数: {', '.join(market_data.keys())}")

    print("加载信号文件...")
    all_bloggers = load_signals(args.blogger)
    print(f"  已加载 {len(all_bloggers)} 位博主")

    windows = WINDOWS
    all_aggs = []

    for blogger_data in all_bloggers:
        agg = aggregate_blogger(blogger_data, market_data, windows)
        if agg:
            all_aggs.append(agg)
            print_blogger_report(agg, windows)
        else:
            print(f"\n  {blogger_data['blogger']}: 无有效信号")

    if not all_aggs:
        print("\n无有效数据")
        return

    # 生成对比报告
    md = generate_comparison_md(all_aggs, windows)

    output_path = args.output or os.path.join(REPORTS_DIR, "precision_comparison.md")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"\n✅ 对比报告已保存: {output_path}")

    # 简要排序
    print("\n" + "=" * 60)
    print("  按平均收益排序")
    print("=" * 60)
    for i, agg in enumerate(sorted(all_aggs, key=lambda a: a["stats"]["avg_return"], reverse=True)):
        s = agg["stats"]
        print(f"  {i+1}. {agg['blogger']}: 胜率{s['win_rate']}% 均收益{s['avg_return']}% "
              f"盈亏比{s['profit_factor'] or 'N/A'} 严重失误{s['severe_errors']}次")


if __name__ == "__main__":
    main()
