"""
Batch signal extraction using DeepSeek V4 Flash.
Reads posts, sends batches to LLM for semantic signal annotation.

Usage:
  export DEEPSEEK_API_KEY="sk-..."
  python scripts/extract_signals.py --blogger TL阳光
  python scripts/extract_signals.py --blogger TL阳光 --batch-size 25 --dry-run
"""

import json
import os
import sys
import time
import argparse
from datetime import datetime
from openai import OpenAI

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

# ── Configuration ──
MODEL = "deepseek-v4-flash"
BASE_URL = "https://api.deepseek.com"
DEFAULT_BATCH_SIZE = 15
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds

# Evaluation cutoff: only posts from 2024-06 onwards
EVAL_START = "2024-06-01"

# ── System Prompt (from SKILL.md Step 1.5) ──
SYSTEM_PROMPT = """你是财经内容分析助手。你的任务是阅读今日头条财经博主的帖子，判断每条帖子是否包含对上证综指/大盘的明确方向性预测，并逐条标注。

## 核心判断标准

整条帖子内容中有对上证/大盘/市场的方向上明确的、非模糊的预测。

**提取为信号**：帖子表达了对大盘未来的明确方向预测。
**不提取为信号**：帖子没有明确的方向预测。

> 默认规则：帖子中未写明任何指数或板块名称，默认该帖子是对上证综指/大盘的判断，应提取信号。
> 发布日期未知：如果帖子的发布日期无法确定，则不提取为信号。

## 信号标注字段

对每条提取为信号的帖子，标注以下字段：

- **direction**: "bullish"（看涨/看多）或 "bearish"（看跌/看空）
- **strength**: "strong" 或 "moderate"
  - "strong"：措辞坚决（"一定""必将""满仓""清仓""坚决"）+ 给出具体点位/仓位
  - "moderate"：有方向判断但措辞温和（"偏多""倾向于""大概率""有望"）
- **time_horizon**: "intraday" / "short" / "medium" / "long" / "unspecified"
  - "intraday"：仅针对当日（"下午拉""尾盘跳水""今天收阳"）
  - "short"：1-2天（"明天涨""明天调整"）
  - "medium"：1周以上（"本周""下周""这波"）
  - "long"：1月以上（"牛市""下半年""趋势""主升浪"）
  - "unspecified"：无明确时间范围
  - 当帖子同时包含不同级别的时间信息时，以最长的有效时间维度为准
- **evidence**: 帖子原文关键句，≤300字。必须是一段连续的原文，不能是概括或改写。
- **publish_time**: 帖子的发布时间，格式 "YYYY-MM-DD HH:MM"，直接从帖子数据中复制。

## 不提取为信号的帖子（需标注原因）

对不提取为信号的帖子，标注以下原因之一：

- "no_market_topic"：纯生活/娱乐/社会新闻/广告等，与市场完全无关
- "pure_description"：仅描述行情（回顾走势、总结发生了什么），没有方向判断
- "directional_vague"：方向模糊/骑墙（"可能涨也可能跌""等待方向明朗"类摇摆表态）
- "other_index_sector"：仅针对具体板块/个股/其他指数（如仅说"半导体""茅台""创业板"），未提及上证/大盘
- "other"：条件未触发、纯转发、心理按摩、纯互动等

## 输出格式

你必须只返回一个 JSON 对象，格式如下：

```json
{
  "results": [
    {"post_n": 0, "is_signal": true, "direction": "bullish", "strength": "moderate", "time_horizon": "short", "evidence": "明天继续看涨，这个位置有支撑。", "publish_time": "2025-01-21 14:30"},
    {"post_n": 1, "is_signal": false, "reason": "pure_description"},
    {"post_n": 2, "is_signal": true, "direction": "bearish", "strength": "strong", "time_horizon": "medium", "evidence": "这波调整远没结束，目标3200，建议清仓等待。", "publish_time": "2025-01-22 09:15"}
  ]
}
```

**重要**：
- 每个 post_n 必须对应输入中的帖子编号
- is_signal=true 时必须包含 direction, strength, time_horizon, evidence, publish_time
- is_signal=false 时必须包含 reason
- evidence 必须从帖子原文中逐字复制，不要改写
- 只返回 JSON，不要有任何其他文字"""


def load_posts(blogger):
    """Load posts from data/posts/<blogger>.json, filter to evaluation period."""
    path = os.path.join(PROJECT_ROOT, "data", "posts", f"{blogger}.json")
    if not os.path.exists(path):
        print(f"ERROR: Posts file not found: {path}")
        return None, None, None

    data = json.load(open(path, encoding="utf-8"))
    all_posts = data.get("posts", [])

    # Filter to evaluation period
    eval_posts = []
    pre_count = 0
    for p in all_posts:
        pd = p.get("publish_date", "")[:10]
        if not pd:
            continue
        if pd < EVAL_START:
            pre_count += 1
        else:
            eval_posts.append(p)

    return all_posts, eval_posts, pre_count


