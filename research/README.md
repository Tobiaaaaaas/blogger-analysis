# research/ — 板块 2/3 信号 · 离线研究单元

`blogger_ana/research/` 把**推送信号**做成两类可复现研究（全离线、无外部调用、无密钥，只在 Mac
本地跑，不进 Windows 简报部署）：

- **单元① trade-PnL 回测**（`research/backtest/`）：当板块**看多表态占比 > 2/3 就买入持多，否则空仓**
  （卖出 = 平多仓持币、不做空）——问"按信号进出场能赚多少"。见本文件下文。
- **单元② 综合信号质量评估**（`research/quality/`）：把综合 2/3 共识信号当"预测"，按博主评价口径
  （score=d×return×100 → 平均分/正确率/波动率/夏普）在 +5 交易日固定周期上验证，并与 swing 板
  21 位成员博主的"波段档"同表对比——问"信号的方向性判断质量如何"。见 `research/quality/README.md`。
- **单元③ 组合规则寻优**（`research/combo/`）：2/3 只是拍脑袋的默认——把共识泛化成 **阈值 q ×
  最低表态数 k** 网格（每日一票·+5交易日质量评估扫全网格，短名单再跑 swing trade-PnL 确认）
  ——问"有没有更好的信号组合方式"。见 `research/combo/README.md`。

## 研究单元导览

根层 `config.py / trading_cal.py / corpus.py / poll.py` 是两单元共享的单一事实源（板块名单/网格/窗口、
交易历、逐档表态重建）；`signals/` 是两单元共用的归一化方向语料（30 人，只读）。

```
research/
├─ config.py trading_cal.py corpus.py poll.py     ← 根层共享（单一事实源）
├─ signals/  _manifest.json                        ← 30 人归一化语料（只读）
├─ backtest/                                       ← 单元① trade-PnL 回测
│  ├─ backtest.py  run_backtest.py  validate.py
│  └─ reports/  short_*/swing_*   （含 _2way 双向、_ticks、_trades）
├─ quality/                                        ← 单元② 综合信号质量评估
│  ├─ _run_direction.py  engine.py  member_bands.py  run_compare.py
│  ├─ README.md
│  └─ reports/  composite_swing_*  （成员侧 = swing 21 人，见 member_bands.py）
└─ combo/                                          ← 单元③ 组合规则寻优
   ├─ rules.py  daygrid.py  engine.py  sweep.py  render.py
   ├─ run_sweep.py  run_confirm.py
   ├─ README.md
   └─ reports/  combo_sweep.{md,csv} + combo_sweep_grid.csv + combo_confirm.{md,csv}
```

**产物命名约定**：各单元产物只写进自己的 `reports/`，不跨单元 —— 回测 = `short_/swing_*`
（`_2way`=双向对照、`_ticks`=信号日志、`_trades`=成交明细）；quality = `composite_swing_*`；
combo = `combo_sweep_*` + `combo_confirm.*`（前缀固定，标各自产物）。

> 研究口径由用户 AskUserQuestion 逐项锁定，**勿擅自放宽**。任何口径改动都应先回来对齐。

## 为什么可回测：历史信号语料已成型

`research/signals/` 是 30 位板块成员的方向信号**可复用标准语料**（一次性构建，后续所有研究读它）：

- 源 = 父仓库 `data/direction_signals/{昵称}.json`（DeepSeek 既有抽取，**未重跑**），只读不改。
- 每条归一化补上 `board`（板块归属）与 `target/target_txt`（解析出的绝对目标日期 / 展示文案）。
- 共 **6044 条**（short 2936 / swing 3108；scored 5334 / unscored 710），发布区间 2026-01-01 → 09-02，
  无剔除。生成：`python -m research.corpus`（幂等，覆盖写 `signals/` + `_manifest.json`）。

**周期明确性标注规则**（用户强调——简报/报告里预测周期必须写清楚）：
- 超短行（spec today/t1，归 short 板）→ 目标必是 今天/明天，`target_txt` = 绝对日期 `MM-DD`；
- 波段行（t2+/week/nweek/nmonth/d:/long，归 swing 板）→ 有明确周段/日期就写（如 `本周（至 09-04）`），
  `long`（unscored）无期限 → 如实标 **`周期不明确`**，不编造目标日期。

## 板块 / 网格 / 窗口（单一事实源 = briefing/scripts/config）

| | short（超短） | swing（波段） |
|---|---|---|
| 板块名单 | PANEL_SHORT 17 人 | PANEL_SWING 21 人（30 人并集） |
| 决策档位 | 30 分一档 09:30~15:00 **10 档/日** | 09:30 / 11:00 / 14:30 **3 档/日** |
| 回看窗口 | **前 1 个交易日** 00:00 → now | **前 3 个交易日** 00:00 → now |
| spec 归属 | today / t1 | 其余（t2+… scored 或 unscored long） |

窗口用**交易日**口径（v14，非自然日）：周一早晨能看到上周五发的"看周一"帖。周末/盘前按最近交易日算。

