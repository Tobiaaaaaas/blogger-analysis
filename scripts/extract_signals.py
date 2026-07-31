"""
Pattern-based signal extraction: pre-extract all directional signals from posts.
LLM should review and refine the output.

Usage:
  python scripts/extract_signals.py                    # all bloggers
  python scripts/extract_signals.py --blogger 顺应周期  # single blogger
"""

import json
import os
import re
import sys
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
POSTS_DIR = os.path.join(PROJECT_ROOT, "data", "posts")
SIGNALS_DIR = os.path.join(PROJECT_ROOT, "data", "signals")

# ============================================================
# Pattern definitions for signal extraction
# ============================================================

# Strong bullish patterns (confident + actionable)
STRONG_BULLISH_PATTERNS = [
    r"满仓", r"重仓", r"全仓",
    r"坚决看多", r"坚定看多", r"强烈看多", r"毫无疑问.*涨",
    r"大底已[现成确]", r"底部已[现成确]", r"绝对底部", r"历史大底",
    r"抄底", r"砸锅卖铁.*买", r"卖房.*买",
    r"大胆买入", r"果断买入", r"勇敢买入", r"毫不犹豫.*买",
    r"最佳买点", r"黄金坑", r"送钱行[情为]",
    r"不买.*后悔", r"错过.*后悔",
    r"闭眼.*买", r"无脑.*买",
    r"必然.*涨", r"一定.*涨", r"肯定.*涨", r"必定.*涨",
    r"必涨", r"暴涨", r"大涨",
    r"主升浪", r"主升行情", r"牛市.*启", r"牛市.*开",
    r"加[仓满]", r"猛[干加]",
    r"翻倍", r"翻番",
    r"大盘.*起飞", r"起飞",
]

# Moderate bullish patterns
MODERATE_BULLISH_PATTERNS = [
    r"看多", r"看涨", r"做多",
    r"偏多", r"偏乐观", r"倾向于涨", r"倾向.*多",
    r"有望.*涨", r"有望.*反弹", r"有望.*回升", r"有望.*上",
    r"大概率.*涨", r"大概率.*反弹", r"大概率.*上行",
    r"预计.*涨", r"预判.*涨", r"判断.*涨",
    r"明天.*阳", r"明日.*阳", r"下周.*阳", r"下周.*涨",
    r"目标.*涨到", r"目标位.*上",
    r"突破.*向上", r"突破.*涨",
    r"反弹", r"回升", r"回暖", r"反攻", r"修复", r"回暖",
    r"企稳", r"震荡向上", r"震荡上行", r"稳步上行",
    r"上涨", r"上[冲攻行]", r"走高", r"拉升", r"走强",
    r"站上\d+", r"站稳", r"突破\d+",
    r"低吸", r"低[位点].*买", r"跌出.*机会",
    r"建仓", r"入场", r"进场", r"买入",
    r"机会.*大于.*风险", r"性价比.*高",
    r"继续看多", r"维持看多", r"延续.*涨",
    r"震荡.*结束.*涨", r"调整.*结束",
    r"洗盘", r"震仓", r"诱空",
    r"支撑", r"守住", r"不破",
    r"收阳", r"阳线", r"中阳", r"大阳", r"长阳",
    r"开门红", r"红盘", r"红包行[情为]",
    r"积极", r"乐观",
    r"探底.*回升", r"触底.*反弹",
    r"政策底", r"市场底",
]

# Strong bearish patterns
STRONG_BEARISH_PATTERNS = [
    r"空仓", r"清仓",
    r"坚决看空", r"坚定看空", r"强烈看空",
    r"大顶已[现成确]", r"顶部已[现成确]", r"绝对顶部",
    r"赶紧.*跑", r"快跑", r"逃命", r"逃顶",
    r"果断卖出", r"毫不犹豫.*卖",
    r"必然.*跌", r"一定.*跌", r"肯定.*跌", r"必定.*跌",
    r"必跌", r"暴跌", r"大跌", r"崩盘",
    r"主跌浪", r"熊市.*[启开来]", r"股灾",
    r"减[仓半]", r"砍仓",
    r"溃败", r"踩踏", r"恐慌.*跌",
    r"毁灭.*打击", r"血洗",
]

