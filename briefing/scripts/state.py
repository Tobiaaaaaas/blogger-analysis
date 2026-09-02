# -*- coding: utf-8 -*-
"""简报状态持久化。

state.json 结构：
{
  "last_run": "2026-09-01 10:00",
  "last_slot": "am",
  "seen": { "<博主>": { "<post_id>": <publish_time>, ... } },   # 已入简报的帖子（按时间裁剪）
  "recent_views": {                                               # 全板观点模型：每博主当前近期观点（发新帖即更新）
    "<博主>": {
      "stance": "多|空|中性", "strength": "强|中|弱", "horizon": "...",
      "quote": "...", "extreme": false, "summary": "...",
      "pub_ts": 1785000000                                        # 该观点来源帖的发布时间（7 天外视为过时，退出统计）
    }, ...
  },
  "previous": {                                                  # 上期卡片（状态延续用）
    "date": "2026-09-01",
    "slot": "am",
    "card": { ... 上期卡片 JSON ... },
    "consensus_text": "偏多，普遍看好企稳反弹",                  # 压缩后的上期共识一句话
    "key_bloggers_text": "云帆观市(多)…刘海娃娃(空)…"
  }
}
"""
import json
import os
import time

from . import paths

RETENTION_DAYS = 14  # seen 只保留最近 N 天的帖子 id，防止无限膨胀


def default_state() -> dict:
    return {"last_run": "", "last_slot": "", "seen": {}, "recent_views": {}, "previous": {}}


def load_state() -> dict:
    try:
        with open(paths.STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        st = default_state()
    st.setdefault("seen", {})
    st.setdefault("recent_views", {})
    st.setdefault("previous", {})
    st.setdefault("last_run", "")
    st.setdefault("last_slot", "")
    return st


def save_state(state: dict):
    paths.ensure_dirs()
    # 原子写：先落临时文件再 os.replace（Windows 上也是原子改名），写一半被杀死不损坏 state.json
    tmp = paths.STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, paths.STATE_FILE)


def mark_seen(state: dict, blogger: str, posts: list):
    """记录本批已消费的帖子 id。"""
    now = time.time()
    cutoff = now - RETENTION_DAYS * 86400
    seen = state["seen"].setdefault(blogger, {})
    for p in posts:
        pid = str(p.get("post_id", ""))
        if pid:
            seen[pid] = p.get("publish_time") or int(now)
    # 裁剪过期项
    state["seen"][blogger] = {k: v for k, v in seen.items() if v >= cutoff}


def seen_ids(state: dict, blogger: str) -> set:
    return set(state["seen"].get(blogger, {}).keys())
