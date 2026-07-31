"""
Generate quantitative report data for all bloggers.
Outputs JSON with all stats needed for report generation.
"""
import json, os, sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from evaluate_precision import load_market_data, load_signals, aggregate_blogger
from evaluate_trade_pairs import load_market_data as lmd, load_signals as ls, evaluate_pairs

WINDOWS = {"T+5": 5, "T+10": 10, "T+15": 15, "T+20": 20}

def get_signal_dist(signals):
    cats = {
        'strong_explicit': 0, 'strong_clear': 0,
        'moderate_explicit': 0, 'moderate_clear': 0, 'vague': 0,
    }
    bull = bear = 0
    for s in signals:
        if s['specific'] == 'directional_vague':
            cats['vague'] += 1
        elif s['strength'] == 'strong' and s['specific'] == 'explicit_action':
            cats['strong_explicit'] += 1
        elif s['strength'] == 'strong' and s['specific'] == 'directional_clear':
            cats['strong_clear'] += 1
        elif s['strength'] == 'moderate' and s['specific'] == 'explicit_action':
            cats['moderate_explicit'] += 1
        elif s['strength'] == 'moderate' and s['specific'] == 'directional_clear':
            cats['moderate_clear'] += 1
        if s['direction'] == 'bullish':
            bull += 1
        elif s['direction'] == 'bearish':
            bear += 1
    total = len(signals)
    cats['total'] = total
    cats['valid'] = total - cats['vague']
    cats['bullish'] = bull
    cats['bearish'] = bear
    return cats


def fmt_pct(v):
    if v is None: return 'N/A'
    return f"{v:+.2f}%"


def generate_blogger_data(blogger_data, market_data):
    name = blogger_data['blogger']
    signals = blogger_data['signals']
    posts_file = os.path.join(PROJECT_ROOT, 'data', 'posts', f'{name}.json')

    # Posts info
    posts_count = 0
    time_range = '?'
    user_info = {}
    if os.path.exists(posts_file):
        with open(posts_file) as f:
            pd = json.load(f)
        if isinstance(pd, dict):
            posts_count = pd.get('total_posts', len(pd.get('posts', [])))
            time_range = pd.get('time_range', '?')
            user_info = pd.get('user_info', {})

    # Precision evaluation
    precision = aggregate_blogger(blogger_data, market_data, WINDOWS, quality_only=True)

    # Trade pair evaluation
    pairs = evaluate_pairs(blogger_data, market_data, quality_only=True)

    # Signal distribution
    dist = get_signal_dist(signals)

    result = {
        'blogger': name,
        'posts_count': posts_count,
        'time_range': str(time_range),
        'user_info': user_info,
        'signal_dist': dist,
        'precision': {},
        'pairs': pairs,
    }

    if precision:
        for dim_key in ['bullish', 'bearish']:
            dim = precision.get(dim_key, {})
            if not dim or dim.get('total', 0) == 0:
                continue

            strong = dim.get('strong_only', {})
            wr = dim.get('win_rates_by_window', {})
            ar = dim.get('avg_returns_by_window', {})
            risk = dim.get('risk_by_window', {})

            dim_data = {
                'total': dim['total'],
                'strong': dim.get('total_strong', 0),
                'moderate': dim.get('total_moderate', 0),
                'win_rate': dim.get('win_rate', 0),
                'avg_return': dim.get('avg_return', 0),
                'max_gain': dim.get('max_gain', 0),
                'max_loss': dim.get('max_loss', 0),
                'profit_factor': dim.get('profit_factor', 0),
                'severe_errors': dim.get('severe_errors', 0),
                'win_rates': {w: wr.get(w, 0) for w in WINDOWS},
                'avg_returns': {w: ar.get(w, 0) for w in WINDOWS},
                'risk': {},
                'strong_win_rates': {},
                'strong_avg_returns': {},
                'strong_risk': {},
                'severe_details': dim.get('severe_details', [])[:5],
            }

            for w in WINDOWS:
                if w in risk:
                    dim_data['risk'][w] = {
                        'weighted_avg_adverse': risk[w].get('weighted_avg_adverse', 0),
                        'worst_adverse': risk[w].get('worst_adverse', 0),
                        'pct_adverse_gt3': risk[w].get('pct_adverse_gt3', 0),
                        'reward_risk_ratio': risk[w].get('reward_risk_ratio', 0),
                    }

            if strong:
                swr = strong.get('win_rates_by_window', {})
                sar = strong.get('avg_returns_by_window', {})
                srisk = strong.get('risk_by_window', {})
                dim_data['strong_total'] = strong.get('total', 0)
                for w in WINDOWS:
                    dim_data['strong_win_rates'][w] = swr.get(w, 0)
                    dim_data['strong_avg_returns'][w] = sar.get(w, 0)
                    if w in srisk:
                        dim_data['strong_risk'][w] = {
                            'weighted_avg_adverse': srisk[w].get('weighted_avg_adverse', 0),
                            'pct_adverse_gt3': srisk[w].get('pct_adverse_gt3', 0),
                        }

            result['precision'][dim_key] = dim_data

    return result


def main():
    market = load_market_data()
    bloggers = load_signals()

    all_data = {}
    for bd in bloggers:
        print(f"Processing {bd['blogger']}...")
        data = generate_blogger_data(bd, market)
        all_data[bd['blogger']] = data

    output_file = os.path.join(PROJECT_ROOT, 'data', 'report_data.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    print(f"\nSaved to {output_file}")


if __name__ == '__main__':
    main()