# Moderate bearish patterns
MODERATE_BEARISH_PATTERNS = [
    r"看空", r"看跌", r"做空",
    r"偏空", r"偏悲观", r"倾向于跌", r"倾向.*空",
    r"有望.*跌", r"有望.*回调", r"有望.*回落", r"有望.*下",
    r"大概率.*跌", r"大概率.*回调", r"大概率.*下行",
    r"预计.*跌", r"预判.*跌", r"判断.*跌",
    r"明天.*阴", r"明日.*阴", r"下周.*阴", r"下周.*跌",
    r"目标.*跌到", r"目标位.*下",
    r"跌破.*向下", r"击穿.*跌",
    r"回调", r"回落", r"下[跌行挫探]", r"走弱", r"调整",
    r"见顶", r"触顶", r"冲高回落", r"冲顶",
    r"破位", r"失守", r"跌破\d+", r"击穿",
    r"卖出", r"离场", r"出场", r"出货", r"减仓",
    r"风险", r"危险", r"警惕", r"谨慎", r"注意风险",
    r"压力", r"阻力", r"承压", r"遇阻",
    r"收阴", r"阴线", r"中阴", r"大阴", r"长阴",
    r"继续看空", r"维持看空", r"延续.*跌",
    r"诱多", r"埋人", r"套[人牢]",
    r"震荡.*[向下行]", r"重心下移",
    r"防守", r"防御", r"避险", r"观望",
]

# Combined patterns for quick check
ALL_BULLISH = STRONG_BULLISH_PATTERNS + MODERATE_BULLISH_PATTERNS
ALL_BEARISH = STRONG_BEARISH_PATTERNS + MODERATE_BEARISH_PATTERNS

# Words that indicate explicit action (specific = explicit_action)
EXPLICIT_ACTION_PATTERNS = [
    r"加[仓满]", r"减[仓半]", r"满仓", r"空仓", r"清仓",
    r"买入", r"卖出", r"抄底", r"逃顶",
    r"建仓", r"补仓", r"砍仓",
    r"入场", r"离场", r"进场", r"出场",
    r"止盈", r"止损",
    r"高抛", r"低吸",
    r"扫货", r"出货",
    r"持有.*仓位", r"持股", r"持币",
    r"我.*[加减].*仓", r"[加减].*成仓",
    r"布局.*仓", r"配置.*仓",
]

# Vague/hedging patterns (these get marked directional_vague)
VAGUE_PATTERNS = [
    r"可能.*也.*可能",
    r"如果.*就.*如果.*就",
    r"两种.*可能", r"三种.*可能",
    r"涨也[好行].*跌也[好行]",
    r"不必.*太.*在意", r"不用.*太.*纠结",
    r"等待.*确认", r"有待.*观察",
    r"不好说", r"难说", r"不确定",
    r"边走边看", r"走一步看一步",
    r"且行且珍惜",
    r"既.*又.*既.*又",
    r"涨跌.*都", r"[多空].*都",
    r"短线.*震荡.*中线.*看好", r"短期.*调整.*长期.*向好",  # mixed signals
]

# Predictive intent patterns — distinguishes prediction from mere description
# A post with direction words but NO predictive marker is "descriptive" (excluded)
PREDICTIVE_PATTERNS = [
    # Explicit forward-looking markers
    r"预计", r"预判", r"预测", r"预期", r"预见",
    r"认为", r"觉得", r"判断", r"估计",
    r"目标\d", r"目标位", r"目标价", r"看到\d+",
    r"有望", r"大概率", r"很可能", r"极有可能", r"概率.*[大高]",
    r"将(?!军|领|近|于|计|被|要|会|来|信)",  # 将 as future marker
    r"(?<!委|员|机|议|协|公|大|商|同|理|学)会(?!议|员|计|所|馆|展|费|话|谈|客|长)",  # 会=will
    r"应该.*[涨跌反弹回调]", r"应当",
    r"趋于", r"倾向", r"偏向", r"势必",
    # Time-anchored predictions (inherently predictive)
    r"明天", r"明日", r"今天", r"今日",
    r"下周[一二三四五六日]?", r"本周[一二三四五六日]?", r"这周",
    r"本月", r"下月", r"年底", r"年末", r"明年", r"下半年",
    r"接下来", r"接下[来去]", r"后面[几些]",
    r"即将", r"马上", r"立刻", r"很快", r"就要",
    r"早盘", r"午盘", r"尾盘", r"盘中", r"日内", r"午后",
    # Action words (implicitly predictive — you act on future expectations)
    r"买入", r"卖出", r"加[仓满重]", r"减[仓半]", r"建仓", r"清仓", r"补仓", r"砍仓",
    r"入场", r"离场", r"进场", r"出场",
    r"抄底", r"逃顶", r"满仓", r"空仓",
    r"止盈", r"止损",
    # Directional look-ahead
    r"看[涨跌多空]", r"看好", r"看[好高]",
    r"准备.*[买进卖出加减清满]", r"打算.*[买进卖]",
    r"建议.*[买进卖加减持观望仓]",
    r"可以.*[买进卖加减仓入场离场抄底逃顶]",
    r"值得.*[买进关注]",
]

