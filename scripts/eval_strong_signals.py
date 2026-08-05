"""
Evaluate strong-conviction signal accuracy for 顺应周期.
"""
import json, os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

with open(os.path.join(PROJECT_ROOT, "data/signals/顺应周期.json"), "r", encoding="utf-8") as f:
    data = json.load(f)

with open(os.path.join(PROJECT_ROOT, "data/market/market_data.json"), "r", encoding="utf-8") as f:
    market = json.load(f)

shanghai = {r["日期"]: float(r["收盘"]) for r in market["上证指数"]}
all_dates = sorted(shanghai.keys())

TH_WINDOW = {"short": [0,1,2], "medium": [5,6,7,8,9,10], "long": [20,21,22], "unspecified": [20,21,22]}

signals = data["signals"]
strong = [s for s in signals if s.get("strength") == "strong"]

# Also evaluate moderate for comparison
moderate = [s for s in signals if s.get("strength") == "moderate"]

def find_next_trading_day(date_str):
    for d in all_dates:
        if d >= date_str:
            return d
    return None

def get_window_close(ref_date, offsets):
    ref_idx = all_dates.index(ref_date)
    closes = []
    for off in offsets:
        tgt = ref_idx + off
        if 0 <= tgt < len(all_dates):
            closes.append(shanghai[all_dates[tgt]])
    return closes

def evaluate(signal):
    pub_date = signal["publish_time"][:10]
    direction = signal["direction"]
    th = signal["time_horizon"]

    ref_date = find_next_trading_day(pub_date)
    if not ref_date:
        return None

    ref_px = shanghai.get(ref_date)
    if not ref_px:
        return None

    offsets = TH_WINDOW.get(th, [20,21,22])
    window_closes = get_window_close(ref_date, offsets)

    if not window_closes:
        return None

    avg_close = sum(window_closes) / len(window_closes)
    pct_change = (avg_close / ref_px - 1) * 100

    correct = (pct_change > 0) if direction == "bullish" else (pct_change < 0)

    return {
        "post_n": signal["post_n"],
        "pub_date": pub_date,
        "ref_date": ref_date,
        "ref_px": ref_px,
        "direction": direction,
        "th": th,
        "window_closes": window_closes,
        "avg_close": avg_close,
        "pct_change": pct_change,
        "correct": correct,
        "evidence": signal.get("evidence", "")
    }

print("=" * 75)
print("  顺应周期 Strong 观点有效性检验")
print("=" * 75)
print()

strong_results = []
for s in strong:
    r = evaluate(s)
    if r:
        strong_results.append(r)
    else:
        pub_date = s["publish_time"][:10]
        direction = s["direction"]
        th = s["time_horizon"]
        ref_date = find_next_trading_day(pub_date)
        if not ref_date:
            print(f"  Signal #{s['post_n']} | {pub_date} | {direction} | th={th}")
            print(f"    SKIPPED: publish date after last available trading day")
        else:
            offsets = TH_WINDOW.get(th, [20,21,22])
            ref_idx = all_dates.index(ref_date)
            max_off = max(offsets)
            print(f"  Signal #{s['post_n']} | {pub_date} | {direction} | th={th}")
            print(f"    SKIPPED: ref={ref_date} (idx={ref_idx}), need offset +{max_off}, only have {len(all_dates)-1-ref_idx} trading days after")
        print()

for r in strong_results:
    closes_str = ", ".join(f"{c:.1f}" for c in r["window_closes"])
    verdict = "CORRECT" if r["correct"] else "WRONG"
    print(f"  Signal #{r['post_n']} | {r['pub_date']} | {r['direction']:7s} | th={r['th']:11s}")
    print(f"    Ref: {r['ref_date']} @ {r['ref_px']:.1f}")
    print(f"    Window ({r['th']}): [{closes_str}] avg={r['avg_close']:.1f}")
    print(f"    Change: {r['pct_change']:+.2f}%  =>  {verdict}")
    print(f"    Evidence: {r['evidence'][:250]}")
    print()

# Summary
n = len(strong_results)
n_correct = sum(1 for r in strong_results if r["correct"])
print(f"  Strong signals: {n_correct}/{n} correct ({n_correct/n*100:.0f}%)" if n else "  No evaluable strong signals")
print()

# Also evaluate moderate signals for baseline
print("=" * 75)
print("  Moderate 信号作为基线对比")
print("=" * 75)
print()

mod_results = []
for s in moderate:
    r = evaluate(s)
    if r:
        mod_results.append(r)

mod_correct = sum(1 for r in mod_results if r["correct"])
mod_n = len(mod_results)

print(f"  Moderate signals evaluated: {mod_n}")
print(f"  Moderate correct: {mod_correct}/{mod_n} ({mod_correct/mod_n*100:.1f}%)")
print()

# By direction for moderate
for d in ["bullish", "bearish"]:
    subset = [r for r in mod_results if r["direction"] == d]
    if subset:
        c = sum(1 for r in subset if r["correct"])
        print(f"  {d}: {c}/{len(subset)} ({c/len(subset)*100:.1f}%)")

# Break down strong results by direction
print()
print("  --- Strong 按方向 ---")
for d in ["bullish", "bearish"]:
    subset = [r for r in strong_results if r["direction"] == d]
    if subset:
        for r in subset:
            print(f"  {r['direction']} #{r['post_n']}: {r['pct_change']:+.2f}% => {'OK' if r['correct'] else 'XX'}")
    else:
        print(f"  {d}: no strong signals")

# Takeaway
print()
print("=" * 75)
print("  结论")
print("=" * 75)
if n >= 3:
    print(f"  Strong 信号共 {n} 条，正确 {n_correct} 条 ({n_correct/n*100:.0f}%)")
    print(f"  Moderate 信号共 {mod_n} 条，正确 {mod_correct} 条 ({mod_correct/mod_n*100:.1f}%)")
    print()
    if n_correct/n > mod_correct/mod_n:
        print(f"  Strong > Moderate: 博主的强烈信念确实比普通观点更有效")
    else:
        print(f"  Strong <= Moderate: 博主的'强烈'信念并不比普通观点更准")
    print(f"  但注意：仅 {n} 条 strong 信号，样本量太小，统计结论不可靠。")
else:
    print(f"  仅有 {n} 条 strong 信号，样本过少无法得出统计结论。")
    print(f"  但这本身就是一个发现：博主在 365 条信号中仅表达了 4 次强烈信念 ({4/365*100:.1f}%)。")
    print(f"  他绝大部分观点是 moderate ({len(moderate)}/365 = {len(moderate)/365*100:.0f}%)。")
