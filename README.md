# 财经博主大盘分析能力量化评估

对今日头条财经博主的大盘预测能力进行系统性量化评估，包含两个子系统：

- **博主分析系统**：爬取帖子 → LLM 语义信号提取 → v11 双因子打分 → 个体报告 + 横向对比
- **仓位模拟系统**：基于博主公开仓位披露，模拟组合净值曲线（仅适用于公开仓位的博主）

## 目录结构

```
ana/
├── README.md
├── .gitignore
├── .claude/                        Claude Code 技能定义
│   └── skills/analyze-blogger/
│       ├── SKILL.md                博主分析主技能
│       └── SIMULATE.md             仓位模拟子技能
│
├── knowledge/                      市场知识库（拐点目录）
│   └── market_analysis.md          2024.06~2026.07 上证 zigzag 拐点链
│
├── data/
│   ├── posts/                      爬取的原始帖子（15 位博主）
│   ├── signals/                    LLM 提取的方向信号
│   ├── scores/                     v11 打分结果（14 分数体系）
│   ├── market/                     日线行情数据
│   ├── minute/                     分钟级行情数据
│   │   ├── 1min/                   6 指数 1 分钟 K 线
│   │   └── 5min/                   6 指数 5 分钟 K 线
│   ├── positions/                  仓位披露数据 + batch 中间文件
│   └── simulations/                NAV 模拟输出
│
├── scripts/
│   ├── pipeline/                   核心流水线（scrape→extract→score→report→pdf）
│   ├── simulate/                   仓位模拟系统
│   ├── eval/                       精确率/交易对/强信号评估
│   └── utils/                      行情获取、关键帖子提取等工具
│
├── reports/
│   ├── bloggers/                   个体分析报告（15 位博主）+ PDF
│   ├── strategy/                   横向对比 / 保留分析 / 跟随指南 + PDF
│   └── simulations/                仓位模拟报告
│
└── archive/                        历史快照 & 临时文件
    ├── scratch/                    一次性调试输出
    ├── data_old/                   旧版 filtered posts
    ├── reports_v1/                 v1 版本报告
    ├── reports_20260730/           2026-07-30 报告快照
    ├── reports_20260801-pre-14score/  14 分改制前快照
    ├── reports_20260803-pre-restructure/  目录重组前快照
    └── knowledge_archive/          旧版打分公式差异说明
```

## 快速开始

### 添加新博主完整流程

```bash
# 1. 爬取帖子（Playwright，需要 chromium）
python scripts/pipeline/scrape_toutiao.py "<帖子链接>" --name "<博主名>"

# 2. LLM 信号提取（需要 DeepSeek API Key）
export DEEPSEEK_API_KEY="sk-xxx"
python scripts/pipeline/extract_signals.py --blogger "<博主名>"

# 3. v11 打分（纯本地）
python scripts/pipeline/score_v11.py --blogger "<博主名>"

# 4. 生成报告骨架（量化部分）
python scripts/pipeline/gen_report_data.py --blogger "<博主名>"

# 5. Agent 定性填充（使用 /analyze-blogger 技能）
# 在 Claude Code 中运行: /analyze-blogger <博主名>

# 6. 生成 PDF
python scripts/pipeline/md_to_pdf.py --blogger "<博主名>"
```

### 下载分钟行情数据

脚本：`scripts/simulate/download_minute_data.py`  
零 Python 依赖，仅需系统安装 `curl`。数据源为东方财富 API。

```bash
# 下载 2026 全年 1 分钟数据（6 指数：上证50/沪深300/中证500/中证1000/创业板指/科创50）
python scripts/simulate/download_minute_data.py --period 1

# 下载 5 分钟数据
python scripts/simulate/download_minute_data.py --period 5

# 先测试一个指数，确认 API 可通
python scripts/simulate/download_minute_data.py --period 1 --test

# 指定日期范围（仅 kline/get 路径支持）
python scripts/simulate/download_minute_data.py --period 1 --start 2026-06-01 --end 2026-08-07
```

**下载策略**（脚本自动执行，无需手动干预）：

| 优先级 | API | 参数 | 说明 |
|--------|-----|------|------|
| A | `kline/get` + `klt=1` | `beg/end` 日期 | 理想路径：支持任意日期区间，一次拉全年 |
| B | `trends2/get` + `ndays` | 仅最近 N 天 | 回退路径：ndays 从 200 递减，取最大可用值 |

输出到 `data/minute/1min/` 和 `data/minute/5min/`，CSV 格式：
`time,open,high,low,close,volume,amount`

**前置条件**：
- 系统已安装 `curl`（macOS 自带，Linux `apt install curl`）
- IP 未被东方财富封禁（封禁表现为 HTTP 000 或空响应，等 1-2 小时自动解封）
- 不要短时间内连续多次运行（会触发 IP 封禁）

**常见问题**：

| 症状 | 原因 | 解决 |
|------|------|------|
| 所有请求返回空 | IP 被封 | 等待 1-2 小时，或换网络/VPN |
| `trends2/get` 只拉到最近几天 | `ndays` 上限受限 | 正常现象，该 API 不提供完整历史 |
| `kline/get` + `klt=1` 返回空 | API 可能不支持 1min | 回退到 5min 数据（`--period 5`），精度损失有限 |