# Time horizon patterns — identifies the blogger's intended prediction timescale
TIME_HORIZON_PATTERNS = {
    "intraday": [
        r"盘中", r"日内", r"尾盘", r"午盘", r"午后",
        r"收盘前", r"下午盘", r"早盘首", r"开盘后",
        r"今天下午", r"今日午后",
    ],
    "short": [
        r"明天", r"明日", r"今天(?!下午|午后)", r"今日(?!午后|下午)",
        r"短线", r"超短", r"次日", r"隔日", r"明后天",
        r"这一两天", r"近[一两]天", r"短[期线].*[看判预]",
        r"明天.*[涨跌阳阴]", r"明日.*[涨跌阳阴]",
    ],
    "medium": [
        r"本周", r"下周", r"这周", r"近期", r"短期",
        r"接下来", r"接下[来去]", r"后面[几些]天",
        r"这波", r"本轮", r"这轮",
        r"最近", r"这几天", r"近[几些]天",
        r"周.*级别", r"周.*行情",
    ],
    "long": [
        r"牛市", r"熊市", r"下半年", r"明年", r"年底", r"年末",
        r"长期", r"中长[期线]", r"趋势", r"大周期", r"主升浪",
        r"未来几个[月周]", r"未来数月", r"季度",
        r"本轮牛市", r"本轮熊市",
        r"今年", r"全年", r"年度", r"年终",
        r"月.*级别", r"月.*行情",
        r"大底", r"大顶", r"历史.*[底顶]",
    ],
}


def match_any(content, patterns):
    """Check if content matches any of the given regex patterns."""
    for p in patterns:
        try:
            if re.search(p, content):
                return True
        except re.error:
            continue
    return False


def get_matched_patterns(content, patterns):
    """Return all patterns that match the content."""
    matched = []
    for p in patterns:
        try:
            if re.search(p, content):
                matched.append(p)
        except re.error:
            continue
    return matched


def detect_time_horizon(content):
    """Detect the intended time horizon of a directional signal.
    Returns one of: intraday, short, medium, long, unspecified.
    Priority: intraday > short > medium > long (most specific wins).
    """
    for horizon in ["intraday", "short", "medium", "long"]:
        if match_any(content, TIME_HORIZON_PATTERNS[horizon]):
            return horizon
    return "unspecified"


def is_predictive(content):
    """Check if the directional language is predictive (forward-looking)
    rather than merely descriptive (reporting what already happened).
    """
    return match_any(content, PREDICTIVE_PATTERNS)


def extract_signals_for_post(post):
    """Extract directional signal(s) from a single post.
    Returns a dict or None if no direction found.

    v7: adds 'predictive' (bool) and 'time_horizon' (str) fields.
    Descriptive posts (direction words but no predictive intent) are
    extracted but marked as excluded from scoring.
    """
    content = post.get("content", "")
    if not content:
        return None

    # Determine direction first
    is_strong_bullish = match_any(content, STRONG_BULLISH_PATTERNS)
    is_moderate_bullish = match_any(content, MODERATE_BULLISH_PATTERNS)
    is_strong_bearish = match_any(content, STRONG_BEARISH_PATTERNS)
    is_moderate_bearish = match_any(content, MODERATE_BEARISH_PATTERNS)

    # Score directions
    bullish_score = (2 if is_strong_bullish else 0) + (1 if is_moderate_bullish else 0)
    bearish_score = (2 if is_strong_bearish else 0) + (1 if is_moderate_bearish else 0)

    if bullish_score == 0 and bearish_score == 0:
        return None

    # Determine direction and strength
    if bullish_score > bearish_score:
        direction = "bullish"
        strength = "strong" if is_strong_bullish else "moderate"
    elif bearish_score > bullish_score:
        direction = "bearish"
        strength = "strong" if is_strong_bearish else "moderate"
    else:
        # Tie - check which has stronger patterns
        if is_strong_bullish and not is_strong_bearish:
            direction = "bullish"
            strength = "strong"
        elif is_strong_bearish and not is_strong_bullish:
            direction = "bearish"
            strength = "strong"
        else:
            return None  # truly ambiguous

    # Check predictive intent (v7)
    predictive = is_predictive(content)

    # Check for vague/hedging
    is_vague = match_any(content, VAGUE_PATTERNS)

    # Determine specific category
    if is_vague:
        specific = "directional_vague"
    elif not predictive:
        specific = "descriptive"  # v7: direction words but no forward-looking intent
    else:
        # Predictive + not vague → normal signal
        is_action = match_any(content, EXPLICIT_ACTION_PATTERNS)
        specific = "explicit_action" if is_action else "directional_clear"

    # Detect time horizon (v7)
    time_horizon = detect_time_horizon(content)

    # Determine index
    index = "上证指数"
    if "创业板" in content:
        index = "创业板指"
    elif "沪深300" in content or "沪深" in content:
        index = "沪深300"

    # Extract evidence
    evidence = content[:300]

    return {
        "date": post.get("publish_date", "")[:10],
        "direction": direction,
        "strength": strength,
        "specific": specific,
        "predictive": predictive,
        "time_horizon": time_horizon,
        "evidence": evidence,
        "index": index,
        "source_url": post.get("url", ""),
    }


