"""
v10 scoring: 10 scores (5 total + 5 average %) with A/B/C/D + short_return.
Usage: python scripts/score_v10.py --blogger 大盘蜂向标

Sources:
  - Inflection points: knowledge/market_analysis.md (上证 zigzag 4%)
  - Scoring formula: .claude/skills/analyze-blogger/SKILL.md §4.3
"""

import json
import sys
import os
import argparse
from datetime import datetime, timedelta
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)


# ── Inflection Points (from market_analysis.md 上证 zigzag 4%) ──
# Format: (date, type, close, extreme_price, label)
#   type: '顶' or '底'
#   extreme_price: 最高价 for 顶, 最低价 for 底 (used as return target)
#   label: Major/Intermediate identifier
INFLECTIONS = [
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
    ("2026-07-20", "底", 3741, 3741, "M8"),   # 暂定 — zigzag 未确认，反弹突破 3891 后正式确认
]

# All已知拐点 for 抄底/逃顶 ±1 day filtering
BOTTOM_DATES = {
    "2024-09-13": "M1",   # 2690 (manual Major)
    "2024-10-18": "I1",
    "2024-11-27": "I3",
    "2025-01-13": "I5",
    "2025-04-07": "M3",
    "2025-12-16": "I9",
    "2026-02-03": "I11",
    "2026-03-23": "M5",
    "2026-06-08": "I13",
    "2026-07-20": "M8",   # 暂定 — zigzag 未确认
}

TOP_DATES = {
    "2024-10-08": "M2",
    "2024-11-08": "I2",
    "2024-12-10": "I4",
    "2025-03-19": "I6",
    "2025-11-14": "M4",
    "2026-01-14": "I10",
    "2026-03-03": "I12",
    "2026-05-14": "M6",
    "2026-06-23": "I14",
    "2026-06-25": "M7",   # 创业板顶
}

# Build segments from consecutive inflection pairs
SEGMENTS = []
for i in range(len(INFLECTIONS) - 1):
    s_date, s_type, s_close, s_extreme, s_label = INFLECTIONS[i]
    e_date, e_type, e_close, e_extreme, e_label = INFLECTIONS[i + 1]
    direction = "rising" if s_type == "底" else "falling"
    SEGMENTS.append({
        "start_date": s_date, "start_type": s_type, "start_extreme": s_extreme,
        "start_label": s_label,
        "end_date": e_date, "end_type": e_type, "end_extreme": e_extreme,
        "end_label": e_label,
        "direction": direction,
    })


def load_prices():
    """Load上证综指 daily prices."""
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
            "open": float(r["开盘"]),
            "close": float(r["收盘"]),
            "high": float(r["最高"]),
            "low": float(r["最低"]),
        }
    return prices, sorted(prices.keys())


def get_ref_price(date_str, pub_time_str, prices, sorted_dates):
    """Determine signal reference price per SKILL.md §4.3.2.

    Returns (price, label, ref_date) where ref_date is the T-day (trading day
    the reference price corresponds to).  ref_date is used as the 3-day window
    start for short_return computation.
    """
    if date_str in prices:
        if pub_time_str:
            try:
                h, m = map(int, pub_time_str.split(":"))
                if h < 9 or (h == 9 and m < 30):
                    return prices[date_str]["open"], f"{date_str} 开盘", date_str
                elif h >= 15:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    for offset in range(1, 15):
                        nd = (dt + timedelta(days=offset)).strftime("%Y-%m-%d")
                        if nd in prices:
                            return prices[nd]["open"], f"{nd} 开盘", nd
                    return prices[date_str]["close"], f"{date_str} 收盘(fb)", date_str
                else:
                    return prices[date_str]["close"], f"{date_str} 收盘", date_str
            except (ValueError, TypeError):
                pass
        return prices[date_str]["close"], f"{date_str} 收盘(def)", date_str
    else:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        for offset in range(1, 15):
            nd = (dt + timedelta(days=offset)).strftime("%Y-%m-%d")
            if nd in prices:
                return prices[nd]["open"], f"{nd} 开盘(非交易日)", nd
        return None, "NO PRICE", None


def get_3d_extreme_prices(date_str, prices, sorted_dates):
    """Get 3-day extreme closing prices starting from date_str."""
    try:
        idx = sorted_dates.index(date_str)
    except ValueError:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        for offset in range(0, 15):
            nd = (dt + timedelta(days=offset)).strftime("%Y-%m-%d")
            if nd in prices:
                try:
                    idx = sorted_dates.index(nd)
                    break
                except ValueError:
                    continue
        else:
            return None, None

    closes = []
    for i in range(idx, min(idx + 3, len(sorted_dates))):
        closes.append(prices[sorted_dates[i]]["close"])
    if not closes:
        return None, None
    return max(closes), min(closes)


def find_segment(date_str):
    """Find the segment that contains date_str."""
    for seg in SEGMENTS:
        if seg["start_date"] <= date_str < seg["end_date"]:
            return seg
    return None


