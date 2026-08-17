#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""帖子完整性二次检查（爬取后、评估前必做）

用法: python scripts/utils/verify_posts.py <博主名> [--since YYYY-MM-DD]
输出: 逐项 ✅/⚠️ + 汇总 VERIFY_RESULT
退出码: 0 = 无硬失败（⚠️ 允许）；1 = 有硬失败（需重爬/修数据）；2 = 用法错误

判定原则：
- 硬失败（客观数据损坏）：重复 post_id / 空字段 / 覆盖 miss（翻页异常停且未覆盖到 since）
- 仅提示（可能正常）：标题型帖多、按日缺口、数量回落、备份少帖（博主删帖/停更/纯视频均属正常）
"""
import argparse
import glob
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(ROOT, "data", "posts")
BACKUP_DIR = os.path.join(DATA_DIR, "_backup")

DEFAULT_SINCE = "2026-01-01"   # 信号范围起点
TITLE_ONLY_LEN = 60            # content 短于此且非视频帖 → 标题型帖，需补正文
EMPTY_CONTENT_LEN = 5          # content 去空格短于此 → 空字段硬失败
GAP_DAYS = 7                   # 按日缺口阈值（博主停更属正常，仅提示）
REGRESSION_RATIO = 0.9         # 新抓取数量 < 备份此比例 → 提示
SINCE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 翻页停止原因（scrape_toutiao.py 写入 result["stop_reason"]）
NATURAL_STOPS = {"since", "no_more", "no_cursor", "target"}       # 自然停：已到起点/无更多内容
TRUNCATED_STOPS = {"api_empty", "js_error", "status_abnormal", "empty_streak", "max_pages"}  # 异常停：可能截断


def fmt(n, hard, warn):
    if hard:
        return f"❌ {n}. 硬失败：{hard}"
    return f"✅ {n}. {warn}"


def main():
    ap = argparse.ArgumentParser(description="帖子完整性二次检查")
    ap.add_argument("blogger", help="博主名（对应 data/posts/<博主名>.json）")
    ap.add_argument("--since", default=None, help=f"覆盖起点 YYYY-MM-DD（默认 {DEFAULT_SINCE}）")
    args = ap.parse_args()
    since = args.since or DEFAULT_SINCE
    if not SINCE_RE.match(since):
        print(f"错误: --since 需为 YYYY-MM-DD 格式（收到: {since}）")
        sys.exit(2)

    path = os.path.join(DATA_DIR, f"{args.blogger}.json")
    if not os.path.exists(path):
        print(f"错误: 帖子文件不存在 {path}")
        sys.exit(2)

    data = json.load(open(path, encoding="utf-8"))
    posts = data.get("posts") if isinstance(data, dict) else data
    if posts is None:
        print(f"错误: {path} 缺少 posts 字段")
        sys.exit(2)

    hard = 0
    warn = 0
    n = 0

    def emit(msg, is_hard):
        nonlocal hard, warn, n
        n += 1
        if is_hard:
            hard += 1
            print(f"  [{n}] ❌ {msg}")
        else:
            warn += 1
            print(f"  [{n}] ⚠️ {msg}")

    def emit_ok(msg):
        nonlocal n
        n += 1
        print(f"  [{n}] ✅ {msg}")

    print(f"== 二次检查: {args.blogger} == {path}")
    print(f"   文件抓取时间: {data.get('scrape_time', '未知')} | 停止原因: {data.get('stop_reason', '无记录（旧文件）')}")

    # ── [1] 基础信息 ──
    total_field = data.get("total_posts") if isinstance(data, dict) else None
    n_meta = n
    if total_field is not None and total_field != len(posts):
        emit(f"total_posts 字段（{total_field}）≠ 实际帖子数（{len(posts)}）", is_hard=False)
    if not posts:
        emit("posts 为空：未抓到任何帖子", is_hard=True)
    else:
        dates = sorted(p["publish_date"][:10] for p in posts if p.get("publish_date"))
        if dates:
            actual = (dates[0], dates[-1])
            tr = data.get("time_range") or {}
            if tr and (tr.get("earliest") != actual[0] or tr.get("latest") != actual[1]):
                emit(f"time_range（{tr.get('earliest')} ~ {tr.get('latest')}）与实际（{actual[0]} ~ {actual[1]}）不一致", is_hard=False)
    if n == n_meta:  # 本项无任何问题
        emit_ok("基础信息一致（total_posts / time_range）")

    # ── [2] 重复 post_id ──
    ids = [p.get("post_id", "") for p in posts]
    dups = {k: v for k, v in Counter(ids).items() if v > 1}
    if dups:
        sample = "、".join(f"{k}×{v}" for k, v in list(dups.items())[:5])
        emit(f"重复 post_id {len(dups)} 组（{sample}…）：数据损坏，需重新爬取", is_hard=True)
    else:
        emit_ok("无重复 post_id")

    # ── [3] 空字段 ──
    empty_id = sum(1 for p in posts if not p.get("post_id"))
    empty_date = sum(1 for p in posts if not p.get("publish_date"))
    empty_content = sum(1 for p in posts if len(str(p.get("content", "")).strip()) < EMPTY_CONTENT_LEN)
    if empty_id or empty_date or empty_content:
        detail = f"空 post_id {empty_id} / 空 publish_date {empty_date} / content<{EMPTY_CONTENT_LEN}字 {empty_content}"
        emit(f"{detail}：空 publish_date 的帖子会被信号提取静默丢弃，需重新爬取", is_hard=True)
    else:
        emit_ok("无空字段（post_id / publish_date / content）")

    # ── [4] 正文完整性 ──
    title_only = [p for p in posts if len(str(p.get("content", ""))) < TITLE_ONLY_LEN and p.get("content") != "[视频帖]"]
    shards = glob.glob(os.path.join(DATA_DIR, f"{args.blogger}_bodies_s*.json"))
    if shards:
        emit(f"存在未合并正文分片（{os.path.basename(shards[0])} 等 {len(shards)} 份）：先跑 merge_bodies_to_posts.py", is_hard=False)
    if title_only:
        emit(f"{len(title_only)}/{len(posts)} 条为标题型帖（正文<{TITLE_ONLY_LEN}字）：未合并正文，先 fetch_bodies_shard.py + merge 后重跑校验", is_hard=False)
    if not shards and not title_only:
        emit_ok("正文完整（无标题型帖）")

    # ── [5] 覆盖检查（--since） ──
    dates = sorted(p["publish_date"][:10] for p in posts if p.get("publish_date"))
    if not dates:
        emit("无任何 publish_date，无法判读覆盖", is_hard=True)
    else:
        earliest = dates[0]
        sr = data.get("stop_reason") if isinstance(data, dict) else None
        if earliest <= since:
            emit_ok(f"日期覆盖到 {earliest} ≤ since（{since}）")
        elif sr in NATURAL_STOPS:
            emit_ok(f"earliest（{earliest}）晚于 since（{since}），但停止原因={sr}（自然停，since 前确实无帖）")
        elif sr in TRUNCATED_STOPS:
            emit(f"earliest（{earliest}）晚于 since（{since}）且停止原因={sr}（异常停）：翻页提前停止，可能漏帖，需重新爬取（必要时 --force）", is_hard=True)
        else:
            emit(f"earliest（{earliest}）晚于 since（{since}），停止原因={sr or '无记录'}：无法判读是否漏帖，建议重爬后复核", is_hard=False)

    # ── [6] 按日缺口（停更属正常，仅提示） ──
    if dates:
        present = set(dates)
        gaps = []
        cur = None
        day = datetime.strptime(dates[0], "%Y-%m-%d")
        end = datetime.strptime(dates[-1], "%Y-%m-%d")
        while day <= end:
            ds = day.strftime("%Y-%m-%d")
            if ds in present:
                if cur is not None and cur >= GAP_DAYS:
                    gaps.append((cur, ds))
                cur = 0
            else:
                if cur is not None:
                    cur += 1
                else:
                    cur = 1
            day += timedelta(days=1)
        gaps.sort(reverse=True)
        if gaps:
            sample = "、".join(f"{d}前缺{n}天" for n, d in gaps[:5])
            emit(f"存在 ≥{GAP_DAYS} 天的按日缺口 {len(gaps)} 段（{sample}）：博主停更属正常，仅提示", is_hard=False)
        else:
            emit_ok("无 ≥7 天的按日缺口")

    # ── [7] 备份回归 ──
    backups = sorted(glob.glob(os.path.join(BACKUP_DIR, f"{args.blogger}_*.json")), key=os.path.getmtime, reverse=True)
    if not backups:
        emit_ok("无备份可比较（data/posts/_backup/ 为空）")
    else:
        backup = json.load(open(backups[0], encoding="utf-8"))
        b_posts = backup.get("posts") if isinstance(backup, dict) else backup
        if b_posts is None:
            emit(f"备份 {os.path.basename(backups[0])} 缺少 posts 字段，无法比较", is_hard=False)
        else:
            cutoff = since if args.since else None
            if cutoff:
                new_sub = [p for p in posts if p.get("publish_date", "")[:10] >= cutoff]
                bak_sub = [p for p in b_posts if p.get("publish_date", "")[:10] >= cutoff]
            else:
                new_sub, bak_sub = posts, b_posts
            if bak_sub and len(new_sub) < REGRESSION_RATIO * len(bak_sub):
                emit(f"新抓取 {len(new_sub)} 条 < 备份 {len(bak_sub)} 条 × {REGRESSION_RATIO:.0%}（{os.path.basename(backups[0])}）：大幅缩水，确认后再继续", is_hard=False)
            bak_ids = {p.get("post_id") for p in bak_sub if p.get("post_id")}
            new_ids = {p.get("post_id") for p in new_sub if p.get("post_id")}
            missing = bak_ids - new_ids
            if missing:
                emit(f"较最新备份丢失 {len(missing)} 条帖子（如 {sorted(missing)[:3]}…）：可能被删/被截断，必要时从备份恢复", is_hard=False)
            if not bak_sub or (len(new_sub) >= REGRESSION_RATIO * len(bak_sub) and not missing):
                emit_ok(f"与最新备份（{os.path.basename(backups[0])}）对比正常")

    # ── 汇总 ──
    print("-" * 68)
    print(f"结果: {warn} 项 ⚠️ / {hard} 项硬失败")
    print(f"VERIFY_RESULT hard={hard} warn={warn}")
    sys.exit(1 if hard else 0)


if __name__ == "__main__":
    main()
