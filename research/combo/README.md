# research/combo/ — 单元③：swing 共识的组合规则寻优（阈值 q × 表态规模 k）

回测与质量评估里的综合共识 = 「看多占比 >2/3 且 表态者≥3（对称看空）」，用户承认这档是拍脑袋定的。
本单元把这条规则**泛化成网格**：**看多占比须严格大于 q** × **最低表态者数 k**（都是整数票，
用 `fractions.Fraction` 精确比较、绝不用 float，避免浮点错格），问**有没有更好的信号组合方式**。

**只针对 swing 波段板**：投票宇宙 = `PANELS["swing"]` 21 人（每日一票取的就是这批人），
short 专属成员不在 swing 表决、与本实验无关。纯研究、离线、无密钥、不改 live briefing 口径。

## 两关流程（为什么是双尺度）

| 关 | 脚本 | 问的问题 | 时态 |
|---|---|---|---|
| ① | `run_sweep.py` | 哪个 (q,k) 的**方向性判断质量**好？ | **每日一票** @14:30 定调 → 终点 = 决策日后**第 5 交易日**，score=d×ret×100（博主评价口径） |
| ② | `run_confirm.py` | 它**真赚钱吗**？ | **真实 trade-PnL**：每交易日 3 档（09:30/11:00/14:30）逐档 decide，bar open 成交、跌破即平 |

两关本是两种时态，**排名不要求一致**：①测"判得准不准"、②测"按它进出场赚不赚"。①扫全网格筛，
②只复核短名单（否则网格点数 × PnL 太重）。

## 规则语义（rules.py）

- 触发 ⟺ 表态者(多+空) `e ≥ k` 且 看多 `bull > q×e`（严格）；对称：`bear > q×e` → d=−1。
  都不达 → 当日无信号。
- `min_bull(q,e) = int(q*e) + 1`（严格大于：q×e 恰为整数时再多 1 票）。
- 网格 = `RATIO_QS {1/2,3/5,2/3,7/10,3/4,4/5,9/10} × KS {1,2,3,4,5,7}` = **42 原始格**；
  基线格 = (2/3, 3)。k=1 语义上退化为"跟单单个最新表态"。

## 去重（关键，报告 header 已注明）

- **行为去重**：两格若在每个干净信号日的 (date, d) 向量全同 → 一"族"。42 原始格 → 有效族。
- **k 惰性（实证）**：本样本每个触发日表态者都 ≥7，k∈{1,2,3,4,5,7} 从不缺票 → 各族 k 全并列，
  42 格行为上归并为 **7 族（每阈值 q 一族、族内 6 格并列）**（2026-09-04 重跑复核仍成立）。这是观测
  事实，不是预设——换时段若表态者不足，k 自然起约束、表会自动分族。

## 打分与稳健列（daygrid.py + engine.py）

- `daygrid.build_contexts()`：150 干净日各取 **一次** 14:30 快照（`poll_tick`，assert clean）→
  `DayContext{expressed/bull/bear + 当日收盘 + t5 终点收盘 + raw_ret}`。**全网格共享同一份上下文**，
  42 格唯一差异 = q×k 判定本身。
- 聚合 = `quality.engine.summarize_metrics`（同源 acc/avg/vol/sharpe + 多空分腿，零漂移）。
- **上/下半期均分**按干净日序对半切（界 = 150//2 = 75），各格对自己那半的计分行平均——N 扎堆某一半
  时两列差距会暴露，防止只盯全期均分。

## 产物与命令

```bash
python -m research.combo.run_sweep --check          # ① 全网格扫描 → reports/ 3 产物
python -m research.combo.run_confirm --check        # ② 短名单 PnL（long-only）→ reports/ 2 产物
python -m research.combo.run_confirm --allow-short  # ② 另加"看空开空"双向对照（指数期货式，未计融券）
python -m research.combo.run_hyst --check           # ③ 滞回平仓规则验证（long-only / 双向）
python -m research.combo.run_hyst_sweep --check     # ③b 滞回参数敏感性 OAT（w×Q×开/平阈值，绕基线）
python -m research.combo.run_hyst_pool --check      # ③c 波段委员会换池（质量先验梯度 × 默认参数，固定 150 干净日；单样本勿改 live PANEL_SWING）
```

