# -*- coding: utf-8 -*-
"""简报系统配置：追踪名单、推送时段、时段标签。"""
from datetime import datetime

# ── 追踪名单（top 30：综合口径 = 显式周期 + 无周期方向 nd 合并，按 t 值排序前 30，2026-09-01 快照，固定）──
TRACKED = [
    "云帆观市", "红红火火的老牛哥", "大盘蜂向标", "衡山佛曰论股", "趋势巡航",
    "四十二流光", "白猫财眼", "一只小小牛", "龙五", "诸葛不亮",
    "山顶望星空的诗人", "三粒光", "强哥解盘", "麟老哥", "时间轨迹",
    "江河之水终有入海之日", "知情达理星空hnR", "爱生活的荷叶Rp", "智由智哉", "大白白",
    "刘海娃娃", "TL阳光", "股评老陈", "财牛", "谭阿坤",
    "波段研究师", "时空鹰眼", "实盘指龙副号", "柚来又去", "鸟瞰股市",
]

# ── 推送时段（交易日 7 推；非交易日仅 evening）──
# slot_key: (HH:MM, 卡片标题用词)
SLOTS = {
    "early_morning": ("09:15", "盘前"),
    "am":            ("10:00", "上午盘中"),
    "am2":           ("11:00", "午前盘中"),
    "pm_pre":        ("12:45", "午后盘前"),
    "pm":            ("14:00", "下午盘中"),
    "close":         ("15:00", "收盘"),
    "evening":       ("20:00", "晚间"),
}

SLOT_TOLERANCE_MIN = 6  # cron 触发时间与槽位时间 ±6 分钟内视为命中


def slot_for(now: datetime, trading_day: bool) -> str | None:
    """给定时刻 + 是否交易日，返回应触发的 slot_key；非推送时刻返回 None。"""
    hm = now.strftime("%H:%M")
    now_min = now.hour * 60 + now.minute
    for key, (slot_hm, _label) in SLOTS.items():
        if not trading_day and key != "evening":
            continue  # 非交易日只保留晚间
        h, m = map(int, slot_hm.split(":"))
        slot_min = h * 60 + m
        if abs(now_min - slot_min) <= SLOT_TOLERANCE_MIN:
            return key
    return None
