# 优质博主观点简报系统（briefing）

实时追踪 18 位已筛选博主（对大盘观点可靠）的帖子，把每位近 3 个交易日的**方向观点**压缩成一张固定 18 行的速览卡，交易日 3 推（09:30 早盘 / 13:00 午后 / 14:30 尾盘）到飞书群。

## 卡片长什么样

```
📊 尾盘简报 · 09-02 周三
🕐 覆盖：近3个交易日（08-31—09-02）
📈 上证 3941.39 -0.97% · 深证成指 13611.55 -1.88% · ... · 两市 1.79万亿
──────────────────────────────
① 🔴 云帆观市 看多 · 超短(0-1日) · 明天
　重点原话：“明天会是一个变盘时间点，就看变盘选择的方向如何了”（昨日15:46）
　摘要：明天是变盘时间点，若放量突破...否则回调蓄势
② 时间轨迹：近3日无观点更新
③ 山顶望星空的诗人：近3日无观点更新
...
⑰ 微风3241：近3日无观点更新
⑱ 爱生活的荷叶Rp：近3日无观点更新
──────────────────────────────
🧭 当前多空阵营2比2对峙，无观望者... 操作上，超短方向分歧大...（收敛总结一段）
（多空 2多/2空 · 观望 0 · 无更新 14）
```

- 行 = 博主最新方向观点（波段 2日+ **和** 超短 0-1 日都算）；每行带**逐字原话**与**发帖时间**（时效性优先）。
- 博主窗口内没发帖 / 发了但没表态 → 占位行（近3日无观点更新 / 有发帖未明确表态）。
- 卡底 = LLM 收敛总结一段：系统多空计数 + 对立代表 + 超短/波段操作参考。

## 数据流（单次运行，v9）

```
交易日 3 档其一触发（09:30/13:00/14:30；非交易日脚本 exit 0）
  → 展示窗口 = 滚动「近 3 个交易日」：calendar 从北京时首交易日 00:00 起
  → 增量抓取新帖（since = state.last_run）→ merge 进父仓库 data/posts/<博主>.json
  → 现读合并主文件：取每博主窗口内最新 ≤5 帖（滤视频帖/短帖）
  → 行情背景（腾讯行情：上证/深成/创业板 + 两市成交额）
  → DeepSeek 逐博主行抽取（每档全量重读，不缓存）：方向/立场/周期/摘要/逐字原话
     引文时间 = 系统回填该帖真实 publish_time（模型只返回帖子下标，不誊写时间）
  → 18 行按固定名单序渲染（无帖博主占位行，绝不编造）
  → 有方向观点 → 卡底 LLM 收敛总结一段；全无方向 → 最小卡（健康信号）
  → 渲染飞书 interactive 卡 → POST webhook → 推进 last_run/last_slot 原子写 state
```

## 目录

```
briefing/
  scripts/           # 简报系统全部代码（本目录即独立部署单元）
    run_briefing.py  # 编排器（入口）
    config.py        # 18 人名单 ROSTER + 3 时段 + 窗口常量
    calendar.py      # 交易日历（akshare 优先 + 2026 节假日兜底）＋ trading_days()
    scrape_merge.py  # 增量抓取 + merge + 补正文
    market.py        # 行情背景
    summarize.py     # DeepSeek 行抽取 + 收敛总结（v9 主路径；v8 旧函数留作 LEGACY）
    render.py        # 飞书卡片渲染 + webhook（v9 roster 卡；旧共识/心跳标 LEGACY）
    profiles.py      # 【LEGACY】v8 博主画像生成（v9 不再用）
    state.py         # 状态持久化（v9：last_run / last_slot / seen）
    paths.py         # 路径/环境解析
  data/              # 运行时状态（state.json / briefings 历史 / 日志）
  runners/           # Windows 调度入口 bat
  README.md / DEPLOY.md / TASKS.md
```

## 依赖的父仓库资源

`briefing/` 是独立部署单元，但运行需要父仓库（`blogger_ana`）提供的：

| 资源 | 用途 |
|---|---|
| `data/posts/*.json` | 博主帖子库（增量抓取 merge 进这里，也是展示窗口的正文数据源） |
| `scripts/pipeline/scrape_toutiao.py` | 增量窗口爬虫（已加 `--out` 参数） |
| `scripts/utils/fetch_bodies_shard.py` `merge_bodies_to_posts.py` | 新帖正文补齐 |
| `scripts/pipeline/extract_signals_direction.py` | DeepSeek 调用底座（call_json/watchdog） |

父仓库路径默认取 `briefing/` 上一级，可用环境变量 `REPO_ROOT` 覆盖（部署时目录结构不同则设置）。

## 运行

```bash
# 本机试跑（落盘预览，不推送、不改状态）
python -m briefing.scripts.run_briefing --dry-run --slot morning --no-scrape
# 非交易日想强制试跑：
python -m briefing.scripts.run_briefing --dry-run --slot morning --skip-calendar

# cron 真实推送（自动判定时段 / 或显式指定）
python -m briefing.scripts.run_briefing --push --slot morning

# 冒烟：只抓前 3 位博主
python -m briefing.scripts.run_briefing --push --slot late --max-bloggers 3
```

依赖环境变量：`DEEPSEEK_API_KEY`（DeepSeek，写简报用）、`FEISHU_WEBHOOK_URL`（飞书群自定义机器人）。从 `briefing/.env` 或父仓库 `.deepseek_keys.env` 自动加载。

## 设计要点

- **两个窗口分离**：展示 = 滚动近 3 交易日（现读主文件，随时可回看）；抓取 = 增量 since last_run。每档推送前先抓新帖，再全量 LLM 重读窗口内帖子。
- **时效性**：博主最新帖若非方向帖、更早仍在窗口内的方向帖照常展示，并标注它真实的发帖时间；引文时间由系统从帖子回填，模型永不誊写时间。
- **不编造**：LLM 失败 / 无方向 → 占位行 + log；全 18 人无方向 → 最小卡（不发心跳）。
- 时段/日期/窗口全用北京时（`BEIJING_TZ`），独立于宿主机时区。

详见 [`DEPLOY.md`](DEPLOY.md) 部署，[`TASKS.md`](TASKS.md) 任务记录。