### 运行仓位模拟（仅 顺应周期）

```bash
# 1. 运行 NAV 模拟
python scripts/simulate/simulate_nav.py

# 2. 对比分析
python scripts/simulate/compare_nav_vs_signals.py
python scripts/simulate/decompose_timing_vs_selection.py
```

### 运行评估

```bash
# 事件研究法精确率评估
python scripts/eval/evaluate_precision.py --blogger "<博主名>"

# 交易对评估
python scripts/eval/evaluate_trade_pairs.py --blogger "<博主名>"
```

## 数据流

```
Toutiao 帖子
    │
    ▼
scrape_toutiao.py ──► data/posts/<name>.json
    │
    ▼
extract_signals.py ──► data/signals/<name>.json
(DeepSeek V4 Flash LLM)
    │
    ▼
score_v11.py ──► data/scores/<name>_v11.json
(market_analysis.md 拐点对齐)
    │
    ├──► gen_report_data.py ──► reports/bloggers/<name>_analysis.md
    │    + Claude Agent 定性填充
    │
    └──► md_to_pdf.py ──► reports/bloggers/pdf/<name>_analysis.pdf

[顺应周期并行分支]
    │
    ▼
仓位披露 batch 提取 ──► data/positions/顺应周期_positions.json
    │
    ▼
simulate_nav.py ──► data/simulations/顺应周期_nav.json
    │
    ▼
compare_* / decompose_* ──► reports/simulations/
```

## 方法论

### v11 打分公式

四场景 A/B/C/D 双因子打分：

| 场景 | 条件 | 得分公式 |
|------|------|----------|
| A | 上升段 + 看多 | `sbase × max(return, short_return)` — 奖励正确看多 |
| B | 上升段 + 看空 | 条件扣分（1.5% 阈值）— 惩罚过早看空 |
| C | 下降段 + 看多 | 条件扣分（1.5% 阈值）— 惩罚过早抄底 |
| D | 下降段 + 看空 | `sbase × max(return, short_return)` — 奖励正确看空 |

- `return = |P_next_inflection - P_ref| / |P_ref|`
- `short_return = |avg_close(T, T+1, T+2) / P_ref - 1|`（3 日均价）
- 信号参考价格：开盘前→开盘价，盘中→收盘价，收盘后→下一交易日开盘价

### 14 分数体系

7 维度 × 2 指标（总得分 + 平均百分比）：
综合、上升段、下降段、抄底、逃顶、看多、看空

### time_horizon 4 分类

- **short**：T ~ T+2
- **medium**：T+3 ~ 1 个月
- **long**：1 个月以上
- **unspecified**：无明确时间范围

## 当前博主

| 博主 | 帖子数 | 时间跨度 | 分类 |
|------|--------|----------|------|
| 顺应周期 | ~2000+ | 2024-06 ~ 2026-08 | 仓位透明型 |
| 大盘蜂向标 | ~3000+ | 2024-06 ~ 2026-08 | 高频判断型 |
| 江河之水终有入海之日 | 1339 | 2024-06 ~ 2026-08 | 高频判断型 |
| 吉星高照 | ~1000+ | 2024-06 ~ 2026-08 | 稳健型 |
| 衡山佛曰论股 | ~800+ | 2024-06 ~ 2026-08 | 技术分析型 |
| 奔走的股票 | ~700+ | 2024-06 ~ 2026-08 | 基本面型 |
| 凸教授 | ~600+ | 2024-06 ~ 2026-08 | 宏观分析型 |
| TL阳光 | ~500+ | 2024-06 ~ 2026-08 | 趋势跟踪型 |
| 梦若神机 | ~400+ | 2024-06 ~ 2026-08 | 价值投资型 |
| 云帆观市 | ~400+ | 2024-08 ~ 2026-08 | 短线交易型 |
| 稀豹 | ~300+ | 2024-06 ~ 2026-08 | 情绪分析型 |
| 鸟瞰股市 | ~300+ | 2024-06 ~ 2026-08 | 宏观分析型 |
| 道术合一 | 344 | 2024-06 ~ 2026-08 | 哲学框架型 |
| 爱生活的荷叶Rp | 140 | 2025-06 ~ 2026-08 | 新晋博主 |
| 实盘指龙 | 23 | 2026-07 ~ 2026-08 | 数据不足型 |

## 依赖

- **Python** ≥ 3.10
- **Playwright** + Chromium（爬虫）
- **DeepSeek API**（信号提取，模型 `deepseek-v4-flash`）
- **Chrome**（PDF 生成，headless 模式）
- **aiohttp / akshare**（行情数据下载）
- **Claude Code**（报告定性填充、策略文档更新）

## 注意事项

- **API Key 安全**：DeepSeek API Key 通过环境变量传入，不要写入脚本或上传到 GitHub
- **今日头条反爬**：爬虫使用 Playwright 浏览器内 API 调用，自带签名
- **拐点知识库**：所有拐点以 `knowledge/market_analysis.md` 为准，不重新识别
