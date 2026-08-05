"""Test new vs old regex on all 2026 posts."""
import json, re, os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

with open(os.path.join(PROJECT_ROOT, "data/posts/顺应周期.json"), "r", encoding="utf-8") as f:
    data = json.load(f)
posts = data["posts"]

# NEW patterns
has_units = (
    r"\d+成|"
    r"\d+层|"
    r"成仓|"
    r"几成|"
    r"半仓|满仓|空仓|重仓|轻仓|底仓|总仓位|总仓|"
    r"仓位[是为在]|"
    r"仓位[降增加减到至]|"
    r"把仓位|将仓位|我的仓|目前仓|当前仓|现在仓"
)
has_action = (
    r"进了|减了|抛了|加了|买了|卖了|"
    r"买进|卖出|买入|"
    r"出了|出掉|抛掉|跑了|跑掉|"
    r"补仓|减仓|加仓|建仓|清仓|平仓|锁仓|"
    r"调仓|换仓|移仓|调入|调出|"
    r"减到|加到|降至|增至|"
    r"T了|T掉|"
    r"回补|补了|补进|接回|接了|接了点|"
    r"止盈|止损|割肉|"
    r"进场|离场|上车|下车|梭哈|打满|"
    r"进\d|减\d|加\d|抛\d|补\d|出\d"
)
has_holding = (
    r"持仓|持有|拿着|在场|在场内|"
    r"我还有|还剩|剩下|留有|保留|"
    r"底仓|仓位管理|仓位结构|"
    r"配置|配比|组合|"
    r"防守仓|进攻仓|短线仓|中线仓|长线仓|"
    r"仓位是|仓位为|仓位在|仓位到|"
    r"目前仓|当前仓|现在仓|我的仓|个人仓|"
    r"\%仓位|仓位\%|"
    r"成仓位|成仓"
)
pattern_new = re.compile(f"{has_units}|{has_action}|{has_holding}")

# OLD patterns
has_units_old = r"\d+成|仓位|成仓|几成|半仓|满仓|空仓"
has_action_old = r"进了|减了|抛了|加了|买了|卖了|补仓|减仓|加仓|建仓|清仓|T了|T掉|买进|卖出"
has_holding_old = r"持仓|持有|拿着|在场|在场内|我还有|还剩|剩下"
pattern_old = re.compile(f"{has_units_old}|{has_action_old}|{has_holding_old}")

p2026 = [p for p in posts if p.get("publish_date", "")[:4] == "2026"]
old_match = [p for p in p2026 if pattern_old.search(p["content"]) and len(p["content"]) > 15]
new_match = [p for p in p2026 if pattern_new.search(p["content"]) and len(p["content"]) > 15]

old_ids = set(p["post_id"] for p in old_match)
new_only = [p for p in new_match if p["post_id"] not in old_ids]
old_only = [p for p in old_match if p["post_id"] not in set(p["post_id"] for p in new_match)]

print(f"2026 posts: {len(p2026)}")
print(f"OLD regex: {len(old_match)} candidates")
print(f"NEW regex: {len(new_match)} candidates (+{len(new_match)-len(old_match)})")
print(f"NEW only (missed by OLD): {len(new_only)}")
print(f"OLD only (NEW no longer matches): {len(old_only)}")
print()

if new_only:
    with open(os.path.join(PROJECT_ROOT, "_regex_compare.txt"), "w", encoding="utf-8") as f:
        f.write("=== Posts caught by NEW but missed by OLD ===\n\n")
        for p in new_only:
            content = p["content"].replace("\n", " ")[:200]
            f.write(f"[{p['publish_date'][:10]}] {content}\n---\n")
    print("Details written to _regex_compare.txt")

    # Show first 10
    print("First 10 new catches:")
    for p in new_only[:10]:
        content = p["content"].replace("\n", " ")[:100]
        print(f"  [{p['publish_date'][:10]}] {content}")
