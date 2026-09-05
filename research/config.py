# -*- coding: utf-8 -*-
"""research/ 包配置：路径解析 + 板块名单/参数（复用 briefing/scripts/config）+ 北京时间。

独立研究单元（在 Mac 本地跑，不进简报 Windows 部署）。数据源全部来自父仓库
（data/direction_signals、data/market、briefing/data/trade_calendar.json），
本包不产生任何外部调用、不接触密钥。

路径说明：RESEARCH_DIR = 本文件所在目录（= 父仓库根/blogger_ana/research）。
父仓库根 ROOT = RESEARCH_DIR 的上一级。briefing 在 ROOT/briefing，运行时把
ROOT/briefing 与 ROOT 加进 sys.path 以便 import briefing.scripts.*。
"""
import os
import sys
import datetime
from fractions import Fraction

RESEARCH_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(RESEARCH_DIR)

for _p in (ROOT, os.path.join(ROOT, "briefing")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 北京时间（A 股时段；简报系统同用 BEIJING_TZ）
BEIJING_TZ = datetime.timezone(datetime.timedelta(hours=8))

# ---- 回测参数（用户锁定口径，勿擅自放宽）----
THRESHOLD = 2 / 3          # 看多占比 > 2/3 触发持多（严格大于）
MIN_EXPRESSED = 3          # 当日有方向表态者 ≥3 才算有效轮次（分母 = 当日表态者 多+空）
IDX_DEFAULT = "上证指数"    # 默认只计上证指数信号（探针：换 any 口径结果几乎不变）
COST_DEFAULT = 0.0          # 每边费率（小数，如 0.0005）；默认 0

# ---- 滞回共识策略参数（单源；规格 .claude/skills/analyze-blogger/Swing_Timing.md §1）----
# 所有开/平/持条件只落「看多比例 ρ = 多方观点/e」一根轴；空头阈值 = 1−多头 镜像派生。
HYST_TO_LONG = Fraction(2, 3)    # 看多开仓线 ρ>TO_LONG → 开多（严格）
HYST_TX_LONG = Fraction(1, 2)    # 多腿平仓线 ρ<TX_LONG（且 e>Q_exit）→ 平多；恰 1/2 续持
HYST_Q_OPEN = 10                 # 开仓法定人数：需 e > Q_open（严格，至少 Q+1 人表态）；不足 → 当日不开、持币
HYST_Q_EXIT = 10                 # 平仓法定人数：需 e > Q_exit；不足 → 当日不平、持仓走滞回
HYST_WINDOW = 5                  # 滞回验证决策窗口 w（交易日）；run_hyst 启动时置入 WINDOW_TRADING_DAYS["swing"]

# 决策区间（direction_signals 自 2026-01 起、行情至 2026-09-02 的可行交集）
START_DATE = "2026-01-05"
END_DATE = "2026-09-02"

# ---- 板块名单 / 网格 / 窗口（从 briefing/scripts/config 导入，单一事实源）----
from briefing.scripts import config as _bcfg  # noqa: E402

PANELS = _bcfg.PANELS                 # {"short": [...], "swing": [...]}
BOARD_WORD = _bcfg.BOARD_WORD          # {"short": "超短", "swing": "波段"}
WINDOW_TRADING_DAYS = _bcfg.WINDOW_TRADING_DAYS   # {"short": 1, "swing": 3}

# 决策网格：short = 30 分档 10 档/日；swing = SWING_TICKS 3 档/日
TRADING_TICKS = list(_bcfg.TRADING_TICKS)
SWING_TICKS = sorted(_bcfg.SWING_TICKS)
GRID_TICKS = {"short": TRADING_TICKS, "swing": SWING_TICKS}

# ---- 父仓库数据路径 ----
DATA_SIGNALS_DIR = os.path.join(ROOT, "data", "direction_signals")   # 信号语料源（只读）
POSTS_DIR = os.path.join(ROOT, "data", "posts")                       # 原始帖（覆盖审计用，只读）
INTRADAY_FILE = os.path.join(ROOT, "data", "market", "intraday", f"{IDX_DEFAULT}_30min.json")
DAILY_FILE = os.path.join(ROOT, "data", "market", "market_data.json")
SIGNALS_OUT_DIR = os.path.join(RESEARCH_DIR, "signals")               # 规范化语料输出
REPORTS_DIR = os.path.join(RESEARCH_DIR, "backtest", "reports")       # 单元①回测产物（backtest/ 子包自带 reports/）


def ensure_dirs():
    os.makedirs(SIGNALS_OUT_DIR, exist_ok=True)
    os.makedirs(REPORTS_DIR, exist_ok=True)


# spec 周期 → 中文（与 eval/run_direction.SPEC_TEXT 同口径）
SPEC_TEXT = {"today": "今天", "t1": "明天", "t2": "后天/1-2天", "t3": "未来几天",
             "t5": "近期/短期/无周期方向", "t10": "10天后", "week": "本周",
             "nweek": "下周", "nweek_first": "下周初", "month": "月底前",
             "nmonth": "下个月", "long": "长期"}
# 超短板块 = today/t1（都是 scored）；波段板块 = 其余（scored t2+… 或 unscored=long）
SHORT_SPECS = {"today", "t1"}


def board_of_spec(spec: str) -> str:
    """信号周期归属板块：today/t1 → short；其余（t2+/week/nweek/nmonth/d:/long）→ swing。"""
    return "short" if spec in SHORT_SPECS else "swing"
