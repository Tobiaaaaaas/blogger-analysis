# 部署（v13：两群两卡 · 盘中 30 分网格）

`briefing/` 是独立部署单元。实际生产运行在用户的 **Windows 电脑**（长期开机 + Tailscale SSH，别名 `windows-server`），镜像 `aws_options` 项目已验证的部署模式；下方 Linux 段仅作通用示意。

## v13 节奏速览

- **超短卡** → 旧群（env `FEISHU_WEBHOOK_URL`）：交易日盘中**每 30 分钟一档**（09:30/10:00/10:30/11:00/11:30/13:00/13:30/14:00/14:30/15:00），窗口 = 前一交易日 00:00 至 now（v14 交易日口径）。
- **波段卡** → 新群（env `FEISHU_WEBHOOK_URL_SWING`）：**三档**（09:30/11:00/14:30），窗口 = 前 3 个交易日 00:00 至 now（v14 交易日口径）。
- 每档一次抓取 + 一次行情，然后按墙钟决定推哪些板块（`config.due_boards`）；每板块各读各窗、各自收敛总结、各推各群。
- 状态双水位：`fetched_at`（爬虫水位，抓完即写）/ `last_run`（推送水位，全板块推完才写）；行抽取缓存 `rows_cache.json`（博主窗口帖集合没变 → 跳过 DeepSeek 复用）。
- 板块 webhook 缺失 → 该板块记失败、错误心跳同群，**绝不回落** `FEISHU_WEBHOOK_URL`。

---

## Linux 部署（通用示意）

### 步骤

```bash
# 1) 拷贝代码（父仓库已在 /srv/blogger_ana；data 由脚本自建，不拷）
rsync -az --exclude data ./briefing 用户@服务器:/srv/blogger_ana/

# 2) 依赖
cd /srv/blogger_ana/briefing && python -m pip install -r requirements.txt
python -m playwright install chromium

# 3) 配置 briefing/.env（勿提交；DEEPSEEK 可复用父仓库 .deepseek_keys.env）
#    DEEPSEEK_API_KEY=sk-...
#    FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/<超短群 token>
#    FEISHU_WEBHOOK_URL_SWING=https://open.feishu.cn/open-apis/bot/v2/hook/<波段群 token>
#    REPO_ROOT=/srv/blogger_ana

# 4) 试跑（落盘预览，不推送不改状态；非交易日可加 --skip-calendar）
python -m briefing.scripts.run_briefing --dry-run --time 09:30 --board both --no-scrape

# 5) 暖场真实推送双群各一卡（人工确认两 webhook 都通）：
python -m briefing.scripts.run_briefing --push --time 09:30 --board both
```

### cron（单条即可）

```bash
sudo timedatectl set-timezone Asia/Shanghai
# 交易日盘中每 30 分触发（含 12:00/12:30/15:30 等伪 tick —— 脚本在抓取/锁之前 exit 0，无副作用）
*/30 9-15 * * 1-5 cd /srv/blogger_ana && python -m briefing.scripts.run_briefing --push >> briefing/data/cron.log 2>&1
```

---

## Windows 部署（生产）

### 环境（已建立）

- SSH：`ssh windows-server`（Tailscale IP `100.64.70.12`，端口 2222，用户 `24966`，Mac 密钥 `~/.ssh/id_ed25519_windows`）
- Python：`C:\Users\24966\AppData\Local\Programs\Python\Python311\python.exe`
- 依赖：`openai requests playwright akshare psutil` + `python -m playwright install chromium`
- 时区：`China Standard Time`（已确认；`schtasks` 按宿主机时区触发）

### 目录与密钥

- 代码：`C:\Users\24966\blogger_ana`（父仓库 + briefing，含 .git 便于更新）
- 密钥（勿提交，scp 单独传入）：
  - `C:\Users\24966\blogger_ana\briefing\.env` → `FEISHU_WEBHOOK_URL`（超短群）、`FEISHU_WEBHOOK_URL_SWING`（波段群）
  - `C:\Users\24966\blogger_ana\.deepseek_keys.env` → `DEEPSEEK_API_KEY`
  - `paths.load_env()` 两者都读，`briefing/.env` 优先