def process_blogger(blogger_name):
    """Process one blogger's posts and extract signals."""
    posts_file = os.path.join(POSTS_DIR, f"{blogger_name}.json")
    if not os.path.exists(posts_file):
        print(f"  ❌ Posts file not found: {posts_file}")
        return None

    with open(posts_file, encoding="utf-8") as f:
        data = json.load(f)

    posts = data.get("posts", data if isinstance(data, list) else [])
    total = len(posts)
    signals = []

    for post in posts:
        if not isinstance(post, dict):
            continue
        sig = extract_signals_for_post(post)
        if sig:
            signals.append(sig)

    # Deduplicate: same date + same direction + same strength + same time_horizon → keep first
    # Different time horizons on same day are genuinely different signals (e.g., short vs long view)
    seen = set()
    deduped = []
    for s in signals:
        key = (s["date"], s["direction"], s["strength"], s.get("time_horizon", "unspecified"))
        if key not in seen:
            seen.add(key)
            deduped.append(s)
        else:
            # Keep the one with higher specific level
            for i, existing in enumerate(deduped):
                ek = (existing["date"], existing["direction"], existing["strength"], existing.get("time_horizon", "unspecified"))
                if ek == key:
                    # Prefer predictive over descriptive, explicit_action over directional_clear
                    s_rank = 0
                    if s.get("predictive", True): s_rank += 10
                    if s["specific"] == "explicit_action": s_rank += 5
                    elif s["specific"] == "directional_clear": s_rank += 3
                    e_rank = 0
                    if existing.get("predictive", True): e_rank += 10
                    if existing["specific"] == "explicit_action": e_rank += 5
                    elif existing["specific"] == "directional_clear": e_rank += 3
                    if s_rank > e_rank:
                        deduped[i] = s
                    break

    deduped.sort(key=lambda x: x["date"])

    # Stats
    bullish = [s for s in deduped if s["direction"] == "bullish"]
    bearish = [s for s in deduped if s["direction"] == "bearish"]
    strong = [s for s in deduped if s["strength"] == "strong"]
    explicit = [s for s in deduped if s["specific"] == "explicit_action"]
    vague = [s for s in deduped if s["specific"] == "directional_vague"]
    descriptive = [s for s in deduped if s["specific"] == "descriptive"]
    predictive_signals = [s for s in deduped if s.get("predictive", True)]
    # Time horizon distribution
    horizons = {"intraday": 0, "short": 0, "medium": 0, "long": 0, "unspecified": 0}
    for s in deduped:
        h = s.get("time_horizon", "unspecified")
        if h in horizons:
            horizons[h] += 1

    valid_count = len(deduped) - len(vague) - len(descriptive)

    print(f"\n  {blogger_name}: {total} posts → {len(deduped)} signals (deduped from {len(signals)} raw)")
    print(f"    看多: {len(bullish)} | 看空: {len(bearish)}")
    print(f"    strong: {len(strong)} | moderate: {len(deduped) - len(strong)}")
    print(f"    explicit_action: {len(explicit)} | directional_clear: {len(deduped) - len(explicit) - len(vague) - len(descriptive)} | directional_vague: {len(vague)} | descriptive: {len(descriptive)}")
    print(f"    有效信号(计入评分): {valid_count} | 排除: vague={len(vague)} descriptive={len(descriptive)}")
    print(f"    时间维度: 日内{horizons['intraday']} 短线{horizons['short']} 中线{horizons['medium']} 长线{horizons['long']} 未指定{horizons['unspecified']}")

    # Write signals
    output = {
        "blogger": blogger_name,
        "signals": deduped,
        "extraction_method": "pattern_based_v2",
        "v7_features": ["predictive_detection", "time_horizon_tagging", "descriptive_filtering"],
        "needs_review": True,
    }

    os.makedirs(SIGNALS_DIR, exist_ok=True)
    output_file = os.path.join(SIGNALS_DIR, f"{blogger_name}.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"    → {output_file}")
    return output


def main():
    parser = argparse.ArgumentParser(description="Pattern-based signal extraction")
    parser.add_argument("--blogger", help="Process single blogger only")
    args = parser.parse_args()

    if args.blogger:
        process_blogger(args.blogger)
    else:
        for fname in sorted(os.listdir(POSTS_DIR)):
            if not fname.endswith(".json"):
                continue
            name = fname.replace(".json", "")
            process_blogger(name)


if __name__ == "__main__":
    main()
