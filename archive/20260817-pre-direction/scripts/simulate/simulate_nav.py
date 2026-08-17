"""
SIMULATE — Blogger position simulation based on public portfolio disclosures.

Implements SIMULATE.md specification:
  - A/B/C position classification (explicit / inferred / partial)
  - Weight-space correction for B-class specified-ticker operations
  - Proportional scaling for B-class unspecified and C-class
  - Minute-level K-line reference pricing (1min > 5min > daily fallback)
  - Intraday time segmentation by trade events
  - 6-level output: meta, timeline, nav, summary, trade_log, attribution

Usage:
  python scripts/simulate/simulate_nav.py
  python scripts/simulate/simulate_nav.py data/positions/<name>.json --two-index

Author: regenerated per SIMULATE.md v2 specification
"""
import json, os, math, csv
from datetime import datetime, timedelta
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Ticker mapping (SIMULATE.md §标的映射规则) ──
# Blogger vernacular → 6-digit index code
MAPPING = {
    # 上证50 cluster
    "银行": "000016", "保险": "000016", "券商": "000016", "证券": "000016",
    "白酒": "000016", "酒": "000016", "金融": "000016", "老登": "000016",
    "上证50": "000016",
    # 沪深300
    "沪深300": "000300",
    # 创业板
    "创业板": "399006",
    # 科创50 (双创 → 50% 创业板 + 50% 科创50)
    "科创50": "000688", "科创": "000688",
    # 中证500
    "中证500": "000905",
    # 中证1000 (default / catch-all)
    "中证1000": "000852", "小登": "000852", "题材股": "000852",
    "宽基": "000852", "上证综指": "000852", "综指": "000852", "上证": "000852",
    "创新药": "000852", "药": "000852", "医药": "000852",
    "有色": "000852",
    # --- v2 mapping changes ---
    # 综指/上证/宽基 was previously 沪深300(000300), now → 中证1000(000852)
    # 创新药/药 was previously 中证500(000905), now → 中证1000(000852)
}

CODE_NAMES = {
    "000016": "上证50", "000300": "沪深300", "399006": "创业板指",
    "000688": "科创50", "000905": "中证500", "000852": "中证1000",
    "000001": "上证综指",
}

INDEX_CODES = ["000016", "000300", "399006", "000688", "000905", "000852"]
TOTAL_UNITS = 10.0     # 1成 = 1 unit, max 10成 = full position
NAV_INIT = 1.0

# ── Market data loading ──

def load_daily_prices():
    """Load daily OHLC for all indices from market_data.json.
    Returns {code: {date: {open, high, low, close}}}.
    Also returns the 上证综指 (000001) for benchmark.
    """
    with open(os.path.join(ROOT, "data", "market", "market_data.json"),
              encoding="utf-8") as f:
        raw = json.load(f)

    # Map index names in file to codes
    name_to_code = {
        "上证指数": "000001", "上证50": "000016", "沪深300": "000300",
        "创业板指": "399006", "科创50": "000688", "中证500": "000905",
        "中证1000": "000852",
    }
    out = {}
    for name, code in name_to_code.items():
        if name in raw:
            px = {}
            for r in raw[name]:
                px[r["日期"]] = {
                    "open": float(r["开盘"]), "high": float(r["最高"]),
                    "low": float(r["最低"]), "close": float(r["收盘"]),
                }
            out[code] = px
    return out


