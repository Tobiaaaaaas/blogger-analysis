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


def extract_signals_for_post(post):
    """Extract directional signal(s) from a single post.
    Returns a dict or None if no direction found.
    """
    content = post.get("content", "")
    if not content:
        return None

    # Check for vague/hedging first
    if match_any(content, VAGUE_PATTERNS):
        # Still might have a directional lean
        is_bullish = match_any(content, ALL_BULLISH)
        is_bearish = match_any(content, ALL_BEARISH)
        if not is_bullish and not is_bearish:
            return None
        # If vague but has direction, mark as directional_vague
        direction = "bullish" if is_bullish and not is_bearish else ("bearish" if is_bearish and not is_bullish else "neutral")
        if direction == "neutral":
            return None
        return {
            "date": post.get("publish_date", "")[:10],
            "direction": direction,
            "strength": "moderate",
            "specific": "directional_vague",
            "evidence": content[:200],
            "index": "上证指数",
            "source_url": post.get("url", ""),
        }

    # Determine direction
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

    # Determine specific
    is_action = match_any(content, EXPLICIT_ACTION_PATTERNS)
    specific = "explicit_action" if is_action else "directional_clear"

    # Determine index
    index = "上证指数"
    if "创业板" in content:
        index = "创业板指"
    elif "沪深300" in content or "沪深" in content:
        index = "沪深300"

    # Extract evidence (first 2-3 sentences that contain the signal)
    evidence = content[:300]

    return {
        "date": post.get("publish_date", "")[:10],
        "direction": direction,
        "strength": strength,
        "specific": specific,
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

    # Deduplicate: same date + same direction + same strength → keep only first
    seen = set()
    deduped = []
    for s in signals:
        key = (s["date"], s["direction"], s["strength"])
        if key not in seen:
            seen.add(key)
            deduped.append(s)
        else:
            # Keep the one with higher specific level
            for i, existing in enumerate(deduped):
                if (existing["date"], existing["direction"], existing["strength"]) == key:
                    if s["specific"] == "explicit_action" and existing["specific"] != "explicit_action":
                        deduped[i] = s
                    break

    deduped.sort(key=lambda x: x["date"])

    # Stats
    bullish = [s for s in deduped if s["direction"] == "bullish"]
    bearish = [s for s in deduped if s["direction"] == "bearish"]
    strong = [s for s in deduped if s["strength"] == "strong"]
    explicit = [s for s in deduped if s["specific"] == "explicit_action"]
    vague = [s for s in deduped if s["specific"] == "directional_vague"]

    print(f"\n  {blogger_name}: {total} posts → {len(deduped)} signals (deduped from {len(signals)} raw)")
    print(f"    看多: {len(bullish)} | 看空: {len(bearish)}")
    print(f"    strong: {len(strong)} | moderate: {len(deduped) - len(strong)}")
    print(f"    explicit_action: {len(explicit)} | directional_clear: {len(deduped) - len(explicit) - len(vague)} | directional_vague: {len(vague)}")

    # Write signals
    output = {
        "blogger": blogger_name,
        "signals": deduped,
        "extraction_method": "pattern_based_v1",
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
