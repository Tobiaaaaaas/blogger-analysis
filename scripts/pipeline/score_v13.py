"""
v13 scoring: dual-contact inflection-aware scoring.

Usage:
  python scripts/pipeline/score_v13.py --all       # score all bloggers
  python scripts/pipeline/score_v13.py --blogger <name>

Rules: .claude/skills/analyze-blogger/SKILL.md v13 §4.1.3
  return  = |P_next  - P_ref| / |P_ref|
  return2 = |P_next2 - P_ref| / |P_ref|

  return >= 1% → Case A: reward alignment / penalize opposition (use return)
  return <  1% → Case B: penalize alignment / reward opposition (use return2)

  P_next2 missing + return < 1% → score = 0
  P_next missing (unclosed segment) → score = 0

14 scores (7 total + 7 average %).  All scores are percentages.
score=0 signals excluded from numerator and denominator of all 14.
"""
import json, os, argparse, sys
from datetime import datetime, timedelta
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))

# ── Inflection points from knowledge/market_analysis.md ──
# (date, type, extreme_price, label)
#   顶 → 交易日最高价;  底 → 交易日最低价
INFLECTIONS = [
    ("2024-06-03", "顶", 3097,  "M0"),
    ("2024-09-13", "底", 2690,  "M1"),
    ("2024-10-08", "顶", 3674,  "M2"),
    ("2024-10-18", "底", 3153,  "I1"),
    ("2024-11-08", "顶", 3510,  "I2"),
    ("2024-11-27", "底", 3227,  "I3"),
    ("2024-12-10", "顶", 3495,  "I4"),
    ("2025-01-13", "底", 3141,  "I5"),
    ("2025-03-19", "顶", 3439,  "I6"),
    ("2025-04-07", "底", 3041,  "M3"),
    ("2025-11-14", "顶", 4034,  "M4"),
    ("2025-12-16", "底", 3816,  "I7"),
    ("2026-01-14", "顶", 4191,  "I8"),
    ("2026-02-03", "底", 4003,  "I9"),
    ("2026-03-03", "顶", 4197,  "I10"),
    ("2026-03-23", "底", 3795,  "M5"),
    ("2026-05-14", "顶", 4259,  "M6"),
    ("2026-06-08", "底", 3928,  "I11"),
    ("2026-06-23", "顶", 4175,  "I12"),
    ("2026-07-20", "底", 3741,  "M7"),
]

BOTTOM_INFS = {}
TOP_INFS = {}
for d, t, p, lbl in INFLECTIONS:
    if lbl == "M0": continue
    if t == "底": BOTTOM_INFS[d] = lbl
    else:         TOP_INFS[d] = lbl

LAST_CONFIRMED = INFLECTIONS[-1]  # M7
SIGNAL_START   = "2026-01-01"
THRESHOLD      = 1.0  # %


def _add_temp_inflection(prices):
    """Add post-M7 highest SH point as temporary inflection (SKILL.md §4.1.3)."""
    hi = 0.0
    hi_date = None
    for ds in sorted(prices.keys()):
        if ds > LAST_CONFIRMED[0]:
            h = prices[ds]["high"]
            if h > hi:
                hi = h
                hi_date = ds
    if hi_date:
        return INFLECTIONS + [(hi_date, "顶", hi, "TMP")]
    return INFLECTIONS


def load_prices():
    with open(os.path.join(ROOT, "data", "market", "market_data.json"),
              encoding="utf-8") as f:
        mkt = json.load(f)
    key = next(k for k in mkt if "上证" in k)
    prices = {}
    for r in mkt[key]:
        prices[r["日期"]] = dict(
            open=float(r["开盘"]), close=float(r["收盘"]),
            high=float(r["最高"]), low=float(r["最低"]),
        )
    return prices, sorted(prices.keys())


