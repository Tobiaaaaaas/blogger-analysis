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

# 4) 试跑一次（落盘预览；非交易日可加 --skip-calendar 强制跑）
python -m briefing.scripts.run_briefing --dry-run --slot morning --no-scrape
# 确认卡片内容 OK 后再真实推送一次验证 webhook：
python -m briefing.scripts.run_briefing --push --slot morning
```
> v9（2026-09）起简报 = 固定 18 人窗口速览卡：交易日 **3 推**（09:30 早盘 / 13:00 午后 / 14:30 尾盘），每档 = 一张交互卡（行情 + ①–⑱ 固定 18 行 + 卡底收敛总结）。v8 的博主画像（profiles.json）已不再参与渲染，首次部署无需生成。

## 定时任务（cron）

```bash
# 服务器时区务必设成 Asia/Shanghai
sudo timedatectl set-timezone Asia/Shanghai   # 或 TZ=Asia/Shanghai 前缀

crontab -e
# 交易日 3 推；非交易日脚本自动 exit 0（weekday 写 1-5 即可，节假日/调休由脚本内交易日历精确判定）
30 9 * * 1-5 cd /srv/blogger_ana/briefing && python -m briefing.scripts.run_briefing --push --slot morning >> data/cron.log 2>&1
0 13 * * 1-5 cd /srv/blogger_ana/briefing && python -m briefing.scripts.run_briefing --push --slot afternoon >> data/cron.log 2>&1
30 14 * * 1-5 cd /srv/blogger_ana/briefing && python -m briefing.scripts.run_briefing --push --slot late >> data/cron.log 2>&1
```

## 已知风险与对策

- **服务器 IP 被头条风控**：爬虫有"当前网络环境无法查看"降级、节流与 2 并发控制；若持续失败，考虑换 IP 或注入登录态 cookie。
- **DeepSeek 偶发挂起**：复用父仓库的 watchdog 硬超时（180s/次），单博主失败不影响整轮。
- **行情接口抖动**：腾讯 → 新浪 → 仓库日线三级兜底。
- **交易日历**：akshare 拉取失败时回退内置 2026 节假日规则（覆盖全 2026），跨年需更新 `calendar.py`。

## 监控

- 日志：`briefing/data/briefing.log`、`data/cron.log`
- 状态：`briefing/data/state.json`（v9：`last_run / last_slot / seen`；`recent_views / board_prev / previous` 已由脚本首跑自动清除）
- 历史简报：`briefing/data/briefings/*.json`
- 每档都推一张卡：18 人全部有方向观点 → 全卡（含收敛总结）；全无方向 → 最小卡（确认系统存活，不发心跳）；任一步失败推错误文本。

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
- 运行时数据：`briefing\data\`（state.json / run.lock / briefing.log / briefings\ 历史）——从 Mac 传输时已排除，由脚本自建
  （v9 起渲染不依赖 profiles.json；旧文件可留可删，不影响运行）

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
2. **手动 warm-up**（真实全流程，人工盯，别让它在调度里超时）：
   `python -m briefing.scripts.run_briefing --push --slot morning`
   成功 → state 提交基线（last_run 落为首跑时刻，清掉 v8 残留键），之后每档都是轻量增量；中断也安全（锁/原子写兜底）。
   若 state 里仍是旧 v8 键，无需手动重置——脚本首跑自动迁移（`_migrate_state_v2`）。
3. 电源硬化：`powercfg /change standby-timeout-ac 0`、`powercfg /change hibernate-timeout-ac 0`
4. 建调度（3 个 schtasks，全部每天触发，非交易日由脚本自行 exit 0）：

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
> **bat 字节不变（2026-09 v9）**：它只透传 `--slot %1`，键名不在 bat 里；上面 rem 注释仍列 v8 槽位仅为历史说明。任务注册见下方 3 条（morning/afternoon/late）。

```bash
:: 2026-09-02 v9：删旧 7 档任务，建新 3 档（morning 09:30 / afternoon 13:00 / late 14:30）
:: 保留 StartWhenAvailable（错过补跑）+ ExecutionTimeLimit 60min（防卡死占锁）
:: ssh 会话非提权（ELEVATED: False）→ 以 24966 身份注册（登录状态=只使用交互方式，24966 自动登录即可触发）；
:: 若在管理员会话执行，改为 Register-ScheduledTask -User 'SYSTEM' -RunLevel Highest（无需交互登录）。
for %t in (BriefingEarly BriefingAm BriefingAm2 BriefingPmPre BriefingPm BriefingClose BriefingEvening) do schtasks /delete /tn %t /f
schtasks /create /tn BriefingMorning   /tr "C:\Users\24966\blogger_ana\briefing\runners\briefing_runner.bat morning"    /sc daily /st 09:30 /f
schtasks /create /tn BriefingAfternoon /tr "C:\Users\24966\blogger_ana\briefing\runners\briefing_runner.bat afternoon"  /sc daily /st 13:00 /f
schtasks /create /tn BriefingLate      /tr "C:\Users\24966\blogger_ana\briefing\runners\briefing_runner.bat late"       /sc daily /st 14:30 /f
```

> 登录状态若显示「只使用交互方式」，需 24966 处于已登录会话任务才会触发（家庭电脑自动登录即可）；`schtasks /run /tn BriefingMorning` 可手动立即触发测试。
> **重建任务用 `runners/register_tasks.ps1`**（2026-09-03 起为标准做法）：`powershell -NoProfile -ExecutionPolicy Bypass -File runners\register_tasks.ps1`。参数**必须全小写** `morning/afternoon/late`（`config.SLOTS` 键小写；曾因注册成 `Morning` 大写导致 `_resolve_slot` 拒绝、09:30 真实触发 exit 1）。脚本幂等：先注销旧任务再以原设置重建，末尾打印 NextRun 校验。

## Windows 监控

- 日志：`C:\Users\24966\blogger_ana\briefing\data\briefing.log`
- 任务：`schtasks /query /fo list | findstr Briefing`
- 每档都推卡：18 人全有方向 → 全卡；全无方向 → 最小卡（健康信号，取代 v8 心跳）；失败推错误文本

## Windows 已知注意

- **子进程编码**：`scrape_merge.py` 已给所有 subprocess 传 `encoding="utf-8"` + `PYTHONIOENCODING=utf-8`（Windows 管道默认 GBK 会崩中文/emoji）。
- **单实例锁**：`briefing/data/run.lock`（pid 存活检测 + 3h 过期接管），进程被杀/断电残留锁下一轮自动接管，不会双发。
- **时区**：代码钉 `BEIJING_TZ`（北京时间），独立于宿主机时区；调度触发时间仍按宿主机时区（当前已为北京时间）。
- **头条风控**：若 Windows IP 触发"网络环境无法查看"，考虑放缓节流或换网络。
