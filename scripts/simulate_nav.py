"""
Simulate 顺应周期's portfolio NAV from his position disclosures.
July 2026 MVP with real index data (沪深300, 创业板指, 上证综指).
"""
import json, os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# === Position timeline (hand-extracted from 顺应周期 July 2026 posts) ===
# Format: (date, description, {index: units})
# 1 unit = 1成 = 10% of portfolio. Cash = 10 - total_units 成.
#
# Index keys match market data: 上证指数, 沪深300, 创业板指
# (上证50/中证500/中证1000/科创50 not in data yet → proxied)

JULY_POSITIONS = [
    ("2026-07-13", "5成:银行1+保险1+券商1+药1+酒1",
     {"上证指数": 3.0, "上证指数": 2.0}),  # all proxied to 上证指数 for now
    # Actually let's fix: SH50, CSI500 proxied to 沪深300
]

# Better approach: 3 available indices (上证,沪深300,创业板指), proxy the rest
# SH50 → 沪深300
# CSI500, CSI1000 → 中证500 not available → 沪深300 proxy
# KC50 → 创业板指 proxy
# CSI300 → 沪深300
# CYB → 创业板指

MAP_TO_AVAILABLE = {
    "SH50": "沪深300",
    "CSI300": "沪深300",
    "CSI500": "沪深300",
    "CSI1000": "沪深300",
    "CYB": "创业板指",
    "KC50": "创业板指",
}

JULY_POSITIONS = [
    # date, desc, {available_index: units}
    ("2026-07-13", "5成:银行1+保险1+券商1+药1+酒1",
     {"沪深300": 5.0}),  # all SH50/CSI500 proxy
    ("2026-07-14", "进1成宽基→6成",
     {"沪深300": 6.0}),
    ("2026-07-15", "出1成宽基,减1成药→4成:银行保险券商酒",
     {"沪深300": 4.0}),
    ("2026-07-20", "减1成银行,加1成创业板,宽基剩2成→6成",
     {"沪深300": 5.0, "创业板指": 1.0}),
    ("2026-07-21", "1证券+1保险+1白酒+1双创+1创业板+3综指→8成",
     {"沪深300": 5.5, "创业板指": 2.5}),  # 证券+保险+白酒+3综指=6→CSI300, 1.5双创+1创业板=2.5→CYB
    ("2026-07-22", "减双创+抛白酒+减2成综指→4成:1证券+1保险+1创业板+1综指",
     {"沪深300": 3.0, "创业板指": 1.0}),
    ("2026-07-24", "回补1.5成宽基→5.5成",
     {"沪深300": 4.5, "创业板指": 1.0}),
    ("2026-07-26", "3成宽基+2成金融+1创业板→6成",
     {"沪深300": 5.0, "创业板指": 1.0}),
    ("2026-07-27", "1证券+1保险+1综指etf+3创业板etf→6成",
     {"沪深300": 3.0, "创业板指": 3.0}),
    ("2026-07-28", "+1成科创50→7成:1证券+1保险+1综指+3创业板+1科创",
     {"沪深300": 3.0, "创业板指": 4.0}),
]

# === Load market data ===
market = json.load(open(os.path.join(PROJECT_ROOT, "data/market/market_data.json"), encoding="utf-8"))
prices = {}
for idx in ["上证指数", "沪深300", "创业板指"]:
    rows = market[idx]
    px = {}
    for r in rows:
        px[r["日期"]] = float(r["收盘"])
    prices[idx] = px

# === NAV simulation with daily rebalancing ===
def sim(positions, prices, start_date="2026-07-13", end_date="2026-07-29"):
    """Simulate daily NAV with position snapshots. When position changes, rebalance at close."""
    all_dates = sorted(prices["上证指数"].keys())

    # Build daily returns for each index (percentage)
    returns = {}
    for idx, px in prices.items():
        rets = {}
        for i in range(1, len(all_dates)):
            today = all_dates[i]
            yesterday = all_dates[i-1]
            p_today = px.get(today)
            p_yest = px.get(yesterday)
            if p_today and p_yest and p_yest > 0:
                rets[today] = (p_today / p_yest - 1)  # decimal
        returns[idx] = rets

    pos_idx = 0
    current_pos = None
    nav = 1.0
    nav_series = []
    first_day = True

    for day in all_dates:
        if day < start_date or day > end_date:
            continue

        # Update position if snapshot on or before today
        while pos_idx < len(positions) and positions[pos_idx][0] <= day:
            current_pos = positions[pos_idx][2]
            pos_idx += 1

        if current_pos is None:
            continue

        # First day: just record NAV=1.0, no return applied
        if first_day:
            nav_series.append((day, 1.0))
            first_day = False
            continue

        # Apply today's return to each position
        daily_pnl = 0.0
        for idx, units in current_pos.items():
            ret = returns.get(idx, {}).get(day, 0)
            daily_pnl += units * ret

        nav *= (1 + daily_pnl / 10.0)
        nav_series.append((day, round(nav, 6)))

    return nav_series

nav = sim(JULY_POSITIONS, prices)

# Benchmark: buy-and-hold 沪深300
bm_nav = 1.0
bm_series = []
for day in sorted(prices["沪深300"].keys()):
    if "2026-07-13" <= day <= "2026-07-29":
        if len(bm_series) == 0:
            bm_series.append((day, 1.0))
        else:
            ret = (prices["沪深300"].get(day, 0) / prices["沪深300"].get(bm_series[-1][0], 1) - 1) if bm_series[-1][0] in prices["沪深300"] else 0
            bm_nav *= (1 + ret)
            bm_series.append((day, round(bm_nav, 6)))

# Output
with open("_nav_july.txt", "w", encoding="utf-8") as f:
    f.write("=== 顺应周期 July 2026 组合净值模拟 ===\n\n")
    f.write("仓位轨迹:\n")
    for d, desc, pos in JULY_POSITIONS:
        total = sum(pos.values())
        items = "+".join(f"{v}{k[:4]}" for k,v in pos.items())
        f.write(f"  {d} {desc}: {items} (总{total}成, 现金{10-total}成)\n")

    f.write(f"\n{'日期':<12} {'组合NAV':>10} {'沪深300基准':>12} {'创业板指基准':>12}\n")
    f.write("-"*50 + "\n")
    for i, (day, n) in enumerate(nav):
        bm = bm_series[i][1] if i < len(bm_series) else 0
        cyb_px = prices["创业板指"].get(day, 0)
        cyb0 = prices["创业板指"].get("2026-07-13", 1)
        cyb_nav = cyb_px / cyb0 if cyb0 > 0 else 0
        f.write(f"{day:<12} {n:>10.6f} {bm:>12.6f} {cyb_nav:>12.6f}\n")

    if nav:
        ret = (nav[-1][1] / 1.0 - 1) * 100
        bm_ret = (bm_series[-1][1] / 1.0 - 1) * 100 if bm_series else 0
        f.write(f"\n组合收益: {ret:+.2f}%")
        f.write(f"\n沪深300基准: {bm_ret:+.2f}%")
        f.write(f"\n超额: {ret - bm_ret:+.2f}%\n")

print("Done — see _nav_july.txt")
if nav:
    ret = (nav[-1][1] - 1) * 100
    print(f"Portfolio return Jul 13-29: {ret:+.2f}%")
