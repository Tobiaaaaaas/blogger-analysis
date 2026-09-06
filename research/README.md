# research/ — 板块 2/3 信号 · 离线研究单元

`blogger_ana/research/` 把**推送信号**做成三类可复现研究（全离线、无外部调用、无密钥，只在 Mac
本地跑，不进 Windows 简报部署）：

- **单元① trade-PnL 回测**（`research/backtest/`）：当板块**看多表态占比 > 2/3 就买入持多，否则空仓**
  （卖出 = 平多仓持币、不做空）——问"按信号进出场能赚多少"。见本文件下文。
- **单元② 综合信号质量评估**（`research/quality/`）：把综合 2/3 共识信号当"预测"，按博主评价口径
  （score=d×return×100 → 平均分/正确率/波动率/夏普）在 +5 交易日固定周期上验证，并与 swing 板
  30 位成员博主的"波段档"同表对比——问"信号的方向性判断质量如何"。见 `research/quality/README.md`。
- **单元③ swing 波段滞回组合**（`research/combo/`）：波段收益口径 = **滞回共识**（Swing_Timing 主档）——
  看多比例 ρ>2/3 开多（both 且 ρ<1/3 开空）、持腿**跌破 1/2 才平**（[1/2,2/3] 滞回带续持）、开/平双门
  Q10；每日一票 @14:30 快照 → 15:00 收盘成交，信号=上证 · 交易=中证1000。含参数敏感性（run_hyst_sweep）
  与委员会换池（run_hyst_pool）。见 `research/combo/README.md`。

## 研究单元导览

根层 `config.py / trading_cal.py / corpus.py / poll.py` 是三单元共享的单一事实源（板块名单/网格/窗口、
交易历、逐档表态重建）；`signals/` 是三单元共用的归一化方向语料（40 人，只读）。

```
research/
├─ config.py trading_cal.py corpus.py poll.py     ← 根层共享（单一事实源）
├─ signals/  _manifest.json                        ← 40 人归一化语料（只读）
├─ backtest/                                       ← 单元① trade-PnL 回测
│  ├─ backtest.py  run_backtest.py  validate.py
│  └─ reports/  short_report.md + short_ticks.csv + short_trades.csv（+ _2way 双向）
├─ quality/                                        ← 单元② 综合信号质量评估
│  ├─ _run_direction.py  engine.py  member_bands.py  run_compare.py
│  ├─ README.md
│  └─ reports/  composite_swing_*  （成员侧 = swing 30 人，见 member_bands.py）
└─ combo/                                          ← 单元③ swing 波段滞回组合
   ├─ daygrid.py  hyst.py  run_hyst.py  run_hyst_sweep.py  run_hyst_pool.py
   ├─ README.md
   └─ reports/  combo_hyst.* + combo_hyst_sweep.* + combo_hyst_pool.*
```

**产物命名约定**：各单元产物只写进自己的 `reports/`，不跨单元 —— 回测 = `short_*`（`_2way`=双向对照、
`_ticks`=信号日志、`_trades`=成交明细）；quality = `composite_swing_*`；combo = `combo_hyst*`
（前缀固定，标各自产物）。

> 研究口径由用户 AskUserQuestion 逐项锁定，**勿擅自放宽**。任何口径改动都应先回来对齐。

## 为什么可回测：历史信号语料已成型

`research/signals/` 是 40 位板块成员（short 17 ∪ swing 30）的方向信号**可复用标准语料**（一次性构建，后续所有研究读它）：

- 源 = 父仓库 `data/direction_signals/{昵称}.json`（DeepSeek 既有抽取，**未重跑**），只读不改。
- 每条归一化补上 `board`（板块归属）与 `target/target_txt`（解析出的绝对目标日期 / 展示文案）。
- 共 **7021 条**（short 3194 / swing 3827；scored 6060 / unscored 961），发布区间 2026-01-01 → 09-05，
  无剔除。生成：`python -m research.corpus`（幂等，覆盖写 `signals/` + `_manifest.json`）。

**周期明确性标注规则**（用户强调——简报/报告里预测周期必须写清楚）：
- 超短行（spec today/t1，归 short 板）→ 目标必是 今天/明天，`target_txt` = 绝对日期 `MM-DD`；
- 波段行（t2+/week/nweek/nmonth/d:/long，归 swing 板）→ 有明确周段/日期就写（如 `本周（至 09-04）`），
  `long`（unscored）无期限 → 如实标 **`周期不明确`**，不编造目标日期。

## 板块 / 网格 / 窗口（单一事实源 = briefing/scripts/config）

| | short（超短） | swing（波段） |
|---|---|---|
| 板块名单 | PANEL_SHORT 17 人 | PANEL_SWING 30 人（40 人并集） |
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
   该语义是 research **全局统一**口径（口径主档 .claude/skills/analyze-blogger/Swing_Timing.md 同网格），
   所有消费方（backtest / quality / combo hyst·sweep·pool）同一 poll 实现、无第二份投票。
