# 财经博主大盘分析能力量化评估

对今日头条财经博主的大盘预测能力进行系统性量化评估，包含两个子系统：

- **博主分析系统**：爬取帖子 → LLM 语义信号提取 → v12 单因子打分 → 个体报告 + 横向对比
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
│   ├── posts/                      爬取的原始帖子（28 位博主）
│   ├── signals/                    LLM 提取的方向信号
│   ├── scores/                     v12 打分结果（14 分数体系）
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
│   ├── bloggers/                   个体分析报告（28 位博主）+ PDF
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

### 主要入口：/analyze-blogger Skill

在 Claude Code 中直接使用 Skill 完成全流程分析（爬取→信号提取→打分→报告生成）：

```
/analyze-blogger <博主名称> <帖子链接>
```

Skill 定义见 `.claude/skills/analyze-blogger/SKILL.md`（方向信号评分）和 `SIMULATE.md`（仓位模拟，仅适用公开仓位的博主）。

### 后台脚本流水线

Skill 内部调用以下 Python 脚本。如需单独运行某一步（调试/批量处理），可手动执行：

```bash
# 1. 爬取帖子（Playwright，需要 chromium）
python scripts/pipeline/scrape_toutiao.py "<帖子链接>" --name "<博主名>"

# 2. LLM 信号提取（Claude Agent 在 Skill 中直接完成，也可用 DeepSeek 批量提取）
export DEEPSEEK_API_KEY="sk-xxx"
python scripts/pipeline/extract_signals.py --blogger "<博主名>"

# 3. v12 打分（由 Claude Agent 按 SKILL.md §4.1 规则执行，也可用脚本批量）
python scripts/pipeline/score_v12.py --blogger "<博主名>"

# 4. 生成报告骨架（量化部分 → 再由 Agent 填充定性内容）
python scripts/pipeline/gen_report_data.py --blogger "<博主名>"

# 5. 生成 PDF
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

### Path A: Claude Agent（SKILL.md 主路径）

```
Toutiao 帖子
    │
    ▼
scrape_toutiao.py ──► data/posts/<name>.json
    │
    ▼
Claude Agent 全量阅读 ──► data/signals/<name>.json
（LLM 逐条语义标注 direction/strength/time_horizon）
    │
    ▼
Claude Agent v12 打分 ──► 14 分数 + time_horizon 分组 + 逐拐点分析
（market_analysis.md 拐点对齐，return 单因子）
    │
    ▼
Claude Agent 生成报告 ──► reports/bloggers/<name>_analysis.md
    │
    ▼
md_to_pdf.py ──► reports/bloggers/pdf/<name>_analysis.pdf
```

### Path B: Python 批量流水线（extract_signals.py + score_v12.py）

```
Toutiao 帖子
    │
    ▼
scrape_toutiao.py ──► data/posts/<name>.json
    │
    ▼
extract_signals.py ──► data/signals/<name>.json
(DeepSeek V4 Flash 批量语义提取)
    │
    ▼
score_v12.py ──► data/scores/<name>_v12.json
    │
    ├──► gen_report_data.py ──► reports/bloggers/<name>_analysis.md
    │    + Claude Agent 定性填充
    │
    └──► md_to_pdf.py ──► reports/bloggers/pdf/<name>_analysis.pdf
```

```
[顺应周期] 仓位披露 batch 提取 ──► data/positions/顺应周期_positions.json
    │
    ▼
simulate_nav.py ──► data/simulations/顺应周期_nav.json
    │
    ▼
compare_* / decompose_* ──► reports/simulations/
```

## 当前博主

> 28 位博主，按 v12.1 均衡综合降序。🆕 = v12.2 新增。分类为 v12.1 多空均衡修正后的能力画像。

| 博主 | 帖子数 | v12.1排名 | 分类 |
|------|--------|:---:|------|
| 奔走的股票 | 1351 | #1 | 双边正收益 |
| 吉星高照 | 827 | #2 | 双边正收益 |
| 稀豹 | 1571 | #3 | 看空隐藏价值 |
| 爱生活的荷叶Rp | 140 | #4 | 双边正收益 |
| 大盘蜂向标 | 278 | #5 | 均衡偏空 |
| 微风3241 🆕 | 912 | #6 | 偏空·抄底逃顶双强 |
| 龙五 | 447 | #7 | 偏多·逃顶强 |
| 江河之水终有入海之日 | 1339 | #8 | 双边正收益 |
| 云帆观市 | 2399 | #9 | 偏多·抄底强 |
| 实盘指龙 | 23 | #10 | 数据不足 |
| 小工匠说股市v 🆕 | 20 | #11 | 数据不足 |
| 强哥解盘 🆕 | 2195 | #12 | 逆向·极端时刻准 |
| 顺应周期 | 2394 | #13 | 均衡 |
| 梦若神机 | 432 | #14 | 偏多·逃顶强 |
| 衡山佛曰论股 | 2380 | #15 | 逆向/防守 |
| 股评老陈 🆕 | 1650 | #16 | 偏多·抄底强 |
| TL阳光 | 2394 | #17 | 逆向/防守 |
| 鸟瞰股市 | 1119 | #18 | 极端偏多 |
| 实盘指龙副号 | 919 | #19 | 偏多·信号多 |
| 股傲 🆕 | 1041 | #20 | 偏多·大底抄底 |
| 凸教授 | 2004 | #21 | 极端偏多（虚高）|
| 枫叶 🆕 | 2400 | #22 | 偏多·看空隐藏价值 |
| 白猫财眼 🆕 | 1836 | #23 | 偏多·逃顶强 |
| 股市求是 🆕 | 1349 | #24 | 整体亏损 |
| 时间合伙人 🆕 | 2299 | #25 | 极端偏多（虚高）|
| 戮神月 | 2389 | #26 | 整体亏损 |
| 道术合一 | 344 | #27 | 整体亏损 |
| 时间轨迹 🆕 | 836 | #28 | 整体亏损 |

## 依赖

- **Python** ≥ 3.10
- **Playwright** + Chromium（爬虫）
- **DeepSeek API**（信号提取，模型 `deepseek-v4-flash`）
- **Chrome**（PDF 生成，headless 模式）
- **aiohttp / akshare**（行情数据下载）
- **Claude Code**（信号提取 / v12 打分 / 报告定性填充 / 策略文档更新 / 仓位模拟 LLM 提取）

## 注意事项

- **API Key 安全**：DeepSeek API Key 通过环境变量传入，不要写入脚本或上传到 GitHub
- **今日头条反爬**：爬虫使用 Playwright 浏览器内 API 调用，自带签名
- **拐点知识库**：所有拐点以 `knowledge/market_analysis.md` 为准，不重新识别
