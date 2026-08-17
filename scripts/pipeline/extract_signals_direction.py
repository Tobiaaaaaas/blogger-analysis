#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSeek 自动提取 Direction 方向信号（替代 Claude 人工逐条标注）。

流程：读 data/posts/<博主>.json 的帖子 + <博主>_bodies_s*.json 的正文
      → 分批调 DeepSeek flash 按 SKILL.md §1~§8 规则逐条判断
      → 脚本强校验（spec/idx/cat/日期/去重，非法条目丢弃）→ 写 data/direction_signals/<博主>.json

用法：
  export DEEPSEEK_API_KEY="sk-..."      # 只经环境变量，绝不写入文件/提交
  python scripts/pipeline/extract_signals_direction.py <博主名>
  python scripts/pipeline/extract_signals_direction.py <博主名> --batch-size 25
  python scripts/pipeline/extract_signals_direction.py <博主名> --limit 30          # 冒烟：只处理前 30 条
  python scripts/pipeline/extract_signals_direction.py <博主名> --out /tmp/x.json   # 写指定路径（不动正式数据）
  python scripts/pipeline/extract_signals_direction.py <博主名> --dry-run           # 不调 API、不写文件
"""

import argparse
import glob
import json
import os
import re
import sys
import time
from collections import Counter

from openai import OpenAI

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))

# ── Configuration ──
MODEL = "deepseek-v4-flash"
BASE_URL = "https://api.deepseek.com"
DEFAULT_BATCH_SIZE = 15
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds

# 只提取 2026-01-01 及之后发布的信号
SIGNAL_START = "2026-01-01"

# ── Direction schema 合法集（引擎 run_direction.py 直接读取，非法值会导致崩溃）──
VALID_IDX = {"上证指数", "上证50", "沪深300", "中证500", "中证1000", "创业板指", "科创50", "双创"}
VALID_CAT = {"scored", "无效-日内", "无预测周期", "目标点位"}
SPEC_RE = re.compile(r"^(today|week|nweek|nweek_first|month|nmonth|yearend|t\d+|d:\d{4}-\d{2}-\d{2})$")
NON_SCORED_CATS = {"无效-日内", "无预测周期", "目标点位"}


def _placeholder(body):
    """登录墙占位正文视为无正文（手机登录/扫码登录/获取验证码）。"""
    if not body:
        return True
    if len(body) <= 20:
        return True
    if "登录" in body and "验证码" in body:
        return True
    return False


# ── System Prompt（SKILL.md §1~§8 规则精简版，Direction schema）──
SYSTEM_PROMPT = """你是财经内容分析助手。你的任务是阅读今日头条财经博主的帖子，判断每条帖子是否包含对上证综指/大盘的**明确方向预测**，并按给定规则逐条提取方向信号。

## 提取为信号的标准（可打分性）
必须同时满足：**明确的预测周期**（明天/本周/下周/下月等）+ **明确的态度**（看涨/看跌、收阳/收阴、涨/跌）。
- ❌ 仅描述形态而无收盘方向态度：震荡、筑底、洗盘、蓄势、拉锯、考验X点支撑、探底回升、冲高回落 → 不提取
- ❌ 仓位自述（"还剩4成仓""满仓持股"）、行情回顾、投资理念、新闻评论 → 不提取
- ❌ 目标点位无时间承诺（"目标是4260点""看到X点""还有X点空间"）→ cat=目标点位，不标 spec
- ❌ 无预测周期（"中长期""未来几个月""大趋势向上"）→ cat=无预测周期，不标 spec
- ❌ 预测对象是不可映射板块（有色/钢铁/医药/创新药/恒科/半导体/电池/房地产/航天/军工等）→ 忽略不提取

## 默认规则与板块→指数映射
帖子未写明任何指数或板块名称 → 默认判断上证综指，idx=上证指数。
| 帖中提到 | idx |
| 创业板/创业板指/创业板ETF | 创业板指 |
| 科创/科创50/科创板 | 科创50 |
| 小盘/成长/中证1000 | 中证1000 |
| 中证500 | 中证500 |
| 上证50/50ETF/老登/大金融/银行/保险/券商/证券/白酒/酒 | 上证50 |
| 沪深300 | 沪深300 |
| 上证综指/上证/综指/大盘 | 上证指数 |
| 双创 | 双创（创业板指+科创50各半） |

