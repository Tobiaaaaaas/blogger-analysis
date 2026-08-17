# 财经博主大盘分析能力量化评估

对今日头条财经博主的大盘预测能力进行系统性量化评估，包含两个子系统：

- **博主分析系统**：爬取帖子 → LLM 逐条标注方向预测信号 → Direction 逐条验证打分（score = direction × return）→ 个体报告 + 横向对比
- **仓位模拟系统**：基于博主公开仓位披露，模拟组合净值曲线（仅适用于公开仓位的博主）

## 目录结构

```
ana/
├── README.md
├── .gitignore
├── .claude/                        Claude Code 技能定义
│   └── skills/analyze-blogger/
│       ├── SKILL.md                博主分析主技能（Direction 方向预测评估）
│       └── SIMULATE.md             仓位模拟子技能
│
├── knowledge/                      市场知识库（拐点目录）
│   └── market_analysis.md          2024.06~2026.07 上证 zigzag 拐点链
│
├── data/
│   ├── posts/                      爬取的原始帖子 + 正文分片缓存
│   ├── direction_signals/          Direction 信号标注（LLM 逐条标注，37 位博主）
│   ├── market/                     日线行情数据（7 指数）
│   ├── minute/                     分钟级行情数据（SIMULATE 用）
│   │   ├── 1min/                   6 指数 1 分钟 K 线
│   │   └── 5min/                   6 指数 5 分钟 K 线
│   ├── positions/                  仓位披露数据 + batch 中间文件
│   └── simulations/                NAV 模拟输出
│
├── scripts/
│   ├── pipeline/                   scrape_toutiao.py（爬虫，唯一流水线脚本）
│   ├── eval/                       Direction 评估引擎（run_direction + comparison_all）
│   ├── simulate/                   仓位模拟系统
│   └── utils/                      行情获取、正文分片抓取等工具
│
├── reports/
│   ├── *_direction.md              37 位博主逐条方向验证报告
│   ├── comparison_direction.md     横向对比总榜
│   ├── Direction_结论报告.md        37 位博主横向结论
│   └── simulations/                仓位模拟报告
│
└── archive/                        历史快照 & 临时文件
    ├── scratch/                    一次性调试输出
    ├── data_old/                   旧版 filtered posts
    ├── reports_v1/                 v1 版本报告
    ├── 20260730/                   2026-07-30 报告快照
    ├── 20260801-pre-14score/       14 分改制前快照
    ├── 20260803-pre-restructure/   目录重组前快照
    ├── 20260808-pre-v12/           v12 体系前快照
    ├── 20260817-pre-direction/     Direction 体系前快照（旧 v11/v12/v13 打分体系）
    ├── knowledge_archive/          旧版打分公式差异说明
    └── skills/                     已归档技能（v13 拐点线段打分）
```

## 快速开始

### 主要入口：/analyze-blogger Skill

在 Claude Code 中直接使用 Skill 完成全流程分析（爬取→信号标注→Direction 验证打分→报告生成）：

```
/analyze-blogger <博主名称> <帖子链接>
```

Skill 定义见 `.claude/skills/analyze-blogger/SKILL.md`（Direction 方向预测评估，主技能）和 `SIMULATE.md`（仓位模拟，仅适用公开仓位的博主）。

### 后台脚本流水线

Skill 内部调用以下 Python 脚本。如需单独运行某一步（调试/批量处理），可手动执行：

```bash
# 1. 爬取帖子（Playwright，需要 chromium）
python scripts/pipeline/scrape_toutiao.py "<帖子链接>" --name "<博主名>"

# 2. 刷新行情数据（7 指数，Direction 前置条件）
python scripts/utils/fetch_market_data.py --start 20240601

# 3. LLM 信号标注 → data/direction_signals/<博主名>.json
#    （由 Claude Agent 按 SKILL.md §1~§8 逐条标注，schema 见 SKILL.md §3）

# 4. Direction 验证打分并生成报告
python scripts/eval/run_direction.py <博主名>        # 单个博主
python scripts/eval/run_direction.py                 # 全部博主

# 5. 横向对比总榜
python scripts/eval/comparison_all.py
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
|--------|------|------|------|
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

## 数据流（Direction 主流程）

```
Toutiao 帖子
    │
    ▼
scrape_toutiao.py ──► data/posts/<name>.json
    │
    ▼
Claude Agent 按 SKILL.md §1~§8 逐条标注
（pub/d/s/idx/spec/summary/cat，语义理解 + 板块→指数映射）
    │
    ▼
data/direction_signals/<name>.json
    │
    ▼
run_direction.py ──► reports/<name>_direction.md
（按 §4 验证终点逐条打分，score = direction × return）
    │
    ▼
comparison_all.py ──► reports/comparison_direction.md
（总榜 + 周期/多空/指数/月份分档 + 覆盖率警告）
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

> 37 位博主已完成 Direction 逐条方向评估（`reports/*_direction.md`），横向结论见 `reports/Direction_结论报告.md` 与 `reports/comparison_direction.md`。
> 另有 8 位博主（乐哥来了、实盘指龙、小工匠说股市v、时间合伙人、时间轨迹、梦若神机、股市求是、飛浪王）已爬取帖子，但尚未完成 Direction 信号标注，未纳入评估。

## 依赖

- **Python** ≥ 3.10
- **Playwright** + Chromium（爬虫）
- **akshare / pandas**（行情数据下载）
- **Chrome**（PDF 生成，headless 模式，仅旧归档流程使用）
- **Claude Code**（LLM 信号标注 / Direction 打分 / 仓位模拟 LLM 提取）

## 注意事项

- **API Key 安全**：DeepSeek API Key 通过环境变量传入，不要写入脚本或上传到 GitHub
- **今日头条反爬**：爬虫使用 Playwright 浏览器内 API 调用，自带签名；风控与重爬校验见 SKILL.md §前置条件
- **拐点知识库**：所有拐点以 `knowledge/market_analysis.md` 为准，不重新识别
- **代码与文档一致性**：SKILL.md §1~§8 与 `run_direction.py` 一一对应，修改任一规则需同步另一方（引擎自测：`python scripts/eval/run_direction.py --selftest`）
