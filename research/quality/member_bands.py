# -*- coding: utf-8 -*-
"""research/quality/member_bands.py — 30 位 swing 板成员的"波段档"子线（与 comparison_all 同源重算）。

成员宇宙 = PANELS["swing"] 30 人（swing 板投综合 2/3 票的就是这批人；short 专属 10 人不出现在
swing 表决里，故不进专项榜）。每博主：读 data/direction_signals/{昵称}.json 原始信号 →
run_direction.calc（30 分口径逐条打分）→ 过滤 score 非 None 且 bucket_of(r) ==
"波段（2个交易日及以上）"（span≥2，SKILL §8 两档归类）→ 得到该博主全部"波段方向预测"。
聚合用 engine.summarize_metrics —— 与综合行同一套 _run_direction 同源函数，零漂移。

不读旧 reports/*_direction.md / comparison_direction.md：① 其波段表缺组内 看多/看空 计数与均分
（专项榜需要这两列）；② 波段表只在 eligible（帖子跨度≥6月 且 2026 信号>10）博主上输出，
如 微风3241 等不 eligible 无表；③ md 只留打印精度，vol/sharpe 有截断漂移。
"""
import json
import os

from .. import config
from . import _run_direction as dr
from .engine import summarize_metrics

MEMBERS = sorted(set(config.PANELS["swing"]))   # 30 人（swing 板；short 专属不在此投票）
BAND_BUCKET = "波段（2个交易日及以上）"    # run_direction.bucket_of 的波段档返回值


def band_rows(blogger):
    fp = os.path.join(config.DATA_SIGNALS_DIR, f"{blogger}.json")
    if not os.path.exists(fp):
        return []
    sigs = json.load(open(fp, encoding="utf-8")).get("signals", [])
    out = []
    for s in sigs:
        r = dr.calc(s)
        if r.get("score") is None:
            continue
        if dr.bucket_of(r) != BAND_BUCKET:
            continue
        out.append(r)
    return out


def band_metrics(blogger):
    return summarize_metrics(band_rows(blogger))


def qualified(m):
    """波段档上榜资格（对齐 comparison_all 波段档：BUCKET_MIN_SIGNALS=10、AVG_MIN=0.1）。"""
    return m["n"] >= 10 and m["avg"] > 0.1
