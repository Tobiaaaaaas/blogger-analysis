#!/usr/bin/env python3
"""
Re-label time_horizon for every signal in 稀豹.json
Rules:
  short  — T~T+2 (within 3 days, including intraday, today, tomorrow)
  medium — T+3 to 1 month (next week, this week, mid-term)
  long   — 1 month+ (bull/bear market, next year, long-term trend)
  unspecified — no clear time horizon

PRIORITY: short > medium > long > unspecified
More specific = higher priority. But genuine long-term predictions must not
be overridden by descriptive/hypothetical short mentions.
"""

import json
import re
import sys


# ---------------------------------------------------------------------------
# Pre-processing: strip known context / descriptive uses of time words
# ---------------------------------------------------------------------------

def strip_context_phrases(text: str) -> str:
    """Remove well-known context phrases that use time words non-predictively."""
    # "明天股市就开盘了" = factual statement, not prediction target
    text = text.replace('明天股市就开盘了！', '')
    text = text.replace('明天股市就开盘了，', '')
    # "不用等明天" + optional reason → the real prediction follows after "因为"
    text = re.sub(r'不用等明天[，,]?\s*因为\s*', '', text)
    # "今天，重点和大家谈谈" = organizational, not prediction
    text = re.sub(r'今天[，,]\s*重点和大家谈[谈论]', '', text)
    # "别把今日上涨当反转" → 今日 is negated/referenced, not predicted
    text = text.replace('别把今日上涨当反转，', '')
    # "中午收盘！" / "收盘！" as standalone post labels → not prediction time indicators
    text = re.sub(r'^中午收盘[！!]\s*', '', text)
    text = re.sub(r'^收盘[！!]\s*', '', text)
    return text


def is_observation_not_prediction(text: str) -> bool:
    """Check if text is purely describing what happened, not making a prediction."""
    obs_patterns = [
        # Headline patterns describing today's market
        r'中国股市今日(?:下跌|上涨|微[涨跌]|[涨跌])',
        r'中国股市全面(?:上涨|下跌)',
        r'A股今日(?:下跌|上涨|[涨跌])',
        r'A股三连阳',
        r'^今日(?:下跌|上涨|[涨跌])',              # 今日涨/今日跌/今日下跌/今日上涨
        r'^今日(?:大幅|小幅|放量|缩量|微|暴|全[面线])(?:下跌|上涨|[涨跌])',  # 今日放量下跌 etc
        r'^今日[^。！？]{0,4}(?:下跌|上涨|微跌|微涨|收[跌涨])',  # factual close/variation
        # Observational patterns
        r'展现出了极强的韧性，这本身就是一种强势信号',
        r'^但今日A股却展现出了极强的韧性',
        r'^今天已对.+?完成了',
        r'^今天又没',                           # "今天又没跌破..." = factual
        r'^今天又[^，。！？]{0,6}[涨跌]',        # "今天又涨了" = factual
        # "下午的行情" = descriptive noun phrase, not prediction target
        r'下午的行情',
        r'上午的行情',
    ]
    for pat in obs_patterns:
        if re.search(pat, text):
            return True
    return False


# ---------------------------------------------------------------------------
# Short-term indicator detection
# ---------------------------------------------------------------------------

