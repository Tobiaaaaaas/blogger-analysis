# research/combo/ — 单元③：swing 波段板共识的滞回组合回测

回测与 live 推送里的波段共识锁定口径 = **滞回**：看多比例 ρ 突破阈值开仓、跌破另一阈值才平，
中间的 [1/2, 2/3] 滞回带继续持有（策略规格主档 = [.claude/skills/analyze-blogger/Swing_Timing.md](../../.claude/skills/analyze-blogger/Swing_Timing.md)，
活文档，勿在 README 复述口径；含当前结果/复现的完整旧版已归档
[_archive/docs/hysteresis_consensus_spec.md](../_archive/docs/hysteresis_consensus_spec.md)，参考）。

**只针对 swing 波段板**：投票宇宙 = `PANELS["swing"]` 30 人（每日一票取的就是这批人），
short 专属成员不在 swing 表决。纯研究、离线、无密钥、不改 live briefing 口径。

> 注（2026-09-06）：swing 收益口径统一 = 本单元滞回（run_hyst 每日一票 · 收盘成交）。
> 旧的 3 档逐档跟随（w=3）trade-PnL 引擎已整体下线，不再有第二套 swing 收益数可比。

## 引擎与 Runner

| 脚本 | 问的问题 | 说明 |
|---|---|---|
| `hyst.py` | 滞回状态机（`hyst_policy`/`_decide`） | 开多 ρ>2/3 · both 开空 ρ<1/3 · 持腿跌破 1/2 才平；整数 Fraction 精确比较、绝不用 float；`metrics()` 汇总指标 |
| `daygrid.py` | 全干净日 14:30 快照一次预计算 | 每干净日一份 `DayContext`，各 runner 共享，避免重复快照偏差 |
| `run_hyst.py` | **canonical**：这套规则赚不赚？ | Q10（开/平双门 e>10）与无门槛(fixed) 对照 × 仅做多/多空双向 四组 → `combo_hyst.*` |
| `run_hyst_sweep.py` | 参数敏感性：w×Q×开/平阈值 | 绕基线 **w5·Q10·TO2/3·TX1/2** 单轴 OAT + 精选跨轴角格 → `combo_hyst_sweep.*` |
| `run_hyst_pool.py` | 委员会换池：换波段博主影响多大？ | 默认参数全不动，唯一自变量 = 博主池（现役 30 → 剔尾 → 榜外替换 → 先验 top-k）→ `combo_hyst_pool.*` |

## 口径（一句话版）

- 信号/投票 = **上证指数**观点：每博主窗口内**时间序最新一条**波段观点（剔 spec=long、无 mixed）；
  看多比例 ρ = bull/e，e = 当日有波段观点者（多+空）。
- 判定 = 每干净交易日一次 **14:30 快照** → 当日 **15:00 收盘**成交；净值/成交/买持基准 = **交易标的
  中证1000**（Swing_Timing §1 信号/交易标的分离）。成本 0、全仓 0/1、样本末末日收盘强平。
- 阈值：开多 ρ>2/3（严格）、both 开空 ρ<1/3；平仓要**跌破** 1/2（持多 ρ<1/2 / 持空 ρ>1/2，
  各需 e>Q_exit）；恰 50% 续持（滞回带，不平不开）。
- 法定人数：Q10 = 开仓 e>Q_open=10、平仓 e>Q_exit=10（严格，无 50% 填充/冻结；e 不足 → 当日不动）；
  fixed = (0,0) 无门槛对照。

## 产物与命令

```bash
python -m research.combo.run_hyst --check           # canonical → combo_hyst.{md,csv} + _trades.csv + _daily.csv + _pnl.png
python -m research.combo.run_hyst_sweep --check     # 参数敏感性 OAT → combo_hyst_sweep.{md,csv}（基细胞 vs canonical 护栏）
python -m research.combo.run_hyst_pool --check      # 委员会换池 → combo_hyst_pool.{md,csv}（S0 vs canonical 护栏）
```

