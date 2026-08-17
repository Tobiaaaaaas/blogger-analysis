# -*- coding: utf-8 -*-
"""把已抓取的正文（<名>_bodies_s*.json）合并进主 posts 文件，保持 data/posts/ 格式一致。

新博主爬取时列表接口只回标题（content 字段只有 8 字标题），真实预测在详情页正文，
由 fetch_bodies_shard.py 分片抓取存到 <名>_bodies_s*.json。本脚本将有效正文回填到
posts[].content，使主文件自包含、与老博主（content 即全文）格式统一。

用法：
    python scripts/utils/merge_bodies_to_posts.py <博主名> [<博主名>...] [--keep-bodies]

- 合并规则：bodies 有条目且正文有效（非登录墙占位）→ 正文覆盖 content，原标题单独存 title 字段
  （标题与正文同等重要，提取时两者并列呈现给 LLM，标题不随正文合并丢失）；否则保留原 content
- 默认把已合并的 _bodies_s*.json 移动到 archive/<日期>-bodies-merged/（--keep-bodies 跳过）
- 不校验、不改动除 content 外的任何字段；格式沿用 json.dump(indent=2, ensure_ascii=False)
"""
import argparse
import glob
import json
import os
import shutil
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
POSTS_DIR = os.path.join(ROOT, "data", "posts")


def _placeholder(body):
    """登录墙占位正文视为无正文，绝不回填（手机登录/扫码登录/获取验证码）。"""
    if not body:
        return True
    if len(body) <= 20:
        return True
    if "登录" in body and "验证码" in body:
        return True
    return False


def merge_blogger(blogger):
    posts_path = os.path.join(POSTS_DIR, f"{blogger}.json")
    if not os.path.exists(posts_path):
        print(f"ERROR: 找不到 {posts_path}，跳过")
        return None

    bodies = {}
    for fp in sorted(glob.glob(os.path.join(POSTS_DIR, f"{blogger}_bodies_s*.json"))):
        try:
            bodies.update(json.load(open(fp, encoding="utf-8")))
        except Exception as e:
            print(f"WARN: 读取正文文件失败跳过 {os.path.basename(fp)}: {e}")

    if not bodies:
        print(f"{blogger}: 无 _bodies_s*.json 正文文件，无需合并")
        return None

    data = json.load(open(posts_path, encoding="utf-8"))
    posts = data.get("posts", [])
    filled = 0
    still_title_only = 0
    for p in posts:
        pid = str(p.get("post_id", ""))
        entry = bodies.get(pid)
        if not entry or not isinstance(entry, dict):
            continue
        body = entry.get("body", "")
        if _placeholder(body):
            continue
        # 标题与正文同等重要：正文回填 content，原标题单独存 title 字段（bodies 归档后仍保留）
        p["title"] = entry.get("title") or p.get("content", "")
        p["content"] = body
        filled += 1
    still_title_only = sum(1 for p in posts if len((p.get("content") or "")) <= 30)

    with open(posts_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

    return {"blogger": blogger, "total": len(posts), "filled": filled,
            "bodies_entries": len(bodies), "still_title_only": still_title_only}


def archive_bodies(blogger, target):
    os.makedirs(target, exist_ok=True)
    for fp in glob.glob(os.path.join(POSTS_DIR, f"{blogger}_bodies_s*.json")):
        dest = os.path.join(target, os.path.basename(fp))
        shutil.move(fp, dest)
        print(f"  归档 {os.path.basename(fp)} → {os.path.relpath(dest, ROOT)}")


def main():
    ap = argparse.ArgumentParser(description="把 _bodies_s*.json 正文回填进主 posts 文件")
    ap.add_argument("bloggers", nargs="+", help="博主名，可多个")
    ap.add_argument("--keep-bodies", action="store_true", help="合并后保留 _bodies_s*.json 不归档")
    args = ap.parse_args()

    archive_dir = os.path.join(ROOT, "archive", f"{date.today().strftime('%Y%m%d')}-bodies-merged")
    for blogger in args.bloggers:
        r = merge_blogger(blogger)
        if r is None:
            continue
        print(f"{r['blogger']:<12} 总帖{r['total']:>5} | 回填正文{r['filled']:>5} "
              f"(bodies {r['bodies_entries']} 条) | 合并后仍标题型 {r['still_title_only']:>5}")
        if not args.keep_bodies:
            archive_bodies(blogger, archive_dir)
    if not args.keep_bodies:
        print(f"bodies 已归档至 archive/{os.path.basename(archive_dir)}/")


if __name__ == "__main__":
    sys.exit(main())