## 时间有效性（盘中不做日内预测）
- 交易日**盘前**（<9:30）发布"今天/今日"预测 → spec=today
- 交易日**盘中/盘后**发布"今天"预测 → 无效，cat=无效-日内，不标 spec
- 非交易日发布"今天"预测 → 无效，cat=无效-日内，不标 spec
- "明天/明日/次日" → spec=t1；"后天" → t2；"N天后/N日内" → tN（如 t3、t10）
- "X-Y天"（如1-2天、3-5天）→ 取窗口最后一天 tY（1-2天→t2，3-5天→t5）
- "未来几天" → t3；"近期/短期/很快/马上/即将/不久/临近"（向前展望）→ t5
- "本周/本周内" → week；"下周" → nweek；"下周一" → nweek_first
- "月底前/月末前" → month；"下个月/下月" → nmonth
- "下半年/全年/今年/2026年" → yearend
- "X月X日"（未来具体日期）→ d:YYYY-MM-DD
- 同一帖子多个周期（如"明天反弹、下周继续涨"）→ 分条记录，各标对应 spec
- 完全无预测周期 → cat=无预测周期，不标 spec

## 信号字段
- post_n：输入批次中帖子的编号（从 0 开始），必须对应
- d：1=看多，-1=看空
- s：2=strong（"一定""必将""满仓""清仓""坚决""毫无疑问"）；1=moderate（"偏多""倾向于""大概率""有望"）
- idx：上面映射出的指数 key（默认"上证指数"）
- spec：上面的周期编码（仅 cat=scored 时必填）
- summary：≤50字，预测关键句概括
- cat：scored（默认）/ 无效-日内 / 无预测周期 / 目标点位
  - **禁止写"待验证"或"无效-过时"**（由打分引擎自动判定）
- 一条帖子可对应多条信号（不同周期/不同方向时）；同帖重复结论只记一条

