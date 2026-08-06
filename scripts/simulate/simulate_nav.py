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
import json, os, math, re
from datetime import datetime, timedelta
from collections import defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


# === 加载市场日线数据（含开盘价） ===
def load_market_data_with_open():
    """Load daily market data including open prices for reference price resolution."""
    market = json.load(open(os.path.join(PROJECT_ROOT, "data/market/market_data.json"),
                           encoding="utf-8"))
    prices = {}
    for idx_name, rows in market.items():
        px = {}
        for r in rows:
            px[r["日期"]] = {
                "open": float(r.get("开盘", r["收盘"])),
                "close": float(r["收盘"])
            }
        prices[idx_name] = px
    return prices


# === 加载分钟数据 ===
def load_minute_data():
    """Load 1-min and 5-min K-lines for all indices. 1-min takes priority by merging
    later (overwrites same-time entries from 5-min).

    Returns:
        dict: code -> list of {time: str, close: float}, sorted by time
    """
    minute_dir = os.path.join(PROJECT_ROOT, "data", "minute")
    # Index name → code mapping for filenames
    name_to_code = {
        "SH50": "000016", "CSI300": "000300", "CYB": "399006",
        "KC50": "000688", "CSI500": "000905", "CSI1000": "000852",
    }

    result = {code: {} for code in name_to_code.values()}

    # Load 5-min first (lower priority)
    _load_minute_dir(result, os.path.join(minute_dir, "5min"), name_to_code, "_5min")
    # Load 1-min second (higher priority, overwrites)
    _load_minute_dir(result, os.path.join(minute_dir, "1min"), name_to_code, "_1min")

    # Convert dicts to sorted lists
    for code in result:
        result[code] = [{"time": t, "close": c} for t, c in
                       sorted(result[code].items())]

    return result


def _load_minute_dir(result, dirpath, name_to_code, suffix):
    """Load CSVs from a directory into the result dict."""
    for fname, code in name_to_code.items():
        path = os.path.join(dirpath, f"{fname}{suffix}.csv")
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            header = f.readline()  # skip header
            for line in f:
                parts = line.strip().split(",")
                if len(parts) < 5:
                    continue
                time_str = parts[0]  # "2026-07-27 14:02:00"
                try:
                    close_px = float(parts[4])  # close column
                except (ValueError, IndexError):
                    continue
                result[code][time_str] = close_px


