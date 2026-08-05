"""
SIMULATE — 博主仓位模拟：基于公开仓位披露，模拟组合净值曲线。
仅适用于公开披露具体仓位的博主（如 顺应周期）。

输出 6 层级：
  A. extraction_meta  — 提取元信息
  B. position_snapshots — 仓位时间线
  C. nav_series       — 净值曲线（组合 + 3 基准）
  D. summary          — 绩效摘要（~20 指标）
  E. trade_log        — 交易明细（含同日多笔操作）
  F. attribution      — 归因与对比
"""
import json, os, math
from datetime import datetime, timedelta
from collections import defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# === 标的映射 (blogger ticker → index code) ===
TICKER_MAP = {
    "银行": "000016", "保险": "000016", "券商": "000016", "证券": "000016",
    "白酒": "000016", "酒": "000016", "金融": "000016", "老登": "000016",
    "上证50": "000016",
    "宽基": "000300", "上证综指": "000300", "综指": "000300",
    "上证": "000300", "沪深300": "000300", "综指etf": "000300",
    "创业板": "399006", "创业板etf": "399006",
    "科创": "000688", "科创50": "000688",
    "创新药": "000905", "药": "000905", "医药": "000905",
    "中证500": "000905", "有色": "000905",
    "中证1000": "000852", "小登": "000852", "题材股": "000852",
}

INDEX_NAME = {
    "000016": "上证50", "000300": "沪深300", "399006": "创业板指",
    "000688": "科创50", "000905": "中证500", "000852": "中证1000",
    "000001": "上证综指"
}

CODE_TO_MARKET_KEY = {
    "000016": "上证50", "000300": "沪深300", "399006": "创业板指",
    "000688": "科创50", "000905": "中证500", "000852": "中证1000",
}


# === 加载市场数据 ===
def load_market_data():
    market = json.load(open(os.path.join(PROJECT_ROOT, "data/market/market_data.json"),
                           encoding="utf-8"))
    prices = {}
    for idx_name, rows in market.items():
        px = {}
        for r in rows:
            px[r["日期"]] = float(r["收盘"])
        prices[idx_name] = px
    return prices


