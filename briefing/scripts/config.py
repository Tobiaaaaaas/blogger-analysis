# -*- coding: utf-8 -*-
"""简报系统配置：双板块名单、板块口径、推送时段、窗口常量。

v11（2026-09-03）：速览卡 =「超短板块 + 波段板块」两个固定名单。
  - 超短板块：只取 今天/明天(0-1日) 表态，回看近 SHORT_WINDOW_TRADING_DAYS 个交易日。
  - 波段板块：只取 近日/本周/下周/更长(结构性中期) 表态，回看近 SWING_WINDOW_CAL_DAYS 个自然日。
  - 两板块前 8 位"双板块博主"重复出现：对同一博主按各自板块口径独立抽取，两块都可成行。
名单来自 reports/top20_值得关注博主.md 双榜（超短榜 / 中期榜）；顺序即板块内展示顺序。
"""
from datetime import datetime

# ── 超短板块（17）：只看 今天/明天 方向观点 ──
PANEL_SHORT = [
    "云帆观市", "红红火火的老牛哥", "白猫财眼", "大盘蜂向标", "一只小小牛",
    "孙万林", "顺应周期", "山顶望星空的诗人",
    "入竹风拂面画船听雨眠", "大白白", "龙五", "三粒光", "麟老哥",
    "波段研究师", "纽约音乐厨房", "股傲", "要有心态",
]

# ── 波段板块（21）：只看 波段(近日/本周/下周/更长) 方向观点 ──
PANEL_SWING = [
    "云帆观市", "红红火火的老牛哥", "白猫财眼", "大盘蜂向标", "一只小小牛",
    "孙万林", "顺应周期", "山顶望星空的诗人",
    "香满衣", "智由智哉", "股评老陈", "趋势巡航", "谭阿坤", "时间轨迹",
    "刘海娃娃", "四十二流光", "诸葛不亮", "衡山佛曰论股", "微风3241",
    "爱生活的荷叶Rp", "江河之水终有入海之日",
]

PANEL_KEYS = ("short", "swing")
PANELS = {"short": PANEL_SHORT, "swing": PANEL_SWING}

# 板块展示元信息（标题/emoji/空板块提示）
BOARD_META = {
    "short": {"label": "超短(0-1日)", "emoji": "⏱️",
              "empty_note": "（近3日无人给出超短方向观点）"},
    "swing": {"label": "波段(2日+)", "emoji": "🌊",
              "empty_note": "（近7日无人给出波段方向观点）"},
}

# 抓取/读帖全集：两板块去重（超短板块原序 + 波段板块新增第 9 位起）
ALL_BLOGGERS = PANEL_SHORT + [b for b in PANEL_SWING if b not in PANEL_SHORT]

TRACKED = ALL_BLOGGERS  # LEGACY：旧 v8 共识卡/画像曾引用 config.TRACKED，泛指"跟踪名单"

# ── 推送时段（交易日 3 推；非交易日不推）──
# slot_key: (HH:MM, 卡片标题用词)
SLOTS = {
    "morning":   ("09:30", "早盘"),
    "afternoon": ("13:00", "午后"),
    "late":      ("14:30", "尾盘"),
}

SLOT_TOLERANCE_MIN = 6  # cron 触发时间与槽位时间 ±6 分钟内视为命中

# ── 展示窗口 / 行抽取常量 ──
SHORT_WINDOW_TRADING_DAYS = 3   # 超短板块：回看近 N 个交易日
SWING_WINDOW_CAL_DAYS = 7       # 波段板块：回看近 N 个自然日（覆盖周末，约 5-6 交易日）
ROWS_MAX_POSTS = 8              # 每博主喂给 LLM 的窗口内帖子上限（最新 N 条，新→旧）
ROWS_BATCH_BLOGGERS = 2         # summarize 每次 DeepSeek 调用放进几位博主
ROWS_WORKERS = 3                # 行抽取并行线程数


def slot_for(now: datetime, trading_day: bool) -> str | None:
    """给定时刻 + 是否交易日，返回应触发的 slot_key；非交易日恒 None。"""
    now_min = now.hour * 60 + now.minute
    for key, (slot_hm, _label) in SLOTS.items():
        h, m = map(int, slot_hm.split(":"))
        slot_min = h * 60 + m
        if abs(now_min - slot_min) <= SLOT_TOLERANCE_MIN:
            return key
    return None


def format_board_counts(counts: dict) -> str:
    """双板块计数 → 文本（摘要注入/兜底/最小卡）。

    全链路唯一文案源之一：板块头行见 render._board_header；此处是"汇总一行"版。
    counts shape 见 summarize.board_counts：{key: {"bull","bear","shown","members"}}。
    """
    counts = counts or {}
    s = counts.get("short") or {}
    w = counts.get("swing") or {}
    return (f"超短(0-1日) {s.get('bull', 0)}多/{s.get('bear', 0)}空"
            f"（{s.get('shown', 0)}/{s.get('members', 0)} 表态） · "
            f"波段(2日+) {w.get('bull', 0)}多/{w.get('bear', 0)}空"
            f"（{w.get('shown', 0)}/{w.get('members', 0)} 表态）")