# === 查找参考价格 ===
def get_reference_price(publish_time, minute_prices, daily_prices):
    """Get the K-line close price at or just after publish_time.

    Priority: minute data → daily close.
    Returns (ref_time, ref_price, source) where source is "minute"/"daily".
    """
    ref_time = publish_time.strip()
    if len(ref_time) == 16:
        ref_time += ":00"

    date = ref_time[:10]
    if minute_prices:
        for bar in minute_prices:
            # Only consider same-day minute bars
            if bar["time"][:10] == date and bar["time"] >= ref_time:
                return bar["time"], bar["close"], "minute"

    # Fallback to daily close
    px = daily_prices.get(date, {})
    return date, px.get("close", 0), "daily"


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
def resolve_position(snapshot, current_pos, current_total, cum_returns=None, nav=1.0):
    """Given a snapshot and current state, return the new (positions, total_units).

    Rules (from SIMULATE.md):
    - explicit: LLM gave absolute positions → use directly
    - inferred: LLM gave absolute positions but must be adjusted for market drift.
      LLM's unit delta from prior state is interpreted as a WEIGHT delta (成).
      Engine computes current actual weight, adds the delta, converts back to units.
    - partial: no position detail → scale by current market-value position ratio.
    """
    confidence = snapshot.get("confidence", "")
    new_pos = snapshot.get("positions", {})
    new_total = snapshot.get("total_units", current_total)

    if not new_pos or sum(new_pos.values()) == 0:
        # Partial (C-type): no detail, scale proportionally
        if current_pos and new_total != current_total:
            if cum_returns and nav > 0:
                actual = actual_position_pct_from_units(current_pos, cum_returns, nav)
                if actual > 0.001:
                    scale = new_total / actual
                else:
                    scale = new_total / max(0.001, current_total)
            else:
                scale = new_total / max(0.001, current_total)
            scaled = {k: round(v * scale, 2) for k, v in current_pos.items()}
            scaled = {k: v for k, v in scaled.items() if v > 0.01}
            return scaled, new_total
        return dict(current_pos), new_total

    # Has position detail — explicit or inferred
    if confidence == "inferred" and current_pos and cum_returns and nav > 0:
        # B-type: LLM's unit delta → weight-space delta
        # Detect which index changed and by how many units
        all_codes = set(list(current_pos.keys()) + list(new_pos.keys()))
        adjusted = dict(current_pos)
        for code in all_codes:
            old_units = current_pos.get(code, 0)
            llm_units = new_pos.get(code, 0)
            unit_delta = llm_units - old_units
            if unit_delta == 0:
                continue

            # LLM's unit delta = intended weight delta in 成
            weight_delta = unit_delta  # 1 unit delta → 1成 weight change
            cr = cum_returns.get(code, 1.0)
            if cr > 0:
                # Current actual weight in 成
                current_weight = old_units * cr / nav
                # Target weight
                target_weight = max(0, current_weight + weight_delta)
                # Convert weight back to units
                adjusted[code] = round(target_weight * nav / cr, 4)

        # Remove tiny positions
        adjusted = {k: v for k, v in adjusted.items() if v > 0.005}
        new_total_adj = sum(adjusted.values())
        return adjusted, new_total_adj

    # Explicit (A-type) or fallback: use LLM output directly
    return dict(new_pos), new_total


def actual_position_pct_from_units(positions, cum_returns, nav):
    """Compute actual market-value position percentage from units."""
    if nav <= 0 or not positions:
        return 0.0
    market_val = 0.0
    for code, units in positions.items():
        market_val += units * 0.1 * cum_returns.get(code, 1.0)
    return market_val / nav * 10.0  # 成


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

    # Shanghai Composite as primary benchmark
    key = "上证指数"
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
    benchmarks["000001_SH"] = bm

    # CSI300 as secondary benchmark
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


# === 2指数映射 ===
def remap_to_two_indices(snapshots):
    """Remap positions to only two indices: 上证50(000016) + 中证1000(000852).

    老登/金融/银行/保险/券商/证券/白酒/酒 → 上证50(000016)
    其余所有标的 → 中证1000(000852)
    B 类规则：操作动词+无指定标的 → 默认中证1000(000852)
    """
    # Tickers that map to 上证50 (the "老登" group)
    SH50_TICKERS = {"老登", "金融", "银行", "保险", "券商", "证券", "白酒", "酒", "上证50"}

    for s in snapshots:
        new_pos = {}
        for code, units in s.get("positions", {}).items():
            if code == "000016":
                new_pos["000016"] = new_pos.get("000016", 0) + units
            else:
                # Everything else → 中证1000
                new_pos["000852"] = new_pos.get("000852", 0) + units

        # Handle unmapped tickers
        new_unmapped = {}
        for ticker, units in s.get("unmapped", {}).items():
            if ticker in SH50_TICKERS:
                new_pos["000016"] = new_pos.get("000016", 0) + units
            else:
                new_pos["000852"] = new_pos.get("000852", 0) + units

        s["positions"] = new_pos
        s["unmapped"] = {}
        # Preserve original total_units for partial snapshots; recompute only if positions changed
        if new_pos:
            s["total_units"] = sum(new_pos.values())

    return snapshots


# === 单指数映射 ===
def remap_to_one_index(snapshots):
    """Remap ALL positions to 中证1000(000852) only."""
    for s in snapshots:
        total = s.get("total_units", 0)
        s["positions"] = {"000852": total} if total > 0 else {}
        s["unmapped"] = {}
    return snapshots


