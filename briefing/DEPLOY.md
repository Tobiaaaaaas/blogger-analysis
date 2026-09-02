# 部署到服务器

`briefing/` 是独立部署单元。部署时把整个 `briefing/` 目录拷到服务器，并保证父仓库（`blogger_ana`）的数据与脚本可用（`REPO_ROOT` 环境变量指向它）。

## 前置

- 服务器：Linux，Python ≥ 3.10
- 需要：`openai`、`requests`、`playwright`（含 Chromium）、可选 `akshare`（交易日历，装了更准）
- 网络：能访问今日头条、腾讯行情 `qt.gtimg.cn`、DeepSeek API、飞书 `open.feishu.cn`

## 部署步骤

```bash
# 1) 拷贝代码（假设父仓库已在 /srv/blogger_ana）
rsync -az --exclude data ./briefing 用户@服务器:/srv/blogger_ana/
#    或整体拷贝后只保留本目录

# 2) 服务器上装依赖
cd /srv/blogger_ana/briefing
python -m pip install -r requirements.txt
python -m playwright install chromium

# 3) 配置
#    brief/.env（briefing/ 目录内）：
#      DEEPSEEK_API_KEY=sk-...          # 可复用父仓库 .deepseek_keys.env，二选一
#      FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/<token>
#      REPO_ROOT=/srv/blogger_ana        # 父仓库路径（默认 briefing/ 上一级）

# 4) 一次性任务：生成博主画像（执行后回填 briefing/TASKS.md）
cd /srv/blogger_ana/briefing
python -m briefing.scripts.profiles

# 5) 试跑一次（落盘预览）
python -m briefing.scripts.run_briefing --dry-run --slot evening
# 确认卡片内容 OK 后再真实推送一次验证 webhook：
python -m briefing.scripts.run_briefing --push --slot evening
```

## 定时任务（cron）

```bash
# 服务器时区务必设成 Asia/Shanghai
sudo timedatectl set-timezone Asia/Shanghai   # 或 TZ=Asia/Shanghai 前缀

crontab -e
# 交易日 7 推；非交易日自动跳过盘中槽、只留 20:00（脚本内用交易日历判断，无需区分写法）
15 9 * * * cd /srv/blogger_ana/briefing && python -m briefing.scripts.run_briefing --push >> data/cron.log 2>&1
0 10 * * * cd /srv/blogger_ana/briefing && python -m briefing.scripts.run_briefing --push >> data/cron.log 2>&1
0 11 * * * cd /srv/blogger_ana/briefing && python -m briefing.scripts.run_briefing --push >> data/cron.log 2>&1
45 12 * * * cd /srv/blogger_ana/briefing && python -m briefing.scripts.run_briefing --push >> data/cron.log 2>&1
0 14 * * * cd /srv/blogger_ana/briefing && python -m briefing.scripts.run_briefing --push >> data/cron.log 2>&1
0 15 * * * cd /srv/blogger_ana/briefing && python -m briefing.scripts.run_briefing --push >> data/cron.log 2>&1
0 20 * * * cd /srv/blogger_ana/briefing && python -m briefing.scripts.run_briefing --push >> data/cron.log 2>&1
```

## 已知风险与对策

- **服务器 IP 被头条风控**：爬虫有"当前网络环境无法查看"降级、节流与 2 并发控制；若持续失败，考虑换 IP 或注入登录态 cookie。
- **DeepSeek 偶发挂起**：复用父仓库的 watchdog 硬超时（180s/次），单博主失败不影响整轮。
- **行情接口抖动**：腾讯 → 新浪 → 仓库日线三级兜底。
- **交易日历**：akshare 拉取失败时回退内置 2026 节假日规则（覆盖全 2026），跨年需更新 `calendar.py`。

## 监控

- 日志：`briefing/data/briefing.log`、`data/cron.log`
- 状态：`briefing/data/state.json`（last_run / seen / 上期卡片）
- 历史简报：`briefing/data/briefings/*.json`
- 每时段若无新观点会推心跳消息（确认系统存活）；任一步失败会推错误心跳。

---

# Windows 部署（长期开机 + 定时推送）

本系统实际运行在用户的 Windows 电脑（Tailscale SSH 可达，别名 `windows-server`）。镜像 `aws_options` 项目已验证的部署模式。

## 环境（已建立）

- SSH：`ssh windows-server`（Tailscale IP `100.64.70.12`，端口 2222，用户 `24966`，Mac 密钥 `~/.ssh/id_ed25519_windows`）
- Python：`C:\Users\24966\AppData\Local\Programs\Python\Python311\python.exe`
- 依赖：`openai requests playwright akshare psutil` + `python -m playwright install chromium`
- 时区：`China Standard Time`（已确认；`schtasks /st` 按宿主机时区触发）

## 目录与密钥

- 代码：`C:\Users\24966\blogger_ana`（父仓库 + briefing，含 .git 便于更新）
- 密钥（勿提交，scp 单独传入）：
  - `C:\Users\24966\blogger_ana\briefing\.env` → `FEISHU_WEBHOOK_URL`
  - `C:\Users\24966\blogger_ana\.deepseek_keys.env` → `DEEPSEEK_API_KEY`
  - `paths.load_env()` 两者都读，`briefing/.env` 优先
