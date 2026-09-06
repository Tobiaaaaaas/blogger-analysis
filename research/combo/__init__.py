# -*- coding: utf-8 -*-
"""research/combo — 单元③：swing 波段板共识的滞回组合回测（策略主档 Swing_Timing）。

把"波段板看多比例 ρ 阈值 + 滞回平仓"规则化。规格活文档 =
.claude/skills/analyze-blogger/Swing_Timing.md（信号=上证指数观点 · 交易=中证1000）。
  · hyst.py + daygrid.py —— 滞回状态机与逐日共享上下文（每博主窗口内时间序最新一条，无 mixed）。
  · run_hyst.py —— canonical：每日 14:30 快照 → 15:00 收盘成交；开多 ρ>2/3 / both 开空 ρ<1/3；
    持腿跌破 1/2 才平（滞回带）；Q10（开/平双门 e>10）与无门槛对照 × long/both → combo_hyst.*。
  · run_hyst_sweep.py —— w / Q / 开平阈值单轴 OAT + 精选角格敏感性 → combo_hyst_sweep.*。
  · run_hyst_pool.py —— 波段委员会换池对照（S0 = 现役 30 人，护栏 Q10 四行 == canonical）→ combo_hyst_pool.*。
成员口径 = PANELS["swing"] 30 人。纯研究、离线、无密钥、不改 live briefing 口径。
产物写本包 reports/（combo_hyst* 前缀）。
"""
import os

COMBO_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(COMBO_DIR, "reports")


def ensure_reports():
    os.makedirs(REPORTS_DIR, exist_ok=True)