| 产物（combo/reports/，UTF-8；csv 用 utf-8-sig） | 内容 |
|---|---|
| `combo_hyst.md` / `.csv` | canonical 指标表（Q10/fixed × long/both：累计/年化/超额/Sharpe/MDD/往返/胜率/在场K/均持K）+ 双门实证 + 逐笔 + 逐月 |
| `combo_hyst_daily.csv` | Q10 日净值曲线（long/both 逐日 nav + 持仓态） |
| `combo_hyst_trades.csv` | Q10 · 多空双向逐笔往返 |
| `combo_hyst_pnl.png` | PnL 图（Q10 实线 both/long + fixed 虚线对照 + 买持基准） |
| `combo_hyst_sweep.md` / `.csv` | 敏感性（**单轴 OAT+角格**：w∈{3,5,7,10} × Q 对称/不对称 × TO/TX，23 cell × long/both 46 行；单样本勿改 live 口径） |
| `combo_hyst_pool.md` / `.csv` | 换池（9 池 × long/both 18 行：S0 现役 30 → X29 → X28（三条件达标 28）→ SWAP30（X28∪榜外 财牛/拉着幸福手）→ TK12/15/18/21/24 先验 top-k；固定公共 149 干净日） |

## 回归护栏（各 runner `--check` 必跑，不过拒写）

- `run_hyst`：确定性双跑 + 结构不变式（navs 长度/正性/n_days）+ §2 整数边界断言（`hyst.assert_policy_edges`）。
- `run_hyst_sweep`：基细胞（S0 / fixed，Q10/fixed × long/both 四行）与 canonical `combo_hyst.csv`
  逐字段（round 同精度后）一致；抽 3 cell 复跑对 navs；§2 边界断言。
- `run_hyst_pool`：S0 行复现 canonical combo_hyst.csv Q10；各池 ctxs 恒 n 日且 date 与 S0 逐日一致；
  确定性双跑 + §2 边界断言。**不改 canonical combo_hyst.\*、不动 live PANEL_SWING。**

## 读法警示（重要）

- 单一样本（约 7 个月、一段行情干净日）+ 多 cell/多池同时比较 → **多重比较风险**：
  "最优格/最优池"的领先量只有 1~2 笔往返的分量，换行情可能反转，勿据此改 live 口径。
- 阈值/小池越严 → 动作越少、单笔越挑 —— 属样本内择优回填表象，跨时段大概率均值回归。
- `run_hyst_pool` 的池构造依据仓库质量先验、与回测落在**同一语料窗** → 用先验选池再回测 =
  样本内选择，天然利好；若日后想动委员会需另起 OOS 评估（见 memory hyst-pool-experiment）。
- 多空双向为指数期货式线性收益、未计融券费/保证金，只是上限方向对照；做空腿对费率敏感。
- Q10 vs 无门槛唯一分叉 = Q 门拦截日（表态人少时比例不可信 → 当日不动）；分叉段极少时谁优谁劣
  只有一两笔分量，勿当普适结论。

## 与其它单元的关系

- 干净日网格/日线收盘复用 `..backtest`（`clean_days`/`load_daily`）；投票语义复用 `..poll`
  （swing 窗口 = `HYST_WINDOW`=5 交易日，每博主取时间序最新一条、剔 spec=long、无 mixed ——
  run_hyst/run_hyst_pool 自动置 w=5，run_hyst_sweep 沿 w 轴改动）。全链路单一事实源，无第二份实现。
- `..quality` 是**博主质量打分**（每日一票 · t5 验证，评"上证观点对错"，非 panel PnL）——与本单元
  收益回测是两码事，不逐位对比；`..quality` 成员筛选决定 `PANELS["swing"]` 现役 30 人构成。
- 本单元不改 `config.THRESHOLD / MIN_EXPRESSED`；阈值比较全走 `hyst._decide` 的 Fraction 整数式。
