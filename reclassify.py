#!/usr/bin/env python3
"""
Re-label time_horizon from old 5 categories to new 7 categories
for 3 bloggers' signal files.
"""

import json
import re
import os

# Files to process
FILES = [
    "d:/claude_code_ana/blogger-analysis/data/signals/TL阳光.json",
    "d:/claude_code_ana/blogger-analysis/data/signals/云帆观市.json",
    "d:/claude_code_ana/blogger-analysis/data/signals/衡山佛曰论股.json",
]

def classify_time_horizon(evidence, old_horizon):
    """
    Classify time_horizon based on evidence text.
    Checks from LONGEST timeframe to SHORTEST.
    When multiple timeframes are present, uses the longest effective one.
    Falls back to mapping from old category if no keywords match.
    """
    text = evidence

    # ============================================================
    # LONG — 更远的预测（"下半年""牛市""趋势""明年"）
    # ============================================================
    long_patterns = [
        # Explicit far-future dates
        r'下半年', r'上半年',
        r'明年', r'后年', r'来年', r'全年', r'年度',
        r'年底', r'年末', r'年[初中]',
        r'跨年', r'年[内终]',

        # Market cycle / regime
        r'(?<!短线)(?<!短期)(?<!短)牛市',
        r'(?<!短线)(?<!短期)(?<!短)熊市',
        r'慢牛', r'快牛', r'长牛', r'结构牛',
        r'大牛市', r'小牛市',

        # Long-term qualifiers
        r'中长期', r'长线', r'长期', r'长远',
        r'中长线', r'长周期', r'大周期',
        r'年线级别', r'年限级别',

        # Long-term trend (not qualified by short/medium-term)
        r'(?<!短线)(?<!短期)(?<!短)(?<!中线)大趋势',
        r'大势所趋', r'大势',

        # Seasonal / quarterly
        r'春季行情', r'夏季行情', r'秋季行情', r'冬季行情',
        r'跨年行情', r'年后行情',
        r'季度行情', r'下半年行情', r'上半年行情',
        r'本季度', r'下季度', r'三季度', r'四季度',
        r'年末行情', r'年底行情',

        # Spring Festival / long holidays implying extended period
        r'春节后.*行情', r'春节后.*反弹', r'春节后.*上涨', r'春节后.*走势',
        r'节后.*行情', r'节后.*走势',
        r'年后.*行情', r'年后.*反弹',

        # Extended future
        r'未来几个月', r'未来半年', r'未来一[两二]年',
        r'几个月后', r'半年后', r'一年后',
        r'未来几[个]?月',

        # Trend-related (standalone, not qualified by short/medium-term)
        r'(?<!短线)(?<!短期)(?<!短)(?<!中线)趋势.*向[上好多]',
        r'(?<!短线)(?<!短期)(?<!短)(?<!中线)趋势.*反[弹转]',
        r'(?<!短线)(?<!短期)(?<!短)(?<!中线)趋势.*上涨',
        r'(?<!短线)(?<!短期)(?<!短)(?<!中线)趋势.*走[强好高]',
        r'中长期趋势',

        # Policy / macro
        r'政策底', r'市场底', r'估值底',
        r'宏观.*向好', r'基本面.*改善',
    ]
    for pat in long_patterns:
        if re.search(pat, text):
            return 'long'

    # ============================================================
    # MONTHLY — 1月后预测（"下个月""未来一个月"）
    # ============================================================
    monthly_patterns = [
        r'下个月', r'下月', r'下[个]?月份', r'次月',
        r'未来一个月', r'未来一[个]?月', r'一个月后', r'一月后',
        r'月底', r'月末', r'月[初尾]',  # 月初/月尾 (NOT 月中 which is biweekly)
        r'本月[底末]', r'本月下旬', r'本月上旬',
        r'月线级别', r'月线', r'月级别',
        r'一个月内', r'一月内',
        r'未来.*[个]?月(?!.*[天周日])',  # 未来一月, 未来一个月 (but not if about days/weeks)
        r'月度行情', r'月度',
    ]
    for pat in monthly_patterns:
        if re.search(pat, text):
            return 'monthly'

    # ============================================================
    # BIWEEKLY — 2周后预测（"月中""未来两周"）
    # ============================================================
    biweekly_patterns = [
        r'未来两[个]?周', r'两周后', r'两周内', r'两周左右',
        r'未来.*两周',
        r'两[个]?星期',
        r'上半月', r'下半月',  # half-month, about 2 weeks
        r'半月',
        r'月中',  # mid-month, roughly 2 weeks
        r'中旬', r'上旬', r'下旬',  # early/mid/late part of month, about 10-15 days
        r'十来天', r'十几天', r'十天左右',
        r'近两周', r'近两[个]?星期',
    ]
    for pat in biweekly_patterns:
        if re.search(pat, text):
            return 'biweekly'

    # ============================================================
    # WEEKLY — 1周后预测（"下周""这周后半段"）
    # ============================================================
    weekly_patterns = [
        r'下周', r'本周', r'这周',
        r'一周后', r'一周内', r'一[个]?星期',
        r'周线级别', r'周级别',
        r'本周[后下]半段', r'这周[后下]半段',
        r'本周.*行情', r'下周.*行情',
        r'周.*走势',
        r'下周[一二三四五日初末]', r'下周[一二三四五]',
        r'下个星期',
        r'周末', r'周[末初]',
        r'这[个]?星期',
        r'本周后半', r'下周前半',
        # Medium-term indicators (约1周~1月, map to weekly as closest)
        r'中线趋势', r'中线行情', r'中线走势',
        r'(?<!短)中线',  # standalone 中线 (medium-term), not 短线 (short-term)
    ]
    for pat in weekly_patterns:
        if re.search(pat, text):
            return 'weekly'

    # ============================================================
    # SHORT — 3天内预测（"明天涨""周初反弹"）
    # ============================================================
    short_patterns = [
        # Explicit days
        r'明天', r'明日', r'明后天', r'明后两天',
        r'后天', r'大后天',
        r'次日', r'隔日',

        # Day-of-week when likely referring to current/next few days
        r'周初',  # beginning of week = typically Mon-Tue

        # Short-term phrases
        r'近日', r'这两天',
        r'短线', r'短周期',
        r'单日',
        r'三天内', r'3天内',
        r'未来.*[两三]天', r'未来.*几[天日]',
        r'近[两三]天', r'近几[天日]',
        r'短期', r'短期内',
        r'近[期日]',

        # Before holiday (typically short term)
        r'节前', r'春节前', r'长假前',
        r'年前',  # before new year

        # Imminent
        r'即将', r'马上', r'很快',

        # "周X" references — likely current week, so within days
        r'周[一二三四五六日](?![级别线后内初末])',

        # Short-term trend (trend qualified by short-term)
        r'短线趋势', r'短期趋势',
        r'短周期.*趋势',
    ]
    for pat in short_patterns:
        if re.search(pat, text):
            # But don't return 'short' if we already matched a weekly/monthly/long keyword
            # that also appears (we already checked longer ones above, so this is fine)
            return 'short'

    # ============================================================
    # INTRADAY — 日内预测（仅针对当日 T）
    # ============================================================
    intraday_patterns = [
        r'今天', r'今日', r'日内',
        r'下午', r'午后', r'尾盘', r'早盘', r'午盘', r'盘中',
        r'今[早晚]', r'今夜', r'今晚',
        r'今日.*收盘', r'今天.*收盘',
        r'午[前后]',
        r'刚刚', r'刚',
        r'今[日天]', r'当日', r'当天',
        r'盘中.*走势',
        r'午间',
        r'正在',  # happening right now
    ]
    for pat in intraday_patterns:
        if re.search(pat, text):
            return 'intraday'

    # ============================================================
    # FALLBACK: Map from old category to new
    # ============================================================
    fallback_map = {
        'long': 'long',
        'medium': 'weekly',    # medium (1 week+) defaults to weekly
        'short': 'short',
        'intraday': 'intraday',
        'unspecified': 'unspecified',
    }
    return fallback_map.get(old_horizon, 'unspecified')


