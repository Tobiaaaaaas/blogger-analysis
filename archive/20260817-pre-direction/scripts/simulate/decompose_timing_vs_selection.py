"""
Decompose returns into timing (仓位变化) vs beta selection (标的配置).
"""
import json, os, math

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load NAV simulation
with open(os.path.join(PROJECT_ROOT, "data/simulations/顺应周期_nav.json"), "r", encoding="utf-8") as f:
    nav = json.load(f)

nav_series = nav["nav_series"]["portfolio"]
actual_nav_end = nav_series[-1]["nav"]

# Load market prices
with open(os.path.join(PROJECT_ROOT, "data/market/market_data.json"), "r", encoding="utf-8") as f:
    market = json.load(f)

code_to_key = {
    "000016": "上证50", "000300": "沪深300", "399006": "创业板指",
    "000688": "科创50", "000905": "中证500", "000852": "中证1000",
}

# Build daily returns
prices = {}
daily_returns = {}
for code, key in code_to_key.items():
    px = {}
    for r in market.get(key, []):
        px[r["日期"]] = float(r["收盘"])
    prices[code] = px
    rets = {}
    prev = None
    prev_px = None
    for d in sorted(px.keys()):
        if prev is not None and prev_px is not None and prev_px > 0:
            rets[d] = px[d] / prev_px - 1
        prev = d
        prev_px = px[d]
    daily_returns[code] = rets

# Avg total position (position_pct / 10 = total_units in 成)
for d in nav_series:
    d["total_units"] = d.get("position_pct", 0) / 10.0
avg_total = sum(d["total_units"] for d in nav_series) / len(nav_series)

# ==============
# SCENARIO A: Pure Timing
# Each day: actual total_units, but EQUALLY split across all held indices
# ==============
timing_nav = 1.0
timing_series = []
for d in nav_series:
    date = d["date"]
    total = d.get("total_units", 0)
    pos_detail = d.get("positions_detail", {})
    if total > 0 and pos_detail:
        n = len(pos_detail)
        daily_ret = sum(daily_returns.get(c, {}).get(date, 0) * (total / n) for c in pos_detail)
        timing_nav *= (1 + daily_ret / 10.0)
    timing_series.append((date, timing_nav))

# ==============
# SCENARIO B: Pure Selection
# Each day: FIXED total (avg 4.6成), but actual composition proportions
# ==============
selection_nav = 1.0
selection_series = []
for d in nav_series:
    date = d["date"]
    pos_detail = d.get("positions_detail", {})
    total = d.get("total_units", 0)
    if total > 0 and pos_detail and avg_total > 0:
        scale = avg_total / total
        daily_ret = sum(daily_returns.get(c, {}).get(date, 0) * (u * scale) for c, u in pos_detail.items())
        selection_nav *= (1 + daily_ret / 10.0)
    selection_series.append((date, selection_nav))

# ==============
# SCENARIO C: Naive static benchmark
# Fixed avg_total equally split across all 6 indices
# ==============
naive_nav = 1.0
for d in nav_series:
    date = d["date"]
    daily_ret = sum(daily_returns.get(c, {}).get(date, 0) * (avg_total / 6) for c in code_to_key)
    naive_nav *= (1 + daily_ret / 10.0)

# ==============
# RESULTS
# ==============
def max_dd(series):
    peak = 1.0
    max_dd_val = 0.0
    for _, n in series:
        if n > peak:
            peak = n
        dd = (n - peak) / peak
        if dd < max_dd_val:
            max_dd_val = dd
    return max_dd_val * 100

timing_ret = (timing_nav - 1.0) * 100
selection_ret = (selection_nav - 1.0) * 100
naive_ret = (naive_nav - 1.0) * 100
actual_ret = (actual_nav_end - 1.0) * 100

# Timing alpha vs naive
timing_alpha = (timing_nav / naive_nav - 1) * 100
selection_alpha = (selection_nav / naive_nav - 1) * 100

timing_dd = max_dd(timing_series)
selection_dd = max_dd(selection_series)

# Monthly breakdown
def monthly_returns(series_ref, label_ref):
    """Extract monthly returns from a nav series aligned with nav_series dates."""
    by_month = {}
    for i, item in enumerate(series_ref):
        date = item[0]
        nav_val = item[1]
        m = date[:7]
        if m not in by_month:
            by_month[m] = {"first": nav_val, "last": nav_val}
        by_month[m]["last"] = nav_val
    return {m: (v["last"] / v["first"] - 1) * 100 for m, v in by_month.items()}