4. idx 口径默认只计 `上证指数`（探针显示换 any 几乎不变，config 可调）。
5. **覆盖缺口门**：方向抽取止于某博主最近信号日、而 `data/posts` 显示其后仍发帖（右侧漏抽），
   且漏抽帖可能进入窗口 → 该成员当日整档**不表态**、该日判不干净。语料 100% 覆盖的干净决策日：
   short **2026-01-05 → 08-27（158 日）**、swing **01-05 → 08-14（149 日）**。回测只跑干净日。

## 回测引擎 backtest.py + run_backtest.py（short 板块）

> 注（2026-09-06）：swing 逐档跟随（A 口径）已下线——本引擎只回测 **short（超短）板块**；
> swing 波段收益口径 = research/combo 滞回（run_hyst 每日一票 · 收盘成交，见下文单元③）。

- **触发**：表态者（多+空）≥ 3 且 多/(多+空) **> 2/3（严格）** → 持多；否则空仓。**分母 = 当日表态者**，
  非板块全员（全员口径 162 日一次都不触发——历史实测，勿回改）。
- **状态机**：`持多 ⇔ trigger`，每档复查，跌破阈值该档平仓、达标该档开仓，同日可多次进出；0/1 全仓。
- **成交**：instant（默认）= 决策时刻价（30 分 bar **time=区间终点**：bar 起点档用下一 bar open =
  该时点开盘，11:30/15:00 时段终用该档 close）；delayed = 拖后 30 分钟用其后 bar close（敏感性对照）。
- 样本末 15:00 强制平仓；日净值按 15:00 收盘盯市。费率 `--cost` 每边（默认 0）。

```bash
python -m research.backtest.run_backtest                # instant + 费率 0 → reports/ short_report.md + short_ticks.csv + short_trades.csv
python -m research.backtest.run_backtest --cost 0.0005   # 费率敏感性
python -m research.backtest.run_backtest --fill delayed  # 成交延迟敏感性（_delayed 后缀）
```

产物：`backtest/reports/short_report.md`（净值/仓位/逐月/完整往返明细）+ `short_ticks.csv`（信号日志）+ `short_trades.csv`。

**双向对照（看空 >2/3 也开空）**：加 `--allow-short`，输出落 `*_2way.*`：
```bash
python -m research.backtest.run_backtest --allow-short   # 双向 instant 费率0
python -m research.backtest.run_backtest --allow-short --cost 0.0005
```
做空口径 = 指数期货式线性收益（px 跌 d% 名义仓赚 d%，非反向杠杆复利）；未计融券费/保证金；
直接翻转按平旧+开新双边计费。默认（不加 flag）仍是用户锁定的"卖出=平多持币"。

## 验证 validate.py

```bash
python -m research.backtest.validate
```

全档位复算 poll 与 run() 行级比对（防漂移）、审计投票 pub 严格早于决策时刻（无未来函数）、
二次运行确定性与 fill×费率敏感性表（做多与双向两套）。当前输出：**全部通过 ✓**
（short 1580/1580 一致，pub≥决策 0 处）。

## 单元③ combo：swing 波段板共识的滞回组合（Swing_Timing 主档）

swing 波段板的**收益口径**统一 = 滞回共识。策略主档 =
[.claude/skills/analyze-blogger/Swing_Timing.md](../.claude/skills/analyze-blogger/Swing_Timing.md)
（活文档，本 README 不复述口径；旧版归档 [_archive/docs/hysteresis_consensus_spec.md](_archive/docs/hysteresis_consensus_spec.md)
参考）。投票宇宙 = swing 30 人。三跑法：

1. **canonical `run_hyst`**：每日一票 @14:30 快照 → 当日 15:00 收盘成交；开多 ρ>2/3、both 且 ρ<1/3 开空、
   持腿跌破 1/2 才平（[1/2,2/3] 滞回带续持）；Q10（开/平双门 e>10，无填充）与无门槛对照 × 仅做多/多空双向。
2. **`run_hyst_sweep`**：绕基线 w5 · Q10 · TO2/3 · TX1/2 的单轴 OAT + 精选跨轴角格（w/Q/开平阈值）敏感性。
3. **`run_hyst_pool`**：默认参数不动，只换波段委员会（现役 30 → 剔未达标尾 → 榜外替换 → 先验 top-k）对照。

```bash
python -m research.combo.run_hyst --check           # canonical → combo_hyst.{md,csv} + _trades.csv + _daily.csv + _pnl.png
python -m research.combo.run_hyst_sweep --check     # 敏感性 → combo_hyst_sweep.{md,csv}（基细胞 vs canonical 护栏）
python -m research.combo.run_hyst_pool --check      # 换池 → combo_hyst_pool.{md,csv}（S0 vs canonical 护栏）
```

**当前景观（样本内，非显著性）**：Q10 仅做多累计 **+17.79%**（基准中证1000 买持 +0.21%、超额 +17.58%、
Sharpe 2.35、MDD 4.8%、4 往返、胜率 100%、在场 30/149 根 K）；双向 **+42.86%**（超额 +42.65%、
Sharpe 3.23、MDD 4.7%、6 往返（多 4/空 2）、在场 56/149 根 K，指数期货式上限对照）。**Q10 与无门槛在
本样本行为全同**（0 分叉段，见 combo_hyst.md「双门实证」）。这是"更挑的信号 + 同一样本多重比较"
叠加出的表象，跨时段大概率均值回归——**live 口径要否改、改哪档，须另项评估，本单元只出研究结论**。
quality 专项榜成员宇宙 = swing 30 人。

