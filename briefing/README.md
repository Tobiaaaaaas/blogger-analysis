# 优质博主观点简报系统（briefing）

实时追踪 top-20 优质博主（已通过可靠性筛选）的帖子，把他们对大盘的观点综合成简报，定时推送到飞书群。

## 数据流（单次运行）

```
cron 触发（交易日 7 推 / 非交易日 20:00 一次）
  → 交易日历判定时段（非交易日跳过盘中槽）
  → 增量抓取：对 20 位博主跑 scrape_toutiao --since 上一时段 → 新帖 merge 进 data/posts → 补齐正文
  → 无新帖 → 推心跳消息（含行情），结束
  → 行情背景（腾讯行情接口：上证/深成/创业板 + 两市成交额）
  → DeepSeek 分批抽点（逐帖提炼观点要点）→ 全局综合（共识/重点博主/分歧/风险，增量变化融入各节）
  → 渲染飞书 interactive 卡片 → POST webhook → 写回状态（seen/上期/上次运行）
```

## 目录

```
briefing/
  scripts/           # 简报系统全部代码（本目录即独立部署单元）
    run_briefing.py  # 编排器（入口）
    config.py        # 追踪名单、7 个时段
    calendar.py      # 交易日历（akshare 优先 + 2026 节假日兜底）
    scrape_merge.py  # 增量抓取 + merge + 补正文
    market.py        # 行情背景
    summarize.py     # DeepSeek 抽点 + 综合（含 prompt 设计）
    render.py        # 飞书卡片渲染 + webhook
    profiles.py      # 【一次性】博主画像生成
    state.py         # 状态持久化
    paths.py         # 路径/环境解析
  data/              # 运行时状态（state.json / profiles.json / briefings 历史 / 日志）
  README.md / DEPLOY.md / TASKS.md
```

## 依赖的父仓库资源

`briefing/` 是独立部署单元，但运行需要父仓库（`blogger_ana`）提供的：

| 资源 | 用途 |
|---|---|
| `data/posts/*.json` | 博主帖子库（增量抓取 merge 进这里，也是正文数据源） |
| `data/direction_signals/` `reports/` | 生成博主画像（一次性） |
| `scripts/pipeline/scrape_toutiao.py` | 增量窗口爬虫（已加 `--out` 参数） |
| `scripts/utils/fetch_bodies_shard.py` `merge_bodies_to_posts.py` | 新帖正文补齐 |
| `scripts/pipeline/extract_signals_direction.py` | DeepSeek 调用底座（call_json/watchdog） |

父仓库路径默认取 `briefing/` 上一级，可用环境变量 `REPO_ROOT` 覆盖（部署时目录结构不同则设置）。

## 运行

```bash
# 一次性：生成博主画像（覆盖全部博主，增量运行——只处理新增博主，见 TASKS.md）
python -m briefing.scripts.profiles

# 本机试跑（落盘预览，不推送、不改状态）
python -m briefing.scripts.run_briefing --dry-run --slot evening

# cron 真实推送（自动判定时段）
python -m briefing.scripts.run_briefing --push

# 冒烟：只抓前 3 位博主
python -m briefing.scripts.run_briefing --dry-run --slot close --max-bloggers 3
```

依赖环境变量：`DEEPSEEK_API_KEY`（DeepSeek，写简报用）、`FEISHU_WEBHOOK_URL`（飞书群自定义机器人）。从 `briefing/.env` 或父仓库 `.deepseek_keys.env` 自动加载。

## 卡片结构

5 个内容节（六节方案中"变化"不设独立小节，增量融入各节叙事）：
`📈 行情 · 🧭 共识 · ⭐ 重点博主（可靠优先 ≤5）· ⚔️ 分歧 · ⚠️ 风险` + 底部博主动态。

详见 [`DEPLOY.md`](DEPLOY.md) 部署，[`TASKS.md`](TASKS.md) 一次性任务清单。