def has_short_indicator(text: str) -> bool:
    """Check if text contains a SHORT-term time indicator as prediction target."""

    # Skip purely observational texts (not making a prediction about today/tomorrow)
    if is_observation_not_prediction(text):
        return False

    # Strip clean context phrases
    cleaned = strip_context_phrases(text)

    # Remove "X个行情日内" or "X个交易日内" → these mean "within X trading days"
    cleaned = re.sub(r'\d+个(行情|交易)日内', '', cleaned)
    # Remove "半年报" to avoid confusing with 半年
    cleaned = re.sub(r'半年报', '', cleaned)
    # Remove past-tense date patterns that would falsely trigger 上午/今日
    # "于上周五上午" / "5月23日上午" / "在X月X日上午" → past context, not prediction
    cleaned = re.sub(r'(?:于|在)?(?:上周[一二三四五六日]|\d+月\d+日)\s*上午', '', cleaned)
    cleaned = re.sub(r'(?:于|在)?(?:上周[一二三四五六日]|\d+月\d+日)\s*下午', '', cleaned)

    short_patterns = [
        # ===== Tomorrow / day after tomorrow =====
        r'明天', r'明日', r'明儿',
        r'后天', r'后日',

        # ===== Today as prediction target =====
        # "今天还有..." → prediction about today
        r'今天还有',
        r'今天[就会能或仍依继]',
        r'今天就[\s，]*[^的]',         # 今天就... (not 今天的 = descriptive)
        r'今天[\s，]*明确',
        r'今天[\s，]*方向',
        r'今[日天][^。！？]*(收|看|会|将|能)[^。！？]*[涨跌阴阳]',
        r'今[日天]下午',
        r'今明两[天日]',
        r'明后两[天日]',

        # "今天" used AS prediction target (not descriptive "今天的XX")
        r'对[今今]日',
        r'今日[\s，]*(会|将|能|是)',
        r'今日[^。！？]*[涨跌收]',

        # ===== Same day - afternoon/morning (allow , in middle) =====
        r'下午[^。！？]*(会|将|能|或|多半|大概率|最终|[涨跌收看走])',
        r'下午[^。！？]*收',
        r'下午[^。！？]*走V',
        r'下午[^。！？]*翻[红绿]',
        r'下午[^。！？]*[中大小][阴阳]线',
        r'下午[^。！？]*阳包阴',
        r'下午[^。！？]*[涨跌]',
        r'下午[^。！？]*[回翻杀]',
        r'午后[^。！？]*(会|将|能|或|多半|大概率|最终|[涨跌收看走])',
        r'上午[^。！？]*(?:会|将|能|或|多半|大概率|最终)[^。！？]{0,6}[涨跌收]',
        r'上午[^。！？]*[会能][以有]',
        r'上午行情就是',  # analysis/prediction about current morning
        r'早盘',
        r'中午收盘',
        r'今晚', r'晚间',

        # ===== Intraday =====
        r'(?<!行情)(?<!交易)日内',
        r'盘中[^。！？]*[会能将]',

        # ===== Short-term keywords =====
        r'近日',
        r'全天[^。！？]*[涨跌收]',
        r'全天[^。！？]*最终',
        r'当日',
        r'节后第一天', r'节后首日',

        # ===== Imminent =====
        r'马上[^。！？]*(涨|跌|杀|崩|反弹|反转)',
        r'立刻[^。！？]*(涨|跌|杀|崩|反弹|反转)',
        r'即将[^。！？]*(开启|杀跌|来临|反弹|反转|崩跌|崩杀|暴涨|暴跌|开场)',
        r'立帖为证[^！。]*[明今]',
        r'等下',

        # ===== 1-3 trading days =====
        r'未来[1-3]个行情日',
        r'[1-3][个]?行情日',
        r'至多[1-3][个]?行情日',
        r'[1-3][到至][23]个行情日',
        r'至多[34]到[45]个行情日',

        # ===== 今天/今日 general (put at end as catch-all) =====
        r'今天[\s，]*[我会看]',
        r'今日[\s，]*[我会看]',
        r'今[日天][\s，]*方向',
        r'今天[\s，]*就',
    ]

    for pat in short_patterns:
        if re.search(pat, cleaned):
            return True

    # "短线" as prediction target (not "对短线" which describes impact type)
    # Must appear at start of evidence or after sentence break, and be followed
    # by prediction language, not "对短线利空/利多" descriptors
    if re.search(r'(?:^|[。！])[^。！？]*短线[^长][^。！？]*[涨跌收破看空多]', cleaned):
        if not re.search(r'对短线[^。！？]*(?:利[空多好]|不利)', cleaned):
            return True

    # Check if 今天/今日 is prediction target by looking at context more broadly
    # "今天" or "今日" appearing with prediction-like verbs nearby
    # BUT: "从今天到X月底" / "从今天到X月" / "从今天起" = the starting point of
    # a longer horizon → NOT a short prediction
    if re.search(r'今天|今日', cleaned):
        # Exclude: "从今天到" pattern (= from today until, medium/long horizon)
        if re.search(r'从今天到', cleaned) or re.search(r'从今日到', cleaned):
            pass  # Skip - this is a medium/long horizon
        # If there's a prediction verb after 今天 (within 20 chars)
        elif re.search(r'(?:今天|今日)[^。！？]{0,20}(?:看空|看涨|看跌|会[涨跌]|收[红绿阴阳]|方向|预判|行情)', cleaned):
            # But not if it's purely descriptive like "今天的XX"
            if not re.search(r'今天的[涨跌]', cleaned):
                return True
        # Also match "看空今天" / "看涨今天" pattern (prediction verb BEFORE 今天)
        elif re.search(r'(?:看空|看涨|看跌|明确看)[^。！？]{0,10}(?:今天|今日)', cleaned):
            return True
        # "今天！" or "今日！" — 今天 at end of clause
        elif re.search(r'[看空看涨看跌说][^。！？]{0,8}(?:今天|今日)[！。]', cleaned):
            return True

    return False


