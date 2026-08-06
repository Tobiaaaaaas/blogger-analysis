"""
v11 scoring: single formula, time_horizon-based return window, + win rates.
14 scores (7 total + 7 average %).

Usage: python scripts/score_v11.py --blogger <name>

Sources:
  - Scoring formula: .claude/skills/analyze-blogger/SKILL.md v11 4.3.3
"""
import json
import os
import argparse
from datetime import datetime, timedelta
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))

# Inflection Points (from market_analysis.md)
INFLECTIONS = [
    ("2024-06-03", "顶", 3086, 3097, "M0"),   # market data start — pre-M1 decline
    ("2024-09-13", "底", 2690, 2690, "M1"),   # manual Major — 两年大底
    ("2024-10-08", "顶", 3490, 3674, "M2"),
    ("2024-10-18", "底", 3262, 3153, "I1"),
    ("2024-11-08", "顶", 3452, 3510, "I2"),
    ("2024-11-27", "底", 3310, 3227, "I3"),
    ("2024-12-10", "顶", 3423, 3495, "I4"),
    ("2025-01-13", "底", 3161, 3141, "I5"),
    ("2025-03-19", "顶", 3426, 3439, "I6"),
    ("2025-04-07", "底", 3097, 3041, "M3"),
    ("2025-11-14", "顶", 3990, 4034, "M4"),
    ("2025-12-16", "底", 3825, 3816, "I9"),
    ("2026-01-14", "顶", 4126, 4191, "I10"),
    ("2026-02-03", "底", 4068, 4003, "I11"),
    ("2026-03-03", "顶", 4123, 4197, "I12"),
    ("2026-03-23", "底", 3813, 3795, "M5"),
    ("2026-05-14", "顶", 4178, 4259, "M6"),
    ("2026-06-08", "底", 3959, 3928, "I13"),
    ("2026-06-23", "顶", 4106, 4175, "I14"),
    ("2026-07-20", "底", 3741, 3741, "M8"),
]

BOTTOM_DATES = {
    "2024-09-13": "M1", "2024-10-18": "I1", "2024-11-27": "I3",
    "2025-01-13": "I5", "2025-04-07": "M3", "2025-12-16": "I9",
    "2026-02-03": "I11", "2026-03-23": "M5", "2026-06-08": "I13",
    "2026-07-20": "M8",
}

TOP_DATES = {
    "2024-10-08": "M2", "2024-11-08": "I2", "2024-12-10": "I4",
    "2025-03-19": "I6", "2025-11-14": "M4", "2026-01-14": "I10",
    "2026-03-03": "I12", "2026-05-14": "M6", "2026-06-23": "I14",
    "2026-06-25": "M7",
}

# time_horizon -> (window_offset_start, window_size)
# Offsets from T (list of trading day offsets to sample)
TH_WINDOW = {
    "short": [0, 1, 2],                # T, T+1, T+2
    "medium": [5, 6, 7, 8, 9, 10],     # T+5 ~ T+10 (6 consecutive)
    "long": [20, 21, 22],              # T+20, T+21, T+22
    "unspecified": [20, 21, 22],       # T+20, T+21, T+22
}

VALID_TH = set(TH_WINDOW.keys())

# Build segments (pure statistical grouping)
SEGMENTS = []
for i in range(len(INFLECTIONS) - 1):
    s_date, s_type, _, s_extreme, s_label = INFLECTIONS[i]
    e_date, e_type, _, e_extreme, e_label = INFLECTIONS[i + 1]
    direction = "rising" if s_type == "底" else "falling"
    SEGMENTS.append({
        "start_date": s_date, "start_type": s_type, "start_extreme": s_extreme,
        "start_label": s_label,
        "end_date": e_date, "end_type": e_type, "end_extreme": e_extreme,
        "end_label": e_label, "direction": direction,
    })