def ref_price(date_str, time_str, prices):
    """SKILL.md §4.1.2. Returns (price, ref_date) or (None, None)."""
    def _next(d):
        dt = datetime.strptime(d, "%Y-%m-%d")
        for off in range(1, 15):
            nd = (dt + timedelta(days=off)).strftime("%Y-%m-%d")
            if nd in prices: return nd
        return None

    if date_str in prices:
        if time_str:
            try:
                h, m = map(int, time_str.split(":"))
                if h < 9 or (h == 9 and m < 30):         # 盘前 → 开盘价
                    return prices[date_str]["open"], date_str
                if h >= 15:                                # 盘后 → 下一交易日开盘
                    nd = _next(date_str)
                    return (prices[nd]["open"], nd) if nd else (None, None)
                return prices[date_str]["close"], date_str  # 盘中 → 收盘价
            except (ValueError, TypeError):
                pass
        return prices[date_str]["close"], date_str          # 时间未知 → 收盘
    nd = _next(date_str)                                    # 非交易日 → 下一交易日开盘
    return (prices[nd]["open"], nd) if nd else (None, None)


def seg_idx(date_str, infs):
    for i in range(len(infs) - 1):
        if infs[i][0] <= date_str < infs[i + 1][0]:
            return i
    if date_str >= infs[-1][0]:
        return len(infs) - 2
    return None


