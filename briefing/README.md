# 优质博主观点简报系统（briefing）

实时追踪 18 位已筛选博主（对大盘观点可靠）的帖子，把每位近 3 个交易日的**方向观点**压缩成一张固定 18 行的速览卡，交易日 3 推（09:30 早盘 / 13:00 午后 / 14:30 尾盘）到飞书群。

## 卡片长什么样

```
📊 早盘简报 · 09-03 周四
🕐 覆盖：近3个交易日（09-01—09-03）
📈 上证 3961.66 +0.51% · 两市 8567亿
──────────────────────────────
⏱️ 超短(0-1日)
① 🔴 云帆观市 看多 · 今天
　重点原话：“今天的A股反弹概率还是比较高的”（今日06:55）
　摘要：缩量不悲观，认为今天反弹概率较高，等待放量
⑩ 🔴 强哥解盘 看多 · 明天
　重点原话：“明天科技王者归来”（昨日17:25）
🌊 波段(2日+)
② 🟢 时间轨迹 看空 · 本周
　重点原话：“9月4号-10号看那一天出低点”（昨日09:36）
⑥ 🟢 衡山佛曰论股 看空 · 近日
　重点原话：“小波段反弹基本结束…回3800下方寻支撑”（昨日19:36）
📭 无明确方向
⑤ 孙万林：近3日无观点更新
──────────────────────────────
🧭 超短层面5多3空，多数看今日/明日反弹…；波段层面2多4空，多数认为调整
   未结束。两层方向相反但层叠兼容：超短反弹更像波段调整中的反抽，而非
   反转信号。操作上，超短可轻仓博弈反弹快进快出；波段宜等调整充分低吸…
（超短(0-1日) 5多/3空 · 波段(2日+) 2多/4空 · 观望 1 · 无更新 3）
```

- **18 行按周期分两段**（超短(0-1日) / 波段(2日+)）＋无方向尾段；编号 ①–⑱ = 原名单位次（博主挪段编号不变）。行 = 博主最新方向观点，每行带**逐字原话**与**发帖时间**（时效性优先）。
- 博主窗口内没发帖 / 发了但没表态 → 归「无明确方向」段占位（近3日无观点更新 / 有发帖未明确表态）。
- **计数分档**：超短/波段各自多空分开，不合并成总比数——跨周期不同方向是层叠（波段调整中的反抽）不是对立。
- 卡底 = LLM 收敛总结一段（周期分层视角）：分档版图 → 层叠/同周期真分歧 → 超短/波段操作参考。

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
