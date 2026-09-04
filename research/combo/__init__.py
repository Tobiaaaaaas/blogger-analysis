# -*- coding: utf-8 -*-
"""research/combo — 单元③：swing 板块共识的组合规则寻优（阈值 q × 表态规模 k 网格）。

把"看多占比 >2/3 且表态者≥3"泛化成网格扫描，问有没有更好的组合方式。两关：
  第一关 run_sweep —— 每日一票 @14:30 → +5 交易日质量评估，扫 q×k 全网格（含基线格），
    行为去重后同表呈现，附资格门槛 N≥20 与上/下半期稳健列。
  第二关 run_confirm —— 从网格短名单（基线 + N≥30/20 均分最优 + N≥20 夏普最优）跑 swing
    trade-PnL 确认是否真赚钱。
成员口径 = PANELS["swing"] 21 人（swing 板 2/3 票的投票人）。纯研究、离线、无密钥、不改
live briefing 口径。产物写本包 reports/（combo_* 前缀）。
"""
import os

COMBO_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(COMBO_DIR, "reports")


def ensure_reports():
    os.makedirs(REPORTS_DIR, exist_ok=True)