# ---------------------------------------------------------------------------
# Medium-term indicator detection
# ---------------------------------------------------------------------------

def has_medium_indicator(text: str) -> bool:
    """Check if text contains a MEDIUM-term time indicator."""
    cleaned = re.sub(r'半年报', '', text)

    medium_patterns = [
        r'下周', r'下星期', r'下周一', r'下周二', r'下周三',
        r'下周四', r'下周五', r'下周三之前',

        r'本周[^。！？]*[会大概率]',
        r'本周[^。！？]*[涨跌收看]',
        r'本周行情', r'本周突破', r'本周内',
        r'本周上半周', r'本周大概率',
        r'本周.*周[阴阳]线',
        r'本周.*目标', r'本周.*方向',
        r'本周会', r'本周方向',
        r'这周', r'这个星期',

        r'中线',
        r'中期趋势',
        r'中期大顶',
        r'中期调整',
        r'短中期',
        r'中短期',
        r'短中线',

        r'近期', r'最近',
        r'月中', r'月底', r'月末',
        r'[46]月底之?前',
        r'半个月',
        r'数日',
        r'接下来几天',
        r'未来[4-9]个行情日',
        r'[4-9][个]?行情日',

        r'节后[^第]',
        r'下半周', r'上半周',
        r'明后两天.*下半周',

        r'下一根.*周[阴阳]线',
        r'[本下]周.*(反弹|反转|回落|冲高|震荡|调整|杀跌|崩跌|大涨|暴跌|上攻|向上|向下|看涨|看空|看跌|收|止跌|上涨|下跌)',
        r'周线阳包阴', r'周线十字星',
        r'周中阳线', r'周大阳线', r'周小[阴阳]线',
        r'周阳线', r'周阴线',

        r'[四五六七八九十]月中旬',
        r'[四五六七八九十]月底',
        r'10月中旬',
        r'6月底之前',
        r'六月[，,\s]',
        r'[四五六七八九]月[，,\s]',

        r'从今天到[六七八九十\d]月底',
        r'从今[日天]到.*月底',
    ]

    for pat in medium_patterns:
        if re.search(pat, cleaned):
            return True

    return False


# ---------------------------------------------------------------------------
# Long-term indicator detection
# ---------------------------------------------------------------------------

