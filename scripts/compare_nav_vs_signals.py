"""
Compare position-based NAV simulation vs signal-based equity curve.
Only meaningful for 顺应周期 (the only blogger who discloses positions).
"""
import json, os, sys
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load_nav_data(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

def load_signal_equity(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

def compare(position_nav_file, signal_equity_file):
    """Generate comparison report between position-based NAV and signal-based equity."""
    nav_data = load_nav_data(position_nav_file)
    signal_data = load_signal_equity(signal_equity_file)

    nav_summary = nav_data.get("summary", {})
    nav_series = nav_data.get("nav_series", {}).get("portfolio", [])
    signal_nav_map = signal_data.get("nav_series", {})

    # Build date-aligned comparison
    aligned = []
    for day_data in nav_series:
        date = day_data["date"]
        nav = day_data["nav"]
        signal_nav = signal_nav_map.get(date)
        if signal_nav is not None:
            aligned.append({
                "date": date,
                "position_nav": nav,
                "signal_nav": signal_nav,
                "position_pct": day_data.get("position_pct", 0)
            })

    if not aligned:
        print("WARNING: No overlapping dates between position and signal NAV series")
        return None

    # Compute comparison metrics
    pos_ret = (aligned[-1]["position_nav"] - 1.0) * 100
    sig_ret = (aligned[-1]["signal_nav"] - 1.0) * 100

    # 20-day rolling correlation
    correlations = []
    for i in range(20, len(aligned)):
        window = aligned[i-20:i+1]
        pos_rets = []
        sig_rets = []
        for j in range(1, len(window)):
            pos_rets.append(window[j]["position_nav"] / window[j-1]["position_nav"] - 1)
            sig_rets.append(window[j]["signal_nav"] / window[j-1]["signal_nav"] - 1)
        corr = _pearson(pos_rets, sig_rets)
        if corr is not None:
            correlations.append({"date": window[-1]["date"], "correlation": round(corr, 4)})

    avg_corr = sum(c["correlation"] for c in correlations) / len(correlations) if correlations else 0

    # Position size comparison
    avg_position_pct = nav_summary.get("position", {}).get("avg_position_pct", 0)
    signal_position_pct = 100.0  # Signal simulation always uses full position (10% of equity per unit = implicit)

    # Drawdown comparison
    pos_dd = nav_summary.get("risk", {}).get("max_drawdown_pct", 0)
    sig_dd = signal_data.get("max_drawdown_pct", 0)

    # Trade count
    pos_trades = nav_summary.get("turnover", {}).get("total_position_changes", 0)
    sig_trades = signal_data.get("trade_count", 0)

    report = {
        "blogger": "顺应周期",
        "comparison_period": f"{aligned[0]['date']} ~ {aligned[-1]['date']}",
        "overlapping_days": len(aligned),

        "returns": {
            "position_based_nav_return_pct": round(pos_ret, 2),
            "signal_based_equity_return_pct": round(sig_ret, 2),
            "difference_pct": round(pos_ret - sig_ret, 2),
            "interpretation": ""
        },

        "risk": {
            "position_max_drawdown_pct": round(pos_dd, 2),
            "signal_max_drawdown_pct": round(sig_dd, 2),
            "position_avg_position_pct": round(avg_position_pct, 1),
            "signal_position_model": "always_100pct"
        },

        "activity": {
            "position_changes": pos_trades,
            "signal_trades": sig_trades,
            "signal_signals": signal_data.get("signal_count", 0)
        },

        "correlation": {
            "avg_20d_rolling": round(avg_corr, 4),
            "rolling_series": correlations
        },

        "aligned_series": aligned
    }

    # Generate interpretation
    if pos_ret > sig_ret:
        report["returns"]["interpretation"] = (
            f"仓位模拟 ({pos_ret:+.2f}%) 大幅优于信号模拟 ({sig_ret:+.2f}%)，"
            f"差异 {pos_ret - sig_ret:+.2f}%。博主实际操作比其方向判断信号更赚钱。"
            f"可能原因：(1) 仓位管理灵活，实际持仓低于信号假设的满仓操作；"
            f"(2) 逆势减仓避免了大跌；(3) 博主'说的'和'做的'不一致——"
            f"观点偏保守或偏激进，但实际操作更理眻。"
        )
    elif pos_ret < sig_ret:
        report["returns"]["interpretation"] = (
            f"信号模拟 ({sig_ret:+.2f}%) 优于仓位模拟 ({pos_ret:+.2f}%)，"
            f"差异 {sig_ret - pos_ret:+.2f}%。博主判断方向的能力强于实际执行。"
            f"可能原因：(1) 仓位管理过于保守，踏空了部分行情；"
            f"(2) 调仓时机不够精准；(3) 部分持仓标的不在6个可投资指数范围内。"
        )
    else:
        report["returns"]["interpretation"] = "两种模拟结果相近，博主的操作与判断基本一致。"

    return report


def _pearson(x, y):
    """Compute Pearson correlation."""
    if len(x) != len(y) or len(x) < 2:
        return None
    n = len(x)
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    var_x = sum((v - mean_x) ** 2 for v in x)
    var_y = sum((v - mean_y) ** 2 for v in y)
    if var_x == 0 or var_y == 0:
        return None
    return cov / (var_x ** 0.5 * var_y ** 0.5)


if __name__ == "__main__":
    nav_file = sys.argv[1] if len(sys.argv) > 1 else \
        os.path.join(PROJECT_ROOT, "data/simulations/顺应周期_nav.json")
    signal_file = sys.argv[2] if len(sys.argv) > 2 else \
        os.path.join(PROJECT_ROOT, "data/simulations/顺应周期_signal_equity.json")

    if not os.path.exists(nav_file):
        print(f"ERROR: NAV simulation not found: {nav_file}")
        print("Run simulate_nav.py first.")
        sys.exit(1)

    report = compare(nav_file, signal_file)

    if report:
        output_file = os.path.join(PROJECT_ROOT,
                                   "data/simulations/顺应周期_comparison.json")
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        print("=" * 60)
        print("  顺应周期: 仓位模拟 vs 信号模拟 对比报告")
        print("=" * 60)
        print(f"\n期间: {report['comparison_period']}")
        print(f"重叠交易日: {report['overlapping_days']}")
        print(f"\n--- 收益 ---")
        print(f"仓位模拟 NAV 收益: {report['returns']['position_based_nav_return_pct']:+.2f}%")
        print(f"信号模拟 权益收益: {report['returns']['signal_based_equity_return_pct']:+.2f}%")
        print(f"差异: {report['returns']['difference_pct']:+.2f}%")
        print(f"\n--- 风险 ---")
        print(f"仓位模拟 最大回撤: {report['risk']['position_max_drawdown_pct']:+.2f}%")
        print(f"信号模拟 最大回撤: {report['risk']['signal_max_drawdown_pct']:+.2f}%")
        print(f"仓位模拟 日均仓位: {report['risk']['position_avg_position_pct']:.1f}%")
        print(f"\n--- 活跃度 ---")
        print(f"仓位调仓次数: {report['activity']['position_changes']}")
        print(f"信号交易次数: {report['activity']['signal_trades']}")
        print(f"信号总数: {report['activity']['signal_signals']}")
        print(f"\n--- 相关性 ---")
        print(f"20日滚动相关均值: {report['correlation']['avg_20d_rolling']:.3f}")
        print(f"\n--- 解读 ---")
        print(report['returns']['interpretation'])
        print(f"\n详细数据已保存至: {output_file}")