def _resolve_trade_ref_price(snap, old_pos, new_pos, daily_full, minute_data):
    """Find the reference price for a trade. Uses the index with the largest
    absolute position change. Falls back to CSI300 (000300)."""
    # Find the code with largest change
    all_codes = set(list(old_pos.keys()) + list(new_pos.keys()))
    best_code, best_delta = "000300", 0.0
    for code in all_codes:
        delta = abs(new_pos.get(code, 0) - old_pos.get(code, 0))
        if delta > best_delta:
            best_delta = delta
            best_code = code

    # Look up daily open/close for this index
    market_key = CODE_TO_MARKET_KEY.get(best_code)
    pub_time = snap.get("publish_time", "")
    pub_date = pub_time[:10] if pub_time else snap.get("date", "")
    daily_px = daily_full.get(market_key, {})
    minute_px = minute_data.get(best_code, [])

    price_type = snap.get("_price_type", "intraday")
    eff_date = snap.get("_effective_date", pub_date)

    if price_type == "open":
        px = daily_px.get(eff_date, {})
        return best_code, eff_date, round(px.get("open", px.get("close", 0)), 4), "daily_open"

    # intraday: use K-line close
    ref_time, ref_px, source = get_reference_price(pub_time, minute_px, daily_px)
    return best_code, ref_time, round(ref_px, 4), source


