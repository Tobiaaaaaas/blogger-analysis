# research/quality/ — 单元②：综合波段信号质量评估（博主评价口径）

把"综合 2/3 共识信号"当**方向预测**来验证判断质量 —— 不是算策略钱（trade-PnL 回测在
`../backtest/` 问"按信号进出场能赚多少"；本单元问"信号本身看多/看空判得准不准"）。

**打分链与 analyze-blogger / `scripts/eval/run_direction.py` 同源**：每条信号
score = d×(终点收盘/参考价 − 1)×100 → 聚合出 平均分 / 正确率 / 波动率 / 夏普 / 看多·看空均分。

## 综合信号怎么取（每日一票 · 用户 AskUserQuestion 锁定口径，勿改）

- 只看 **swing 波段板（21 人共识）**，不看超短。
- 每"干净交易日"（语料 100% 覆盖 = 回测 `clean_days` 同一网格；现 swing 2026-01-05 → 08-17 共 150 日）
  取 **14:30 快照**定调（`poll_tick` 无未来函数：只取 pub 严格早于 14:30 的帖）。
- 触发：表态者（多+空）≥ 3 且 看多占比 **> 2/3（严格）** → 当日 **一条看多**（d=+1）；对称地
  看空 >2/3 → 看空（d=−1）；都不达 → 该日无信号。分母 = 当日**有波段观点者**（多+空）——
  投票 = poll 单条 last、swing 剔 spec=long、**无 mixed 概念**（research 全局统一语义，见 ../README）。
- 参考价 = 决策日上证 **15:00 收盘**（14:30 定调、收盘成交）；终点 = 决策日后**第 5 个交易日**（t5）收盘。
- 当前快照结果（2026-09-04 重跑）：**N=46**（看多 37 / 看空 9），全部可打分。若未来补抽让干净窗右移、
  尾部终点超出行情末端，会落 `note=待验证` 防御分支、不入聚合（engine.signal_rows 已留）。

## 对比榜（波段专项榜）

- **成员侧**：21 位 swing 板成员（`config.PANELS["swing"]` = 综合 2/3 票的投票人；short 专属 9 人
  ——三粒光/入竹风拂面画船听雨眠/大白白/波段研究师/纽约音乐厨房/股傲/要有心态/麟老哥/龙五——
  不出现在 swing 表决，故不进专项榜）。每博主读父仓库
  `data/direction_signals/{昵称}.json` 原始信号 → `run_direction.calc` 按其**自己的 spec 周期/目标指数**
  逐条打分 → 过滤 **波段档（span≥2 交易日）** → 聚合。与 comparison_all 波段档**同源重算**
  （成员侧抽样硬对拍 18/18 ✓）；**不读旧 reports md** —— 精度/资格边界差异见 member_bands.py 注释。
- 综合行并入同表，按**平均分**降序；资格 = N≥10 且 平均分>0.1（对齐 comparison_all 波段档），
  未达标者列榜尾并标注原因。
- ⚠️ **口径不对称**（报告已注明）：综合 = swing 21 人共识·上证指数·固定 t5 端点；成员 = 各自 spec /
  自身目标指数。同表比的是**方向性质量**，不是同一标的同一期限。综合看空腿仅 9 条，
  结论以均分/正确率措辞、注明样本小。

## 运行

```bash
python -m research.quality.run_compare            # 构建榜单 → 写 quality/reports/ 3 个产物
python -m research.quality.run_compare --check    # 先跑内联校验（无未来/触发复算/同源重算/确定性）再写
```

`--check` 逐信号日：① 计入票 pub 严格早于 14:30（无未来函数）；② 复算 14:30 触发方向 == 记录方向；
③ 终点恒晚于决策日且当前可打分；④ 每行 return/分 用独立 `load_daily` 重算对拍；⑤ 二次采样逐行一致。
两次独立运行产物字节一致（确定性）。

## 产物（quality/reports/，UTF-8；csv 用 utf-8-sig）

| 文件 | 内容 |
|---|---|
| `composite_swing_compare.md` | 波段专项榜（21 位 swing 成员 + 综合 ★）+ 口径注释 + 综合逐信号明细 + 关键读数 |
| `composite_swing_signals.csv` | 综合逐信号明细：`date,content,direction,strength,period,idx,ref,ep,ep_close,ret,score,note` |
| `composite_swing_compare.csv` | 榜单机器可读（含综合行）：`rank,name,type,n,acc,avg,vol,sharpe,bull_n,bull_avg,bear_n,bear_avg,qual` |

## 同源与复用（防第二份实现漂移）

- `_run_direction.py` = 唯一复用点：惰性 import `scripts/eval/run_direction`（模块级只读行情、无副作用），
  转发 `calc / bucket_of / acc_of / avg_of / vol_of / sharpe_of`。**不 import comparison_all**
  （模块级直接 open().write 榜文件，只可作规范参考）。
- `engine.summarize_metrics(rows)` = 全单元共用行→指标聚合器（综合行与 21 位成员行走同一函数，零漂移）。
- 干净日网格与日线收盘复用 `..backtest.backtest.clean_days / load_daily`（与回测单元同一数据源）。
