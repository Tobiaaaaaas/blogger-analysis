# -*- coding: utf-8 -*-
"""简报状态持久化。

state.json 结构（2026-09 v13：双板块两卡 + 盘中 30 分档）：
{
  "fetched_at": "2026-09-03 14:30",  # 爬虫水位：抓取+merge 成功即写（不等推送成败）；下一档 since
  "last_run":   "2026-09-03 14:30",  # 推送水位：本档全部板块推送尝试后写
  "last_slot":  "short,swing",       # v12 遗留键保留；v13 语义 = 本档推送的板块串（仅展示）
  "seen": { "<博主>": { "<post_id>": <publish_time>, ... } }   # 保留：旧 v8 遗留
}
旧 v8 的 recent_views / board_prev / previous 已被 v9 移除（见 run_briefing._migrate_state_v2）。
Windows 首次跑 v13 时若无 fetched_at，迁移函数回填 fetched_at = 旧 last_run（见 run_briefing._migrate_state_v3）。
"""
import json
import os

from . import paths


def default_state() -> dict:
    return {"fetched_at": "", "last_run": "", "last_slot": "", "seen": {}}


def load_state() -> dict:
    try:
        with open(paths.STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        st = default_state()
    st.setdefault("seen", {})
    st.setdefault("fetched_at", "")
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