- 运行时数据：`briefing\data\`（state.json / rows_cache.json / run.lock / briefing.log / briefings\ 历史）——从 Mac 传输时已排除，由脚本自建
- `briefing_runner.bat` 已冻结不改（v9 起的 ASCII+CRLF 约束仍有效，仅作历史入口；v13 调度不再经它）

### 更新代码（Mac → Windows）

```bash
cd /Users/potato/MyDoc/Study/MF/quant
tar --exclude='__pycache__' --exclude='*.pyc' --exclude='briefing/data' \
    --exclude='data/posts/_backup' --exclude='*_bodies_s*' \
    --exclude='.deepseek_keys.env' --exclude='briefing/.env' \
    -cf - blogger_ana | ssh windows-server "cd C:\\Users\\24966 && tar -xf -"
```

### 首次/版本切换运行（关键：先 warm-up 再开调度）

1. 传/更新密钥：`scp briefing/.env .deepseek_keys.env windows-server:...`（注意目标路径；`.env` 要含 `FEISHU_WEBHOOK_URL_SWING`）
2. **手动 warm-up**（真实双群各推一卡，人工盯两端都收到，别让它在调度里超时）：
   `python -m briefing.scripts.run_briefing --push --time 09:30 --board both`
   成功 → 双群各收一张（超短盘中 09:30 / 波段早盘 09:30），state 建立双水位基线。
   旧 v12 state 无需手动改——脚本首跑自动迁移：`_migrate_state_v2`（清 v8 残留键）+ `_migrate_state_v3`（无 `fetched_at` → 回填旧 `last_run`）。
3. 电源硬化：`powercfg /change standby-timeout-ac 0`、`powercfg /change hibernate-timeout-ac 0`
4. **建调度（单任务）**：
   `powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\24966\blogger_ana\briefing\runners\register_tasks.ps1`
   - 注册唯一任务 **BriefingIntraday**：Daily 09:30 触发 + Repetition 每 30 分钟 × 6 小时（覆盖到 15:00 收盘档）；直接调 `python.exe -m briefing.scripts.run_briefing --push`（WorkingDirectory=`C:\Users\24966\blogger_ana`，绕过 bat）。
   - 设置：StartWhenAvailable（错过补跑）+ ExecutionTimeLimit **20min（<30 分网格，防跨档）** + MultipleInstances IgnoreNew；Principal 24966 Interactive Limited。
   - 脚本幂等：先注销旧 `BriefingMorning/Afternoon/Late`（及本任务旧版）再重建，末尾打印 trigger 校验。
   - 手动触发测试：`schtasks /run /tn BriefingIntraday`（在 30 分网格时刻触发 = 真推；离网格触发 = 脚本门禁 exit 0，result 仍 0）。

### Windows 监控

- 日志：`C:\Users\24966\blogger_ana\briefing\data\briefing.log`
- 任务：`schtasks /query /fo list | findstr Briefing`
- 每档都推卡：超短每档一张；波段三档时另加一张（各群各卡，内容没变也发、无 🆕）；空板发最小卡；板块失败发错误心跳（各自 webhook，不串群）

### Windows 已知注意

- **单实例锁**：`briefing/data/run.lock`（pid 存活检测 + 3h 过期接管），进程被杀/断电残留锁下一轮自动接管，不会双发。
- **时区**：代码钉 `BEIJING_TZ`（北京时间），独立于宿主机时区；调度触发时间仍按宿主机时区（当前已为北京时间）。
- **子进程编码**：`scrape_merge.py` 已给所有 subprocess 传 `encoding="utf-8"` + `PYTHONIOENCODING=utf-8`（Windows 管道默认 GBK 会崩中文/emoji）。
- **头条风控**：若 Windows IP 触发"网络环境无法查看"，考虑放缓节流或换网络。
- **窗口口径（v14 交易日，用户修正）**：超短 = 前一交易日 00:00 至 now、波段 = 前 3 个交易日 00:00 至 now（`config.WINDOW_TRADING_DAYS` + `calendar.n_trading_days_ago`），非自然日——周一早晨窗口天然含上周五帖，无 v13"周一漏帖"取舍。
- 交易日历：akshare 拉取失败时回退内置 2026 节假日规则；跨年需更新 `calendar.py`。