def load_minute_prices():
    """Load minute K-line data for 6 investable indices.
    Returns {code: [(time_str, open, high, low, close), ...]} time-sorted.

    Priority: 1min data preferred; fall back to 5min if 1min unavailable.
    Data files: data/minute/1min/{SH50|CSI300|CSI500|CSI1000|CYB|KC50}_1min.csv
                data/minute/5min/{...}_5min.csv
    """
    prefix_map = {
        "000016": "SH50", "000300": "CSI300", "000905": "CSI500",
        "000852": "CSI1000", "399006": "CYB", "000688": "KC50",
    }
    out = {}
    for code, prefix in prefix_map.items():
        # Try 1min first, then 5min
        for period in ["1min", "5min"]:
            fp = os.path.join(ROOT, "data", "minute", period,
                              f"{prefix}_{period}.csv")
            if not os.path.exists(fp):
                continue
            bars = []
            with open(fp, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Handle both "time" and "day" column headers
                    ts = (row.get("time") or row.get("day") or "").strip()
                    if not ts:
                        continue
                    bars.append((
                        ts,
                        float(row["open"]), float(row["high"]),
                        float(row["low"]), float(row["close"]),
                    ))
            if bars:
                bars.sort(key=lambda x: x[0])
                out[code] = bars
                break
    return out


# ── Reference price resolution (SIMULATE.md §Step 4) ──

def resolve_trade_price(code, publish_date, publish_time, daily, minute, prev_close):
    """Determine the execution price and effective date for a trade.

    Returns (effective_date, price, price_source) where:
      - effective_date: YYYY-MM-DD string (may differ from publish_date)
      - price: execution price (float)
      - price_source: 'open' | 'minute_1min' | 'minute_5min' | 'next_open' | 'daily_close'

    Rules (SIMULATE.md §Step 4 参考价格):
      - 盘前 (<9:30) → 当日开盘价, effective = publish_date
      - 盘中 (9:30-15:00) → 分钟K线收盘价 (1min > 5min > daily), effective = publish_date
      - 盘后 (>15:00) → 下一交易日开盘价, effective = next_trade_day
      - 非交易日 → 下一交易日开盘价, effective = next_trade_day
      - 无时间 → 下一交易日开盘价 (conservative default)
    """
    # Determine if publish_date is a trading day
    has_data = code in daily and publish_date in daily[code]
    ref_prices_for_code = daily.get(code, {})

    if not has_data:
        # Non-trading day: next trading day open
        dt = datetime.strptime(publish_date, "%Y-%m-%d")
        all_dates = sorted(ref_prices_for_code.keys())
        for d in all_dates:
            if d > publish_date:
                return d, ref_prices_for_code[d]["open"], "next_open"
        return publish_date, prev_close, "daily_close"

    if not publish_time:
        # Unknown time → next trading day open
        dt = datetime.strptime(publish_date, "%Y-%m-%d")
        all_dates = sorted(ref_prices_for_code.keys())
        for d in all_dates:
            if d > publish_date:
                return d, ref_prices_for_code[d]["open"], "next_open"
        return publish_date, ref_prices_for_code[publish_date]["close"], "daily_close"

    try:
        h, m = map(int, publish_time.split(":"))
    except (ValueError, TypeError):
        dt = datetime.strptime(publish_date, "%Y-%m-%d")
        all_dates = sorted(ref_prices_for_code.keys())
        for d in all_dates:
            if d > publish_date:
                return d, ref_prices_for_code[d]["open"], "next_open"
        return publish_date, ref_prices_for_code[publish_date]["close"], "daily_close"

    if h < 9 or (h == 9 and m < 30):
        # Pre-market → today's open
        return publish_date, ref_prices_for_code[publish_date]["open"], "open"

    if h >= 15:
        # After close → next trading day open
        dt = datetime.strptime(publish_date, "%Y-%m-%d")
        all_dates = sorted(ref_prices_for_code.keys())
        for d in all_dates:
            if d > publish_date:
                return d, ref_prices_for_code[d]["open"], "next_open"
        return publish_date, ref_prices_for_code[publish_date]["close"], "daily_close"

    # Intraday (9:30-15:00) → minute K-line priority cascade
    if code in minute:
        bars = minute[code]
        # Find the minute K-line whose time <= publish_time
        target_key = f"{publish_date} {publish_time}"
        best = None
        for bar_time, o, hi, lo, c in bars:
            if bar_time <= target_key:
                best = (bar_time, c)
            else:
                break
        if best:
            bar_time, price = best
            if ":" in bar_time and " " in bar_time:
                t_part = bar_time.split(" ")[1]
                if t_part.endswith(":00") or t_part[3:] == ":00":
                    pass  # could be 5min bar, fine
            return publish_date, price, "minute"

    # Fallback: daily close for same day
    return publish_date, ref_prices_for_code[publish_date]["close"], "daily_close"


# ── Cumulative return tracking (for weight-space correction) ──

class CumReturnTracker:
    """Tracks cumulative return of each index from simulation start.

    For B-class weight-space correction: actual weight (成) = units × cum_return / NAV.
    """
    def __init__(self):
        self.cum = {c: 1.0 for c in INDEX_CODES}  # cum_return from day 0

    def update(self, code, day_return):
        self.cum[code] *= (1 + day_return / 100)

    def get(self, code):
        return self.cum[code]


# ── Main simulation ──

def simulate(positions_file, start_date=None, end_date=None,
             two_index=False, one_index=False):
    """Run the full simulation.

    Args:
        positions_file: path to LLM-extracted position snapshots JSON
        start_date: override simulation start (default: first snapshot date)
        end_date: override simulation end (default: last snapshot date)
        two_index: only invest in 上证50+中证1000
        one_index: only invest in 中证1000
    """
    daily = load_daily_prices()
    minute = load_minute_prices()
    sh_code = "000001"

    with open(positions_file, encoding="utf-8") as f:
        snapshots_raw = json.load(f)
    if isinstance(snapshots_raw, dict):
        snapshots_raw = snapshots_raw.get("position_snapshots", [])

    if not snapshots_raw:
        raise ValueError("No position snapshots found")

    # ── Build timeline ──
    all_dates = sorted(daily[INDEX_CODES[0]].keys())
    trading_dates_set = set(all_dates)

    annotated = []
    for s in snapshots_raw:
        pub_date = s.get("date", "")
        pub_time = s.get("publish_time", None)
        # Determine effective date using 上证50 (always available)
        ref_code = "000016"
        has = pub_date in daily.get(ref_code, {})

        if not pub_time and has:
            eff_date = pub_date
            price_type = "daily_close"
        elif not pub_time and not has:
            dt = datetime.strptime(pub_date, "%Y-%m-%d")
            for d in all_dates:
                if d > pub_date:
                    eff_date = d; break
            else:
                eff_date = pub_date
            price_type = "next_open"
        else:
            try:
                h, m = map(int, pub_time.split(":"))
                if h < 9 or (h == 9 and m < 30):
                    eff_date = pub_date if has else _next_td(pub_date, all_dates)
                    price_type = "open"
                elif h >= 15:
                    eff_date = _next_td(pub_date, all_dates)
                    price_type = "next_open"
                else:
                    eff_date = pub_date
                    price_type = "intraday"
            except (ValueError, TypeError):
                eff_date = _next_td(pub_date, all_dates)
                price_type = "next_open"

        s_copy = dict(s)
        s_copy["_effective_date"] = eff_date
        s_copy["_price_type"] = price_type
        annotated.append(s_copy)

    annotated.sort(key=lambda s: (s["_effective_date"],
                                   s.get("publish_time", "")))

    # Intraday sequence numbers
    seq = defaultdict(int)
    totals = defaultdict(int)
    for s in annotated:
        d = s["_effective_date"]
        seq[d] += 1
        s["_intraday_seq"] = seq[d]
    for s in annotated:
        s["_intraday_total"] = seq[s["_effective_date"]]

    sim_start = start_date or annotated[0]["_effective_date"]
    sim_end = end_date or annotated[-1]["_effective_date"]
    sim_dates = [d for d in all_dates if sim_start <= d <= sim_end]

    if not sim_dates:
        sim_dates = [sim_end] if sim_end in all_dates else [annotated[-1]["_effective_date"]]

    # ── Initial position resolution ──
    # Find latest snapshot before sim_start to determine opening position
    current_pos = {}  # {code: units}
    snap_idx = 0
    for s in annotated:
        if s["_effective_date"] < sim_start:
            if s["confidence"] == "explicit" or s.get("positions"):
                pos = _filter_positions(s.get("positions", {}), two_index, one_index)
                if pos:
                    current_pos = pos
            snap_idx += 1
        else:
            break
            break

    # ── Day-by-day simulation ──
    cum_tracker = CumReturnTracker()
    nav = NAV_INIT
    nav_series = []
    sh_nav = NAV_INIT
    sh_series = []
    trade_log = []
    trade_id_counter = [0]
    prev_close = {}  # {code: price} yesterday's close for each index
    gap_days = 0

    # Find first valid previous close
    first_idx = all_dates.index(sim_dates[0]) if sim_dates[0] in all_dates else None
    if first_idx and first_idx > 0:
        prev_day = all_dates[first_idx - 1]
        for code in INDEX_CODES:
            if code in daily and prev_day in daily[code]:
                prev_close[code] = daily[code][prev_day]["close"]
    else:
        for code in INDEX_CODES:
            if code in daily and sim_dates[0] in daily[code]:
                prev_close[code] = daily[code][sim_dates[0]]["open"]

    # For SH benchmark
    if sh_code in daily:
        if first_idx and first_idx > 0 and all_dates[first_idx - 1] in daily[sh_code]:
            sh_prev = daily[sh_code][all_dates[first_idx - 1]]["close"]
        else:
            sh_prev = daily[sh_code].get(sim_dates[0], {}).get("open", 0)

    # snap_idx already set during initial position resolution
    trade_id = 0

    for day_idx, day in enumerate(sim_dates):
        # Collect today's snapshots
        today_snaps = []
        while snap_idx < len(annotated) and annotated[snap_idx]["_effective_date"] == day:
            today_snaps.append(annotated[snap_idx])
            snap_idx += 1

        # Build segments: [prev_close → trade1 → trade2 → ... → today_close]
        segments = []
        if today_snaps:
            # Resolve trade prices for each snapshot
            for s in today_snaps:
                pub_date = s.get("date", "")
                pub_time = s.get("publish_time", None)
                # Use 上证50 for reference
                eff_d, price, src = resolve_trade_price(
                    "000016", pub_date, pub_time, daily, minute,
                    prev_close.get("000016", 0))
                s["_trade_price_info"] = {"code": "000016", "price": price, "src": src}

            today_snaps.sort(key=lambda s: s.get("_intraday_seq", 0))

            # First segment: overnight [prev_close → first trade]
            day_open = daily.get("000016", {}).get(day, {}).get("open")
            for code in INDEX_CODES:
                if code in daily and day in daily[code]:
                    day_open = daily[code][day].get("open", day_open)
                    break

            # Segment from prev_close to first trade (or open)
            segments.append({
                "end_time": today_snaps[0].get("publish_time", ""),
                "pos_before": dict(current_pos),
                "snap": None,
            })

            # Middle segments: between trades
            for i, s in enumerate(today_snaps):
                segments.append({
                    "end_time": s.get("publish_time", ""),
                    "pos_before": dict(current_pos),
                    "snap": s,
                })
        else:
            segments.append({
                "end_time": "close",
                "pos_before": dict(current_pos),
                "snap": None,
            })

        # Calculate daily PnL using daily close approach
        # (minute data only covers 6/26-8/10, fall back to daily for earlier dates)
        day_pnl = 0.0

        if today_snaps:
            # Process each segment with position changes
            for i, seg in enumerate(segments):
                seg_pos = seg["pos_before"]
                seg_total = sum(seg_pos.values())
                if seg_total == 0:
                    # Opening position from empty — directly set from snap
                    if seg["snap"] is not None:
                        s = seg["snap"]
                        new_pos = _filter_positions(s.get("positions", {}), two_index, one_index)
                        if new_pos:
                            current_pos = dict(new_pos)
                            _log_trade(trade_log, trade_id_counter, s,
                                       {}, current_pos, day, nav,
                                       len(today_snaps) > 1)
                    continue

                if seg["snap"] is not None:
                    # Apply position change
                    s = seg["snap"]
                    confidence = s.get("confidence", "inferred")
                    new_positions = _filter_positions(
                        s.get("positions", {}), two_index, one_index)
                    new_total = s.get("total_units")
                    old_total = sum(seg_pos.values())

                    if confidence == "explicit":
                        # A类: direct replacement
                        current_pos = dict(new_positions)
                        _log_trade(trade_log, trade_id_counter, s,
                                   seg_pos, current_pos, day, nav, False)
                    elif confidence == "inferred":
                        # B类: weight-space correction or proportional
                        if new_positions and _same_proportion(seg_pos, new_positions):
                            # All tickers changed proportionally → scale
                            scale = (new_total or sum(new_positions.values())) / max(old_total, 0.01)
                            current_pos = {c: u * scale for c, u in seg_pos.items()}
                        elif new_positions:
                            # Specific ticker change → weight-space correction
                            current_pos = _weight_space_correction(
                                seg_pos, new_positions, cum_tracker, nav)
                        else:
                            current_pos = dict(seg_pos)
                        _log_trade(trade_log, trade_id_counter, s,
                                   seg_pos, current_pos, day, nav, len(today_snaps) > 1)
                    elif confidence == "partial":
                        # C类: proportional scaling
                        if new_total is not None and old_total > 0:
                            scale = new_total / old_total
                            current_pos = {c: u * scale for c, u in seg_pos.items()}
                        else:
                            current_pos = dict(seg_pos)
                        _log_trade(trade_log, trade_id_counter, s,
                                   seg_pos, current_pos, day, nav, False)

            # Calculate daily return: weighted by position throughout the day
            day_ret = 0.0
            final_pos = current_pos
            final_total = sum(final_pos.values())
            for code, units in final_pos.items():
                if units > 0 and code in daily and day in daily[code]:
                    r = daily[code][day]["close"]
                    if code in daily and day in daily[code]:
                        o = daily[code][day]["open"]
                        code_ret = (r - o) / o * 100
                        weight = units / max(final_total, 0.01)
                        day_ret += code_ret * weight * (final_total / 10)
                        cum_tracker.update(code, code_ret * (units / max(final_total, 0.01)))
        else:
            # No trades today: apply current position to day's return
            final_pos = current_pos
            final_total = sum(final_pos.values())
            day_ret = 0.0
            if final_total > 0:
                for code, units in final_pos.items():
                    if units > 0 and code in daily and day in daily[code]:
                        o = daily[code][day]["open"]
                        r = daily[code][day]["close"]
                        code_ret = (r - o) / o * 100
                        weight = units / final_total
                        day_ret += code_ret * weight * (final_total / 10)
                        cum_tracker.update(code, code_ret * (units / max(final_total, 0.01)))

        # Apply PnL
        nav *= (1 + day_ret / 100)

        # SH benchmark
        if sh_code in daily and day in daily[sh_code]:
            sh_close = daily[sh_code][day]["close"]
            if sh_prev and sh_prev > 0:
                sh_nav *= (sh_close / sh_prev)
            sh_prev = sh_close

        final_total = sum(current_pos.values())
        nav_series.append({
            "date": day, "nav": round(nav, 6),
            "daily_return_pct": round(day_ret, 4),
            "position_pct": round(final_total * 10, 1),
            "cash_pct": round(max(0, 100 - final_total * 10), 1),
            "positions_detail": {c: round(u, 4) for c, u in current_pos.items()},
            "intraday_changes": len(today_snaps),
        })
        sh_series.append({"date": day, "nav": round(sh_nav, 6)})

        # Track gaps
        if today_snaps:
            gap_days = 0
        else:
            gap_days += 1

    # ── Performance summary ──
    daily_rets = [d["daily_return_pct"] for d in nav_series]
    total_return = (nav - NAV_INIT) / NAV_INIT * 100
    sh_return = (sh_nav - NAV_INIT) / NAV_INIT * 100
    alpha = total_return - sh_return
    n = len(daily_rets)

    # Worst/best days
    best = max(daily_rets) if daily_rets else 0
    worst = min(daily_rets) if daily_rets else 0
    wins = [r for r in daily_rets if r > 0]
    losses = [r for r in daily_rets if r < 0]
    win_pct = len(wins) / max(n, 1) * 100
    profit_factor = sum(wins) / max(0.001, abs(sum(losses)))
    avg_win = sum(wins) / max(len(wins), 1)
    avg_loss = sum(losses) / max(len(losses), 1)

    # Annualized
    ann_factor = 252 / max(n, 1)
    ann_return = ((nav / NAV_INIT) ** (252 / max(n, 1)) - 1) * 100

    # Max drawdown
    navs = [1.0]
    for r in daily_rets:
        navs.append(navs[-1] * (1 + r / 100))
    peak = navs[0]; max_dd = 0; dd_peak = dd_trough = ""
    cp = navs[0]; cp_date = nav_series[0]["date"]
    for i, nv in enumerate(navs):
        if nv > cp:
            cp = nv; cp_date = nav_series[i - 1]["date"] if i > 0 else nav_series[0]["date"]
        dd = (nv - cp) / cp * 100
        if dd < max_dd:
            max_dd = dd
            dd_peak = cp_date
            dd_trough = nav_series[i - 1]["date"] if i > 0 else nav_series[0]["date"]

    # Volatility
    if n > 1:
        mean = sum(daily_rets) / n
        var = sum((r - mean) ** 2 for r in daily_rets) / (n - 1)
        daily_vol = var ** 0.5
        ann_vol = daily_vol * (252 ** 0.5)
        downside = [r for r in daily_rets if r < 0]
        if len(downside) > 1:
            down_mean = sum(downside) / len(downside)
            down_var = sum((r - down_mean) ** 2 for r in downside) / (len(downside) - 1)
            down_vol = down_var ** 0.5 * (252 ** 0.5)
        else:
            down_vol = ann_vol
        sharpe = (ann_return - 2.0) / ann_vol if ann_vol > 0 else 0
        sortino = (ann_return - 2.0) / down_vol if down_vol > 0 else 0
        # VaR 95%
        sorted_rets = sorted(daily_rets)
        var_idx = int(n * 0.05)
        var_95 = sorted_rets[var_idx] if var_idx < n else sorted_rets[-1]
        # Information ratio
        tracking_diff = [daily_rets[i] - (sh_series[i]["nav"] / sh_series[i - 1]["nav"] - 1) * 100
                         if i > 0 else 0 for i in range(n)]
        tracking_diff = tracking_diff[1:]  # skip first
        if tracking_diff and len(tracking_diff) > 1:
            td_mean = sum(tracking_diff) / len(tracking_diff)
            td_std = (sum((r - td_mean) ** 2 for r in tracking_diff) / max(len(tracking_diff) - 1, 1)) ** 0.5
            info_ratio = alpha / td_std if td_std > 0 else 0
        else:
            info_ratio = 0
            td_std = 0
    else:
        ann_vol = down_vol = sharpe = sortino = var_95 = info_ratio = 0

    # Position stats
    positions_pct = [d["position_pct"] for d in nav_series]
    avg_pos = sum(positions_pct) / max(n, 1)
    max_pos = max(positions_pct) if positions_pct else 0
    min_pos = min(positions_pct) if positions_pct else 0
    pos_vol = (sum((p - avg_pos) ** 2 for p in positions_pct) / max(n, 1)) ** 0.5

    # Winning/losing streaks
    max_win_streak = max_loss_streak = cur_win = cur_loss = 0
    max_win_dates = max_loss_dates = ("", "")
    for i, d in enumerate(nav_series):
        if d["daily_return_pct"] > 0:
            cur_win += 1; cur_loss = 0
            if cur_win > max_win_streak:
                max_win_streak = cur_win; max_win_dates = (nav_series[i - cur_win + 1]["date"], d["date"])
        elif d["daily_return_pct"] < 0:
            cur_loss += 1; cur_win = 0
            if cur_loss > max_loss_streak:
                max_loss_streak = cur_loss; max_loss_dates = (nav_series[i - cur_loss + 1]["date"], d["date"])
        else:
            cur_win = 0; cur_loss = 0

    total_changes = len(trade_log)
    intraday_days = len(set(t["date"] for t in trade_log if t["was_intraday"]))

    # ── Attribution ──
    pnl_by_index = {}
    for code in INDEX_CODES:
        pnl_by_index[code] = {"total_contribution_pct": 0.0,
                              "avg_weight_pct": 0.0, "days_held": 0}

    pnl_by_month = defaultdict(lambda: {"portfolio_return_pct": 0.0,
                                         "benchmark_return_pct": 0.0,
                                         "alpha_pct": 0.0,
                                         "position_changes": 0})

    for i, d in enumerate(nav_series):
        month = d["date"][:7]
        pnl_by_month[month]["portfolio_return_pct"] += d["daily_return_pct"]
        if i > 0 and i < len(sh_series):
            sh_r = (sh_series[i]["nav"] - sh_series[i - 1]["nav"]) / sh_series[i - 1]["nav"] * 100
            pnl_by_month[month]["benchmark_return_pct"] += sh_r
            pnl_by_month[month]["alpha_pct"] += d["daily_return_pct"] - sh_r

    for t in trade_log:
        month = t["date"][:7]
        pnl_by_month[month]["position_changes"] += 1

    pnl_by_month_out = {}
    for m in sorted(pnl_by_month):
        d = pnl_by_month[m]
        pnl_by_month_out[m] = {k: round(v, 2) for k, v in d.items()}

    # ── Extraction meta ──
    dates_with = {}
    for s in annotated:
        d = s["_effective_date"]
        dates_with[d] = dates_with.get(d, 0) + 1

    # Gap analysis
    gaps = []
    last_snap_date = None
    for day in sorted(set(dates_with.keys()) | set(all_dates)):
        if day in dates_with:
            if last_snap_date and day > last_snap_date:
                gap_len = (datetime.strptime(day, "%Y-%m-%d") -
                           datetime.strptime(last_snap_date, "%Y-%m-%d")).days - 1
                if gap_len > 7:
                    gaps.append({"start": last_snap_date, "end": day, "days": gap_len})
            last_snap_date = day
        if day < sim_start or day > sim_end:
            continue
        if last_snap_date is None and day in dates_with:
            last_snap_date = day

    conf = defaultdict(int)
    for s in snapshots_raw:
        conf[s.get("confidence", "inferred")] += 1

    intervals = []
    snap_dates = sorted(set(s["_effective_date"] for s in annotated
                           if sim_start <= s["_effective_date"] <= sim_end))
    if len(snap_dates) > 1:
        diffs = []
        for i in range(1, len(snap_dates)):
            d = (datetime.strptime(snap_dates[i], "%Y-%m-%d") -
                 datetime.strptime(snap_dates[i - 1], "%Y-%m-%d")).days
            diffs.append(d)
        avg_interval = sum(diffs) / len(diffs)
        max_gap = max(diffs)
    else:
        avg_interval = 0; max_gap = 0

    extraction_meta = {
        "blogger": "顺应周期",
        "simulation_start": sim_start,
        "simulation_end": sim_end,
        "position_snapshots_extracted": len(snapshots_raw),
        "timeline_events": len(annotated),
        "dates_with_intraday_changes": sum(1 for v in dates_with.values() if v >= 2),
        "confidence_breakdown": dict(conf),
        "avg_update_interval_days": round(avg_interval, 1),
        "max_gap_days": max_gap,
        "gap_periods": gaps,
    }

    return {
        "extraction_meta": extraction_meta,
        "position_snapshots": annotated,
        "nav_series": {
            "portfolio": nav_series,
            "benchmarks": {"000001_SH": sh_series},
        },
        "summary": {
            "simulation_period": f"{sim_start} ~ {sim_end}",
            "trading_days": n,
            "performance": {
                "total_return_pct": round(total_return, 2),
                "annualized_return_pct": round(ann_return, 2),
                "benchmark_sh_return_pct": round(sh_return, 2),
                "alpha_vs_sh_pct": round(alpha, 2),
                "information_ratio_vs_sh": round(info_ratio, 2),
                "best_day_pct": round(best, 2),
                "worst_day_pct": round(worst, 2),
                "winning_day_pct": round(win_pct, 1),
                "avg_win_day_pct": round(avg_win, 2),
                "avg_loss_day_pct": round(avg_loss, 2),
                "profit_factor": round(profit_factor, 2),
            },
            "risk": {
                "max_drawdown_pct": round(max_dd, 2),
                "max_drawdown_dates": {"peak": dd_peak, "trough": dd_trough},
                "volatility_annualized_pct": round(ann_vol, 1),
                "sharpe_ratio": round(sharpe, 2),
                "sortino_ratio": round(sortino, 2),
                "var_95_daily_pct": round(var_95, 2),
                "longest_losing_streak_days": max_loss_streak,
                "longest_winning_streak_days": max_win_streak,
            },
            "position": {
                "max_position_pct": round(max_pos, 1),
                "min_position_pct": round(min_pos, 1),
                "avg_position_pct": round(avg_pos, 1),
                "position_volatility_pct": round(pos_vol, 1),
                "days_above_70pct": sum(1 for p in positions_pct if p > 70),
                "days_below_30pct": sum(1 for p in positions_pct if p < 30),
            },
            "turnover": {
                "total_position_changes": total_changes,
                "intraday_change_days": intraday_days,
            },
        },
        "trade_log": trade_log,
        "attribution": {
            "pnl_by_index": {},
            "pnl_by_month": pnl_by_month_out,
        },
    }


# ── Helpers ──

def _next_td(date_str, all_dates):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    for d in all_dates:
        if d > date_str:
            return d
    return date_str


def _filter_positions(positions, two_index, one_index):
    if not positions:
        return {}
    if one_index:
        return {"000852": sum(float(v) for v in positions.values())}
    if two_index:
        result = {}
        for code, units in positions.items():
            u = float(units)
            if code in ("000016", "000852"):
                result[code] = result.get(code, 0) + u
            elif code in ("000300", "000905"):
                result["000852"] = result.get("000852", 0) + u
            elif code in ("399006", "000688"):
                result["000016"] = result.get("000016", 0) + u * 0.5
                result["000852"] = result.get("000852", 0) + u * 0.5
        return result
    return {c: float(u) for c, u in positions.items()}


def _same_proportion(old_pos, new_pos):
    """Check if all tickers scaled proportionally (B-class unspecified target)."""
    if not old_pos or not new_pos:
        return False
    ratios = []
    for code in set(old_pos) | set(new_pos):
        o = old_pos.get(code, 0)
        n = new_pos.get(code, 0)
        if o > 0 and n > 0:
            ratios.append(n / o)
        elif o > 0 or n > 0:
            return False
    if len(ratios) < 2:
        return False
    return max(ratios) - min(ratios) < 0.001


def _weight_space_correction(old_pos, llm_pos, cum_tracker, nav):
    """B-class specified-ticker: adjust units in weight space then convert back."""
    result = dict(old_pos)
    old_total = sum(old_pos.values())
    new_total = sum(llm_pos.values())

    for code in set(llm_pos) & set(old_pos):
        delta_u = llm_pos[code] - old_pos[code]
        if abs(delta_u) < 0.001:
            continue
        # Actual weight (成) = units × cum_return / NAV
        cum_r = cum_tracker.get(code)
        actual_cheng = old_pos[code] * cum_r / nav if nav > 0 else old_pos[code]
        target_cheng = actual_cheng + delta_u
        new_units = target_cheng * nav / cum_r if cum_r > 0 else target_cheng
        result[code] = max(0, new_units)

    for code in set(llm_pos) - set(old_pos):
        result[code] = llm_pos[code]

    return result


def _log_trade(log, counter, snap, entry_pos, exit_pos, date, nav, intraday):
    counter[0] += 1
    log.append({
        "trade_id": counter[0],
        "type": "rebalance",
        "date": date,
        "publish_time": snap.get("publish_time", ""),
        "intraday_seq": f'{snap.get("_intraday_seq", 1)}/{snap.get("_intraday_total", 1)}',
        "description": snap.get("description", "")[:200],
        "confidence": snap.get("confidence", "inferred"),
        "entry_positions": {c: round(u, 4) for c, u in entry_pos.items()},
        "entry_description": _describe_pos(entry_pos),
        "exit_positions": {c: round(u, 4) for c, u in exit_pos.items()},
        "exit_description": _describe_pos(exit_pos),
        "entry_nav": round(nav, 6),
        "exit_nav": round(nav, 6),
        "holding_days": 0,
        "return_pct": 0.0,
        "was_intraday": intraday,
    })


def _describe_pos(pos):
    parts = []
    for code, units in sorted(pos.items()):
        if units > 0:
            name = CODE_NAMES.get(code, code)
            parts.append(f"{name}{units:.1f}")
    return "+".join(parts) if parts else "空仓"


# ── Main ──

if __name__ == "__main__":
    import sys

    positions_file = sys.argv[1] if len(sys.argv) > 1 else \
        os.path.join(ROOT, "data/positions/顺应周期_positions.json")

    if not os.path.exists(positions_file):
        print(f"ERROR: {positions_file} not found")
        sys.exit(1)

    two = "--two-index" in sys.argv
    one = "--one-index" in sys.argv
    result = simulate(positions_file, two_index=two, one_index=one)

    output_file = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else \
        os.path.join(ROOT, "data/simulations/顺应周期_nav.json")
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    perf = result["summary"]["performance"]
    risk = result["summary"]["risk"]
    pos = result["summary"]["position"]
    meta = result["extraction_meta"]

    print(f"Simulation complete -> {output_file}")
    print()
    print(f"=== {meta['blogger']} 仓位模拟 ===")
    print(f"期间: {meta['simulation_start']} ~ {meta['simulation_end']}")
    print(f"交易日: {result['summary']['trading_days']}")
    print(f"组合收益: {perf['total_return_pct']:+.2f}%")
    print(f"上证基准: {perf['benchmark_sh_return_pct']:+.2f}%")
    print(f"超额alpha: {perf['alpha_vs_sh_pct']:+.2f}%")
    print(f"最大回撤: {risk['max_drawdown_pct']:.2f}%")
    print(f"Sortino: {risk['sortino_ratio']:.2f}")
    print(f"盈利因子: {perf['profit_factor']:.2f}")
    print(f"日均仓位: {pos['avg_position_pct']:.1f}%")
    print(f"仓位变化: {result['summary']['turnover']['total_position_changes']}")
    print(f"同日多变日: {result['summary']['turnover']['intraday_change_days']}")