## 逐档表态重建 poll.py（回测 = 简报当时会推什么）

对每个决策时刻 dt 重建该板块快照（与实时卡口径一致）：
1. 窗口 = [决策日往前 N 个交易日的 00:00, dt)，只取 `pub 严格早于 dt` 的帖（无未来函数）。
2. 每博主在窗口内取 **时间序最新一条**该板块有效候选（目标未过）→ 一票多/空；swing 投票候选
   **剔除 spec=long**（长线/年度目标不算波段观点；超短 today/t1 天然不含 long）。单条即票 →
   **无 mixed / 无"双向组"概念**（同 pub 无秒级时间，按语料行序取最后一条）。
   该语义是 research **全局统一**新口径（口径主档 .claude/skills/analyze-blogger/Swing_Timing.md 同网格），
   所有消费方（backtest / quality / combo sweep·confirm·hyst）同一 poll 实现、无第二份投票。
4. idx 口径默认只计 `上证指数`（探针显示换 any 几乎不变，config 可调）。
5. **覆盖缺口门**：方向抽取止于某博主最近信号日、而 `data/posts` 显示其后仍发帖（右侧漏抽），
   且漏抽帖可能进入窗口 → 该成员当日整档**不表态**、该日判不干净。语料 100% 覆盖的干净决策日：
   short **2026-01-05 → 08-27（158 日）**、swing **01-05 → 08-17（150 日）**。回测只跑干净日。

## 回测引擎 backtest.py + run_backtest.py

- **触发**：表态者（多+空）≥ 3 且 多/(多+空) **> 2/3（严格）** → 持多；否则空仓。**分母 = 当日表态者**，
  非板块全员（全员口径 162 日一次都不触发——历史实测，勿回改）。
- **状态机**：`持多 ⇔ trigger`，每档复查，跌破阈值该档平仓、达标该档开仓，同日可多次进出；0/1 全仓。
- **成交**：instant（默认）= 决策时刻价（30 分 bar **time=区间终点**：bar 起点档用下一 bar open =
  该时点开盘，11:30/15:00 时段终用该档 close）；delayed = 拖后 30 分钟用其后 bar close（敏感性对照）。
- 样本末 15:00 强制平仓；日净值按 15:00 收盘盯市。费率 `--cost` 每边（默认 0）。

```bash
python -m research.backtest.run_backtest --board both        # instant + 费率 0 → backtest/reports/ 两套 md+csv
python -m research.backtest.run_backtest --board short --cost 0.0005  # 费率敏感性
python -m research.backtest.run_backtest --board swing --fill delayed # 成交延迟敏感性（_delayed 后缀）
```

产物：`backtest/reports/{short|swing}_report.md`（净值/仓位/逐月/完整往返明细）+ `*_ticks.csv`（信号日志）+ `*_trades.csv`。

**双向对照（看空 >2/3 也开空）**：加 `--allow-short`，输出落 `*_2way.*`：
```bash
python -m research.backtest.run_backtest --board both --allow-short   # 双向 instant 费率0
python -m research.backtest.run_backtest --board short --allow-short --cost 0.0005
```
做空口径 = 指数期货式线性收益（px 跌 d% 名义仓赚 d%，非反向杠杆复利）；未计融券费/保证金；
直接翻转按平旧+开新双边计费。默认（不加 flag）仍是用户锁定的"卖出=平多持币"。

## 验证 validate.py

```bash
python -m research.backtest.validate
```

全档位复算 poll 与 run() 行级比对（防漂移）、审计投票 pub 严格早于决策时刻（无未来函数）、
二次运行确定性与 fill×费率敏感性表（做多与双向两套）。当前输出：**全部通过 ✓**
（做多/双向各 1580/1580、450/450 一致，pub≥决策 0 处）。

## 单元③ combo：swing 共识的组合规则寻优（q×k 网格 + PnL 确认）

回测/质量都用"看多 >2/3 且 表态≥3"（用户也承认拍脑袋）。combo 把它泛化成 **阈值 q × 最低表态 k**
（q ∈ {1/2, 3/5, 2/3, 7/10, 3/4, 4/5, 9/10}、k ∈ {1,2,3,4,5,7}，Fraction 严格大于、对称看空）扫网格
找更好的组合。只针对 **swing 波段板（21 人投票宇宙）**。两关：
1. **第一关 run_sweep**：每日一票 @14:30 → +5交易日质量评估（150 干净日共享一份 14:30 快照）扫
   **42 原始格**，按逐信号日方向向量**行为去重**归并成有效族（本样本 k 惰性 → 7 族、每阈值 q 一族）。
2. **第二关 run_confirm**：短名单 = 基线(2/3,3) + N≥30 平均分最优（N≥20 夏普最优并入若同族）——
   本样本 = 基线 + q=7/10,k=3（2 条，见 combo_sweep.md「关键读数」）。放回 swing trade-PnL
   （引擎 3 档/日原节奏；`backtest.py` 加可选 `decide` 参数，`decide=None` 路径逐字节不变）。