actual_monthly = {}
for i in range(len(nav_series)):
    m = nav_series[i]["date"][:7]
    if m not in actual_monthly:
        actual_monthly[m] = {"first": nav_series[i]["nav"], "last": nav_series[i]["nav"]}
    actual_monthly[m]["last"] = nav_series[i]["nav"]
actual_monthly = {m: (v["last"] / v["first"] - 1) * 100 for m, v in actual_monthly.items()}

timing_monthly = monthly_returns(timing_series, "timing")
selection_monthly = monthly_returns(selection_series, "selection")

print("=" * 72)
print("  顺应周期 收益归因：择时 vs 选β")
print("=" * 72)
print()
print(f"  策略                           | 收益       | 最大回撤")
print(f"  -------------------------------|------------|----------")
print(f"  实际组合 (择时+选β)            | {actual_ret:+7.2f}%  | {actual_ret:+7.2f}% mirror")
t_dd = max_dd(timing_series)
s_dd = max_dd(selection_series)
print(f"  A. 纯择时 (等权分配, 仓位变化) | {timing_ret:+7.2f}%  | {timing_dd:+7.2f}%")
print(f"  B. 纯选β (固定{avg_total:.0f}成, 标的变化)  | {selection_ret:+7.2f}%  | {selection_dd:+7.2f}%")
print(f"  C. 静态基准 (固定{avg_total:.0f}成, 等权6指)  | {naive_ret:+7.2f}%  | {'—':>7}")
print()

print("  --- 超额分解 (相对静态基准) ---")
print(f"  择时超额 (A - C):  {timing_alpha:+.2f}%")
print(f"  选β超额 (B - C):   {selection_alpha:+.2f}%")
print(f"  择时占比:          {abs(timing_alpha)/(abs(timing_alpha)+abs(selection_alpha))*100:.0f}%")
print(f"  选β占比:           {abs(selection_alpha)/(abs(timing_alpha)+abs(selection_alpha))*100:.0f}%")
print()

print("  --- 月度超额拆解 ---")
print(f"  {'月份':<8} {'实际':>8} {'择时贡献':>10} {'选β贡献':>10} {'主导':>6}")
for m in sorted(actual_monthly):
    act = actual_monthly.get(m, 0)
    tim = timing_monthly.get(m, 0)
    sel = selection_monthly.get(m, 0)
    tim_ex = tim - act
    sel_ex = sel - act
    if abs(tim_ex) > abs(sel_ex):
        dom = "择时" if tim_ex > sel_ex else "择时"
    else:
        dom = "选β" if sel_ex > tim_ex else "选β"
    print(f"  {m:<8} {act:>+7.2f}% {tim_ex:>+9.2f}% {sel_ex:>+9.2f}% {dom:>6}")

# === KEY INSIGHT ===
print()
print("  --- 风险来源拆解 ---")
print(f"  组合回撤:     {actual_ret:+7.2f}% (来自summary)")
tim_vol = math.sqrt(sum((x[1]/y[1]-1)**2 for x,y in zip(timing_series[1:], timing_series[:-1])) / (len(timing_series)-2)) * math.sqrt(252) * 100
sel_vol = math.sqrt(sum((x[1]/y[1]-1)**2 for x,y in zip(selection_series[1:], selection_series[:-1])) / (len(selection_series)-2)) * math.sqrt(252) * 100
print(f"  择时策略波动率: {tim_vol:.1f}%")
print(f"  选β策略波动率:  {sel_vol:.1f}%")
print(f"  波动率差异说明: 择时策略波动{'更大' if tim_vol > sel_vol else '更小'} → 仓位变化是{'放大' if tim_vol > sel_vol else '缩小'}波动的因素")

# Final verdict
print()
print("  ═══════════════════════════════════")
if abs(timing_alpha) > abs(selection_alpha):
    print(f"  结论：博主的收益/风险主要来自 ★择时★")
    print(f"  择时贡献 {timing_alpha:+.2f}% vs 选β贡献 {selection_alpha:+.2f}%")
else:
    print(f"  结论：博主的收益/风险主要来自 ★标的配置★")
    print(f"  选β贡献 {selection_alpha:+.2f}% vs 择时贡献 {timing_alpha:+.2f}%")
print("  ═══════════════════════════════════")