def build_batches(posts, batch_size):
    """Split posts into batches."""
    for i in range(0, len(posts), batch_size):
        yield posts[i:i + batch_size]


def format_post_for_prompt(post, index):
    """Format a single post for the prompt."""
    pd = post.get("publish_date", "?")
    content = post.get("content", "")
    # Truncate very long posts to 500 chars (keep start and end)
    if len(content) > 500:
        content = content[:300] + "\n...[省略中间内容]...\n" + content[-150:]
    return f"[Post #{index}] {pd}\n{content}\n"


def call_api(client, batch_posts, batch_size, blogger, batch_num, total_batches):
    """Send one batch to DeepSeek API, return parsed results."""
    # Build user message
    lines = []
    for i, post in enumerate(batch_posts):
        lines.append(format_post_for_prompt(post, i))

    user_message = f"以下是博主「{blogger}」的 {len(batch_posts)} 条帖子。请逐条分析，判断是否包含对上证/大盘的明确方向预测，并返回标注 JSON。\n\n" + "\n".join(lines)

    for attempt in range(MAX_RETRIES):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=0,
                max_tokens=8192,
                # Disable reasoning mode — V4 Flash otherwise wastes tokens on CoT
                extra_body={"thinking": {"type": "disabled"}},
            )

            raw = response.choices[0].message.content
            # Try to parse JSON from response (may have markdown fences)
            result = parse_response(raw)
            if result is not None:
                return result, raw
            else:
                print(f"  Batch {batch_num}/{total_batches} attempt {attempt+1}: JSON parse failed, retrying...")
                time.sleep(RETRY_DELAY * (attempt + 1))

        except Exception as e:
            print(f"  Batch {batch_num}/{total_batches} attempt {attempt+1}: API error: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                raise

    return None, None


def parse_response(raw):
    """Parse JSON from LLM response, handling markdown fences and truncated JSON."""
    if not raw:
        return None

    text = raw.strip()

    # Remove markdown code fences
    if text.startswith("```"):
        nl = text.find("\n")
        if nl >= 0:
            text = text[nl + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    # Try direct parse first
    try:
        data = json.loads(text)
        if "results" in data and isinstance(data["results"], list):
            return data
    except json.JSONDecodeError:
        pass

    # Try to extract JSON by finding matching braces
    # Find the outermost { that starts a JSON object containing "results"
    import re
    for m in re.finditer(r'\{', text):
        start = m.start()
        depth = 0
        end = -1
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end > start:
            try:
                candidate = text[start:end]
                data = json.loads(candidate)
                if "results" in data and isinstance(data["results"], list):
                    return data
            except json.JSONDecodeError:
                continue

    # Last resort: try to fix truncated JSON by closing open structures
    # Count unclosed braces and close them
    open_braces = text.count('{') - text.count('}')
    open_brackets = text.count('[') - text.count(']')
    if open_braces > 0 or open_brackets > 0:
        fixed = text + ']' * open_brackets + '}' * open_braces
        try:
            data = json.loads(fixed)
            if "results" in data and isinstance(data["results"], list):
                return data
        except json.JSONDecodeError:
            pass

    return None


def validate_and_build_signals(results, batch_posts, blogger):
    """Validate API results and build signal list + not_extracted counts."""
    signals = []
    not_extracted = {
        "no_market_topic": 0,
        "pure_description": 0,
        "directional_vague": 0,
        "other_index_sector": 0,
        "other": 0,
    }

    for r in results:
        post_n = r.get("post_n", -1)
        is_signal = r.get("is_signal", False)

        if is_signal:
            # Validate required fields
            direction = r.get("direction", "")
            if direction not in ("bullish", "bearish"):
                continue
            strength = r.get("strength", "moderate")
            if strength not in ("strong", "moderate"):
                strength = "moderate"
            th = r.get("time_horizon", "unspecified")
            if th not in ("intraday", "short", "medium", "long", "unspecified"):
                th = "unspecified"
            evidence = r.get("evidence", "")
            publish_time = r.get("publish_time", "")
            if not publish_time and post_n < len(batch_posts):
                publish_time = batch_posts[post_n].get("publish_date", "")

            signals.append({
                "post_n": post_n,
                "direction": direction,
                "strength": strength,
                "time_horizon": th,
                "evidence": evidence[:300] if evidence else "",
                "publish_time": publish_time,
            })
        else:
            reason = r.get("reason", "other")
            if reason not in not_extracted:
                reason = "other"
            not_extracted[reason] += 1

    return signals, not_extracted


def compute_stats(signals, total_eval):
    """Compute scored_bullish/scored_bearish breakdowns."""
    scored_bullish = {"strong": 0, "moderate": 0, "long": 0, "medium": 0, "short": 0, "intraday": 0, "unspecified": 0}
    scored_bearish = {"strong": 0, "moderate": 0, "long": 0, "medium": 0, "short": 0, "intraday": 0, "unspecified": 0}

    for s in signals:
        target = scored_bullish if s["direction"] == "bullish" else scored_bearish
        target[s["strength"]] += 1
        target[s["time_horizon"]] += 1

    return scored_bullish, scored_bearish


def main():
    parser = argparse.ArgumentParser(description="Batch signal extraction with DeepSeek V4 Flash")
    parser.add_argument("--blogger", required=True, help="Blogger name")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Posts per batch")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without API calls")
    args = parser.parse_args()

    blogger = args.blogger
    batch_size = args.batch_size

    # Load posts
    all_posts, eval_posts, pre_count = load_posts(blogger)
    if all_posts is None:
        return

    total_posts = len(all_posts)
    total_eval = len(eval_posts)

    print(f"{'='*60}")
    print(f"Signal Extraction: {blogger}")
    print(f"{'='*60}")
    print(f"Total posts: {total_posts}")
    print(f"Pre-2024-06 (excluded): {pre_count}")
    print(f"Evaluation posts: {total_eval}")
    batches = list(build_batches(eval_posts, batch_size))
    total_batches = len(batches)
    print(f"Batch size: {batch_size}, Total batches: {total_batches}")

    if args.dry_run:
        print("\n[DRY RUN] Would send {total_batches} batches to DeepSeek V4 Flash")
        return

    # Check API key
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY environment variable not set")
        print("  export DEEPSEEK_API_KEY='sk-...'")
        return

    # Init client
    client = OpenAI(api_key=api_key, base_url=BASE_URL)

    # Process batches
    all_signals = []
    all_not_extracted = {
        "no_market_topic": 0,
        "pure_description": 0,
        "directional_vague": 0,
        "other_index_sector": 0,
        "other": 0,
    }

    start_time = time.time()
    for batch_num, batch_posts in enumerate(batches, 1):
        print(f"\n[Batch {batch_num}/{total_batches}] {len(batch_posts)} posts...", end=" ", flush=True)

        try:
            result, raw = call_api(client, batch_posts, batch_size, blogger, batch_num, total_batches)
            if result is None:
                print(f"FAILED after {MAX_RETRIES} retries, skipping batch")
                continue

            signals, not_ext = validate_and_build_signals(result["results"], batch_posts, blogger)
            all_signals.extend(signals)
            for k in all_not_extracted:
                all_not_extracted[k] += not_ext.get(k, 0)

            # Re-number signals with global post numbers
            elapsed = time.time() - start_time
            print(f"✓ {len(signals)} signals | total: {len(all_signals)} | {elapsed:.0f}s")

        except Exception as e:
            print(f"ERROR: {e}")
            continue

        # Small delay between batches to avoid rate limits
        if batch_num < total_batches:
            time.sleep(0.5)

    elapsed = time.time() - start_time

    # Compute stats
    scored_bullish, scored_bearish = compute_stats(all_signals, total_eval)

    # Build output
    not_extracted_total = sum(all_not_extracted.values())
    output = {
        "blogger": blogger,
        "extraction_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "model": MODEL,
        "total_posts": total_posts,
        "pre_2024_06": pre_count,
        "evaluation_posts": total_eval,
        "not_extracted": {
            "no_market_topic": all_not_extracted["no_market_topic"],
            "pure_description": all_not_extracted["pure_description"],
            "directional_vague": all_not_extracted["directional_vague"],
            "other_index_sector": all_not_extracted["other_index_sector"],
            "other": all_not_extracted["other"],
        },
        "scored_bullish": scored_bullish,
        "scored_bearish": scored_bearish,
        "signals": sorted(all_signals, key=lambda x: x.get("publish_time", "")),
    }

    # Save
    out_dir = os.path.join(PROJECT_ROOT, "data", "signals")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{blogger}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Summary
    signal_count = len(all_signals)
    extract_rate = signal_count / total_eval * 100 if total_eval > 0 else 0

    print(f"\n{'='*60}")
    print(f"✅ Extraction complete: {blogger}")
    print(f"{'='*60}")
    print(f"Total posts: {total_posts}")
    print(f"Evaluation posts: {total_eval}")
    print(f"Signals extracted: {signal_count} ({extract_rate:.1f}%)")
    print(f"Not extracted: {not_extracted_total}")
    print(f"  - no_market_topic: {all_not_extracted['no_market_topic']}")
    print(f"  - pure_description: {all_not_extracted['pure_description']}")
    print(f"  - directional_vague: {all_not_extracted['directional_vague']}")
    print(f"  - other_index_sector: {all_not_extracted['other_index_sector']}")
    print(f"  - other: {all_not_extracted['other']}")
    print(f"Bullish: {sum(scored_bullish.values())} (strong={scored_bullish['strong']}, moderate={scored_bullish['moderate']})")
    print(f"Bearish: {sum(scored_bearish.values())} (strong={scored_bearish['strong']}, moderate={scored_bearish['moderate']})")
    print(f"Time: {elapsed:.1f}s")
    print(f"Output: {out_path}")


if __name__ == "__main__":
    main()
