#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重爬后恢复旧文件正文：从 _backup/<名>_<ts>.json 抽出有效正文 → 生成 <名>_bodies_s0.json → merge_bodies_to_posts 回填。

背景: 重爬用 feed API 只回标题，覆盖会丢旧文件里已抓的正文。本脚本在重爬完成后执行，
把旧文件里有正文（content>=60 非占位）的帖子按 post_id 抽出，写成标准 bodies 分片格式，
再走 merge_bodies_to_posts.py 合并（标题存 title、正文回填 content）。

用法: python scripts/utils/restore_bodies_from_backup.py <博主名> [备份文件名]
  - 默认取 _backup/ 里最新一份；也可显式指定备份文件路径
"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
POSTS_DIR = os.path.join(ROOT, "data", "posts")


def _placeholder(b):
    if not b:
        return True
    if len(b) <= 20:
        return True
    if "登录" in b and "验证码" in b:
        return True
    return False


def main():
    name = sys.argv[1]
    backup = sys.argv[2] if len(sys.argv) > 2 else None
    if not backup:
        cands = sorted(glob.glob(os.path.join(POSTS_DIR, "_backup", f"{name}_*.json")))
        if not cands:
            print(f"ERROR: 无 {name} 备份"); sys.exit(1)
        backup = cands[-1]
    print(f"备份: {backup}")
    old = json.load(open(backup, encoding="utf-8"))
    old_posts = old.get("posts") or []
    # 抽出有效正文
    bodies = {}
    for p in old_posts:
        pid = str(p.get("post_id") or "")
        c = p.get("content") or ""
        if pid and not _placeholder(c) and "[视频帖]" not in c:
            bodies[pid] = {"title": c, "body": c, "url": p.get("url", "")}
    out = os.path.join(POSTS_DIR, f"{name}_bodies_s0.json")
    json.dump(bodies, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"恢复 {len(bodies)} 条正文 → {os.path.basename(out)}")
    print(f"下一步: python scripts/utils/merge_bodies_to_posts.py {name}")


if __name__ == "__main__":
    main()