def has_long_indicator(text: str) -> bool:
    """Check if text contains a LONG-term time indicator."""
    cleaned = re.sub(r'半年报', '', text)

    long_patterns = [
        # Market regime (inherently long-term)
        r'牛市', r'熊市', r'牛屎',
        r'慢牛', r'长牛', r'牛头行情',
        r'史诗级.*慢牛',
        r'长期慢牛',
        r'全面牛市',
        r'慢牛起点',

        # Very long time spans
        r'三十年一遇', r'十年一遇',
        r'短则十年', r'长则四十年',
        r'三十年', r'四十年',
        r'十年封印',
        r'十年[之以]',
        r'世纪奇观',

        # Explicit long-term
        r'长线',
        r'长期趋势',
        r'长期上涨',
        r'长期向上',
        r'长期慢牛的',

        # Major direction
        r'大方向[^。！？]*[涨牛上]',
        r'(长期|慢牛|长牛).*大方向',
        r'大方向[^。！？]*不变',

        # Next year / half year
        r'明年', r'来年',
        r'下半年', r'上半年',

        # Future years/months
        r'未来几年', r'未来数年',
        r'未来\d+年', r'未来\d+月',
        r'两年内', r'三年内', r'几年内',

        # This year / within year
        r'今年[内底].*破',
        r'年内[必破]',
        r'年内破',
        r'年底[前必]',
        r'今年\d+',
        r'今年[七八九十].*主升浪',
        r'今年必涨',
        r'今年七月',

        # Monthly candles (full month)
        r'月阴线', r'月阳线',
        r'下一根月线',
        r'月线七连阳',
        r'下个月',
        r'[七八九十]月会收',

        # Multi-year references
        r'十年',
    ]

    for pat in long_patterns:
        if re.search(pat, cleaned):
            return True

    # "十年" in "十年高点" is descriptive (a 10-year high), not a prediction horizon
    # But standalone "十年" is long... this is handled by "十年封印" etc being more specific

    return False


# ---------------------------------------------------------------------------
# Conditional / concessive detection
# ---------------------------------------------------------------------------

def is_in_conditional_clause(text: str, word: str) -> bool:
    """Check if a time-indicator word appears in a conditional/hypothetical clause.
    This is checked PER SENTENCE now, not per entire evidence."""
    conditions = [
        rf'一旦[^。！？]*[，,]?\s*比如\s*{word}',
        rf'一旦[^。！？]*{word}',
        rf'如果\s*{word}',
        rf'假设\s*{word}',
    ]
    for pat in conditions:
        if re.search(pat, text):
            return True
    return False


def is_first_sentence_observational(sent: str) -> bool:
    """Check if first sentence is purely observational (not a prediction)."""
    obs_patterns = [
        r'^今日高点.{0,20}就是.{0,10}(顶|大顶)',
        r'^今日.{0,10}就是.{0,10}(顶|底)',
        r'^\d+\.?\d*就是顶',
        r'^中国股市今日[上涨跌].+?！$',
        r'^A股今日[上涨跌].+?！$',
        r'^A股三连阳.*',
        r'^中国股市全面[上涨].+?！$',
        r'^但今日A股却展现出了极强的韧性',
        r'^今天已对.+?完成了',
    ]
    for pat in obs_patterns:
        if re.search(pat, sent):
            return True
    return False


def is_concessive_long(text: str) -> bool:
    """Check if text has concessive pattern where long-term wins over short-term."""
    patterns = [
        r'不论短线.*(长期|大方向|慢牛|长牛|牛市)',
        r'不管短线.*(长期|大方向|慢牛|长牛|牛市)',
        r'虽然短线.*(长期|大方向|慢牛|长牛|牛市)',
        r'尽管短线.*(长期|大方向|慢牛|长牛|牛市)',
        r'但这种短线.*(长期|大方向|慢牛|长牛)',
        r'而这种短线.*(长期|大方向)',
        r'短[线期].*撼动不了.*(长期|大方向)',
        r'短线[^。！？]*丝[毫撼].*(长期|大方向)',
        r'但.*不论.*短线.*(长期|大方向)',
        r'虽[然说].*短线.*但.*(长期|大方向)',
        r'长线.*[可无]视.*短',
        r'不论.*短线.*(长期|大方向)',
        r'但短线.*不改.*(长期|大方向)',
    ]
    for pat in patterns:
        if re.search(pat, text):
            return True
    return False


