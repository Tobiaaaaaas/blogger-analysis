# -*- coding: utf-8 -*-
"""简报状态持久化。

state.json 结构（2026-09 redesign v9：18 人窗口速览卡）：
{
  "last_run": "2026-09-02 09:30",   # 最近一次成功跑完的档（用作下一档爬虫增量 since）
  "last_slot": "morning",
  "seen": { "<博主>": { "<post_id>": <publish_time>, ... } }   # 保留：爬虫去重用（旧 v8 遗留）
}
旧 v8 的 recent_views / board_prev / previous 已被 v9 移除（见 run_briefing._migrate_state_v2）。
"""
import json
import os

from . import paths


def default_state() -> dict:
    return {"last_run": "", "last_slot": "", "seen": {}}


def load_state() -> dict:
    try:
        with open(paths.STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        st = default_state()
    st.setdefault("seen", {})
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