```bash
python -m research.combo.run_sweep --check          # 全网格 → reports/combo_sweep.{md,csv} + combo_sweep_grid.csv
python -m research.combo.run_confirm --check        # 短名单 PnL → reports/combo_confirm.{md,csv}（--allow-short 加双向）
```

**附加研究③ · 滞回平仓规则**（每日一票·收盘成交、开/平双门无填充、w=5）：口径主档 =
[.claude/skills/analyze-blogger/Swing_Timing.md](../.claude/skills/analyze-blogger/Swing_Timing.md)（活文档，
随迭代更新）；含当前结果/复现/待办的完整旧版已归档 [_archive/docs/hysteresis_consensus_spec.md](_archive/docs/hysteresis_consensus_spec.md)
（参考），产物在 combo/reports/combo_hyst.*，本 README 不复述。

**当前景观（探索性，非显著性）**：阈值越严 → N 越少、样本内均分/PnL 往往越好。q=7/10,k=3（N=30，
质量均分 +0.98 / 夏普 +0.60）两关都优于基线 2/3（N=46：质量 +0.75 / +0.46；PnL 仅做多 +2.92% vs +1.96%、
Sharpe 0.92 vs 0.51、MDD 3.5% vs 4.4%）；q=3/4（N=15）质量 +1.12 最高但 N<20 不足第二关资格。
这是"更挑的信号 + 同一样本多重比较"叠加出的表象，跨时段大概率均值回归——**live 口径要否改、
改哪档，须另项评估，本单元只出研究结论**。quality 专项榜成员宇宙已收窄为 swing 21 人。

## 结论速览（instant、费率 0，干净日样本）

| 板块 | 样本 | 策略累计 | 基准(买持) | 超额 | 日Sharpe(策略/基准) | 触发档 | 完整往返 | 胜率 | MDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| short 做多 | 158 日 | **+11.72%** | -0.76% | +12.49% | 1.99 / -0.09 | 470/1580（29.7%） | 43 | 62.8% | 2.72% |
| short 双向 | 158 日 | **+17.18%** | -0.76% | +17.94% | 2.55 / -0.09 | 多470/空215 档 | 66（多43/空23） | 62.1% | 2.72% |
| swing 做多 | 150 日 | **+1.96%** | -0.11% | +2.07% | 0.51 / -0.03 | 107/450（23.8%） | 24 | 58.3% | 4.43% |
| swing 双向 | 150 日 | **+0.43%** | -0.11% | +0.54% | 0.13 / -0.03 | 多107/空35 档 | 34（多24/空10） | 52.9% | 5.00% |

**双向 vs 只做多**：允许做空把空仓期的一部分（看空 >2/3 的时段）变成收益——short 超额
+12.49%→+17.94%（Sharpe 1.99→2.55）；swing +2.07%→+0.54%（Sharpe 0.51→0.13），做空腿样本更薄
（35 档、9 个空日）且不赚反拖累。做空贡献对费率与成交假设**更脆**（见下），且未计融券成本；
swing 双向在费率 0.0005 下转负。默认口径仍是只做多。

敏感性（validate 输出）：short 做多 费率 0.0005 → +7.02%、delayed → +8.26%；**short 双向** 费率 →
+9.70%、delayed → +12.79%。swing 做多 费率 → -0.46%、delayed → +5.25%；**swing 双向** 费率 → -2.93%、
delayed → +2.06%（详见 validate 输出的敏感性表）。在市时间短（short 做多 41/158 交易日 · 29.7% 档位；
双向 63/158 日 · 43.4% 档；swing 做多 37/150 日 · 23.8% 档；双向 46/150 日 · 31.6% 档）——策略 ≈
信号高密时在场、其余空仓，回撤显著小于买持。

## 口径与风险（如实记录）

- **语料 vs 实时卡的复刻边界**：回测以 `direction_signals` 的 spec 周期归属为据重述板块表态，语义等价
  于实时卡但非逐字同源（实时卡含 LLM 自由措辞）。结论以语料为准。
- **右侧漏抽**：约 7 位成员在最近数周 direction_signals 与 data/posts 有 gap（帖子没抽成信号），
  clean 区间因此止于 08-27/08-17 而非 09-02。补抽（`--runs 1`）后可自动前移端界。
- **左侧（入册前）**不视为漏抽：那是成员进名册前的历史，poll 按"该时期无信号 → 不表态"自然处理。
- **窗口收紧 vs 探针**：v14 交易日窗口（前1/前3）使内容面比自然日（2/5 日）窄，触发轮次略低于早前
  自然日探针，属用户选定语义，报告如实给分布。
- **做空（`--allow-short`）是理想化建模**：指数期货式线性收益、无融券费/保证金占用/强平、可随时按档
  开平空。实盘 A 股个股做空门槛与成本远高于此；做空腿对费率敏感（双向 flip 双边计费），报告中
  `_2way` 结果宜视为"信号方向被双向兑现"的上限估计。