def load_prices():
    mkt_path = os.path.join(PROJECT_ROOT, "data", "market", "market_data.json")
    mkt = json.load(open(mkt_path, encoding="utf-8"))
    key = "上证指数"
    if key not in mkt:
        for k in mkt:
            if "上证" in k:
                key = k
                break
    prices = {}
    for r in mkt[key]:
        prices[r["日期"]] = {
            "open": float(r["开盘"]), "close": float(r["收盘"]),
            "high": float(r["最高"]), "low": float(r["最低"]),
        }
    return prices, sorted(prices.keys())


def get_ref_price(date_str, pub_time_str, prices, sorted_dates):
    """Returns (price, label, ref_date) per SKILL.md 4.3.2."""
    if date_str in prices:
        if pub_time_str:
            try:
                h, m = map(int, pub_time_str.split(":"))
                if h < 9 or (h == 9 and m < 30):
                    return prices[date_str]["open"], f"{date_str} open", date_str
                elif h >= 15:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    for offset in range(1, 15):
                        nd = (dt + timedelta(days=offset)).strftime("%Y-%m-%d")
                        if nd in prices:
                            return prices[nd]["open"], f"{nd} open", nd
                    return prices[date_str]["close"], f"{date_str} close(fb)", date_str
                else:
                    return prices[date_str]["close"], f"{date_str} close", date_str
            except (ValueError, TypeError):
                pass
        return prices[date_str]["close"], f"{date_str} close(def)", date_str
    else:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        for offset in range(1, 15):
            nd = (dt + timedelta(days=offset)).strftime("%Y-%m-%d")
            if nd in prices:
                return prices[nd]["open"], f"{nd} open(non-trading)", nd
        return None, "NO PRICE", None


def sampled_close_avg(ref_date, offsets, prices, sorted_dates):
    """Average close at specific trading day offsets from ref_date. offsets = list of ints."""
    try:
        idx = sorted_dates.index(ref_date)
    except ValueError:
        dt = datetime.strptime(ref_date, "%Y-%m-%d")
        for off in range(0, 15):
            nd = (dt + timedelta(days=off)).strftime("%Y-%m-%d")
            if nd in prices:
                try:
                    idx = sorted_dates.index(nd)
                    break
                except ValueError:
                    continue
        else:
            return None
    closes = []
    for offset in offsets:
        i = idx + offset
        if 0 <= i < len(sorted_dates):
            closes.append(prices[sorted_dates[i]]["close"])
    return sum(closes) / len(closes) if closes else None


def find_segment(date_str):
    for seg in SEGMENTS:
        if seg["start_date"] <= date_str < seg["end_date"]:
            return seg
    return None


def in_date_range(signal_date, target_date, days=1):
    sd = datetime.strptime(signal_date, "%Y-%m-%d")
    td = datetime.strptime(target_date, "%Y-%m-%d")
    return abs((sd - td).days) <= days


