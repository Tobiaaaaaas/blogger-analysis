# research/_archive/ — 旧口径 / 探索残留快照（仅存档，勿再读）

本目录收纳 research 各单元演进中**被取代或已废弃**的旧产物。归档原则：

- **无脚本再生成**：现役 `run_*.py` 不再产出这些文件（文件名/口径均已换代），只留快照供对照旧 git blob 或旧会话。
- **全仓库零引用**：归档前已核无任何 .py / README / 规格文档引用这些文件；归档后不再有新引用。
- **只存档、不再读入新分析**：任何结论一律以各单元现役 `reports/`（最新一次 `run_*` 重跑）为准。

目录按**源路径镜像**：源在 `research/<单元>/reports/` 的文件落在 `research/_archive/<单元>/reports/`，
改动无需搬动。现役产物仍在各自单元 `reports/` 下（最近一次全量重跑：**2026-09-04 18:36**）。

## 归档清单

### combo/reports/（单元③ 组合规则寻优 · 探索期残留）

| 文件 | 性质 |
|---|---|
| `combo_curve_w5k6.png` | 早期 w5·k6 口径净值/曲线图（现役开/平双门设计之前） |
| `combo_w5k6_pnl.png` | 同上的 PnL 图 |
| `combo_w5k6_pnl_t5.csv` | 同上的 t5 逐档/逐日 PnL 明细 |
| `bull_share_daily.png` | 早期"每日看多份额"出图（口径定稿前） |
| `combo_hyst_w5k10_pnl.png` | 旧滞回 hyst 语义（k10·50% 冻结口径）PnL 图；已被现役 `combo/reports/combo_hyst_pnl.png`（Q10 双门·无填充）取代 |