def is_primary_prediction_long(text: str) -> bool:
    """Check if the primary prediction target is clearly long-term,
    even though short-term words appear in descriptive/observational roles."""
    # "今天的跌大于涨，不是坏事...坚定长期慢牛" → long
    if re.search(r'今天的[涨跌].*长期慢牛', text):
        return True
    # "今天已对...完成了...长期慢牛" → long
    if re.search(r'今天已对.*完成了.*长期', text):
        return True
    # "...今天的...不是坏事...长期慢牛" → long
    if re.search(r'今天的[涨跌].*(?:不是|并非).*(?:长期|慢牛|牛市|大方向)', text):
        return True
    # "今天的跌...在坚定长期慢牛的大格局下" → long
    if re.search(r'今天的.*在坚定.*(?:长期|慢牛|牛|大方向)', text):
        return True
    return False


# ---------------------------------------------------------------------------
# Main classification
# ---------------------------------------------------------------------------

def classify_time_horizon(evidence: str) -> str:
    """
    Determine time_horizon based on evidence text.
    Priority: short > medium > long > unspecified
    But with careful handling of:
      - Conditional/hypothetical time references
      - Concessive clauses (不论短线...长期...)
      - Descriptive vs predictive use of time words
    """
    e = evidence

    # ---- 0. Check for concessive patterns where LONG wins ----
    if is_concessive_long(e):
        return "long"

    # ---- 0b. Check if primary prediction is long despite short descriptive words ----
    if is_primary_prediction_long(e):
        return "long"

    # ---- 1. Clean context phrases ----
    cleaned = strip_context_phrases(e)

    # ---- 2. Split into sentences ----
    sentences = re.split(r'[。！？!]+', cleaned)
    sentences = [s.strip() for s in sentences if s.strip()]

    # ---- 3. Detect per-sentence indicators ----
    any_short = False
    any_medium = False
    any_long = False

    for sent in sentences:
        # Check if 明天/今日 in THIS sentence is in a conditional clause
        tmrw_cond = is_in_conditional_clause(sent, '明天')
        today_cond = is_in_conditional_clause(sent, '今日')

        short_in_sent = has_short_indicator(sent)
        # If the short indicator is only from 明天 in a conditional clause, skip it
        if short_in_sent and tmrw_cond and '明天' in sent:
            # Check if there's another short indicator besides 明天
            tmp = re.sub(r'明天', '', sent)
            if has_short_indicator(tmp):
                short_in_sent = True
            else:
                short_in_sent = False

        if short_in_sent:
            any_short = True
        if has_medium_indicator(sent):
            any_medium = True
        if has_long_indicator(sent):
            any_long = True

    # ---- 4. Check first sentence for primary prediction target ----
    first_sentence = sentences[0] if sentences else e

    # If first sentence is purely observational, look at ALL sentences
    first_is_obs = is_first_sentence_observational(first_sentence)

    first_is_short = has_short_indicator(first_sentence) if not first_is_obs else False
    first_is_medium = has_medium_indicator(first_sentence)
    first_is_long = has_long_indicator(first_sentence)

    # If first sentence has conditional 明天, check if it still has other short indicators
    if '明天' in first_sentence and is_in_conditional_clause(first_sentence, '明天'):
        tmp_s = re.sub(r'明天', '', first_sentence)
        first_is_short = has_short_indicator(tmp_s)

    # Override: strong long-term keywords in first sentence should win over medium
    # e.g. "年底必破4000...短中线..." → long (年底 is the main prediction)
    strong_long_keywords = [r'年底[必前]', r'明年', r'下半年', r'长线', r'长期', r'未来\d+年', r'年[内底]']
    has_strong_long = any(re.search(kw, first_sentence) for kw in strong_long_keywords)
    if has_strong_long and first_is_long and first_is_medium:
        # Strong long-term keyword overrides co-occurring medium indicator
        first_is_medium = False

    # Priority: first sentence's indicator wins (it's usually the main prediction)
    # But if first sentence is observational, fall through to any_* checks
    if not first_is_obs:
        if first_is_short:
            return "short"
        if first_is_medium:
            return "medium"
        if first_is_long:
            return "long"

    # Otherwise, use presence-based priority
    if any_short:
        return "short"
    if any_medium:
        return "medium"
    if any_long:
        return "long"

    return "unspecified"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    filepath = r"D:\claude_code_ana\blogger-analysis\data\signals\稀豹.json"

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    signals = data["signals"]
    total = len(signals)
    changes = 0

    print(f"Processing {total} signals...")
    changed_signals = []

    for i, sig in enumerate(signals):
        old_horizon = sig["time_horizon"]
        evidence = sig["evidence"]
        new_horizon = classify_time_horizon(evidence)

        if old_horizon != new_horizon:
            changes += 1
            changed_signals.append((i, old_horizon, new_horizon, evidence))
            sig["time_horizon"] = new_horizon

    # Print all changes
    for idx, old_h, new_h, ev in changed_signals:
        print(f"  [{idx}] {old_h:11s} -> {new_h:11s} | {ev[:110]}")

    print(f"\nTotal changes: {changes}/{total}")

    # Recalculate counts
    def count_dir(direction):
        dir_signals = [s for s in signals if s["direction"] == direction]
        return {
            "strong": sum(1 for s in dir_signals if s["strength"] == "strong"),
            "moderate": sum(1 for s in dir_signals if s["strength"] == "moderate"),
            "short": sum(1 for s in dir_signals if s["time_horizon"] == "short"),
            "medium": sum(1 for s in dir_signals if s["time_horizon"] == "medium"),
            "long": sum(1 for s in dir_signals if s["time_horizon"] == "long"),
            "unspecified": sum(1 for s in dir_signals if s["time_horizon"] == "unspecified"),
        }

    old_bullish = data["scored_bullish"].copy()
    old_bearish = data["scored_bearish"].copy()

    data["scored_bullish"] = count_dir("bullish")
    data["scored_bearish"] = count_dir("bearish")

    print("\n=== RECALCULATED COUNTS ===")
    print(f"scored_bullish: OLD {old_bullish}")
    print(f"               NEW {data['scored_bullish']}")
    print(f"scored_bearish: OLD {old_bearish}")
    print(f"               NEW {data['scored_bearish']}")

    # Verify
    bull_signals = [s for s in signals if s['direction'] == 'bullish']
    bear_signals = [s for s in signals if s['direction'] == 'bearish']
    nb = data['scored_bullish']
    nr = data['scored_bearish']

    ok = True
    checks = [
        ("bullish short+medium+long+unspecified",
         nb['short'] + nb['medium'] + nb['long'] + nb['unspecified'], len(bull_signals)),
        ("bearish short+medium+long+unspecified",
         nr['short'] + nr['medium'] + nr['long'] + nr['unspecified'], len(bear_signals)),
        ("bullish strong+moderate",
         nb['strong'] + nb['moderate'], len(bull_signals)),
        ("bearish strong+moderate",
         nr['strong'] + nr['moderate'], len(bear_signals)),
    ]
    for label, actual, expected in checks:
        if actual != expected:
            print(f"ERROR: {label} = {actual}, expected {expected}")
            ok = False

    if ok:
        print("All counts VERIFIED correctly.")

    print(f"\n=== DISTRIBUTION ({total} signals) ===")
    all_horizons = [s["time_horizon"] for s in signals]
    for h in ["short", "medium", "long", "unspecified"]:
        cnt = all_horizons.count(h)
        print(f"  {h:12s}: {cnt:3d} ({100*cnt/total:.1f}%)")

    # Write back
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\nWritten updated JSON to {filepath}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
