# -*- coding: utf-8 -*-
"""简报系统配置：双板块名单、板块口径、盘中 30 分网格、双 webhook、窗口常量。

v14（2026-09-04）：展示窗口由自然日改为**交易日口径**（用户修正）：
  - 超短板块（17）：扫 WINDOW_TRADING_DAYS["short"]=1 → 前一交易日 00:00 起至现在的
    帖子内的 今天/明天 表态，只显示 目标日∈{卡片日,下一交易日} 者；盘中每 30 分钟一档
    （TRADING_TICKS 10 档）。
  - 波段板块（21）：扫 WINDOW_TRADING_DAYS["swing"]=3 → 前 3 个交易日 00:00 起至现在的
    帖子内的 近日/本周/下周/更长 表态，本周/下周 锚定发帖日所在周一~五周、目标周已过剔除；
    无明确周期的表态（近日/更长/未提）如实标注、不编造目标日期；
    仅在 SWING_TICKS（09:30/11:00/14:30）三档推送。
  - 两板块各自一张卡、各自收敛总结，各推各的飞书群（WEBHOOK_ENV）。
  - 交易日窗口消除了原自然日取舍：周一早晨回看窗口含上周五帖（起点=前一交易日 00:00）。
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
BOARD_WORD = {"short": "超短", "swing": "波段"}  # 卡标题用词（区别于 label 里的括号周期说明）

# 板块展示元信息（标题/emoji/空板块提示；v14 空板文案随交易日窗口口径）
BOARD_META = {
    "short": {"label": "超短(0-1日)", "emoji": "⏱️",
              "empty_note": "（前一交易日起的表态无指向今/下一交易日的超短方向）"},
    "swing": {"label": "波段(2日+)", "emoji": "🌊",
              "empty_note": "（前3个交易日起无人给出波段方向观点）"},
}

# 抓取/读帖全集：两板块去重（超短板块原序 + 波段板块新增第 9 位起）
ALL_BLOGGERS = PANEL_SHORT + [b for b in PANEL_SWING if b not in PANEL_SHORT]

TRACKED = ALL_BLOGGERS  # LEGACY：旧 v8 共识卡/画像曾引用 config.TRACKED，泛指"跟踪名单"

# ── 展示窗口（v14）：交易日回看天数 → 窗口起点 = 前一/前N个交易日 00:00（非自然日）──
# 窗口内容面 = 前 N 个交易日的全天帖 + 今日盘中至 now；now 非交易日按最近交易日取参考日。
# 由此周一早晨的窗口天然含上周五帖（消除 v13 自然日口径的"周一漏上周五帖"取舍）。
WINDOW_TRADING_DAYS = {"short": 1, "swing": 3}

# ── 盘中 30 分网格（v13，取代 v12 的 3 档 SLOTS）──
# 连续竞价时段 09:30–11:30 与 13:00–15:00，每 30 分钟整点一档（10 档）。
TRADING_TICKS = ["09:30", "10:00", "10:30", "11:00", "11:30",
                 "13:00", "13:30", "14:00", "14:30", "15:00"]
SWING_TICKS = {"09:30", "11:00", "14:30"}  # 波段仅在这 3 档额外推（⊂ TRADING_TICKS）

# 波段各档的标题用词（swing 只在 3 档出现；超短一律"盘中"+时刻）
_SWING_TICK_WORD = {"09:30": "早盘", "11:00": "午前", "14:30": "尾盘"}

# ── 双 webhook（v13）：各板块推各群的飞书机器人 ──
WEBHOOK_ENV = {"short": "FEISHU_WEBHOOK_URL",     # 旧群（原综合卡群 → 现收超短卡）
               "swing": "FEISHU_WEBHOOK_URL_SWING"}  # 新群（波段卡）

# ── 行抽取常量 ──
ROWS_MAX_POSTS = 8              # 每博主喂给 LLM 的窗口内帖子上限（最新 N 条，新→旧）
ROWS_BATCH_BLOGGERS = 2         # summarize 每次 DeepSeek 调用放进几位博主
ROWS_WORKERS = 3                # 行抽取并行线程数


def in_intraday_grid(dt: datetime) -> bool:
    """快速门禁：给定北京时时刻是否落在盘中 30 分网格上（:00/:30 且 ∈盘中两段）。

    用于调度伪 tick 挡板：12:00/12:30/15:30、StartWhenAvailable 离网格补跑，
    都在抓取/锁之前由此 exit 0（本函数不自行 exit，交给调用方判断）。
    """
    if dt.minute not in (0, 30):
        return False
    m = dt.hour * 60 + dt.minute
    return (570 <= m <= 690) or (780 <= m <= 900)  # 09:30–11:30 / 13:00–15:00 闭区间


def due_boards(dt: datetime) -> list:
    """墙钟（已过 in_intraday_grid 门禁）→ 应推板块：超短每档都推；波段仅 SWING_TICKS。"""
    boards = ["short"]
    if dt.strftime("%H:%M") in SWING_TICKS:
        boards.append("swing")
    return boards


def board_title(board_key: str, date_str: str, hm: str) -> str:
    """单板块卡标题：含板块与时刻。如 '📊 超短盘中 10:00 · 09-03 周四' /
    '📊 波段尾盘 14:30 · 09-03 周四'。hm = 'HH:MM'。"""
    if board_key == "swing":
        seg = _SWING_TICK_WORD.get(hm, "盘中")
    else:
        seg = "盘中"
    return f"📊 {BOARD_WORD[board_key]}{seg} {hm} · {date_str}"


def format_board_count(board_key: str, c: dict) -> str:
    """单板块计数行（v13 全链路唯一文案源之一）：板块头行见 render._board_section_lines。

    c 形如 {bull,bear,shown,members}（summarize.board_counts 单板块子集）。
    例：'⏱️ 超短(0-1日) 3多/2空（5/17 表态）'
    """
    meta = BOARD_META[board_key]
    return (f"{meta['emoji']} {meta['label']} "
            f"{c.get('bull', 0)}多/{c.get('bear', 0)}空"
            f"（{c.get('shown', 0)}/{c.get('members', 0)} 表态）")


def format_board_counts(counts: dict) -> str:
    """LEGACY：v12 双板块合并一行计数（超短…/波段…），v13 单板块卡不再用（保留供历史复刻）。"""
    s = (counts or {}).get("short") or {}
    w = (counts or {}).get("swing") or {}
    return (f"超短(0-1日) {s.get('bull', 0)}多/{s.get('bear', 0)}空"
            f"（{s.get('shown', 0)}/{s.get('members', 0)} 表态） · "
            f"波段(2日+) {w.get('bull', 0)}多/{w.get('bear', 0)}空"
            f"（{w.get('shown', 0)}/{w.get('members', 0)} 表态）")