def main():
    parser = argparse.ArgumentParser(description="v11 scoring")
    parser.add_argument("--blogger", required=True)
    args = parser.parse_args()
    blogger = args.blogger

    prices, sorted_dates = load_prices()

    sigs_path = os.path.join(PROJECT_ROOT, "data", "signals", f"{blogger}.json")
    sigs_d = json.load(open(sigs_path, encoding="utf-8"))
    signals = sigs_d.get("signals", [])
    if not signals:
        print(f"ERROR: No signals found for {blogger}")
        return

    # Accumulators
    score_all = 0.0; score_rising = 0.0; score_falling = 0.0
    score_bottom = 0.0; score_top = 0.0
    score_bull = 0.0; score_bear = 0.0
    cnt_all = 0; cnt_rising = 0; cnt_falling = 0
    cnt_bottom = 0; cnt_top = 0; cnt_bull = 0; cnt_bear = 0
    win_all = 0; win_rising = 0; win_falling = 0
    win_bottom = 0; win_top = 0; win_bull = 0; win_bear = 0

    # Per time_horizon accumulators
    th_acc = defaultdict(lambda: {"cnt": 0, "score": 0.0, "win": 0})

    no_price = 0
    no_segment = 0
    score_zero = 0  # intraday/unspecified/long

    by_seg = defaultdict(lambda: {
        "direction": "", "total": 0, "bull": 0, "bear": 0, "score": 0.0,
        "start_label": "", "end_label": "",
    })
    inflection_signals = defaultdict(list)
    equity_records = []  # for equity curve simulation

    last_inf = INFLECTIONS[-1]

    for s in signals:
        pt = s.get("publish_time", "")
        if not pt:
            continue
        date_str = pt[:10]
        direction = s.get("direction", "")
        strength = s.get("strength", "moderate")
        th = s.get("time_horizon", "unspecified")

        if direction not in ("bullish", "bearish"):
            continue
        if strength not in ("strong", "moderate"):
            strength = "moderate"
        if th not in VALID_TH:
            th = "unspecified"

        pub_time = ""
        if len(pt) >= 16:
            pub_time = pt[11:16]

        ev = s.get("evidence", "")[:80]

        ref_p, ref_label, ref_date = get_ref_price(date_str, pub_time, prices, sorted_dates)
        if ref_p is None:
            no_price += 1
            continue

        sbase = 2 if strength == "strong" else 1

        # Find segment (pure statistical grouping)
        seg = find_segment(date_str)
        seg_key = None
        is_rising = None

        if seg is None:
            if date_str >= last_inf[0]:
                seg_key = f"{last_inf[4]}->now"
                is_rising_seg = "rising" if last_inf[1] == "底" else "falling"
                by_seg[seg_key]["direction"] = is_rising_seg
                by_seg[seg_key]["start_label"] = last_inf[4]
                by_seg[seg_key]["end_label"] = "?"
                by_seg[seg_key]["total"] += 1
                if direction == "bullish":
                    by_seg[seg_key]["bull"] += 1
                else:
                    by_seg[seg_key]["bear"] += 1
                is_rising = (is_rising_seg == "rising")
            else:
                no_segment += 1
                continue
        else:
            seg_key = f"{seg['start_label']}->{seg['end_label']}"
            is_rising = (seg["direction"] == "rising")

        # v10 A/B/C/D scoring with return (inflection) + short_return (3d avg close)
        if seg is None:
            # Last segment: no next inflection → return cannot be computed
            score = 0.0
            ret = 0.0
            short_r = 0.0
            score_zero += 1
        else:
            # return = |next_inflection_extreme - ref_price| / |ref_price|
            return_val = abs(seg["end_extreme"] - ref_p) / abs(ref_p) * 100  # percentage
            if return_val < 0:
                return_val = 0

            # short_return = 3-day avg close vs ref
            avg_c_3d = sampled_close_avg(ref_date, [0, 1, 2], prices, sorted_dates)
            if avg_c_3d is None:
                score = 0.0
                ret = 0.0
                short_r = 0.0
                score_zero += 1
            else:
                short_r = abs(avg_c_3d / ref_p - 1) * 100  # percentage

                # A/B/C/D formulas
                if is_rising and direction == "bullish":
                    # A: rising + bullish → reward
                    score = sbase * max(return_val, short_r)
                elif is_rising and direction == "bearish":
                    # B: rising + bearish → penalty-adjusted
                    if short_r > 1.5:
                        score = sbase * short_r
                    else:
                        score = -sbase * return_val
                elif not is_rising and direction == "bullish":
                    # C: falling + bullish → penalty-adjusted
                    if short_r > 1.5:
                        score = sbase * short_r
                    else:
                        score = -sbase * return_val
                else:
                    # D: falling + bearish → reward
                    score = sbase * max(return_val, short_r)
                ret = return_val

        # Record for equity curve simulation
        th_offsets = TH_WINDOW.get(th)
        if th_offsets:
            # Find the last trading day of the window
            try:
                idx = sorted_dates.index(ref_date)
            except ValueError:
                idx = None
                for i, d in enumerate(sorted_dates):
                    if d >= ref_date:
                        idx = i; break
            if idx is not None:
                max_off = max(th_offsets)
                exit_day_idx = idx + max_off
                exit_date = sorted_dates[min(exit_day_idx, len(sorted_dates)-1)] if exit_day_idx < len(sorted_dates) else None
            else:
                exit_date = None
        else:
            exit_date = None

        equity_records.append({
            "publish_time": pt,
            "date": date_str,
            "ref_date": ref_date,
            "ref_price": ref_p,
            "direction": direction,
            "strength": strength,
            "time_horizon": th,
            "exit_date": exit_date,
            "ret": ret,        # percentage
            "score": score,    # percentage
        })

        # Accumulate (score=0 signals excluded from averages/win rates)
        is_effective = (score != 0)
        if is_effective:
            cnt_all += 1
            score_all += score
            if score > 0:
                win_all += 1

        # Per time_horizon
        th_acc[th]["cnt"] += 1
        th_acc[th]["score"] += score
        if score > 0:
            th_acc[th]["win"] += 1

        if is_rising:
            if is_effective:
                cnt_rising += 1
                score_rising += score
                if score > 0:
                    win_rising += 1
        else:
            if is_effective:
                cnt_falling += 1
                score_falling += score
                if score > 0:
                    win_falling += 1

        if direction == "bullish":
            if is_effective:
                cnt_bull += 1
                score_bull += score
                if score > 0:
                    win_bull += 1
            for bd, blabel in BOTTOM_DATES.items():
                if in_date_range(date_str, bd, 1):
                    if is_effective:
                        cnt_bottom += 1
                        score_bottom += score
                        if score > 0:
                            win_bottom += 1
                    inflection_signals[blabel].append({
                        "date": date_str, "time": pub_time,
                        "direction": direction, "strength": strength,
                        "return": round(ret, 4),
                        "score": round(score, 4),
                        "ref": ref_label, "evidence": ev[:60],
                    })
                    break
        else:
            if is_effective:
                cnt_bear += 1
                score_bear += score
                if score > 0:
                    win_bear += 1
            for td, tlabel in TOP_DATES.items():
                if in_date_range(date_str, td, 1):
                    if is_effective:
                        cnt_top += 1
                        score_top += score
                        if score > 0:
                            win_top += 1
                    inflection_signals[tlabel].append({
                        "date": date_str, "time": pub_time,
                        "direction": direction, "strength": strength,
                        "return": round(ret, 4),
                        "score": round(score, 4),
                        "ref": ref_label, "evidence": ev[:60],
                    })
                    break

        # Segment tracking
        if seg is not None:
            by_seg[seg_key]["direction"] = seg["direction"]
            by_seg[seg_key]["start_label"] = seg["start_label"]
            by_seg[seg_key]["end_label"] = seg["end_label"]
            by_seg[seg_key]["total"] += 1
            by_seg[seg_key]["score"] += score
            if direction == "bullish":
                by_seg[seg_key]["bull"] += 1
            else:
                by_seg[seg_key]["bear"] += 1

    def avg_pct(total, count):
        return round(total / count, 2) if count > 0 else 0.0

    def win_rate_pct(win_count, total_count):
        return round(win_count / total_count * 100, 2) if total_count > 0 else 0.0

    scores = {
        "综合":  {"total": round(score_all,2),"count":cnt_all,"avg_pct":avg_pct(score_all,cnt_all),"win_rate":win_rate_pct(win_all,cnt_all)},
        "上升段": {"total": round(score_rising,2),"count":cnt_rising,"avg_pct":avg_pct(score_rising,cnt_rising),"win_rate":win_rate_pct(win_rising,cnt_rising)},
        "下降段": {"total": round(score_falling,2),"count":cnt_falling,"avg_pct":avg_pct(score_falling,cnt_falling),"win_rate":win_rate_pct(win_falling,cnt_falling)},
        "抄底":  {"total": round(score_bottom,2),"count":cnt_bottom,"avg_pct":avg_pct(score_bottom,cnt_bottom),"win_rate":win_rate_pct(win_bottom,cnt_bottom)},
        "逃顶":  {"total": round(score_top,2),"count":cnt_top,"avg_pct":avg_pct(score_top,cnt_top),"win_rate":win_rate_pct(win_top,cnt_top)},
        "看多":  {"total": round(score_bull,2),"count":cnt_bull,"avg_pct":avg_pct(score_bull,cnt_bull),"win_rate":win_rate_pct(win_bull,cnt_bull)},
        "看空":  {"total": round(score_bear,2),"count":cnt_bear,"avg_pct":avg_pct(score_bear,cnt_bear),"win_rate":win_rate_pct(win_bear,cnt_bear)},
    }

    # Per time_horizon scores
    th_scores = {}
    for th in ["short", "medium", "long", "unspecified"]:
        a = th_acc[th]
        th_scores[th] = {
            "count": a["cnt"],
            "total": round(a["score"], 2),
            "avg_pct": avg_pct(a["score"], a["cnt"]),
            "win_rate": win_rate_pct(a["win"], a["cnt"]),
        }

    # Terminal output
    print(f"Signals: {len(signals)} total, {cnt_all} scored, {score_zero} score-zero, "
          f"{no_price} no-price, {no_segment} no-segment")

    print(f"\n{'='*70}")
    print(f"14 SCORES -- {blogger}")
    print(f"{'='*70}")
    print(f"{'Dim':<8} {'Total':>10} {'Count':>8} {'Avg':>10} {'WinRate':>10}")
    print(f"{'-'*50}")
    for dim, d in scores.items():
        print(f"{dim:<8} {d['total']:>+10.2f}% {d['count']:>8} {d['avg_pct']:>+9.2f}% {d['win_rate']:>9.1f}%")
    print(f"{'='*70}")

    # By time_horizon
    print(f"\n{'='*70}")
    print("BY TIME_HORIZON")
    print(f"{'='*70}")
    print(f"{'th':<14} {'Count':>6} {'Total':>10} {'Avg':>10} {'WinRate':>10}")
    print(f"{'-'*52}")
    for th in ["short", "medium", "long", "unspecified"]:
        d = th_scores[th]
        if d["count"] > 0:
            print(f"{th:<14} {d['count']:>6} {d['total']:>+10.2f}% {d['avg_pct']:>+9.2f}% {d['win_rate']:>9.1f}%")

    # ── Equity curve simulation ──
    def simulate_equity(records, prices, sorted_dates, signal_filter=None):
        recs = [r for r in records if signal_filter is None or r["direction"] == signal_filter]
        if not recs:
            return [], {}
        recs = sorted(recs, key=lambda r: r["publish_time"])

        cash = 1.0
        pos = None  # None or {"direction": "long"/"short", "entry_price": float, "exit_date": str}
        nav_series = []
        trades = []

        first_date = recs[0]["date"]
        last_date = max(r.get("exit_date", sorted_dates[-1]) or sorted_dates[-1] for r in recs)

        signal_idx = 0
        for day in sorted_dates:
            if day < first_date: continue
            if day > last_date: break

            while signal_idx < len(recs) and recs[signal_idx]["date"] <= day:
                r = recs[signal_idx]
                if r["date"] != day:
                    signal_idx += 1; continue

                r_dir = "long" if r["direction"] == "bullish" else "short"
                r_exit = r.get("exit_date")

                if pos is None:
                    pos = {"direction": r_dir, "entry_price": r["ref_price"], "exit_date": r_exit}
                elif pos["direction"] == r_dir:
                    pos_exit = pos.get("exit_date") or ""
                    if r_exit and (not pos_exit or r_exit > pos_exit):
                        pos["exit_date"] = r_exit
                else:
                    # Reverse: close old at today's close, stay in cash
                    close_price = prices.get(day, {}).get("close")
                    if close_price and pos["entry_price"] > 0:
                        if pos["direction"] == "long":
                            old_ret = (close_price / pos["entry_price"] - 1) * 100
                        else:
                            old_ret = (1 - close_price / pos["entry_price"]) * 100
                        cash *= (1 + old_ret / 100)
                        trades.append({"exit_date": day, "direction": pos["direction"], "return_pct": round(old_ret, 4)})
                    pos = None
                signal_idx += 1

            if pos and pos.get("exit_date") == day:
                close_price = prices.get(day, {}).get("close")
                if close_price and pos["entry_price"] > 0:
                    if pos["direction"] == "long":
                        trade_ret = (close_price / pos["entry_price"] - 1) * 100
                    else:
                        trade_ret = (1 - close_price / pos["entry_price"]) * 100
                    cash *= (1 + trade_ret / 100)
                    trades.append({"exit_date": day, "direction": pos["direction"], "return_pct": round(trade_ret, 4)})
                pos = None

            nav = cash
            if pos and pos["entry_price"] > 0:
                close_price = prices.get(day, {}).get("close")
                if close_price:
                    if pos["direction"] == "long":
                        unrealized = (close_price / pos["entry_price"] - 1)
                    else:
                        unrealized = (1 - close_price / pos["entry_price"])
                    nav = cash * (1 + unrealized)
            nav_series.append((day, round(nav, 6)))

        if not nav_series: return [], {}
        navs = [n for _, n in nav_series]
        total_return = (navs[-1] - 1.0) * 100
        n_days = len(navs)
        annual_return = ((navs[-1] / 1.0) ** (252 / max(n_days, 1)) - 1) * 100

        peak = navs[0]; peak_date = nav_series[0][0]
        max_dd = 0; dd_start = dd_end = ""
        for d, n in nav_series:
            if n > peak: peak = n; peak_date = d
            dd = (n - peak) / peak * 100
            if dd < max_dd: max_dd = dd; dd_start = peak_date; dd_end = d

        trade_rets = [t["return_pct"] for t in trades]
        max_loss_streak = max_win_streak = cur_loss = cur_win = 0
        loss_start = win_start = loss_end = win_end = ""
        for i, tr in enumerate(trade_rets):
            exit_d = trades[i].get("exit_date", "")
            if tr < 0:
                cur_loss += 1; cur_win = 0
                if cur_loss == 1: loss_start = exit_d
                if cur_loss > max_loss_streak: max_loss_streak = cur_loss; loss_end = exit_d
            else:
                cur_win += 1; cur_loss = 0
                if cur_win == 1: win_start = exit_d
                if cur_win > max_win_streak: max_win_streak = cur_win; win_end = exit_d

        avg_tr = sum(trade_rets) / len(trade_rets) if trade_rets else 0
        std_tr = (sum((r - avg_tr)**2 for r in trade_rets) / max(len(trade_rets)-1, 1))**0.5 if len(trade_rets) > 1 else 0
        sharpe = avg_tr / std_tr if std_tr > 0 else 0

        return nav_series, {
            "total_return": round(total_return, 2), "annual_return": round(annual_return, 2),
            "max_drawdown": round(max_dd, 2),
            "max_drawdown_dates": [dd_start, dd_end] if max_dd < 0 else [],
            "max_loss_streak": max_loss_streak,
            "max_loss_streak_dates": [loss_start, loss_end] if max_loss_streak > 0 else [],
            "max_win_streak": max_win_streak,
            "max_win_streak_dates": [win_start, win_end] if max_win_streak > 0 else [],
            "sharpe_like": round(sharpe, 2), "trade_count": len(trades),
            "signal_count": len(recs), "avg_trade_return": round(avg_tr, 2),
        }

    nav_all, risk_all = simulate_equity(equity_records, prices, sorted_dates)
    nav_bull, risk_bull = simulate_equity(equity_records, prices, sorted_dates, "bullish")
    nav_bear, risk_bear = simulate_equity(equity_records, prices, sorted_dates, "bearish")

    benchmark_nav = []
    if nav_all:
        first_day = nav_all[0][0]; last_day = nav_all[-1][0]
        if first_day in prices:
            base_price = prices[first_day]["close"]
            for day, _ in nav_all:
                if day in prices:
                    benchmark_nav.append((day, round(prices[day]["close"] / base_price, 6)))
        bench_return = round((benchmark_nav[-1][1] - 1.0) * 100, 2) if benchmark_nav else 0
        risk_all["benchmark_return"] = bench_return
        risk_all["alpha"] = round(risk_all.get("total_return", 0) - bench_return, 2)

    equity_output = {"overall": nav_all, "bullish_only": nav_bull, "bearish_only": nav_bear, "benchmark": benchmark_nav}
    risk_output = {"overall": risk_all, "bullish": risk_bull, "bearish": risk_bear}

    # JSON output (write first to survive terminal encoding errors)
    segments_output = {}
    for sk in sorted(by_seg.keys()):
        sv = by_seg[sk]
        if not sv["start_label"]:
            continue
        segments_output[sk] = {
            "direction": sv["direction"], "start_label": sv["start_label"],
            "end_label": sv["end_label"], "total": sv["total"],
            "bull": sv["bull"], "bear": sv["bear"], "score": round(sv["score"], 2),
        }

    inflection_output = {}
    for label, sigs in sorted(inflection_signals.items()):
        total_at = sum(x["score"] for x in sigs)
        inflection_output[label] = {
            "signals": sigs, "total_score": round(total_at, 2),
            "signal_count": len(sigs),
            "avg_score_pct": round(total_at / len(sigs) * 100, 2) if sigs else 0,
        }

    output = {
        "blogger": blogger,
        "scoring_version": "v11 (time_horizon-windowed single formula)",
        "signals_total": len(signals),
        "signals_scored": cnt_all,
        "score_zero": score_zero,
        "no_price": no_price,
        "no_segment": no_segment,
        "scores": scores,
        "time_horizon_scores": th_scores,
        "equity_curve": equity_output,
        "risk_metrics": risk_output,
        "segments": segments_output,
        "inflection_details": inflection_output,
    }

    out_dir = os.path.join(PROJECT_ROOT, "data", "scores")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{blogger}_v11.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"JSON: {out_path}")

    # Terminal output (inflection details)
    try:
        print(f"\n{'='*70}")
        print("BY SEGMENT")
        print(f"{'='*70}")
        for sk in sorted(by_seg.keys()):
            sv = by_seg[sk]
            if not sv["start_label"]:
                continue
            sd = "UP" if sv["direction"] == "rising" else "DN"
            print(f"  {sk:20s} {sd}: {sv['total']:3d} sigs, B={sv['bull']:3d} S={sv['bear']:3d}, score={sv['score']:+.2f}%")

        for label in sorted(inflection_signals.keys()):
            sigs_at = inflection_signals[label]
            if not sigs_at:
                continue
            total_at = sum(x["score"] for x in sigs_at)
            avg_at = total_at / len(sigs_at) if sigs_at else 0
            is_bottom = any(label == bl for bl in BOTTOM_DATES.values())
            tag = "[BOTTOM]" if is_bottom else "[TOP]"
            print(f"\n{tag} {label}: {len(sigs_at)} signals, total={total_at:+.2f}%, avg={avg_at:+.1f}%")
            for sig in sorted(sigs_at, key=lambda x: x["date"]):
                print(f"    {sig['date']} {sig['time']:>5} {sig['strength']:>8} "
                      f"ret={sig['return']:+.2f}% score={sig['score']:+.2f}% | {sig['evidence'][:50]}")
    except UnicodeEncodeError:
        print("(inflection details skipped - encoding issue)")


if __name__ == "__main__":
    main()
