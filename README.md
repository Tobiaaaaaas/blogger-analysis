# 财经博主大盘分析能力量化评估

对今日头条财经博主的大盘预测能力进行系统性量化评估：

**博主分析系统**：爬取帖子 → LLM 逐条标注方向预测信号 → Direction 逐条验证打分（score = direction × return）→ 个体报告 + 横向对比

## 目录结构

```
ana/
├── README.md
├── .gitignore
├── .claude/                        Claude Code 技能定义
│   └── skills/analyze-blogger/
│       └── SKILL.md                博主分析主技能（Direction 方向预测评估）
│
├── knowledge/                      市场知识库（拐点目录）
│   └── market_analysis.md          2024.06~2026.07 上证 zigzag 拐点链
│
├── data/
│   ├── posts/                      爬取的原始帖子
│   ├── direction_signals/          Direction 信号标注（LLM 逐条标注，41 位博主）
│   ├── market/                     日线行情数据（7 指数）
│   └── (scores/signals/minute/positions/simulations 已随旧体系归档至 archive/20260817-pre-direction/)
│
├── scripts/
│   ├── eval/                       Direction 评估引擎（run_direction + comparison_all）
│   ├── pipeline/                   scrape_toutiao.py（爬虫，唯一流水线脚本）
│   └── utils/                      行情获取、正文分片抓取等工具
│
├── reports/
│   ├── *_direction.md              41 位博主逐条方向验证报告
│   ├── comparison_direction.md     横向对比总榜
│   └── Direction_结论报告.md        41 位博主横向结论
│
└── archive/                        历史快照 & 临时文件
    ├── scratch/                    一次性调试输出
    ├── data_old/                   旧版 filtered posts
    ├── reports_v1/                 v1 版本报告
    ├── 20260730/                   2026-07-30 报告快照
    ├── 20260801-pre-14score/       14 分改制前快照
    ├── 20260803-pre-restructure/   目录重组前快照
    ├── 20260808-pre-v12/           v12 体系前快照
    ├── 20260817-pre-direction/     Direction 体系前快照（旧 v11/v12/v13 打分体系 + 仓位模拟 SIMULATE 子系统）
    ├── knowledge_archive/          旧版打分公式差异说明
    └── skills/                     已归档技能（v13 拐点线段打分）
```

## 快速开始

### 主要入口：/analyze-blogger Skill

在 Claude Code 中直接使用 Skill 完成全流程分析（爬取→信号标注→Direction 验证打分→报告生成）：

```
/analyze-blogger <博主名称> <帖子链接>
```

Skill 定义见 `.claude/skills/analyze-blogger/SKILL.md`（Direction 方向预测评估，主技能）。

### 后台脚本流水线

Skill 内部调用以下 Python 脚本。如需单独运行某一步（调试/批量处理），可手动执行：

```bash
# 1. 爬取帖子（Playwright，需要 chromium）
python scripts/pipeline/scrape_toutiao.py "<帖子链接>" --name "<博主名>"

# 2. 刷新行情数据（7 指数，Direction 前置条件）
python scripts/utils/fetch_market_data.py --start 20240601

# 3. DeepSeek 自动提取信号 → data/direction_signals/<博主名>.json
export DEEPSEEK_API_KEY="sk-..."   # 只经环境变量，绝不写入文件/提交
python scripts/pipeline/extract_signals_direction.py <博主名>
#    （DeepSeek flash 按 SKILL.md §1~§8 自动逐条标注 + 格式强校验 + 信号自查，schema 见 SKILL.md §3）

# 4. Direction 验证打分并生成报告
python scripts/eval/run_direction.py <博主名>        # 单个博主
python scripts/eval/run_direction.py                 # 全部博主

# 5. 横向对比总榜
python scripts/eval/comparison_all.py
```

## 数据流（Direction 主流程）

```
Toutiao 帖子
    │
    ▼
scrape_toutiao.py ──► data/posts/<name>.json
    │
    ▼
extract_signals_direction.py（DeepSeek flash）
按 SKILL.md §1~§8 自动逐条标注
（pub/d/s/idx/spec/summary/cat，语义理解 + 板块→指数映射 + 格式强校验 + 信号自查）
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

## 当前博主

> 41 位博主已完成 Direction 逐条方向评估（`reports/*_direction.md`），横向结论见 `reports/Direction_结论报告.md` 与 `reports/comparison_direction.md`。
> 另有 6 位博主（实盘指龙、小工匠说股市v、时间合伙人、时间轨迹、梦若神机、股市求是）已爬取帖子，但尚未完成 Direction 信号标注，未纳入评估。

## 依赖

- **Python** ≥ 3.10
- **Playwright** + Chromium（爬虫）
- **akshare / pandas**（行情数据下载）
- **openai**（DeepSeek flash 自动信号提取，`pip install openai`）
- **Claude Code**（Direction 打分 / 报告生成）

## 注意事项

- **API Key 安全**：DeepSeek API Key 通过环境变量传入，不要写入脚本或上传到 GitHub
- **今日头条反爬**：爬虫使用 Playwright 浏览器内 API 调用，自带签名；风控与重爬校验见 SKILL.md §前置条件
- **拐点知识库**：所有拐点以 `knowledge/market_analysis.md` 为准，不重新识别
- **代码与文档一致性**：SKILL.md §1~§8 与 `run_direction.py` 一一对应，修改任一规则需同步另一方（引擎自测：`python scripts/eval/run_direction.py --selftest`）
