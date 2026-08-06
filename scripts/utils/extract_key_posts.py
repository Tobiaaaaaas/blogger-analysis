"""
提取博主在拐点附近 ±4天的帖子 + 最新帖子，极简输出。
仅保留拐点相关帖子，供 workflow agent 直接读取分析。
"""
import json
import os
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

# 关键拐点日期（来自 market_analysis.md）
INFLECTION_DATES = {
    # Major
    "M1": ("2024-09-13", "2690大底", "阶段1→2"),
    "M2": ("2024-10-08", "3674脉冲顶", "阶段2→3"),
    "M3": ("2025-04-07", "3041关税底", "阶段3→4"),
    "M4": ("2025-11-14", "4034首次触4000", "阶段4内"),
    "M5": ("2026-03-23", "3795急跌底", "阶段4内"),
    "M6": ("2026-05-14", "4259上证年度顶", "阶段4→5"),
    "M7": ("2026-06-25", "4380创业板年度顶", "阶段5内"),
    "M8": ("2026-07-20", "3741调整底", "阶段5内"),
    # Intermediate (与博主分析相关的)
    "I1": ("2024-10-18", "3262回调底", "M2后"),
    "I2": ("2024-11-08", "3452反弹顶", "I1后"),
    "I5": ("2025-01-13", "3161年初底", "年初"),
    "I6": ("2025-03-19", "3426反弹顶", "M3前"),
    "I9": ("2025-12-16", "3816年末底", "M4后"),
    "I10": ("2026-01-14", "4191年初顶", "I9后"),
    "I12": ("2026-03-03", "4197二次冲顶", "M5前"),
    "I13": ("2026-06-08", "3928六月底", "M6后"),
    "I14": ("2026-06-23", "4175上证次顶", "M7前"),
}

WINDOW = 4  # ±4天


def extract_blogger(input_file, output_file, blogger_name):
    with open(input_file, encoding="utf-8") as f:
        data = json.load(f)

    posts = data["posts"]
    key_posts = []
    seen_dates = {}

    inf_dates = {}
    for label, (idate, idesc, phase) in INFLECTION_DATES.items():
        idt = datetime.strptime(idate, "%Y-%m-%d")
        inf_dates[label] = (idt, idesc, phase)

    for p in posts:
        content = p.get("content", "")
        date_str = p.get("publish_date", "")[:10]

        if not date_str or len(content) < 20:
            continue

        try:
            pd = datetime.strptime(date_str, "%Y-%m-%d")
        except:
            continue

        # 检查是否在任意拐点 ±WINDOW 天范围内
        for label, (idt, idesc, phase) in inf_dates.items():
            diff = abs((pd - idt).days)
            if diff <= WINDOW:
                # 每个拐点每天最多保留3条帖子
                day_key = f"{label}_{date_str}"
                if day_key not in seen_dates:
                    seen_dates[day_key] = 0
                if seen_dates[day_key] >= 3:
                    break
                seen_dates[day_key] += 1

                key_posts.append({
                    "date": date_str,
                    "time": p.get("publish_date", "")[11:16] or "",
                    "content": content[:400],
                    "url": p.get("url", ""),
                    "inflection": label,
                    "inflection_desc": idesc,
                    "phase": phase,
                    "days_offset": (pd - idt).days,
                })
                break

    # Also add most recent posts (for "current view")
    recent_count = 0
    for p in posts:
        content = p.get("content", "")
        date_str = p.get("publish_date", "")[:10]
        if not date_str or len(content) < 30:
            continue
        # Skip if already included
        already = any(kp["date"] == date_str and kp["content"][:100] == content[:100] for kp in key_posts)
        if already:
            continue
        key_posts.append({
            "date": date_str,
            "time": p.get("publish_date", "")[11:16] or "",
            "content": content[:400],
            "url": p.get("url", ""),
            "inflection": "RECENT",
            "inflection_desc": "最新观点",
            "phase": "",
            "days_offset": 0,
        })
        recent_count += 1
        if recent_count >= 10:
            break

    # 按日期+拐点排序
    key_posts.sort(key=lambda x: (x["date"], x["inflection"]))

    # 统计拐点覆盖
    inflection_hits = set(kp["inflection"] for kp in key_posts if kp["inflection"] != "RECENT")

    result = {
        "blogger": blogger_name,
        "total_posts": len(posts),
        "key_posts": len(key_posts),
        "inflection_hits": sorted(inflection_hits),
        "time_range": {
            "earliest": data.get("time_range", {}).get("earliest", ""),
            "latest": data.get("time_range", {}).get("latest", ""),
        },
        "user_info": data.get("user_info", {}),
        "key_posts_list": key_posts,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"  {blogger_name}: {len(posts)}→{len(key_posts)}帖, 拐点{len(inflection_hits)}个")
    return len(key_posts)


def main():
    posts_dir = os.path.join(DATA_DIR, "posts")
    output_dir = os.path.join(DATA_DIR, "key_posts")
    os.makedirs(output_dir, exist_ok=True)

    for fname in sorted(os.listdir(posts_dir)):
        if not fname.endswith(".json") or fname == "posts.json":
            continue
        blogger = fname.replace(".json", "")
        input_path = os.path.join(posts_dir, fname)
        output_path = os.path.join(output_dir, f"{blogger}_key.json")
        extract_blogger(input_path, output_path, blogger)

    print("\nDone!")


if __name__ == "__main__":
    main()