def score_blogger(blogger, prices):
    path = os.path.join(ROOT, "data", "signals", f"{blogger}.json")
    if not os.path.exists(path): return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    signals_raw = data.get("signals", [])
    if not signals_raw: return None

    # Ranking eligibility: first signal must be before 2026-05-13 (SKILL.md §4.1.4)
    first_signal_date = None
    for s in signals_raw:
        pt = s.get("publish_time", "")
        if pt >= SIGNAL_START:
            d = pt[:10]
            if first_signal_date is None or d < first_signal_date:
                first_signal_date = d
    rankable = first_signal_date is not None and first_signal_date < "2026-05-13"

    signals = signals_raw
    infs = _add_temp_inflection(prices)

    # ── Accumulators ──
    DIMS = ["综合", "上升段", "下降段", "抄底", "逃顶", "看多", "看空"]
    dims  = {k: {"total": 0.0, "count": 0, "win": 0} for k in DIMS}
    th_acc  = defaultdict(lambda: {"total": 0.0, "cnt": 0, "win": 0, "eff": 0})
    seg_acc = defaultdict(lambda: {"dir": "", "total": 0, "bull": 0, "bear": 0, "score": 0.0})
    inf_sigs = defaultdict(list)

    pre_2026 = score_zero = no_price = no_segment = 0
    case_a = case_b = 0

    n_scorable = 0  # signals that actually go through scoring (not pre-2026)

    for s in signals:
        pt = s.get("publish_time", "")
        if not pt or pt[:10] < SIGNAL_START:
            pre_2026 += 1; continue

        d = s.get("direction", "")
        strength = s.get("strength", "moderate")
        th = s.get("time_horizon", "unspecified")
        if d not in ("bullish", "bearish"): continue
        if strength not in ("strong", "moderate"): strength = "moderate"
        if th not in ("short", "medium", "long", "unspecified"): th = "unspecified"

        n_scorable += 1
        date_str = pt[:10]
        time_str = pt[11:16] if len(pt) >= 16 else ""
        sbase = 2 if strength == "strong" else 1

        rp_val, ref_date = ref_price(date_str, time_str, prices)
        if rp_val is None: no_price += 1; continue

        si = seg_idx(date_str, infs)
        if si is None: no_segment += 1; continue

        p_next_date, p_next_type, p_next_price, p_next_label = infs[si + 1]
        has_p2 = si + 2 < len(infs)
        p_next2_price = infs[si + 2][2] if has_p2 else None
        is_rising = infs[si][1] == "底"

        ret  = abs(p_next_price - rp_val) / abs(rp_val) * 100
        ret2 = abs(p_next2_price - rp_val) / abs(rp_val) * 100 if has_p2 else None
        agrees = (is_rising and d == "bullish") or (not is_rising and d == "bearish")

        # ── v13 scoring ──
        if ret >= THRESHOLD:
            score = +sbase * ret if agrees else -sbase * ret
            case_a += 1
        else:
            if has_p2:
                score = -sbase * ret2 if agrees else +sbase * ret2
                case_b += 1
            else:
                score = 0.0

        if score == 0:
            score_zero += 1

        # ── Time horizon ──
        th_acc[th]["total"] += score
        th_acc[th]["cnt"] += 1
        if score != 0:
            th_acc[th]["eff"] += 1
            if score > 0: th_acc[th]["win"] += 1

        # ── Score=0 excluded from 14 scores ──
        if score == 0: continue

        seg_key = f"{infs[si][3]}→{p_next_label}"

        def add(dk):
            dims[dk]["total"] += score
            dims[dk]["count"] += 1
            if score > 0: dims[dk]["win"] += 1

        add("综合")
        add("上升段" if is_rising else "下降段")
        add("看多" if d == "bullish" else "看空")

        # 抄底 / 逃顶
        sig_dt = datetime.strptime(date_str, "%Y-%m-%d")
        if d == "bullish":
            for bd, bl in BOTTOM_INFS.items():
                if abs((sig_dt - datetime.strptime(bd, "%Y-%m-%d")).days) <= 1:
                    add("抄底")
                    inf_sigs[bl].append(dict(date=date_str, time=time_str,
                        direction=d, strength=strength, score=round(score, 2),
                        evidence=s.get("evidence", "")[:60]))
                    break
        else:
            for td, tl in TOP_INFS.items():
                if abs((sig_dt - datetime.strptime(td, "%Y-%m-%d")).days) <= 1:
                    add("逃顶")
                    inf_sigs[tl].append(dict(date=date_str, time=time_str,
                        direction=d, strength=strength, score=round(score, 2),
                        evidence=s.get("evidence", "")[:60]))
                    break

        # Segment
        sg = seg_acc[seg_key]
        if not sg["dir"]: sg["dir"] = "rising" if is_rising else "falling"
        sg["total"] += 1; sg["score"] += score
        if d == "bullish": sg["bull"] += 1
        else:              sg["bear"] += 1

    # ── Build output ──
    def mk(dk):
        d = dims[dk]
        c = d["count"]
        return dict(
            total_pct=round(d["total"], 2), count=c,
            avg_pct=round(d["total"] / c, 2) if c else 0.0,
            win_rate=round(d["win"] / c * 100, 2) if c else 0.0,
        )

    th_out = {}
    for th in ["short", "medium", "long", "unspecified"]:
        a = th_acc[th]
        e = a["eff"]
        th_out[th] = dict(
            count=a["cnt"], effective=e,
            total_pct=round(a["total"], 2),
            avg_pct=round(a["total"] / e, 2) if e else 0.0,
            win_rate=round(a["win"] / e * 100, 2) if e else 0.0,
        )

    seg_out = {}
    for sk in sorted(seg_acc.keys()):
        sv = seg_acc[sk]
        seg_out[sk] = dict(direction=sv["dir"], total=sv["total"],
                           bull=sv["bull"], bear=sv["bear"],
                           score_pct=round(sv["score"], 2))

    inf_out = {}
    for lbl, sigs in sorted(inf_sigs.items()):
        t = sum(x["score"] for x in sigs)
        inf_out[lbl] = dict(signals=sigs, total_score_pct=round(t, 2),
                            count=len(sigs),
                            avg_pct=round(t / len(sigs), 2) if sigs else 0)

    return dict(
        blogger=blogger, version="v13",
        total_raw=len(signals), pre_2026=pre_2026,
        scorable=n_scorable,
        score_zero=score_zero, no_price=no_price, no_segment=no_segment,
        case_a=case_a, case_b=case_b,
        first_signal_date=first_signal_date,
        rankable=rankable,
        scores={k: mk(k) for k in DIMS},
        time_horizon=th_out,
        segments=seg_out,
        inflections=inf_out,
    )