def in_date_range(signal_date, target_date, days=1):
    """Check if signal_date is within ±days of target_date."""
    sd = datetime.strptime(signal_date, "%Y-%m-%d")
    td = datetime.strptime(target_date, "%Y-%m-%d")
    return abs((sd - td).days) <= days


def main():
    parser = argparse.ArgumentParser(description="v10 10-score evaluation")
    parser.add_argument("--blogger", required=True, help="Blogger name")
    args = parser.parse_args()
    blogger = args.blogger

    prices, sorted_dates = load_prices()

    sigs_path = os.path.join(PROJECT_ROOT, "data", "signals", f"{blogger}.json")
    sigs_d = json.load(open(sigs_path, encoding="utf-8"))
    signals = sigs_d.get("signals", [])
    if not signals:
        print(f"ERROR: No signals found for {blogger}")
        return

    # ── Accumulators ──
    score_all = 0.0
    score_rising = 0.0
    score_falling = 0.0
    score_bottom = 0.0
    score_top = 0.0
    cnt_all = 0
    cnt_rising = 0
    cnt_falling = 0
    cnt_bottom = 0
    cnt_top = 0

    no_price = 0
    no_segment = 0
    last_segment = 0

    by_seg = defaultdict(lambda: {
        "direction": "", "total": 0, "bull": 0, "bear": 0, "score": 0.0,
        "start_label": "", "end_label": "",
    })
    inflection_signals = defaultdict(list)

    for s in signals:
        pt = s.get("publish_time", "")
        if not pt:
            continue
        date_str = pt[:10]
        direction = s.get("direction", "")
        strength = s.get("strength", "moderate")

        if direction not in ("bullish", "bearish"):
            continue
        if strength not in ("strong", "moderate"):
            strength = "moderate"

        pub_time = ""
        if len(pt) >= 16:
            pub_time = pt[11:16]

        ev = s.get("evidence", "")[:80]

        ref_p, ref_label, ref_date = get_ref_price(date_str, pub_time, prices, sorted_dates)
        if ref_p is None:
            no_price += 1
            continue

        seg = find_segment(date_str)
        if not seg:
            last_inf = INFLECTIONS[-1]
            if date_str >= last_inf[0]:
                last_segment += 1
                sk = f"{last_inf[4]}→now"
                by_seg[sk]["direction"] = "falling" if last_inf[1] == "顶" else "rising"
                by_seg[sk]["total"] += 1
                by_seg[sk]["start_label"] = last_inf[4]
                by_seg[sk]["end_label"] = "?"
                if direction == "bullish":
                    by_seg[sk]["bull"] += 1
                else:
                    by_seg[sk]["bear"] += 1
            else:
                no_segment += 1
            continue

        return_val = abs(seg["end_extreme"] - ref_p) / abs(ref_p) if ref_p > 0 else 0
        if return_val < 0:
            return_val = 0

        sbase = 2 if strength == "strong" else 1
        high_3d, low_3d = get_3d_extreme_prices(ref_date, prices, sorted_dates)
        short_return = 0.0
        is_rising = (seg["direction"] == "rising")

        if is_rising and direction == "bullish":
            if high_3d is not None:
                short_return = abs(high_3d / ref_p - 1)
            score = sbase * max(return_val, short_return)
        elif is_rising and direction == "bearish":
            if low_3d is not None:
                short_return = abs(low_3d / ref_p - 1)
            if short_return > 0.015:
                score = sbase * short_return
            else:
                score = -sbase * return_val
        elif not is_rising and direction == "bullish":
            if high_3d is not None:
                short_return = abs(high_3d / ref_p - 1)
            if short_return > 0.015:
                score = sbase * short_return
            else:
                score = -sbase * return_val
        else:
            if low_3d is not None:
                short_return = abs(low_3d / ref_p - 1)
            score = sbase * max(return_val, short_return)

        # Accumulate
        cnt_all += 1
        score_all += score

        if is_rising:
            cnt_rising += 1
            score_rising += score
        else:
            cnt_falling += 1
            score_falling += score

        if direction == "bullish":
            for bd, blabel in BOTTOM_DATES.items():
                if in_date_range(date_str, bd, 1):
                    cnt_bottom += 1
                    score_bottom += score
                    inflection_signals[blabel].append({
                        "date": date_str, "time": pub_time,
                        "direction": direction, "strength": strength,
                        "return": round(return_val, 4),
                        "short_return": round(short_return, 4),
                        "score": round(score, 4),
                        "ref": ref_label, "evidence": ev[:60],
                    })
                    break
        else:
            for td, tlabel in TOP_DATES.items():
                if in_date_range(date_str, td, 1):
                    cnt_top += 1
                    score_top += score
                    inflection_signals[tlabel].append({
                        "date": date_str, "time": pub_time,
                        "direction": direction, "strength": strength,
                        "return": round(return_val, 4),
                        "short_return": round(short_return, 4),
                        "score": round(score, 4),
                        "ref": ref_label, "evidence": ev[:60],
                    })
                    break

        sk = f"{seg['start_label']}→{seg['end_label']}"
        by_seg[sk]["direction"] = seg["direction"]
        by_seg[sk]["start_label"] = seg["start_label"]
        by_seg[sk]["end_label"] = seg["end_label"]
        by_seg[sk]["total"] += 1
        by_seg[sk]["score"] += score
        if direction == "bullish":
            by_seg[sk]["bull"] += 1
        else:
            by_seg[sk]["bear"] += 1

    # ── Compute average scores ──
    def avg_pct(total, count):
        return round(total / count * 100, 2) if count > 0 else 0.0

    scores = {
        "综合":   {"total": round(score_all, 2),    "count": cnt_all,    "avg_pct": avg_pct(score_all, cnt_all)},
        "上升段": {"total": round(score_rising, 2), "count": cnt_rising, "avg_pct": avg_pct(score_rising, cnt_rising)},
        "下降段": {"total": round(score_falling, 2),"count": cnt_falling,"avg_pct": avg_pct(score_falling, cnt_falling)},
        "抄底":   {"total": round(score_bottom, 2), "count": cnt_bottom, "avg_pct": avg_pct(score_bottom, cnt_bottom)},
        "逃顶":   {"total": round(score_top, 2),    "count": cnt_top,    "avg_pct": avg_pct(score_top, cnt_top)},
    }

    # ── Terminal output ──
    print(f"Signals: {len(signals)} total, {cnt_all} scored, "
          f"{last_segment} last-segment(score=0), {no_price} no-price, {no_segment} no-segment")

    print(f"\n{'='*70}")
    print(f"10 SCORES -- {blogger}")
    print(f"{'='*70}")
    print(f"{'Dim':<8} {'Total':>10} {'Count':>8} {'Avg':>10}")
    print(f"{'-'*40}")
    for dim, d in scores.items():
        print(f"{dim:<8} {d['total']:>+10.2f} {d['count']:>8} {d['avg_pct']:>+9.2f}%")
    print(f"{'='*70}")

    # Segment details
    print(f"\n{'='*70}")
    print("BY SEGMENT")
    print(f"{'='*70}")
    for sk in sorted(by_seg.keys()):
        sv = by_seg[sk]
        if not sv["start_label"]:
            continue
        seg_dir = "↑上升" if sv["direction"] == "rising" else "↓下降"
        print(f"  {sk:20s} {seg_dir}: {sv['total']:3d} sigs, "
              f"bull={sv['bull']:3d} bear={sv['bear']:3d}, "
              f"score={sv['score']:+.2f}")

    # Inflection details
    for label in sorted(inflection_signals.keys()):
        sigs_at = inflection_signals[label]
        if not sigs_at:
            continue
        total_at = sum(x["score"] for x in sigs_at)
        avg_at = total_at / len(sigs_at) * 100 if sigs_at else 0
        is_bottom = any(label == bl for bl in BOTTOM_DATES.values())
        tag = "[BOTTOM]" if is_bottom else "[TOP]"
        print(f"\n{tag} {label}: {len(sigs_at)} signals, total={total_at:+.2f}, avg={avg_at:+.1f}%")
        for sig in sorted(sigs_at, key=lambda x: x["date"]):
            print(f"    {sig['date']} {sig['time']:>5} {sig['strength']:>8} "
                  f"ret={sig['return']:.3f} short={sig['short_return']:.3f} "
                  f"score={sig['score']:+.3f} | {sig['evidence'][:50]}")

    # ── Build JSON output ──
    segments_output = {}
    for sk in sorted(by_seg.keys()):
        sv = by_seg[sk]
        if not sv["start_label"]:
            continue
        segments_output[sk] = {
            "direction": sv["direction"],
            "start_label": sv["start_label"],
            "end_label": sv["end_label"],
            "total": sv["total"],
            "bull": sv["bull"],
            "bear": sv["bear"],
            "score": round(sv["score"], 2),
        }

    inflection_output = {}
    for label, sigs in sorted(inflection_signals.items()):
        total_at = sum(x["score"] for x in sigs)
        inflection_output[label] = {
            "signals": sigs,
            "total_score": round(total_at, 2),
            "signal_count": len(sigs),
            "avg_score_pct": round(total_at / len(sigs) * 100, 2) if sigs else 0,
        }

    output = {
        "blogger": blogger,
        "scoring_version": "v10 (10-score)",
        "signals_total": len(signals),
        "signals_scored": cnt_all,
        "last_segment_unscored": last_segment,
        "no_price": no_price,
        "no_segment": no_segment,
        "scores": scores,
        "segments": segments_output,
        "inflection_details": inflection_output,
    }

    out_dir = os.path.join(PROJECT_ROOT, "data", "scores")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{blogger}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\nJSON output: {out_path}")


if __name__ == "__main__":
    main()