| 产物（combo/reports/，UTF-8；csv 用 utf-8-sig） | 内容 |
|---|---|
| `combo_sweep.md` | 行为去重后有效族同表（达标 N≥20 按均分降序）+ 口径/关键读数/多重比较警示 |
| `combo_sweep.csv` | 有效族机器可读（含上/下半期均分、入选短名单依据 basis） |
| `combo_sweep_grid.csv` | **42 原始格全披露**（附 behavior 族 id），不被去重藏格 |
| `combo_confirm.md` / `.csv` | 短名单 trade-PnL 对照（累计/年化/基准/超额/Sharpe/MDD/往返/胜率/在场日 + 第一关质量参考） |
| `combo_hyst.md` / `.csv` | 滞回平仓规则验证（每日一票·收盘成交）：Q10/fixed × long/both 指标表 + 双门实证 + 逐笔 + 逐月 |
| `combo_hyst_daily.csv` | 日净值曲线（long/both 两模式逐日 nav + 持仓态） |
| `combo_hyst_trades.csv` | both 滞回逐笔往返明细 |
| `combo_hyst_pnl.png` | PnL 图（Q10 双门 both/long 实线 + 无门槛 fixed 对照虚线 + 买持） |
| `combo_hyst_sweep.md` / `.csv` | 滞回参数敏感性（**单轴 OAT+角格**：w∈{3,5,7,10} × Q 对称/不对称 × TO/TX 阈值，23 cell × long/both 46 行，绕基线 w5·Q10·TO2/3·TX1/2；单样本勿改 live 口径） |
| `combo_hyst_pool.md` / `.csv` | 滞回**波段委员会换池**（默认参数不动，只换 swing 博主池：现役 21 → X1/X2/X3 逐步剔除 → SWAP21a/b 替换 → TK12..24 质量 top-k，11 池 × long/both 22 行；固定公共 150 干净日，先验来源注，单样本勿改 live PANEL_SWING） |

## 附加研究③：滞回平仓规则验证（hyst）

滞回共识的**口径主档**见 [.claude/skills/analyze-blogger/Swing_Timing.md](../../.claude/skills/analyze-blogger/Swing_Timing.md)
（活文档：口径/状态机随迭代更新，本 README 不复述）；含当前结果/复现的完整旧版已归档
[_archive/docs/hysteresis_consensus_spec.md](../_archive/docs/hysteresis_consensus_spec.md)（参考）。跑法
`python -m research.combo.run_hyst --check`（每日一票 · 收盘成交，产物入 reports/，见上表）。

## 回归护栏（run_confirm 每次必跑，不过拒写）

`decide = rules.decide(2/3, 3)` 的回测必须与 `decide = None`（默认 2/3 口径）在
stats/daily/trades/ticks **逐项相等** —— 证明 `backtest.run(decide=...)` 接缝零漂移、
默认路径未被改动（`research/backtest/validate.py` 仍全过）。
此外 `--check` 还做：① `min_bull` 整数票边界用例；② 基线格 rows 与 `quality/engine.signal_rows`
逐行(date,d,score) 一致且指标 N=46/acc 0.674/avg +0.750/sharpe +0.460 零漂移；③ 双跑确定性；
④ 各族原始格数合计 == 42。

## 读法警示（重要）

- 同一样本上扫 42 格/≈7 族、各格共享同一表态分布 → **多重比较**。"平均分最高"不能单独作结论，
  须看 N、上/下半期稳健列，并等第二关 PnL。
- 景观规律：阈值越严 → N 越少、样本内均分/PnL 往往越高 —— "更挑的信号 + 择优回填"的叠加表象，
  跨时段大概率均值回归。**切勿直接按最优点改 live 口径**；要改另项评估。
- 双向(看空开空)按指数期货式线性收益计、未计融券成本/保证金，只是上限方向对照。

## 与其它单元的关系

- 干净日网格/日线收盘/回测引擎复用 `..backtest`；投票语义复用 `..poll`（swing 窗口 = 前 3 交易日，
  每博主取时间序最新一条、swing 剔 spec=long、无 mixed —— ① ② 用 w=3，③ 滞回 run_hyst 自动置 w=5）；
  打分聚合复用 `..quality.engine`——全链路单一事实源，无第二份实现。
- 本单元不改 `config.THRESHOLD / MIN_EXPRESSED`（import 即缓存，扫网格不安全），网格比较全走
  `rules.py` 的 Fraction 判定。
