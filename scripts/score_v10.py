"""
v10 scoring: match signals to posts for publish_time, compute ret-based scores.
Usage: python scripts/score_v10.py --blogger 大盘蜂向标
"""
import json, sys, os
from datetime import datetime, timedelta
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

def load_prices():
    mkt = json.load(open(os.path.join(PROJECT_ROOT, 'data/market/market_data.json'), encoding='utf-8'))
    prices = {}
    for r in mkt['上证综指']:
        prices[r['日期']] = {'open': float(r['开盘']), 'close': float(r['收盘'])}
    return prices, sorted(prices.keys())

def get_ref_price(date_str, pub_time_str, prices, sorted_dates):
    if date_str in prices:
        if pub_time_str:
            try:
                h, m = map(int, pub_time_str.split(':'))
                if h < 9 or (h == 9 and m < 30):
                    return prices[date_str]['open'], f"{date_str} open"
                elif h >= 15:
                    dt = datetime.strptime(date_str, '%Y-%m-%d')
                    for o in range(1, 15):
                        nd = (dt + timedelta(days=o)).strftime('%Y-%m-%d')
                        if nd in prices:
                            return prices[nd]['open'], f"{nd} open"
                    return prices[date_str]['close'], f"{date_str} close(fb)"
                else:
                    return prices[date_str]['close'], f"{date_str} close"
            except:
                pass
        return prices[date_str]['close'], f"{date_str} close(def)"
    else:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        for o in range(1, 15):
            nd = (dt + timedelta(days=o)).strftime('%Y-%m-%d')
            if nd in prices:
                return prices[nd]['open'], f"{nd} open"
        return None, 'NO PRICE'

