"""
What if: blogger only manages position sizing, all capital in CSI300?
"""
import json, os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

with open(os.path.join(PROJECT_ROOT, "data/simulations/顺应周期_nav.json"), "r", encoding="utf-8") as f:
    nav = json.load(f)

nav_series = nav["nav_series"]["portfolio"]

with open(os.path.join(PROJECT_ROOT, "data/market/market_data.json"), "r", encoding="utf-8") as f:
    market = json.load(f)

# CSI300 daily returns
px = {}
for r in market.get("沪深300", []):
    px[r["日期"]] = float(r["收盘"])

daily_ret = {}
prev_px = None
for d in sorted(px.keys()):
    if prev_px is not None and prev_px > 0:
        daily_ret[d] = px[d] / prev_px - 1
    prev_px = px[d]

# Scenario: all CSI300, same position sizing
csi300_nav = 1.0
csi300_series = []

for d in nav_series:
    date = d["date"]
    total_units = d.get("position_pct", 0) / 10.0
    ret = daily_ret.get(date, 0.0)
    daily_ret_pct = total_units * ret / 10.0
    csi300_nav *= (1 + daily_ret_pct)
    csi300_series.append((date, csi300_nav, total_units * 10))

# Fixed 4.6成 CSI300 (no timing)
avg_units = sum(d.get("position_pct", 0) / 10 for d in nav_series) / len(nav_series)
fixed_nav = 1.0
for date, _, _ in csi300_series:
    ret = daily_ret.get(date, 0.0)
    fixed_nav *= (1 + avg_units * ret / 10.0)

# Buy-and-hold CSI300
first_date = nav_series[0]["date"]
bh_nav = 1.0
if first_date in px and nav_series[-1]["date"] in px:
    bh_nav = px[nav_series[-1]["date"]] / px[first_date]

# Max DD
def max_dd(series):
    peak = 1.0
    dd = 0.0
    for _, n, *_ in series:
        if n > peak:
            peak = n
        d = (n - peak) / peak
        if d < dd:
            dd = d
    return dd * 100

actual_ret = (nav_series[-1]["nav"] - 1) * 100
csi300_ret = (csi300_nav - 1) * 100
fixed_ret = (fixed_nav - 1) * 100
bh_ret = (bh_nav - 1) * 100

actual_dd = nav["summary"]["risk"]["max_drawdown_pct"]
csi300_dd = max_dd(csi300_series)

# Volatility
import math
for name, ser in [("实际", [(0, d["nav"], 0) for d in nav_series]),
                   ("CSI300版", csi300_series)]:
    rets = []
    for i in range(1, len(ser)):
        rets.append(ser[i][1] / ser[i-1][1] - 1)
    if rets:
        vol = math.sqrt(sum((r - sum(rets)/len(rets))**2 for r in rets) / (len(rets)-1)) * math.sqrt(252) * 100

print("=" * 65)
print("  全仓沪深300 + 博主仓位管理 vs 原始组合")
print("=" * 65)
print()
print(f"  {'Strategy':<35} {'Return':>8} {'MaxDD':>8} {'Vol':>8}")
print(f"  {'-'*35} {'-'*8} {'-'*8} {'-'*8}")

# Calc vol for both
def vol(series):
    rets = []
    for i in range(1, len(series)):
        rets.append(series[i][1] / series[i-1][1] - 1)
    if not rets:
        return 0
    return math.sqrt(sum((r - sum(rets)/len(rets))**2 for r in rets) / (len(rets)-1)) * math.sqrt(252) * 100

actual_vol = vol([(0, d["nav"], 0) for d in nav_series])
csi300_vol = vol(csi300_series)

print(f"  {'1. Original (timing + selection)':<35} {actual_ret:>+7.2f}% {actual_dd:>+7.2f}% {actual_vol:>7.2f}%")
print(f"  {'2. All CSI300 + blogger timing':<35} {csi300_ret:>+7.2f}% {csi300_dd:>+7.2f}% {csi300_vol:>7.2f}%")
print(f"  {'3. Fixed {:.0f}% CSI300 (no timing)':<35} {fixed_ret:>+7.2f}% {'--':>8} {'--':>8}".format(avg_units*10))
print(f"  {'4. 100% CSI300 buy-and-hold':<35} {bh_ret:>+7.2f}% {'--':>8} {'--':>8}")
print()

# Monthly
print(f"  {'Month':<8} {'Original':>9} {'CSI300 ver':>10} {'Diff':>8} {'Interpretation':>25}")
print(f"  {'-'*8} {'-'*9} {'-'*10} {'-'*8} {'-'*25}")
for m in ["2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07"]:
    orig_m = [d for d in nav_series if d["date"][:7] == m]
    csi_m = [d for d in csi300_series if d[0][:7] == m]
    if orig_m and csi_m and orig_m[0]["nav"] > 0:
        o_ret = (orig_m[-1]["nav"] / orig_m[0]["nav"] - 1) * 100
        c_ret = (csi_m[-1][1] / csi_m[0][1] - 1) * 100
        diff = c_ret - o_ret
        if diff > 0.3:
            note = "CSI300 wins (selection hurt)"
        elif diff < -0.3:
            note = "Original wins (selection helped)"
        else:
            note = "~same"
        print(f"  {m:<8} {o_ret:>+8.2f}% {c_ret:>+9.2f}% {diff:>+7.2f}% {note:>25}")

# Final verdict
print()
print("  ===== VERDICT =====")
diff_total = csi300_ret - actual_ret
if diff_total > 0:
    print(f"  All-CSI300: {csi300_ret:+.2f}% vs Original: {actual_ret:+.2f}%")
    print(f"  Switching to CSI300 improves by {diff_total:+.2f}%")
    print(f"  Blogger's index selection SUBTRACTED value.")
    print(f"  Pure timing (position sizing) alone is better than timing + selection.")
else:
    print(f"  Original: {actual_ret:+.2f}% vs All-CSI300: {csi300_ret:+.2f}%")
    print(f"  Original beats CSI300-only by {abs(diff_total):+.2f}%")
    print(f"  Blogger's index selection ADDED value.")
    print(f"  His choice of indices is worth keeping.")

# Risk comparison
print()
print(f"  Risk comparison:")
print(f"  Original max DD: {actual_dd:+.2f}%")
print(f"  CSI300-only DD:  {csi300_dd:+.2f}%")
if abs(actual_dd) > abs(csi300_dd):
    print(f"  Original is RISKIER -- diversification across indices increased drawdown")
else:
    print(f"  CSI300-only is RISKIER -- concentrating in one index amplified losses")

# Month-by-month position tracking
print()
print(f"  Position sizing timeline (same in both scenarios):")
for d in nav_series:
    if d["date"][-2:] in ["01", "08", "15", "22", "29"] or d == nav_series[0] or d == nav_series[-1]:
        pos = d["position_pct"]
        bar = "#" * int(pos / 5)
        print(f"    {d['date']}  {pos:5.1f}% |{bar}")
