"""
Step 1.5: 关键词匹配帖子初筛
纯关键词匹配：帖子含任意方向判断或操作建议关键词 → 保留，否则丢弃。
"""
import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT, "data", "posts")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "data")

INPUT = os.path.join(DATA_DIR, "posts.json")
OUTPUT = os.path.join(OUTPUT_DIR, "posts_filtered.json")

KEYWORDS = [
    # # === 方向判断 ===
    # "看涨", "看跌", "看多", "看空", "做多", "做空",
    # "上涨", "下跌", "上升", "下行", "走高", "走低",
    # "冲高", "回落", "拉升", "杀跌", "急跌", "急涨",
    # "见顶", "见底", "筑底", "触底", "探底",
    # "突破", "跌破", "击穿", "站上", "失守",
    # "反弹", "回调", "回踩", "反抽", "反转", "拐点",
    # "牛市", "熊市", "主升浪", "主跌浪",
    # "多头", "空头", "诱多", "诱空",
    # "顶部", "底部", "大底", "大顶", "阶段顶", "阶段底",
    # # "高位", "低位", "高点", "低点",
    # # === 技术分析术语（顺应周期风格）===
    # "背离", "破位", "不超过",
    # "见顶", "见底", "筑底", "探底",
    # "突破", "跌破", "站上", "失守",
    # "反弹", "反转", "拐点",
    "顶", "底",
    # === 操作建议 ===
    "买入", "卖出",
    # "加仓", "减仓",
    "加", "减",
    "清", "满", "空仓", "半仓",
    "入场", "离场", "进场", "出场", "扫货", "出货",
    "高抛", "低吸", "抄底", "逃顶",
    "止盈", "止损", "持有", "持股", "持币",
    "建仓", "补仓", "观望", "等待时机", "仓位",
]


def has_keyword(content):
    return any(kw in content for kw in KEYWORDS)


def main():
    with open(INPUT, encoding="utf-8") as f:
        data = json.load(f)

    posts = data["posts"]
    total = len(posts)

    kept = [p for p in posts if has_keyword(p.get("content", ""))]
    discarded = [p for p in posts if not has_keyword(p.get("content", ""))]

    print(f"总帖子数: {total}")
    print(f"保留: {len(kept)} ({len(kept)/total*100:.1f}%)")
    print(f"丢弃: {len(discarded)} ({len(discarded)/total*100:.1f}%)")

    # 保存
    result = dict(data)
    result["total_posts"] = len(kept)
    result["filtered_from"] = total
    result["posts"] = kept

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n输出: {OUTPUT}")

    # 抽查丢弃样本
    print(f"\n--- 丢弃样本（全部 {len(discarded)} 条）---")
    for p in discarded:
        date = p.get("publish_date", "?")
        content = p.get("content", "")[:150]
        print(f"  [{date}] {content}...")


if __name__ == "__main__":
    main()
