# -*- coding: utf-8 -*-
"""research/backtest — 单元①：超短（short）板块 2/3 信号 trade-PnL 回测。

见 backtest.py（引擎）/ run_backtest.py（CLI）/ validate.py（验证）。产物写本包 reports/。
注（2026-09-06）：swing 逐档跟随（A 口径）已下线——swing 收益口径 = research/combo 滞回
（run_hyst / Swing_Timing.md）；backtest.py 的 clean_days/load_daily 仍被 quality/combo 复用。
"""