## 结论速览

两套引擎**节奏/标的/基准都不同，不可互比**：
- **short 逐档**（instant · 费率 0 · 干净日样本）：30 分十档逐档跟随，基准 = 上证买持。
- **swing 滞回**（combo `run_hyst` · 每日一票 @14:30 → 15:00 收盘成交 · w=5 · 双门 Q10）：基准 = 中证1000
  买持（信号标的上证 · 30 人波段共识）。

**short（逐档引擎）**

| 模式 | 样本 | 策略累计 | 基准(买持) | 超额 | 日Sharpe(策略/基准) | 触发档 | 完整往返 | 胜率 | MDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 做多 | 158 日 | **+11.72%** | -0.76% | +12.49% | 1.99 / -0.09 | 470/1580（29.7%） | 43 | 62.8% | 2.72% |
| 双向 | 158 日 | **+17.18%** | -0.76% | +17.94% | 2.55 / -0.09 | 多470/空215 档 | 66（多43/空23） | 62.1% | 2.72% |

**swing（滞回 · Q10）**

| 模式 | 样本 | 策略累计 | 基准买持 | 超额 | 日Sharpe(策略/基准) | 往返(多/空) | 胜率 | MDD | 在场K |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 仅做多 | 149 日 | **+17.79%** | +0.21% | +17.58% | 2.35 / 0.16 | 4（4/0） | 100% | 4.8% | 30/149 |
| 多空双向 | 149 日 | **+42.86%** | +0.21% | +42.65% | 3.23 / 0.16 | 6（多4/空2） | 100% | 4.7% | 56/149 |

**short**：允许做空把空仓期里"看空 >2/3"的时段变成收益——超额 +12.49%→+17.94%（Sharpe 1.99→2.55），
做空腿为正贡献（23 个空往返净收益为正）。做空贡献对费率与成交假设更脆（见下），且未计融券成本；
short 双向在费率 0.0005 下仍 +9.70%。默认口径仍是只做多。

**swing（combo 滞回）**：仅做多 4 往返、胜率 100%、在场仅 30/149 根 K——多数时间空仓、只在共识高密时
在场，回撤（4.8%）远小于买持；往返从 2026-03-25 才开始（1–2 月零持仓，见 combo_hyst.md 逐笔/逐月）。
双向把空头时段也兑现 → +42.86%（指数期货式、未计融券费，仅上限方向对照）。**Q10 与无门槛在本样本
行为全同**：149 日里 e≤10 的拦截日仅 2 天、且那两天无需开/平动作 → Q 门没实际改变任何动作。

敏感性（validate 输出，short）：做多 费率 0.0005 → +7.02%、delayed → +8.26%；双向 费率 → +9.70%、
delayed → +12.79%（详见 validate 敏感性表）。在市时间短（short 做多 41/158 交易日 · 29.7% 档位；
双向 63/158 日 · 43.4% 档）——策略 ≈ 信号高密时在场、其余空仓，回撤显著小于买持。swing 滞回的其他
数字（w/Q/阈值敏感性、委员会换池）见 combo_hyst_sweep.* / combo_hyst_pool.*。

## 口径与风险（如实记录）

- **swing 收益口径已统一（2026-09-06）**：旧的 3 档逐档跟随引擎（backtest.py 的 swing 分支、run_backtest
  `--board swing`、validate 的 swing 行）与 q×k 网格组合研究（run_sweep/run_confirm/rules/sweep/render +
  combo_sweep_*/combo_confirm_* 产物）已下线删除——波段收益只看 research/combo 滞回（run_hyst ·
  Swing_Timing.md）。逐档 backtest.py 仅保留 short；其 clean_days/load_daily 仍被 quality/combo 复用。
- **语料 vs 实时卡的复刻边界**：回测以 `direction_signals` 的 spec 周期归属为据重述板块表态，语义等价
  于实时卡但非逐字同源（实时卡含 LLM 自由措辞）。结论以语料为准。
- **右侧漏抽**：约 7 位成员在最近数周 direction_signals 与 data/posts 有 gap（帖子没抽成信号），
  clean 区间因此止于 08-27/08-14 而非 09-02。补抽（`--runs 1`）后可自动前移端界。
- **左侧（入册前）**不视为漏抽：那是成员进名册前的历史，poll 按"该时期无信号 → 不表态"自然处理。
- **窗口收紧 vs 探针**：v14 交易日窗口（前1/前3）使内容面比自然日（2/5 日）窄，触发轮次略低于早前
  自然日探针，属用户选定语义，报告如实给分布。
- **做空（`--allow-short`）是理想化建模**：指数期货式线性收益、无融券费/保证金占用/强平、可随时按档
  开平空。实盘 A 股个股做空门槛与成本远高于此；做空腿对费率敏感（双向 flip 双边计费），报告中
  `_2way` 结果宜视为"信号方向被双向兑现"的上限估计。