## 输出格式
只返回一个 JSON 对象，无任何其他文字：
{"signals": [{"post_n": 0, "d": 1, "s": 1, "idx": "上证指数", "spec": "t1", "summary": "明天看涨", "cat": "scored"}, ...]}
- 没有信号的帖子不用出现在 signals 里
- cat=scored 时 spec、s 必填；cat=无效-日内/无预测周期/目标点位 时不填 spec、s
- 只返回 JSON，不要有任何其他文字"""


def load_posts_and_bodies(blogger):
    """读 data/posts/<名>.json 的帖子 + 合并 <名>_bodies_s*.json 正文，过滤 2026+。"""
    posts_path = os.path.join(PROJECT_ROOT, "data", "posts", f"{blogger}.json")
    if not os.path.exists(posts_path):
        print(f"ERROR: Posts file not found: {posts_path}")
        return None, None, None, None

    data = json.load(open(posts_path, encoding="utf-8"))
    all_posts = data.get("posts", [])

    bodies = {}
    for fp in sorted(glob.glob(os.path.join(PROJECT_ROOT, "data", "posts", f"{blogger}_bodies_s*.json"))):
        try:
            bodies.update(json.load(open(fp, encoding="utf-8")))
        except Exception as e:
            print(f"WARN: 读取正文文件失败跳过 {os.path.basename(fp)}: {e}")

    eval_posts, pre_count = [], 0
    for p in all_posts:
        pd = (p.get("publish_date") or "").strip()
        if not pd:
            continue
        if pd[:10] < SIGNAL_START:
            pre_count += 1
        else:
            eval_posts.append(p)
    return all_posts, eval_posts, pre_count, bodies


def post_text(post, bodies):
    """正文优先（非登录墙占位），否则用列表标题 content。"""
    b = bodies.get(str(post.get("post_id", "")), {})
    body = b.get("body", "") if isinstance(b, dict) else ""
    if _placeholder(body):
        body = ""
    text = body or post.get("content", "") or ""
    if len(text) > 500:
        text = text[:300] + "\n...[省略中间内容]...\n" + text[-250:]
    return text


def build_batches(posts, batch_size):
    for i in range(0, len(posts), batch_size):
        yield posts[i:i + batch_size]


def format_post_for_prompt(post, index, bodies):
    pd = post.get("publish_date", "?")
    return f"[Post #{index}] {pd}\n{post_text(post, bodies)}\n"


def call_api(client, batch_posts, blogger, batch_num, total_batches, bodies):
    """发一批给 DeepSeek，返回 (parsed_json, raw) 或 (None, None)。"""
    lines = [format_post_for_prompt(p, i, bodies) for i, p in enumerate(batch_posts)]
    user_message = (
        f"以下是博主「{blogger}」的 {len(batch_posts)} 条帖子。请逐条分析，"
        f"判断是否包含对上证/大盘的明确方向预测，并返回标注 JSON。\n\n" + "\n".join(lines)
    )

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
                # 关推理模式：V4 Flash 否则会浪费 token 在 CoT 上
                extra_body={"thinking": {"type": "disabled"}},
            )
            raw = response.choices[0].message.content
            result = parse_response(raw)
            if result is not None:
                return result, raw
            print(f"  Batch {batch_num}/{total_batches} attempt {attempt + 1}: JSON parse failed, retrying...")
            time.sleep(RETRY_DELAY * (attempt + 1))
        except Exception as e:
            print(f"  Batch {batch_num}/{total_batches} attempt {attempt + 1}: API error: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                raise
    return None, None


def parse_response(raw):
    """解析 LLM 返回的 JSON，处理 markdown 代码块与截断 JSON。"""
    if not raw:
        return None

    text = raw.strip()

    # 去 markdown 代码块
    if text.startswith("```"):
        nl = text.find("\n")
        if nl >= 0:
            text = text[nl + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    # 直接解析
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "signals" in data and isinstance(data["signals"], list):
            return data
    except json.JSONDecodeError:
        pass

    # 括号配对扫描：找包含 "signals" 的最外层对象
    for m in re.finditer(r"\{", text):
        start = m.start()
        depth, end = 0, -1
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end > start:
            try:
                data = json.loads(text[start:end])
                if isinstance(data, dict) and "signals" in data and isinstance(data["signals"], list):
                    return data
            except json.JSONDecodeError:
                continue

    # 兜底：补全未闭合括号
    open_braces = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")
    if open_braces > 0 or open_brackets > 0:
        try:
            data = json.loads(text + "]" * open_brackets + "}" * open_braces)
            if isinstance(data, dict) and "signals" in data and isinstance(data["signals"], list):
                return data
        except json.JSONDecodeError:
            pass

    return None


def validate_signals(raw_signals, batch_posts):
    """把 DeepSeek 输出映射/校验成 Direction schema。返回 (ok_signals, dropped_counter)。"""
    ok, dropped = [], Counter()
    for r in raw_signals:
        if not isinstance(r, dict):
            dropped["非对象"] += 1
            continue
        try:
            post_n = int(r.get("post_n", -1))
        except (TypeError, ValueError):
            post_n = -1
        if not (0 <= post_n < len(batch_posts)):
            dropped["post_n 越界"] += 1
            continue
        post = batch_posts[post_n]
        pub = (post.get("publish_date") or "").strip()
        if not pub or pub[:10] < SIGNAL_START:
            dropped["非 2026"] += 1
            continue

        d = r.get("d")
        if isinstance(d, str):
            try:
                d = int(d)
            except ValueError:
                d = None
        if d not in (1, -1):
            dropped["d 非法"] += 1
            continue

        cat = r.get("cat") or "scored"
        if cat not in VALID_CAT:
            dropped[f"cat 非法: {cat}"] += 1
            continue

        idx = r.get("idx") or "上证指数"
        if idx in ("上证综指", "上证", "综指"):
            idx = "上证指数"
        if idx not in VALID_IDX:
            dropped[f"idx 非法: {idx}"] += 1
            continue

        summary = (r.get("summary") or "").strip()
        if not summary:
            dropped["summary 缺失"] += 1
            continue
        summary = summary[:50]

        sig = {"pub": pub, "d": d, "idx": idx, "summary": summary}
        if cat == "scored":
            spec = str(r.get("spec") or "")
            if not SPEC_RE.match(spec):
                dropped[f"spec 非法/缺失: {spec or '(空)'}"] += 1
                continue
            s = r.get("s", 1)
            try:
                s = int(s)
            except (TypeError, ValueError):
                s = 1
            if s not in (1, 2):
                s = 1
            sig["s"] = s
            sig["spec"] = spec
            sig["cat"] = "scored"
        else:
            sig["cat"] = cat
        ok.append(sig)
    return ok, dropped


def dedup_and_sort(signals):
    """去重：同日同周期同方向→1 条（保留当天最晚发布）。"""
    seen = {}
    for s in signals:
        date = s["pub"][:10]
        key = (date, s.get("spec") if s["cat"] == "scored" else f"cat:{s['cat']}", s["d"])
        cur = seen.get(key)
        if cur is None or s["pub"] > cur["pub"]:
            seen[key] = s
    return sorted(seen.values(), key=lambda s: s["pub"])


def main():
    parser = argparse.ArgumentParser(description="DeepSeek 自动提取 Direction 方向信号")
    parser.add_argument("blogger", help="博主名（data/posts/<名>.json）")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="每批帖子数")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 条帖子（冒烟测试）")
    parser.add_argument("--out", default="", help="输出文件路径（默认 data/direction_signals/<名>.json）")
    parser.add_argument("--dry-run", action="store_true", help="不调 API、不写文件")
    args = parser.parse_args()

    blogger = args.blogger
    all_posts, eval_posts, pre_count, bodies = load_posts_and_bodies(blogger)
    if all_posts is None:
        sys.exit(1)

    if args.limit > 0:
        eval_posts = eval_posts[:args.limit]

    total_eval = len(eval_posts)
    batches = list(build_batches(eval_posts, args.batch_size))
    total_batches = len(batches)

    print("=" * 60)
    print(f"Direction 信号提取（DeepSeek {MODEL}）：{blogger}")
    print("=" * 60)
    print(f"帖子总数: {len(all_posts)} | 2026 前剔除: {pre_count} | 参与提取: {total_eval}")
    print(f"批次: {total_batches}（batch-size={args.batch_size}）")

    if args.dry_run:
        print(f"\n[DRY RUN] 将向 DeepSeek 发送 {total_batches} 批帖子（不调 API、不写文件）")
        return

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY 环境变量未设置（只经环境变量传入，绝不写入文件）")
        print("  export DEEPSEEK_API_KEY='sk-...'")
        sys.exit(1)

    client = OpenAI(api_key=api_key, base_url=BASE_URL)

    all_signals, all_dropped = [], Counter()
    start_time = time.time()
    failed_batches = 0
    for batch_num, batch_posts in enumerate(batches, 1):
        print(f"\n[Batch {batch_num}/{total_batches}] {len(batch_posts)} 条...", end=" ", flush=True)
        try:
            result, _ = call_api(client, batch_posts, blogger, batch_num, total_batches, bodies)
            if result is None:
                failed_batches += 1
                print("FAILED after %d retries, skipping batch" % MAX_RETRIES)
                continue
            ok, dropped = validate_signals(result.get("signals", []), batch_posts)
            all_signals.extend(ok)
            all_dropped.update(dropped)
            print(f"✓ {len(ok)} 信号 | 累计 {len(all_signals)} | {time.time() - start_time:.0f}s")
        except Exception as e:
            failed_batches += 1
            print(f"ERROR: {e}")
            continue
        if batch_num < total_batches:
            time.sleep(0.5)

    elapsed = time.time() - start_time
    signals = dedup_and_sort(all_signals)
    cat_counts = Counter(s["cat"] for s in signals)

    print(f"\n{'=' * 60}")
    print(f"✅ 提取完成: {blogger} | 耗时 {elapsed:.0f}s | 失败批次 {failed_batches}")
    print(f"参与提取: {total_eval} | 提取信号: {len(signals)}")
    print(f"  按 cat: {dict(cat_counts)}")
    if all_dropped:
        print(f"  丢弃: {sum(all_dropped.values())} 条 -> {dict(all_dropped)}")

    partial = args.limit > 0
    if args.dry_run:
        return
    if partial and not args.out:
        print("\n[部分运行] 仅处理前 %d 条，未写正式文件（加 --out 可写指定路径）" % args.limit)
        return

    out_path = args.out or os.path.join(PROJECT_ROOT, "data", "direction_signals", f"{blogger}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"blogger": blogger, "signals": signals}, f, ensure_ascii=False, indent=2)
    print(f"输出: {out_path}（{len(signals)} 条信号）")


if __name__ == "__main__":
    main()