def update_counts(signals):
    """Recalculate scored_bullish and scored_bearish counts."""
    bullish = {
        "strong": 0, "moderate": 0,
        "intraday": 0, "short": 0, "weekly": 0,
        "biweekly": 0, "monthly": 0, "long": 0, "unspecified": 0
    }
    bearish = {
        "strong": 0, "moderate": 0,
        "intraday": 0, "short": 0, "weekly": 0,
        "biweekly": 0, "monthly": 0, "long": 0, "unspecified": 0
    }

    for sig in signals:
        target = bullish if sig['direction'] == 'bullish' else bearish
        target[sig['strength']] += 1
        target[sig['time_horizon']] += 1

    return bullish, bearish


def process_file(filepath):
    print(f"\n{'='*60}")
    print(f"Processing: {filepath}")
    print(f"{'='*60}")

    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    signals = data['signals']
    total = len(signals)
    print(f"Total signals: {total}")

    # Track changes
    changes = {}
    for old_cat in ['intraday', 'short', 'medium', 'long', 'unspecified']:
        changes[old_cat] = {}

    processed = 0
    for sig in signals:
        old_h = sig['time_horizon']
        evidence = sig.get('evidence', '')
        new_h = classify_time_horizon(evidence, old_h)

        if new_h != old_h:
            if new_h not in changes[old_h]:
                changes[old_h][new_h] = 0
            changes[old_h][new_h] += 1

        sig['time_horizon'] = new_h
        processed += 1

    # Print summary of changes
    print("\n--- Reclassification Summary ---")
    total_changed = 0
    for old_cat in ['intraday', 'short', 'medium', 'long', 'unspecified']:
        if changes[old_cat]:
            for new_cat, count in sorted(changes[old_cat].items(), key=lambda x: -x[1]):
                print(f"  {old_cat} → {new_cat}: {count}")
                total_changed += count
    print(f"Total changes: {total_changed}/{total}")

    # Update counts
    bullish, bearish = update_counts(signals)
    data['scored_bullish'] = bullish
    data['scored_bearish'] = bearish

    print(f"\n--- New Counts ---")
    print(f"Bullish: strong={bullish['strong']}, moderate={bullish['moderate']}, "
          f"intraday={bullish['intraday']}, short={bullish['short']}, "
          f"weekly={bullish['weekly']}, biweekly={bullish['biweekly']}, "
          f"monthly={bullish['monthly']}, long={bullish['long']}, unspecified={bullish['unspecified']}")
    print(f"Bearish: strong={bearish['strong']}, moderate={bearish['moderate']}, "
          f"intraday={bearish['intraday']}, short={bearish['short']}, "
          f"weekly={bearish['weekly']}, biweekly={bearish['biweekly']}, "
          f"monthly={bearish['monthly']}, long={bearish['long']}, unspecified={bearish['unspecified']}")

    # Write back
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nWritten to: {filepath}")
    return changes, total_changed


def main():
    for filepath in FILES:
        if not os.path.exists(filepath):
            print(f"ERROR: File not found: {filepath}")
            continue
        process_file(filepath)

    print("\n\nAll files processed!")


if __name__ == '__main__':
    main()