def main():
    blogger = sys.argv[2] if len(sys.argv) > 2 else '大盘蜂向标'

    prices, sorted_dates = load_prices()

    # Load posts
    posts_d = json.load(open(
        os.path.join(PROJECT_ROOT, 'data/posts', f'{blogger}.json'), encoding='utf-8'))
    posts = posts_d.get('posts', [])

    # Load signals
    sigs_d = json.load(open(
        os.path.join(PROJECT_ROOT, 'data/signals', f'{blogger}.json'), encoding='utf-8'))
    sigs = sigs_d['signals']
    valid = [s for s in sigs if s.get('specific') not in ('directional_vague','descriptive')
             and s.get('time_horizon') not in ('intraday','short')]

    # Match signals to posts by date + content overlap
    matched = 0
    for s in valid:
        ev = s.get('evidence','')[:50].strip()
        date = s['date']
        best_post, best_overlap = None, 0
        for p in posts:
            pdate = p.get('publish_date','')[:10]
            pcontent = p.get('content','')
            if pdate != date:
                continue
            overlap = 0
            for i in range(min(len(ev), 30)):
                if ev[i:i+15] in pcontent:
                    overlap += 1
            if overlap > best_overlap:
                best_overlap = overlap
                best_post = p
        if best_post and best_overlap >= 1:
            pd = best_post.get('publish_date','')
            parts = pd.split(' ')
            s['date'] = parts[0]
            s['publish_time'] = parts[1] if len(parts) > 1 else ''
            matched += 1
        else:
            s['publish_time'] = ''

    # Segments (上证 only)
    segments = [
        ('I9','2025-12-16','底',3825,'I10','2026-01-14','顶',4126,'rising'),
        ('I10','2026-01-14','顶',4126,'I11','2026-02-03','底',4068,'falling'),
        ('I11','2026-02-03','底',4068,'I12','2026-03-03','顶',4123,'rising'),
        ('I12','2026-03-03','顶',4123,'M5','2026-03-23','底',3813,'falling'),
        ('M5','2026-03-23','底',3813,'M6','2026-05-14','顶',4178,'rising'),
        ('M6','2026-05-14','顶',4178,'I13','2026-06-08','底',3959,'falling'),
        ('I13','2026-06-08','底',3959,'I14','2026-06-23','顶',4106,'rising'),
        ('I14','2026-06-23','顶',4106,'M8','2026-07-20','底',3741,'falling'),
    ]
    bottoms = {'I9':'2025-12-16','I11':'2026-02-03','M5':'2026-03-23','I13':'2026-06-08','M8':'2026-07-20'}
    tops = {'I10':'2026-01-14','I12':'2026-03-03','M6':'2026-05-14','I14':'2026-06-23'}

    results = {'total_bull':0,'total_bear':0,'bottom_bull':0,'top_bear':0}
    by_seg = defaultdict(lambda: {'dir':'','total':0,'correct':0,'wrong':0,'bull':0,'bear':0,'score':0})
    bottom_details, top_details = [], []

    for s in valid:
        d = s['date']; direction = s.get('direction',''); strength = s.get('strength','moderate')
        pt = s.get('publish_time',''); ev = s.get('evidence','')[:80]

        ref_p, ref_label = get_ref_price(d, pt, prices, sorted_dates)
        if ref_p is None: continue

        seg = None
        for sg in segments:
            if sg[1] <= d < sg[5]:
                seg = sg; break
        if not seg: continue

        start_p, end_p = seg[3], seg[7]
        ret = abs(end_p - ref_p) / abs(ref_p) if ref_p else 0
        if ret < 0:
            ret = 0

        sd = seg[8]
        correct = (sd == 'rising' and direction == 'bullish') or (sd == 'falling' and direction == 'bearish')
        dsign = 1 if correct else -1
        sbase = 2 if strength == 'strong' else 1
        score = sbase * dsign * ret

        if direction == 'bullish': results['total_bull'] += score
        else: results['total_bear'] += score

        for bl, bdate in bottoms.items():
            bdt = datetime.strptime(bdate, '%Y-%m-%d'); sdt = datetime.strptime(d, '%Y-%m-%d')
            if abs((sdt-bdt).days) <= 1 and direction == 'bullish':
                results['bottom_bull'] += score
                bottom_details.append((d, pt, bl, strength, ret, score, ref_label, f'{seg[0]}-{seg[4]}', sd, ev[:60]))
                break
        for tl, tdate in tops.items():
            tdt = datetime.strptime(tdate, '%Y-%m-%d'); sdt = datetime.strptime(d, '%Y-%m-%d')
            if abs((sdt-tdt).days) <= 1 and direction == 'bearish':
                results['top_bear'] += score
                top_details.append((d, pt, tl, strength, ret, score, ref_label, f'{seg[0]}-{seg[4]}', sd, ev[:60]))
                break

        sk = f'{seg[0]}-{seg[4]}'
        by_seg[sk]['dir'] = sd; by_seg[sk]['total'] += 1; by_seg[sk]['score'] += score
        if correct: by_seg[sk]['correct'] += 1
        else: by_seg[sk]['wrong'] += 1
        if direction == 'bullish': by_seg[sk]['bull'] += 1
        else: by_seg[sk]['bear'] += 1

    print(f'Matched publish_time: {matched}/{len(valid)}')
    print(f'\n=== FOUR SCORES ===')
    print(f'综合看多: {results["total_bull"]:+.2f}')
    print(f'综合看空: {results["total_bear"]:+.2f}')
    print(f'抄底: {results["bottom_bull"]:+.2f}')
    print(f'逃顶: {results["top_bear"]:+.2f}')

    print('\n=== BOTTOM DETAILS ===')
    for b in bottom_details:
        print(f'{b[0]} {b[1]:>6} @{b[2]} {b[3]} seg={b[7]} {b[8]} ref={b[6]} ret={b[4]:.2f} score={b[5]:+.2f} | {b[9]}')

    print('\n=== TOP DETAILS ===')
    for t in top_details:
        print(f'{t[0]} {t[1]:>6} @{t[2]} {t[3]} seg={t[7]} {t[8]} ref={t[6]} ret={t[4]:.2f} score={t[5]:+.2f} | {t[9]}')

    print('\n=== BY SEGMENT ===')
    for sk, sv in sorted(by_seg.items()):
        print(f'{sk} {sv["dir"]:>7}: {sv["total"]} sigs, b={sv["bull"]} s={sv["bear"]}, ok={sv["correct"]} no={sv["wrong"]}, score={sv["score"]:+.2f}')

if __name__ == '__main__':
    main()
