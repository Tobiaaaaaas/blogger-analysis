# -*- coding: utf-8 -*-
"""增量抓取：对追踪博主跑短窗口爬虫 → 新帖 merge 进主文件 → 补齐正文。

复用父仓库三件套（subprocess / import）：
  1. scrape_toutiao.py --since <上次> --force --out <临时窗文件>   （window 文件，不碰主文件）
  2. fetch_bodies_shard.py <博主> 0 1 <日期>                        （done-dict 增量补正文）
  3. merge_bodies_to_posts.merge_blogger()                          （正文回填主文件）

返回本时段的新帖列表（content 已尽可能补齐全文），供简报 LLM 直接阅读。
"""
import json
import logging
import os
import shutil
import subprocess
import sys
import time

from . import paths

log = logging.getLogger("briefing")


def _run(cmd, timeout):
    try:
        # Windows 管道下子进程默认 GBK/cp1252，中文/emoji 打印会崩；显式 UTF-8 收发
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                           timeout=timeout)
        tail = (r.stdout or "")[-400:]
        return r.returncode, tail
    except subprocess.TimeoutExpired:
        return -1, f"超时({timeout}s)"
    except Exception as e:
        return -2, str(e)


def _seed_url(posts_file):
    """取一个可用于启动爬虫的该博主帖子链接。"""
    try:
        with open(posts_file, encoding="utf-8") as f:
            d = json.load(f)
        posts = d.get("posts") or []
        for p in posts:
            if p.get("url"):
                return p["url"], d
        if d.get("source_url"):
            return d["source_url"], d
    except Exception:
        pass
    return None, None


def _merge_window(main_file, window_file):
    """把 window 文件里的新帖（按 post_id 去重）并入主文件，返回 (新帖列表, 主数据 dict)。"""
    with open(main_file, encoding="utf-8") as f:
        main = json.load(f)
    with open(window_file, encoding="utf-8") as f:
        win = json.load(f)

    existing = {}
    for p in main.get("posts", []):
        existing[p.get("post_id")] = p

    new_posts = []
    for p in win.get("posts", []):
        pid = p.get("post_id")
        if not pid or pid in existing:
            continue
        existing[pid] = p
        new_posts.append(p)

    if not new_posts:
        return [], main, new_posts

    posts = sorted(existing.values(), key=lambda x: x.get("publish_time") or 0, reverse=True)
    main["posts"] = posts
    main["total_posts"] = len(posts)
    ts = [p.get("publish_time") for p in posts if p.get("publish_time")]
    main["time_range"] = {
        "earliest": time.strftime("%Y-%m-%d", time.localtime(min(ts))) if ts else "",
        "latest": time.strftime("%Y-%m-%d", time.localtime(max(ts))) if ts else "",
    }
    main["scrape_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
    main["stop_reason"] = "briefing_merge"
    return new_posts, main, new_posts


def fetch_blogger_new_posts(blogger, since_str, timeout=240):
    """抓取单个博主自 since 以来的新帖并合并。返回 (new_posts, error_str)。

    new_posts: 本时段新增帖子（可能含未补正文的短标题帖，随后由补正文步骤填充）。
    """
    main_file = os.path.join(paths.POSTS_DIR, f"{blogger}.json")
    if not os.path.exists(main_file):
        return [], f"缺主文件 data/posts/{blogger}.json"

    url, _ = _seed_url(main_file)
    if not url:
        return [], "主文件无可用帖子链接"

    os.makedirs(os.path.join(paths.DATA_DIR, "windows"), exist_ok=True)
    window_file = os.path.join(paths.DATA_DIR, "windows", f"{blogger}.json")
    # 窗口已存在（上次失败残留）则删除重抓
    if os.path.exists(window_file):
        os.remove(window_file)

    cmd = [sys.executable, paths.SCRAPE_SCRIPT, url,
           "--name", blogger, "--since", since_str, "--force", "--out", window_file]
    code, tail = _run(cmd, timeout)
    if code != 0:
        return [], f"爬虫退出 code={code}：{tail}"

    # 名称不匹配时爬虫会写 <name>_feed_check.json 而不是 --out 文件
    if not os.path.exists(window_file):
        feed_check = os.path.join(paths.POSTS_DIR, f"{blogger}_feed_check.json")
        if os.path.exists(feed_check):
            os.remove(feed_check)
            return [], f"feed 主体用户与 {blogger} 不一致（疑抓错账号）"
        return [], "爬虫未产出窗口文件"

    new_posts, main, _ = _merge_window(main_file, window_file)
    os.remove(window_file)
    if not new_posts:
        return [], ""  # 无新帖不是错误

    # 备份主文件后写回
    backup_dir = os.path.join(paths.POSTS_DIR, "_backup")
    os.makedirs(backup_dir, exist_ok=True)
    shutil.copy2(main_file, os.path.join(
        backup_dir, f"{blogger}_briefing_{time.strftime('%Y%m%d_%H%M%S')}.json"))
    with open(main_file, "w", encoding="utf-8") as f:
        json.dump(main, f, ensure_ascii=False, indent=2)

    # 补正文（done-dict 增量）：先跑 fetch_bodies_shard，再 merge_blogger 回填
    since_date = since_str[:10]
    code, tail = _run([sys.executable, paths.BODIES_SCRIPT, blogger, "0", "1", since_date], timeout=600)
    if code == 0:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("merge_bodies", paths.MERGE_BODIES_MOD)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            r = mod.merge_blogger(blogger)
            if r:
                log.debug("  正文回填 %s: %s", blogger, r)
        except Exception as e:
            log.warning("  正文回填失败 %s: %s", blogger, e)
    else:
        log.warning("  fetch_bodies_shard %s 退出 code=%s，正文可能未补齐: %s", blogger, code, tail)

    # 重新读取主文件，返回补齐后的新帖
    with open(main_file, encoding="utf-8") as f:
        main = json.load(f)
    by_id = {p["post_id"]: p for p in main.get("posts", [])}
    refreshed = [by_id.get(p["post_id"]) for p in new_posts if p.get("post_id") in by_id]
    return [p for p in refreshed if p], ""


def fetch_all_new_posts(tracked, since_str, max_bloggers=None, per_timeout=240):
    """对所有追踪博主增量抓取，返回 {blogger: [new_posts]} 与失败列表。"""
    results, errors = {}, []
    if max_bloggers:
        tracked = tracked[:max_bloggers]
    for i, blogger in enumerate(tracked, 1):
        log.info("  [%d/%d] 抓取 %s (since=%s)", i, len(tracked), blogger, since_str)
        try:
            new_posts, err = fetch_blogger_new_posts(blogger, since_str, timeout=per_timeout)
        except Exception as e:
            err = f"异常: {e}"
            new_posts = []
        if err:
            errors.append((blogger, err))
            log.warning("  ⚠️ %s 抓取失败: %s", blogger, err)
        if new_posts:
            results[blogger] = new_posts
            log.info("    ✅ 新增 %d 条", len(new_posts))
        time.sleep(1)  # 博主间节流
    return results, errors