- 运行时数据：`briefing\data\`（state.json / run.lock / profiles.json / briefing.log / briefings\ 历史）——从 Mac 传输时已排除，由脚本自建

## 更新代码（Mac → Windows）

```bash
cd /Users/potato/MyDoc/Study/MF/quant
tar --exclude='__pycache__' --exclude='*.pyc' --exclude='briefing/data' \
    --exclude='data/posts/_backup' --exclude='*_bodies_s*' \
    --exclude='.deepseek_keys.env' --exclude='briefing/.env' \
    -cf - blogger_ana | ssh windows-server "cd C:\\Users\\24966 && tar -xf -"
```

## 首次运行（关键：先 warm-up 再开调度）

1. 传密钥：`scp briefing/.env .deepseek_keys.env windows-server:...`（注意目标路径）
2. 生成画像：`python -m briefing.scripts.profiles`（90 人 ~9 次 DeepSeek；增量只处理新增博主）
3. **手动 warm-up**（真实全流程，人工盯，别让它在调度里超时）：
   `python -m briefing.scripts.run_briefing --push --slot evening`
   成功 → state 提交基线（last_run/seen），之后每轮都是轻量增量；中断也安全（锁/备份/原子写兜底）。
4. 电源硬化：`powercfg /change standby-timeout-ac 0`、`powercfg /change hibernate-timeout-ac 0`
5. 建调度（7 个 schtasks，全部每天触发，非交易日由脚本自跳过盘中槽）：

```bat
@echo off
rem Briefing scheduled-task entry. arg1 = slot key (early_morning/am/am2/pm_pre/pm/close/evening)
rem 2026-09-02: removed `>> briefing.log 2>&1` redirect - cmd write handle + python FileHandler
rem   double-open same file causes PermissionError (task exits 1). Log owned by FileHandler.
rem   Keep this file ASCII-only with CRLF line endings so cmd parses cleanly under any codepage
rem   (a UTF-8 + LF-only version made cmd mis-split `rem`/`set` lines under GBK; python ran by
rem   luck but the breadcrumb echo silently died).
cd /d "%~dp0..\.."
set PYTHONPATH=C:\Users\24966\blogger_ana
if not exist "C:\Users\24966\blogger_ana\briefing\data" mkdir "C:\Users\24966\blogger_ana\briefing\data"
set PYTHONIOENCODING=utf-8
echo [%date% %time%] ====== TRIGGER slot=%1 ====== >> "C:\Users\24966\blogger_ana\briefing\data\briefing.log"
C:\Users\24966\AppData\Local\Programs\Python\Python311\python.exe -m briefing.scripts.run_briefing --push --slot %1
```

> **bat 必须 ASCII + CRLF**：中文 Windows 的 cmd 按 GBK/OEM 码页解析批处理，UTF-8 中文（尤其 `rem` 注释里）会把后续行拆成乱命令；LF 行尾同样危险。改动 bat 后务必 `cmd /c ...\bat <slot>` 手动验证无 `'xxx' 不是内部或外部命令` 报错且面包屑落盘。

```bash
:: 2026-09-02 重建：加 StartWhenAvailable（错过补跑）+ ExecutionTimeLimit 60min（防卡死占锁）
:: ssh 会话非提权（ELEVATED: False）→ 以 24966 身份注册（登录状态=只使用交互方式，24966 自动登录即可触发）；
:: 若在管理员会话执行，改为 Register-ScheduledTask -User 'SYSTEM' -RunLevel Highest（无需交互登录）。
schtasks /create /tn BriefingEarly    /tr "C:\Users\24966\blogger_ana\briefing\runners\briefing_runner.bat early_morning" /sc daily /st 09:15 /f
schtasks /create /tn BriefingAm       /tr "C:\Users\24966\blogger_ana\briefing\runners\briefing_runner.bat am"            /sc daily /st 10:00 /f
schtasks /create /tn BriefingAm2      /tr "C:\Users\24966\blogger_ana\briefing\runners\briefing_runner.bat am2"           /sc daily /st 11:00 /f
schtasks /create /tn BriefingPmPre    /tr "C:\Users\24966\blogger_ana\briefing\runners\briefing_runner.bat pm_pre"        /sc daily /st 12:45 /f
schtasks /create /tn BriefingPm       /tr "C:\Users\24966\blogger_ana\briefing\runners\briefing_runner.bat pm"            /sc daily /st 14:00 /f
schtasks /create /tn BriefingClose    /tr "C:\Users\24966\blogger_ana\briefing\runners\briefing_runner.bat close"         /sc daily /st 15:00 /f
schtasks /create /tn BriefingEvening  /tr "C:\Users\24966\blogger_ana\briefing\runners\briefing_runner.bat evening"       /sc daily /st 20:00 /f
```

> 登录状态若显示「只使用交互方式」，需 24966 处于已登录会话任务才会触发（家庭电脑自动登录即可）；`schtasks /run` 可手动立即触发测试。

## Windows 监控

- 日志：`C:\Users\24966\blogger_ana\briefing\data\briefing.log`
- 任务：`schtasks /query /fo list | findstr Briefing`
- 心跳兜底：每时段无新观点也会推心跳消息确认存活

## Windows 已知注意

- **子进程编码**：`scrape_merge.py` 已给所有 subprocess 传 `encoding="utf-8"` + `PYTHONIOENCODING=utf-8`（Windows 管道默认 GBK 会崩中文/emoji）。
- **单实例锁**：`briefing/data/run.lock`（pid 存活检测 + 3h 过期接管），进程被杀/断电残留锁下一轮自动接管，不会双发。
- **时区**：代码钉 `BEIJING_TZ`（北京时间），独立于宿主机时区；调度触发时间仍按宿主机时区（当前已为北京时间）。
- **头条风控**：若 Windows IP 触发"网络环境无法查看"，考虑放缓节流或换网络。