# === 主模拟函数 ===
def simulate(positions_file, start_date=None, end_date=None, two_index=False, one_index=False):
    """Run full position-based NAV simulation with intraday support.

    Args:
        two_index: If True, remap all positions to only 上证50 + 中证1000.
    """

    prices = load_market_data()
    daily_full = load_market_data_with_open()
    minute_data = load_minute_data()
    snapshots_raw = load_positions(positions_file)

    if two_index:
        snapshots_raw = remap_to_two_indices(snapshots_raw)
    if one_index:
        snapshots_raw = remap_to_one_index(snapshots_raw)

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

    # Track cumulative return multipliers and NAV (initialized here for pre-snap processing)
    cum_returns = {code: 1.0 for code in CODE_TO_MARKET_KEY}
    nav = 1.0

    current_pos = {}
    current_total = 0.0
    pre_snaps = [s for s in timeline if s["_effective_date"] <= sim_start]
    if pre_snaps:
        for s in pre_snaps:
            current_pos, current_total = resolve_position(s, current_pos, current_total, cum_returns, nav)
        timeline = [s for s in timeline if s["_effective_date"] > sim_start]
    else:
        current_pos, current_total = resolve_position(timeline[0], {}, 0.0, cum_returns, nav)
        timeline = timeline[1:]

    if end_date is None:
        end_date = snapshots_raw[-1]["date"] if snapshots_raw else "2026-07-31"

    sim_dates = [d for d in all_dates if sim_start <= d <= end_date]
    benchmarks = compute_benchmarks(prices, sim_dates)

    # === SIMULATE (minute-price-driven NAV) ===
    # Helper: index price lookup at a point in time
    def _index_price(code, ref_time):
        """Return index price at ref_time. Minute data if available, else daily open/close."""
        date = ref_time[:10]
        mins = minute_data.get(code, [])
        if mins:
            # Only use minute bars from the SAME day
            for bar in mins:
                if bar["time"] >= ref_time and bar["time"][:10] == date:
                    return bar["close"]
            # If no same-day bar found, fall through to daily data
        # Fallback: use daily open/close
        market_key = CODE_TO_MARKET_KEY.get(code, "沪深300")
        dp = daily_full.get(market_key, {})
        px = dp.get(date, {})
        if "09:30" in ref_time:
            return px.get("open", px.get("close", 1.0))
        return px.get("close", 1.0)

    def _prev_trading_day(day, dates):
        """Return the previous trading day before 'day'."""
        idx = dates.index(day) if day in dates else 0
        return dates[idx - 1] if idx > 0 else day

    def _segment_pnl(pos, t_start, t_end):
        pnl = 0.0
        for code, units in pos.items():
            px_s = _index_price(code, t_start)
            px_e = _index_price(code, t_end)
            if px_s > 0:
                pnl += units * (px_e / px_s - 1)
        return pnl

    nav = 1.0
    nav_series = []
    trade_log = []
    trade_id = 0
    current_pos = {}
    current_total = 0.0
    snap_idx = 0

    for day in sim_dates:
        day_snaps = []
        while snap_idx < len(timeline) and timeline[snap_idx]["_effective_date"] == day:
            day_snaps.append(timeline[snap_idx])
            snap_idx += 1

        # Process open-type snapshot first (if any)
        # Open-type: position applies from market open
        for snap in day_snaps:
            if snap["_price_type"] == "open":
                new_pos, new_total = resolve_position(snap, current_pos, current_total, cum_returns, nav)
                current_pos, current_total = new_pos, new_total

        # Build intraday trades sorted by publish_time
        intra_trades = []
        for snap in day_snaps:
            if snap["_price_type"] != "intraday":
                continue
            pub_time = snap.get("publish_time", day + " 12:00")
            new_pos, new_total = resolve_position(snap, current_pos, current_total, cum_returns, nav)
            ref_code, ref_time, ref_price, ref_source = _resolve_trade_ref_price(
                snap, current_pos, new_pos, daily_full, minute_data
            )
            intra_trades.append({
                "snap": snap, "new_pos": new_pos, "new_total": new_total,
                "ref_code": ref_code, "ref_time": ref_time,
                "ref_price": ref_price, "ref_source": ref_source,
            })
        intra_trades.sort(key=lambda t: t["snap"].get("publish_time", ""))

        # --- Minute-priced daily return ---
        # Split day into segments: [open, trade1_time, trade2_time, ..., close]
        seg_positions = [dict(current_pos)]
        prev_day = _prev_trading_day(day, sim_dates)
        seg_times = [prev_day + " 15:00:00"]
        for tr in intra_trades:
            seg_positions.append(dict(tr["new_pos"]))
            seg_times.append(tr["ref_time"])
        # Add close segment: last trade position earns from last trade → close
        seg_times.append(day + " 15:00:00")

        if nav_series:
            if len(seg_times) > 1:
                daily_pnl_sum = 0.0
                for si in range(len(seg_times) - 1):
                    daily_pnl_sum += _segment_pnl(seg_positions[si], seg_times[si], seg_times[si + 1])
                daily_ret_pct = daily_pnl_sum / 10.0
            else:
                # No intraday trades: one segment from prev close to today close
                # Find previous trading day
                prev_day = _prev_trading_day(day, sim_dates)
                seg_times = [prev_day + " 15:00:00", day + " 15:00:00"]
                seg_positions = [dict(current_pos)]
                daily_pnl_sum = _segment_pnl(current_pos, prev_day + " 15:00:00", day + " 15:00:00")
                daily_ret_pct = daily_pnl_sum / 10.0
            nav *= (1 + daily_ret_pct)

            # Update cum_returns
            for code in CODE_TO_MARKET_KEY:
                ret = daily_returns.get(code, {}).get(day, 0.0)
                cum_returns[code] *= (1 + ret)
        else:
            daily_ret_pct = 0.0
            for code in CODE_TO_MARKET_KEY:
                daily_returns.get(code, {}).get(day, 0.0)
                cum_returns[code] *= (1 + daily_returns.get(code, {}).get(day, 0.0))

        # --- Record trades ---
        for ti, tr in enumerate(intra_trades):
            snap = tr["snap"]
            new_pos = tr["new_pos"]
            new_total = tr["new_total"]

            desc = snap.get("description", "")
            is_t_hint = bool(re.search(r"T了|T掉|做T", desc)) if desc else False
            is_t_only = is_t_hint and (current_pos == new_pos)

            if current_pos != new_pos or is_t_only:
                trade_id += 1
                trade_type = "T" if is_t_only else ("open" if not current_pos else ("close" if not new_pos else "rebalance"))

                entry_nav = nav_series[-1]["nav"] if nav_series else 1.0
                hd, rp = 0, 0.0
                if current_pos and nav_series:
                    for j in range(len(nav_series) - 1, -1, -1):
                        if nav_series[j].get("positions_detail") != current_pos:
                            hd = len(nav_series) - j
                            rp = round((nav / nav_series[j]["nav"] - 1) * 100, 2) if nav_series[j]["nav"] > 0 else 0.0
                            break

                trade_log.append({
                    "trade_id": trade_id, "type": trade_type, "date": day,
                    "publish_time": snap.get("publish_time", ""),
                    "intraday_seq": f"{snap['_intraday_seq']}/{snap['_intraday_total']}" if snap["_intraday_total"] > 1 else "1/1",
                    "entry_positions": dict(current_pos),
                    "entry_description": _describe_positions(current_pos),
                    "exit_positions": dict(new_pos),
                    "exit_description": _describe_positions(new_pos),
                    "entry_nav": round(entry_nav, 6), "exit_nav": round(nav, 6),
                    "holding_days": hd, "return_pct": rp,
                    "reference_code": tr["ref_code"], "reference_time": tr["ref_time"],
                    "reference_price": tr["ref_price"], "reference_source": tr["ref_source"],
                    "description": snap.get("description", ""),
                    "confidence": snap.get("confidence", ""),
                    "was_intraday": snap["_intraday_total"] > 1,
                })

            current_pos = dict(new_pos)
            current_total = new_total

        nav_series.append({
            "date": day, "nav": round(nav, 6),
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

    bm_sh_ret = 0.0
    if "000001_SH" in benchmarks and benchmarks["000001_SH"]:
        bm_sh_ret = (benchmarks["000001_SH"][-1]["nav"] - 1.0) * 100
    bm_csi300_ret = 0.0
    if "000300_CSI300" in benchmarks and benchmarks["000300_CSI300"]:
        bm_csi300_ret = (benchmarks["000300_CSI300"][-1]["nav"] - 1.0) * 100
    bm_basket_ret = 0.0
    if "equal_weight_6" in benchmarks and benchmarks["equal_weight_6"]:
        bm_basket_ret = (benchmarks["equal_weight_6"][-1]["nav"] - 1.0) * 100

    alpha_sh = total_ret - bm_sh_ret

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
    if "000001_SH" in benchmarks and len(benchmarks["000001_SH"]) > 1:
        bm_navs = {x["date"]: x["nav"] for x in benchmarks["000001_SH"]}
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
            "benchmark_sh_return_pct": round(bm_sh_ret, 2),
            "benchmark_csi300_return_pct": round(bm_csi300_ret, 2),
            "benchmark_basket_return_pct": round(bm_basket_ret, 2),
            "alpha_vs_sh_pct": round(alpha_sh, 2),
            "information_ratio_vs_sh": round(ir, 2),
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
    if "000001_SH" in benchmarks:
        bm_navs = {x["date"]: x["nav"] for x in benchmarks["000001_SH"]}
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

    sim_start = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else None
    two_index = "--two-index" in sys.argv
    one_index = "--one-index" in sys.argv
    result = simulate(positions_file, start_date=sim_start, two_index=two_index, one_index=one_index)

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
        print(f"上证指数基准: {s['performance']['benchmark_sh_return_pct']:+.2f}%")
        print(f"超额alpha: {s['performance']['alpha_vs_sh_pct']:+.2f}%")
        print(f"最大回撤: {s['risk']['max_drawdown_pct']:+.2f}%")
        print(f"Sortino: {s['risk']['sortino_ratio']:.2f}")
        print(f"盈利因子: {s['performance']['profit_factor']:.2f}")
        print(f"日均仓位: {s['position']['avg_position_pct']:.1f}%")
        print(f"仓位变化: {s['turnover']['total_position_changes']}")
        if s['turnover'].get('intraday_change_days', 0) > 0:
            print(f"含日内多变的交易日: {s['turnover']['intraday_change_days']}")
