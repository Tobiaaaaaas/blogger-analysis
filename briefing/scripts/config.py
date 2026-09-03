# -*- coding: utf-8 -*-
"""简报系统配置：推送名单、推送时段、时段标签、展示窗口常量。"""
from datetime import datetime

# ── 推送名单（18 人固定，2026-09 快照；"衡山佛曰论股"以"曰"为准）──
ROSTER = [
    "云帆观市", "时间轨迹", "山顶望星空的诗人", "一只小小牛", "孙万林",
    "衡山佛曰论股", "四十二流光", "三粒光", "波段研究师", "强哥解盘",
    "知情达理星空hnR", "顺应周期", "白猫财眼", "股评老陈", "大盘蜂向标",
    "红红火火的老牛哥", "微风3241", "爱生活的荷叶Rp",
]
TRACKED = ROSTER  # LEGACY：旧 v8 共识卡/画像曾引用 config.TRACKED，保持同指 18 人

# ── 推送时段（交易日 3 推；非交易日不推）──
# slot_key: (HH:MM, 卡片标题用词)
SLOTS = {
    "morning":   ("09:30", "早盘"),
    "afternoon": ("13:00", "午后"),
    "late":      ("14:30", "尾盘"),
}

SLOT_TOLERANCE_MIN = 6  # cron 触发时间与槽位时间 ±6 分钟内视为命中

# ── 展示窗口 / 行抽取常量 ──
WINDOW_TRADING_DAYS = 3      # 每博主取"过去 N 个交易日"内发表的观点
ROWS_MAX_POSTS = 5           # 每博主喂给 LLM 的窗口内帖子上限（最新 N 条，新→旧）
ROWS_BATCH_BLOGGERS = 2      # summarize 每次 DeepSeek 调用放进几位博主
ROWS_WORKERS = 3             # 行抽取并行线程数
ROWS_NO_VIEW_TEXT = "近3日无观点更新"
BUCKET_SHORT = "超短(0-1日)"  # 今天/明天
BUCKET_SWING = "波段(2日+)"   # 后天及更远 / 本周 / 下周 / 更长 / 有方向无周期


def slot_for(now: datetime, trading_day: bool) -> str | None:
    """给定时刻 + 是否交易日，返回应触发的 slot_key；非交易日恒 None。"""
    now_min = now.hour * 60 + now.minute
    for key, (slot_hm, _label) in SLOTS.items():
        h, m = map(int, slot_hm.split(":"))
        slot_min = h * 60 + m
        if abs(now_min - slot_min) <= SLOT_TOLERANCE_MIN:
            return key
    return None


def format_counts(counts: dict) -> str:
    """分档计数 → 角标/兜底文案（超短/波段各自多空 + 观望 + 无更新）。

    全链路唯一文案源：render 角标、dry-run 预览、总结失败兜底共用，保证口径不漂移。
    counts shape 见 summarize.count_rows：{"short":{"bull","bear"}, "swing":{"bull","bear"},
    "neutral", "none"}。缺键按 0 兜底。
    """
    counts = counts or {}
    s = counts.get("short") or {}
    w = counts.get("swing") or {}
    return (f"超短(0-1日) {s.get('bull', 0)}多/{s.get('bear', 0)}空 · "
            f"波段(2日+) {w.get('bull', 0)}多/{w.get('bear', 0)}空 · "
            f"观望 {counts.get('neutral', 0)} · 无更新 {counts.get('none', 0)}")
