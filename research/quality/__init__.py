# -*- coding: utf-8 -*-
"""research/quality — 单元②：综合波段信号的博主风格质量评估。

衡量"综合 2/3 共识信号"本身的方向性判断质量（非 trade-PnL）：每干净交易日取 swing 板
14:30 快照定调 → 一条方向预测，参考价=当日收盘、终点=决策日后第 5 交易日，score=d×ret×100，
聚合指标与 30 位成员博主的"波段档"子线同源同表对比。产物写本包 reports/。
"""
import os

QUALITY_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(QUALITY_DIR, "reports")


def ensure_reports():
    os.makedirs(REPORTS_DIR, exist_ok=True)