def print_result(r):
    tag = "" if r.get("rankable", True) else " [NOT RANKABLE]"
    print(f"\n{'='*70}")
    print(f"  {r['blogger']}  v13{tag}")
    print(f"  raw={r['total_raw']}  pre-2026={r['pre_2026']}  "
          f"scorable={r['scorable']}  first_signal={r.get('first_signal_date','?')}")
    print(f"  case_A={r['case_a']}  case_B={r['case_b']}  "
          f"score_zero={r['score_zero']}  no_price={r['no_price']}")
    print(f"{'='*70}")
    print(f"{'Dim':<8} {'Total%':>9} {'N':>5} {'Avg%':>9} {'Win%':>8}")
    print(f"{'-'*42}")
    for dim in ["综合", "上升段", "下降段", "抄底", "逃顶", "看多", "看空"]:
        d = r["scores"][dim]
        print(f"{dim:<8} {d['total_pct']:>+9.2f}% {d['count']:>5} "
              f"{d['avg_pct']:>+8.2f}% {d['win_rate']:>7.1f}%")
    print(f"{'='*70}")


def print_ranking(results):
    rankable = [r for r in results if r.get("rankable", True)]
    excluded = [r for r in results if not r.get("rankable", True)]

    if excluded:
        print(f"\n  EXCLUDED FROM RANKING (first signal >= 2026-05-13):")
        for r in excluded:
            print(f"    {r['blogger']:<20s}  first_signal={r.get('first_signal_date','?')}")

    ranked = sorted(rankable, key=lambda r: r["scores"]["综合"]["avg_pct"], reverse=True)
    print(f"\n{'='*100}")
    print(f"  V13 RANKING — sorted by 综合 avg% ({len(ranked)} rankable bloggers)")
    print(f"{'='*100}")
    print(f"{'#':>3} {'Blogger':<22} {'综合':>8} {'上升段':>8} {'下降段':>8} "
          f"{'抄底':>8} {'逃顶':>8} {'看多':>8} {'看空':>8} {'Win%':>7} {'N':>5}")
    print(f"{'-'*100}")
    for i, r in enumerate(ranked, 1):
        s = r["scores"]
        print(f"{i:>3} {r['blogger']:<22} "
              f"{s['综合']['avg_pct']:>+7.2f}% {s['上升段']['avg_pct']:>+7.2f}% "
              f"{s['下降段']['avg_pct']:>+7.2f}% {s['抄底']['avg_pct']:>+7.2f}% "
              f"{s['逃顶']['avg_pct']:>+7.2f}% {s['看多']['avg_pct']:>+7.2f}% "
              f"{s['看空']['avg_pct']:>+7.2f}% {s['综合']['win_rate']:>6.1f}% "
              f"{s['综合']['count']:>5}")


def main():
    p = argparse.ArgumentParser(description="v13 scoring")
    p.add_argument("--blogger")
    p.add_argument("--all", action="store_true")
    args = p.parse_args()

    prices, _ = load_prices()

    if args.all:
        sd = os.path.join(ROOT, "data", "signals")
        bloggers = sorted(f.replace(".json", "") for f in os.listdir(sd)
                         if f.endswith(".json"))
    elif args.blogger:
        bloggers = [args.blogger]
    else:
        p.print_help(); return

    results = []
    out_dir = os.path.join(ROOT, "data", "scores")
    os.makedirs(out_dir, exist_ok=True)

    for b in bloggers:
        r = score_blogger(b, prices)
        if r is None:
            print(f"SKIP {b}: no signals"); continue
        print_result(r)
        with open(os.path.join(out_dir, f"{b}_v13.json"), "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2)
        results.append(r)

    if len(results) >= 2:
        print_ranking(results)


if __name__ == "__main__":
    main()