# === 加载仓位数据 ===
def load_positions(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


# === 参考价格判定 ===
def resolve_effective_date(publish_date, publish_time, all_dates, trading_dates_set):
    """Determine the trading day on which a position change takes effect,
    and which reference price to use.

    Rules (from SIMULATE.md):
    - Trading day + before open (<9:30)   → today's OPEN  → effective today
    - Trading day + during (9:30-15:00)   → nearest K-line close after post time → effective today
    - Trading day + after close (>15:00)  → NEXT trading day's OPEN
    - Non-trading day                     → NEXT trading day's OPEN
    - Unknown time                        → NEXT trading day's OPEN (conservative)

    Returns (effective_date, price_type):
      effective_date: trading day string (YYYY-MM-DD) when position activates
      price_type: "open" | "intraday" — which reference price to use
    """
    hour = minute = None
    if publish_time:
        try:
            time_part = publish_time.split(" ")[-1].split("T")[-1]
            parts = time_part.split(":")
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            pass

    is_trading_day = publish_date in trading_dates_set

    next_td = None
    for d in all_dates:
        if d > publish_date:
            next_td = d
            break
    if next_td is None:
        next_td = publish_date

    if not is_trading_day:
        return next_td, "open"

    if hour is None:
        return next_td, "open"

    if hour < 9 or (hour == 9 and minute < 30):
        return publish_date, "open"
    elif hour < 15:
        return publish_date, "intraday"
    else:
        return next_td, "open"


def resolve_intraday_price(price_type, publish_date, publish_time, daily_prices, minute_prices=None):
    """Resolve the actual reference price for a position change.

    Args:
        price_type: "open" | "intraday"
        publish_date: date string YYYY-MM-DD
        publish_time: full timestamp string
        daily_prices: dict of date -> {open, close} for the relevant index
        minute_prices: optional, list of {time, close} for minute-level granularity

    Returns:
        (reference_date, reference_price)
    """
    if price_type == "open":
        px = daily_prices.get(publish_date, {})
        return publish_date, px.get("open", px.get("close", 0))

    elif price_type == "intraday":
        # Use the K-line whose time window CONTAINS post_time
        # 1-min: post 14:23:15 → bar at 14:23 with close price
        # 5-min: post 14:23:15 → bar at 14:25 covering 14:20-14:25
        # Fallback: daily close
        if minute_prices:
            for bar in minute_prices:
                if bar["time"] >= publish_time:
                    return bar["time"][:10], bar["close"]
            if minute_prices:
                return minute_prices[-1]["time"][:10], minute_prices[-1]["close"]

        px = daily_prices.get(publish_date, {})
        return publish_date, px.get("close", 0)

    # Fallback
    px = daily_prices.get(publish_date, {})
    return publish_date, px.get("close", 0)


# === 构建快照时间线 ===
def build_snapshot_timeline(snapshots, all_dates):
    """Resolve effective dates for all snapshots and sort into a timeline.
    Each snapshot produces one event; same-day events are preserved individually.

    Returns:
      timeline: list of snapshots sorted by (_effective_date, publish_time),
                each annotated with _effective_date, _price_type, _seq (intraday sequence)
    """
    trading_dates_set = set(all_dates)
    annotated = []

    for s in snapshots:
        pub_date = s.get("date", "")
        pub_time = s.get("publish_time", None)
        eff_date, price_type = resolve_effective_date(
            pub_date, pub_time, all_dates, trading_dates_set
        )
        s_copy = dict(s)
        s_copy["_effective_date"] = eff_date
        s_copy["_price_type"] = price_type
        annotated.append(s_copy)

    # Sort by effective date, then by publish_time for same-date ordering
    annotated.sort(key=lambda s: (s["_effective_date"], s.get("publish_time", "")))

    # Assign intraday sequence numbers
    from collections import Counter
    date_seq = Counter()
    for s in annotated:
        date_seq[s["_effective_date"]] += 1
        s["_intraday_seq"] = date_seq[s["_effective_date"]]
        s["_intraday_total"] = 0  # filled below

    # Second pass: fill intraday_total
    totals = {}
    for s in annotated:
        totals[s["_effective_date"]] = max(totals.get(s["_effective_date"], 0), s["_intraday_seq"])
    for s in annotated:
        s["_intraday_total"] = totals[s["_effective_date"]]

    return annotated


# === 解析仓位（处理 partial scaling） ===
def resolve_position(snapshot, current_pos, current_total):
    """Given a snapshot and current state, return the new (positions, total_units).

    Rules (from SIMULATE.md):
    - explicit/inferred: LLM set absolute positions → use directly
    - partial: no position detail, no action verb → scale prior composition proportionally
    """
    new_pos = snapshot.get("positions", {})
    new_total = snapshot.get("total_units", current_total)

    if new_pos and sum(new_pos.values()) > 0:
        # Explicit/inferred: LLM already set absolute positions
        return dict(new_pos), new_total
    elif current_pos and new_total != current_total:
        # Partial: no detail, scale last-known composition
        scale = new_total / max(0.001, current_total)
        scaled = {k: round(v * scale, 2) for k, v in current_pos.items()}
        scaled = {k: v for k, v in scaled.items() if v > 0.01}
        return scaled, new_total
    else:
        # No change
        return dict(current_pos), new_total


def _describe_positions(positions):
    """Convert positions dict to human-readable description."""
    if not positions:
        return "空仓"
    parts = []
    for code, units in sorted(positions.items()):
        name = INDEX_NAME.get(code, code)
        if units == int(units):
            parts.append(f"{int(units)}{name}")
        else:
            parts.append(f"{units}{name}")
    return "+".join(parts) if parts else "空仓"


# === 基准计算 ===
def compute_benchmarks(prices, all_dates):
    benchmarks = {}

    # CSI300
    key = "沪深300"
    px = prices.get(key, {})
    bm = []
    base = None
    for day in all_dates:
        if day not in px: continue
        if base is None:
            base = px[day]
            bm.append({"date": day, "nav": 1.0})
        else:
            bm.append({"date": day, "nav": round(px[day] / base, 6)})
    benchmarks["000300_CSI300"] = bm

    # Shanghai Composite
    key = "上证综指"
    px = prices.get(key, {})
    bm, base = [], None
    for day in all_dates:
        if day not in px: continue
        if base is None:
            base = px[day]
            bm.append({"date": day, "nav": 1.0})
        else:
            bm.append({"date": day, "nav": round(px[day] / base, 6)})
    benchmarks["000001_SH"] = bm

    # Equal weight 6-index basket (monthly rebalance)
    basket_codes = ["000016", "000300", "399006", "000688", "000905", "000852"]
    bm, nav, prev_month = [], 1.0, None
    month_bases = {}
    for day in all_dates:
        current_month = day[:7]
        if current_month != prev_month:
            prev_month = current_month
            month_bases = {}
            for code in basket_codes:
                key = CODE_TO_MARKET_KEY.get(code)
                if key and key in prices and day in prices[key]:
                    month_bases[code] = prices[key][day]
            daily_ret = 0.0
        else:
            daily_ret, n = 0.0, 0
            for code in basket_codes:
                key = CODE_TO_MARKET_KEY.get(code)
                if key and key in prices and code in month_bases and day in prices[key]:
                    daily_ret += prices[key][day] / month_bases[code] - 1
                    n += 1
            daily_ret = daily_ret / n if n > 0 else 0.0
        if bm:
            nav = bm[-1]["nav"] * (1 + daily_ret)
        bm.append({"date": day, "nav": round(nav, 6)})
    benchmarks["equal_weight_6"] = bm

    return benchmarks


# === 主模拟函数 ===
def simulate(positions_file, start_date=None, end_date=None):
    """Run full position-based NAV simulation with intraday support."""

    prices = load_market_data()
    snapshots_raw = load_positions(positions_file)

    first_idx = list(prices.keys())[0]
    all_dates = sorted(prices[first_idx].keys())

    # Build daily returns for each investable index
    daily_returns = {}
    for code, key in CODE_TO_MARKET_KEY.items():
        px = prices.get(key, {})
        rets = {}
        prev = None
        for day in all_dates:
            if day not in px: continue
            if prev is not None and prev in px and px[prev] > 0:
                rets[day] = px[day] / px[prev] - 1
            prev = day
        daily_returns[code] = rets

    # Build snapshot timeline
    timeline = build_snapshot_timeline(snapshots_raw, all_dates)

    # Find first snapshot with explicit position detail
    first_explicit = None
    for i, s in enumerate(timeline):
        if s.get("positions") and sum(s["positions"].values()) > 0:
            first_explicit = i
            break
    if first_explicit is None:
        raise ValueError("No snapshot with position detail found")

    # Remove snapshots before the first explicit one (can't simulate without data)
    timeline = timeline[first_explicit:]

    # Fast-forward to custom start_date: resolve initial position from prior snapshots
    sim_start = start_date if start_date else timeline[0]["_effective_date"]
    current_pos = {}
    current_total = 0.0
    pre_snaps = [s for s in timeline if s["_effective_date"] <= sim_start]
    if pre_snaps:
        for s in pre_snaps:
            current_pos, current_total = resolve_position(s, current_pos, current_total)
        # Remove processed snapshots from timeline
        timeline = [s for s in timeline if s["_effective_date"] > sim_start]
    else:
        current_pos, current_total = resolve_position(timeline[0], {}, 0.0)
        timeline = timeline[1:]

    if end_date is None:
        end_date = snapshots_raw[-1]["date"] if snapshots_raw else "2026-07-31"

    sim_dates = [d for d in all_dates if sim_start <= d <= end_date]
    benchmarks = compute_benchmarks(prices, sim_dates)

    # === SIMULATE (intraday-aware) ===
    nav = 1.0
    nav_series = []
    trade_log = []
    trade_id = 0

    current_pos = {}       # positions currently held
    current_total = 0.0    # total units currently held
    earn_positions = {}    # positions that will earn NEXT day's return
    snap_idx = 0

    for day in sim_dates:
        # Collect all snapshots effective today
        day_snaps = []
        while snap_idx < len(timeline) and timeline[snap_idx]["_effective_date"] == day:
            day_snaps.append(timeline[snap_idx])
            snap_idx += 1

        # --- Apply daily return ---
        # Which positions earn today's return?
        # "open" (before market) → new position earns today's return
        # "intraday" → old position earns today, new earns tomorrow
        # No snapshots → yesterday's end position earns it
        if nav_series:
            daily_pnl = 0.0
            if day_snaps and day_snaps[0]["_price_type"] == "open":
                # First snapshot is open-type: resolve its position first
                earn_pos, _ = resolve_position(day_snaps[0], dict(current_pos), current_total)
            else:
                earn_pos = dict(current_pos)

            for code, units in earn_pos.items():
                ret = daily_returns.get(code, {}).get(day, 0.0)
                daily_pnl += units * ret
            daily_ret_pct = daily_pnl / 10.0 if earn_pos else 0.0
            nav *= (1 + daily_ret_pct)
        else:
            daily_ret_pct = 0.0

        # --- Process each intraday snapshot ---
        for si, snap in enumerate(day_snaps):
            new_pos, new_total = resolve_position(snap, current_pos, current_total)

            # Determine entry NAV for this trade
            if not trade_log and si == 0 and not nav_series:
                # First trade ever: entry_nav = 1.0
                entry_nav = 1.0
            elif si == 0 and day_snaps[0]["_price_type"] == "open" and not nav_series:
                entry_nav = 1.0
            elif si == 0:
                # First snapshot of the day: look back to when current_pos was established
                entry_nav = nav_series[-1]["nav"] if nav_series else 1.0
            else:
                # Intraday change: entry is current NAV (same day)
                entry_nav = nav

            # Record trade if position actually changed
            if current_pos != new_pos or (si == 0 and not current_pos):
                trade_id += 1

                if not current_pos:
                    trade_type = "open"
                elif not new_pos:
                    trade_type = "close"
                else:
                    trade_type = "rebalance"

                # Holding period: from when current_pos was established to now
                holding_days = 0
                return_pct = 0.0
                if current_pos and nav_series:
                    # Find when current_pos was first established
                    for j in range(len(nav_series) - 1, -1, -1):
                        if nav_series[j].get("positions_detail") == current_pos:
                            continue
                        # Found the transition point
                        entry_day_nav = nav_series[j]["nav"]
                        holding_days = len(nav_series) - j
                        return_pct = round((nav / entry_day_nav - 1) * 100, 2) if entry_day_nav > 0 else 0.0
                        break

                trade_log.append({
                    "trade_id": trade_id,
                    "type": trade_type,
                    "date": day,
                    "publish_time": snap.get("publish_time", ""),
                    "intraday_seq": f"{snap['_intraday_seq']}/{snap['_intraday_total']}"
                        if snap["_intraday_total"] > 1 else "1/1",
                    "entry_positions": dict(current_pos),
                    "entry_description": _describe_positions(current_pos),
                    "exit_positions": dict(new_pos),
                    "exit_description": _describe_positions(new_pos),
                    "entry_nav": round(entry_nav, 6),
                    "exit_nav": round(nav, 6),
                    "holding_days": holding_days,
                    "return_pct": return_pct,
                    "description": snap.get("description", ""),
                    "confidence": snap.get("confidence", ""),
                    "was_intraday": snap["_intraday_total"] > 1,
                })

            # Update current position and earn_positions
            current_pos = new_pos
            current_total = new_total

            if snap["_price_type"] == "open":
                # Before-market trade: new position earned today's return, continues
                earn_positions = dict(current_pos)
            # "intraday": new position earns NEXT day's return

        # After processing all snapshots, earn_positions for tomorrow
        # is the final position of today
        if day_snaps:
            last_snap = day_snaps[-1]
            if last_snap["_price_type"] == "intraday":
                earn_positions = dict(current_pos)
        else:
            earn_positions = dict(current_pos)

        # Record daily NAV
        nav_series.append({
            "date": day,
            "nav": round(nav, 6),
            "daily_return_pct": round(daily_ret_pct * 100, 4),
            "position_pct": round(current_total * 10, 1),
            "cash_pct": round((10 - current_total) * 10, 1),
            "positions_detail": dict(current_pos),
            "intraday_changes": len(day_snaps),
        })

    # === A. Extraction Meta ===
    extraction_meta = _build_extraction_meta(snapshots_raw, timeline, sim_start, end_date)

    # === D. Summary ===
    summary = _build_summary(nav_series, benchmarks, daily_returns)

    # === F. Attribution ===
    attribution = _build_attribution(nav_series, benchmarks, trade_log, daily_returns, timeline)

    return {
        "extraction_meta": extraction_meta,
        "position_snapshots": timeline,
        "nav_series": {
            "portfolio": nav_series,
            "benchmarks": benchmarks
        },
        "summary": summary,
        "trade_log": trade_log,
        "attribution": attribution
    }


# === A. Extraction Meta ===
def _build_extraction_meta(snapshots_raw, timeline, start_date, end_date):
    dates = sorted(set(s["_effective_date"] for s in timeline))
    gaps = []
    for i in range(1, len(dates)):
        d1 = datetime.strptime(dates[i-1], "%Y-%m-%d")
        d2 = datetime.strptime(dates[i], "%Y-%m-%d")
        gap = (d2 - d1).days
        if gap > 7:
            gaps.append({"start": dates[i-1], "end": dates[i], "days": gap})

    total_units_all = sum(s.get("total_units", 0) for s in snapshots_raw)
    unmapped_units = sum(sum(v for v in s.get("unmapped", {}).values()) for s in snapshots_raw)

    explicit = sum(1 for s in snapshots_raw if s.get("confidence") == "explicit")
    inferred = sum(1 for s in snapshots_raw if s.get("confidence") == "inferred")
    partial = sum(1 for s in snapshots_raw if s.get("confidence") == "partial")

    unmapped_items = set()
    for s in snapshots_raw:
        if s.get("unmapped"):
            unmapped_items.update(s["unmapped"].keys())

    # Count intraday events
    intraday_dates = sum(1 for s in timeline if s["_intraday_total"] > 1)

    avg_interval = (datetime.strptime(dates[-1], "%Y-%m-%d") -
                    datetime.strptime(dates[0], "%Y-%m-%d")).days / max(1, len(dates) - 1)

    return {
        "blogger": "顺应周期",
        "total_posts": None,
        "simulation_start": start_date,
        "simulation_end": end_date,
        "position_snapshots_extracted": len(snapshots_raw),
        "timeline_events": len(timeline),
        "dates_with_intraday_changes": intraday_dates,
        "confidence_breakdown": {"explicit": explicit, "inferred": inferred, "partial": partial},
        "avg_update_interval_days": round(avg_interval, 1),
        "max_gap_days": max(g["days"] for g in gaps) if gaps else 0,
        "gap_periods": gaps,
        "mapped_exposures_pct": round(100 - (unmapped_units / max(1, total_units_all + unmapped_units) * 100), 1),
        "unmapped_exposures_pct": round(unmapped_units / max(1, total_units_all + unmapped_units) * 100, 1),
        "unmapped_items": sorted(list(unmapped_items))
    }


# === D. Summary ===
def _build_summary(nav_series, benchmarks, daily_returns):
    if not nav_series:
        return {}

    navs = [x["nav"] for x in nav_series]
    returns = [x["daily_return_pct"] for x in nav_series[1:]]
    positions_pct = [x["position_pct"] for x in nav_series]

    total_ret = (navs[-1] - 1.0) * 100
    days = len(navs)
    ann_ret = ((navs[-1] / 1.0) ** (252 / max(1, days)) - 1) * 100

    bm_csi300_ret = 0.0
    if "000300_CSI300" in benchmarks and benchmarks["000300_CSI300"]:
        bm_csi300_ret = (benchmarks["000300_CSI300"][-1]["nav"] - 1.0) * 100
    bm_sh_ret = 0.0
    if "000001_SH" in benchmarks and benchmarks["000001_SH"]:
        bm_sh_ret = (benchmarks["000001_SH"][-1]["nav"] - 1.0) * 100
    bm_basket_ret = 0.0
    if "equal_weight_6" in benchmarks and benchmarks["equal_weight_6"]:
        bm_basket_ret = (benchmarks["equal_weight_6"][-1]["nav"] - 1.0) * 100

    alpha_csi300 = total_ret - bm_csi300_ret
    alpha_basket = total_ret - bm_basket_ret

    best_day = max(returns) if returns else 0
    worst_day = min(returns) if returns else 0

    winning_days = [r for r in returns if r > 0]
    losing_days = [r for r in returns if r < 0]
    win_pct = len(winning_days) / max(1, len(returns)) * 100
    avg_win = sum(winning_days) / max(1, len(winning_days))
    avg_loss = sum(losing_days) / max(1, len(losing_days))
    profit_factor = sum(winning_days) / max(0.001, abs(sum(losing_days)))

    # Max drawdown
    peak = navs[0]
    max_dd_val = 0.0
    dd_peak_date = dd_trough_date = nav_series[0]["date"]
    current_peak = navs[0]
    current_peak_date = nav_series[0]["date"]
    for i, n in enumerate(navs):
        if n > current_peak:
            current_peak = n
            current_peak_date = nav_series[i]["date"]
        dd = (n - current_peak) / current_peak * 100
        if dd < max_dd_val:
            max_dd_val = dd
            dd_peak_date = current_peak_date
            dd_trough_date = nav_series[i]["date"]

    # Volatility
    if len(returns) > 1:
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
        vol = math.sqrt(variance) * math.sqrt(252)
    else:
        vol = 0.0

    # Sortino
    downside = [r for r in returns if r < 0]
    if downside and len(downside) > 1:
        mean_down = sum(downside) / len(downside)
        down_dev = math.sqrt(sum((r - mean_down)**2 for r in downside) / (len(downside)-1)) * math.sqrt(252)
    else:
        down_dev = 0.0
    sortino = (ann_ret - 2.0) / max(0.01, down_dev)

    # VaR 95%
    var_95 = sorted(returns)[int(len(returns) * 0.05)] if returns else 0.0

    # Streaks
    longest_win = longest_lose = current_win = current_lose = 0
    for r in returns:
        if r > 0:
            current_win += 1; current_lose = 0
            longest_win = max(longest_win, current_win)
        elif r < 0:
            current_lose += 1; current_win = 0
            longest_lose = max(longest_lose, current_lose)

    # Position stats
    max_pos = max(positions_pct) if positions_pct else 0
    min_pos = min(positions_pct) if positions_pct else 0
    avg_pos = sum(positions_pct) / len(positions_pct) if positions_pct else 0
    pos_vol = 0.0
    if len(positions_pct) > 1:
        pos_vol = math.sqrt(sum((p - avg_pos) ** 2 for p in positions_pct) / (len(positions_pct) - 1))

    # Information ratio vs CSI300
    ir = 0.0
    if "000300_CSI300" in benchmarks and len(benchmarks["000300_CSI300"]) > 1:
        bm_navs = {x["date"]: x["nav"] for x in benchmarks["000300_CSI300"]}
        excess_rets = []
        for i in range(1, len(nav_series)):
            d = nav_series[i]["date"]
            if d in bm_navs and nav_series[i-1]["date"] in bm_navs:
                port_ret = nav_series[i]["nav"] / nav_series[i-1]["nav"] - 1
                bm_ret = bm_navs[d] / bm_navs[nav_series[i-1]["date"]] - 1
                excess_rets.append(port_ret - bm_ret)
        if excess_rets and len(excess_rets) > 1:
            mean_excess = sum(excess_rets) / len(excess_rets)
            te = math.sqrt(sum((r - mean_excess)**2 for r in excess_rets) / (len(excess_rets) - 1))
            ir = mean_excess / max(0.001, te) * math.sqrt(252)

    # Turnover (count intraday separately)
    pos_changes = sum(1 for i in range(1, len(nav_series))
                      if nav_series[i]["positions_detail"] != nav_series[i-1]["positions_detail"])
    intraday_changes = sum(1 for x in nav_series if x.get("intraday_changes", 0) > 1)
    avg_holding = len(nav_series) / max(1, pos_changes) if pos_changes else 0
    ann_turnover = pos_changes / max(1, len(nav_series)) * 252

    return {
        "simulation_period": f"{nav_series[0]['date']} ~ {nav_series[-1]['date']}",
        "trading_days": days,

        "performance": {
            "total_return_pct": round(total_ret, 2),
            "annualized_return_pct": round(ann_ret, 2),
            "benchmark_csi300_return_pct": round(bm_csi300_ret, 2),
            "benchmark_sh_return_pct": round(bm_sh_ret, 2),
            "benchmark_basket_return_pct": round(bm_basket_ret, 2),
            "alpha_vs_csi300_pct": round(alpha_csi300, 2),
            "alpha_vs_basket_pct": round(alpha_basket, 2),
            "information_ratio_vs_csi300": round(ir, 2),
            "best_day_pct": round(best_day, 2),
            "worst_day_pct": round(worst_day, 2),
            "winning_day_pct": round(win_pct, 1),
            "avg_win_day_pct": round(avg_win, 2),
            "avg_loss_day_pct": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
        },

        "risk": {
            "max_drawdown_pct": round(max_dd_val, 2),
            "max_drawdown_dates": {"peak": dd_peak_date, "trough": dd_trough_date},
            "volatility_annualized_pct": round(vol, 2),
            "downside_deviation_pct": round(down_dev, 2),
            "sortino_ratio": round(sortino, 2),
            "var_95_daily_pct": round(var_95, 2),
            "longest_losing_streak_days": longest_lose,
            "longest_winning_streak_days": longest_win,
        },

        "position": {
            "max_position_pct": round(max_pos, 1),
            "min_position_pct": round(min_pos, 1),
            "avg_position_pct": round(avg_pos, 1),
            "position_volatility_pct": round(pos_vol, 1),
            "days_full_invested": sum(1 for p in positions_pct if p >= 99),
            "days_above_70pct": sum(1 for p in positions_pct if p > 70),
            "days_below_30pct": sum(1 for p in positions_pct if p < 30),
        },

        "turnover": {
            "total_position_changes": pos_changes,
            "intraday_change_days": intraday_changes,
            "avg_holding_days": round(avg_holding, 1),
            "annualized_turnover_rate": round(ann_turnover, 1),
        }
    }


# === F. Attribution ===
def _build_attribution(nav_series, benchmarks, trade_log, daily_returns, timeline):
    if not nav_series:
        return {}

    # PnL by index
    pnl_by_index = {}
    for code in CODE_TO_MARKET_KEY:
        name = f"{code}_{INDEX_NAME.get(code, '')}"
        total_contribution = 0.0
        days_held = 0
        total_weight = 0.0

        for day_data in nav_series:
            if code in day_data.get("positions_detail", {}):
                units = day_data["positions_detail"][code]
                total_weight += units
                days_held += 1
                ret = daily_returns.get(code, {}).get(day_data["date"], 0.0)
                total_contribution += units * ret

        avg_weight = (total_weight / days_held * 10) if days_held > 0 else 0
        pnl_by_index[name] = {
            "total_contribution_pct": round(total_contribution, 2),
            "avg_weight_pct": round(avg_weight, 1),
            "days_held": days_held
        }

    # PnL by month
    pnl_by_month = {}
    for day_data in nav_series:
        month = day_data["date"][:7]
        if month not in pnl_by_month:
            pnl_by_month[month] = {"portfolio_return_pct": 0.0, "benchmark_return_pct": 0.0,
                                    "alpha_pct": 0.0, "position_changes": 0}

    month_navs = {}
    for day_data in nav_series:
        month_navs[day_data["date"][:7]] = day_data["nav"]
    months = sorted(month_navs.keys())
    for i, m in enumerate(months):
        if i == 0:
            pnl_by_month[m]["portfolio_return_pct"] = round((month_navs[m] - 1.0) * 100, 2)
        else:
            prev_m = months[i-1]
            pnl_by_month[m]["portfolio_return_pct"] = round(
                (month_navs[m] / month_navs[prev_m] - 1) * 100, 2)

    # Benchmark monthly returns
    if "000300_CSI300" in benchmarks:
        bm_navs = {x["date"]: x["nav"] for x in benchmarks["000300_CSI300"]}
        for m in months:
            m_dates = [d for d in bm_navs if d[:7] == m]
            if m_dates:
                first_d = min(m_dates)
                last_d = max(m_dates)
                if first_d in bm_navs and last_d in bm_navs:
                    pnl_by_month[m]["benchmark_return_pct"] = round(
                        (bm_navs[last_d] / bm_navs[first_d] - 1) * 100, 2)
        for m in months:
            pnl_by_month[m]["alpha_pct"] = round(
                pnl_by_month[m]["portfolio_return_pct"] - pnl_by_month[m]["benchmark_return_pct"], 2)

    # Count position changes per month
    for i in range(1, len(nav_series)):
        if nav_series[i]["positions_detail"] != nav_series[i-1]["positions_detail"]:
            month = nav_series[i]["date"][:7]
            if month in pnl_by_month:
                pnl_by_month[month]["position_changes"] += 1

    # Best/worst trades
    best_trade = max(trade_log, key=lambda t: t.get("return_pct", -999)) if trade_log else None
    worst_trade = min(trade_log, key=lambda t: t.get("return_pct", 999)) if trade_log else None

    return {
        "pnl_by_index": pnl_by_index,
        "pnl_by_month": pnl_by_month,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "comparison_with_signals": None
    }


# === Main ===
if __name__ == "__main__":
    import sys

    positions_file = sys.argv[1] if len(sys.argv) > 1 else \
        os.path.join(PROJECT_ROOT, "data/positions/顺应周期_positions.json")

    if not os.path.exists(positions_file):
        print(f"ERROR: Positions file not found: {positions_file}")
        print("Run LLM extraction first to generate position data.")
        sys.exit(1)

    sim_start = sys.argv[3] if len(sys.argv) > 3 else None
    result = simulate(positions_file, start_date=sim_start)

    output_file = sys.argv[2] if len(sys.argv) > 2 else \
        os.path.join(PROJECT_ROOT, "data/simulations/顺应周期_nav.json")

    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    def json_default(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serializable")

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=json_default)

    print(f"Simulation complete → {output_file}")

    s = result["summary"]
    if s:
        print(f"\n=== {result['extraction_meta']['blogger']} 仓位模拟 ===")
        print(f"期间: {s['simulation_period']}")
        print(f"交易日: {s['trading_days']}")
        print(f"组合收益: {s['performance']['total_return_pct']:+.2f}%")
        print(f"沪深300基准: {s['performance']['benchmark_csi300_return_pct']:+.2f}%")
        print(f"超额alpha: {s['performance']['alpha_vs_csi300_pct']:+.2f}%")
        print(f"最大回撤: {s['risk']['max_drawdown_pct']:+.2f}%")
        print(f"Sortino: {s['risk']['sortino_ratio']:.2f}")
        print(f"盈利因子: {s['performance']['profit_factor']:.2f}")
        print(f"日均仓位: {s['position']['avg_position_pct']:.1f}%")
        print(f"仓位变化: {s['turnover']['total_position_changes']}")
        if s['turnover'].get('intraday_change_days', 0) > 0:
            print(f"含日内多变的交易日: {s['turnover']['intraday_change_days']}")
