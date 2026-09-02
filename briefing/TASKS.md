# 一次性任务清单

以下任务只需执行一次（或按需低频重跑），每次完成后在这里记录执行时间与结果。运行后 `profiles.json` 会自带 `updated` 时间戳作为第二重记录。

| 状态 | 任务 | 命令 | 执行时间 | 备注 |
|---|---|---|---|---|
| ✅ 已完成 | 生成博主画像档案（Mac） | `python -m briefing.scripts.profiles` | 2026-09-01 21:12 | 覆盖全部 90 位博主（非仅追踪 20 人），写入 `data/briefing/profiles.json`；**增量逻辑**：已画像博主跳过，只处理新增博主 |
| ✅ 已完成 | 生成博主画像档案（Windows） | `python -m briefing.scripts.profiles` | 2026-09-01 21:45 | Windows 侧重新生成（90 位，`C:\Users\24966\blogger_ana\briefing\data\profiles.json`），供长期运行自足 |
| ✅ 已完成 | 建飞书群机器人并填入 webhook | 手动 | 2026-09-01 21:35 | webhook 已写入 `briefing/.env`（gitignored），Mac 实测发卡成功（`code:0`） |
| ✅ 已完成 | TRACKED 刷新为综合口径 t 值 top-20 | `comparison_all.py` + `config.py` | 2026-09-01 | 总榜改综合口径（显式+nd 合并）按 t 值排序、资格放宽到综合均分>0；TRACKED = 新 top-20（掉出 刘海娃娃/乐哥来了/股傲，新进 强哥解盘/时间轨迹/江河之水终有入海之日） |
| ✅ 已完成 | Windows 代码同步（补缺失脚本） | tar（scripts/ + briefing/scripts/ + runners/） | 2026-09-01 23:1x | Windows 缺 `scripts/utils/fetch_bodies_shard.py`（正文补抓脚本，旧部署遗漏）→ 导致新博主 body-fetch code=2；已补传 + 全代码树同步，实测 fetch_bodies_shard 正常退出 |
| ✅ 已完成 | TRACKED 扩为 top-30 | `comparison_all.py` + `config.py` | 2026-09-01 | 总榜综合口径 t 值前 30（新增 刘海娃娃/TL阳光/股评老陈/财牛/谭阿坤/波段研究师/时空鹰眼/实盘指龙副号/柚来又去/鸟瞰股市；#30 鸟瞰股市 t=+0.46 与 #31 梦幻之歌并列，按稳定排序取鸟瞰股市）；config.py 已同步 Windows |
| ✅ 已完成 | 首轮真实推送（过去 2 日观点） | `--push --slot evening` | 2026-09-01 23:34 | 重置 state.json 触发 48h 首期窗口（since=08-30 23:19）；30 位博主全量抓取（[1/30]→[30/30]，40 条新帖 / 7 位博主发帖）、抽点 8 条、共识偏多（2多/0空）；**飞书推送成功 code:0**，历史 `20260901_233403_晚间.json`；state 已入增量（last_run=23:19、seen 7 博主）。⚠️ 实盘指龙副号 抓取失败（feed 主体与账号名不一致，疑抓错账号）；⚠️ 机器 23:34 后 sshd 间歇卡顿数小时，09:15/10:00 晨间推送被 Task Scheduler 跳过，11:00 起各档已恢复正常（机器现已稳定） |
| ✅ 已完成 | 抽点批覆盖 bug 修复 + 首报全名单 | `summarize.py` | 2026-09-02 | `extract_points()` 用批内索引 `j` 当 key，后一批覆盖前一批（首报 40 帖只留 8 条）→ 改全局索引 `i+j`；`MAX_KEY_BLOGGERS` 5→30。本地模拟 40 帖→40 点全保留（PASS） |
| ✅ 已完成 | 首报重构：每博主最新观点（无窗口限制） | `run_briefing.py` | 2026-09-02 | `_collect_firstrun_views()`：直接读主文件全量历史，每博主取最近一篇有实质内容（非视频帖）的观点帖，`FIRST_RUN_MAX_AGE_DAYS=7` 时效把关（超龄视为无近期观点）；`FIRST_RUN_FETCH_DAYS=7` 作爬虫刷新窗口。之后严格增量（since=last_run）。首报 dry-run 29/30 博主取到近期观点（仅 实盘指龙副号 因 25.7 天超龄排除） |
| ✅ 已完成 | 综合输出容错（拍平/缺段重试） | `summarize.py` | 2026-09-02 | 模型偶发把 consensus 拍平到顶层（`{stance,bull,bear,neutral,summary}` 无 consensus 键）→ `_normalize_synth_result()` 包回 + `_synth_result_ok()` 完整性检查 + `SYNTH_MAX_ATTEMPTS=3` 重试；另 `_overwrite_counts()` 用抽点权威值覆盖 LLM 漏计的多空/中性数（观测 10 中性被计成 1） |
| ✅ 已完成 | bat 双开修复 + ASCII/CRLF 硬化 | `briefing_runner.bat` | 2026-09-02 | 根因①：`>> briefing.log 2>&1` cmd 手柄与 python FileHandler 双开同文件 → PermissionError 退出 1；已删重定向、加面包屑 echo。根因②：bat 曾为 UTF-8 中文 + LF 行尾，cmd 按 GBK 解析把 `rem`/`set` 行拆成乱命令（面包屑静默丢失）→ 改为**纯 ASCII + CRLF**，实测 `cmd /c bat testxyz` 无乱命令错误、面包屑 `TRIGGER slot=` 落盘 |
| ✅ 已完成 | 定时任务重建 | PowerShell `Register-ScheduledTask` | 2026-09-02 | 7 档（BriefingEarly/Am/Am2/PmPre/Pm/Close/Evening）重建为 24966 身份 + `StartWhenAvailable` + `ExecutionTimeLimit 60min`（ssh 非提权无法 `/ru SYSTEM`）；11:00 自然触发验证 Last Result=0、锁跳过正常、面包屑待确认（旧 UTF-8 bat 未写面包屑，修复后已写入） |
| ✅ 已完成 | 卡片重排版：重点博主按总榜排名挑 top-8 | `render.py` | 2026-09-02 | 用户反馈"挑重点（排名高的博主）+ 排版太满不可读"。`config.TRACKED` 顺序即综合口径 t 值总榜（top-30 快照），`TRACKED.index(name)+1` 即排名。新增 `select_key_bloggers()`（按排名排序截 top-8）+ `_fmt_key_blogger()`（单行 `#n **名字** 🔴看多·强·近几日｜“quote”`，去掉 ▸ 点评行）；`_previous_from_card`/`_preview_text` 复用同一选择（上期基准=实际展示）。**已推送**：从 `20260902_112802_上午盘中.json` 重构 card → 新布局重渲染 → 飞书 code:0，历史 `20260902_113948_上午盘中_重排版.json`；零重抓、零 LLM 调用、零状态改动 |
| ✅ 已完成 | **v2 全板观点模型 + 重点博主两行式（Top 5 + 帖子时间 + 画像风格）** | `state.py`/`summarize.py`/`render.py`/`run_briefing.py` | 2026-09-02 | 用户确认：重点博主 Top 5、两行式（第一行 `#n 🟢 **名** 看空·强·近几日｜“quote”（时间）`，第二行缩进画像档案 style）、引文后括号标帖子时间（今日HH:MM/昨日HH:MM/MM-DD HH:MM）、中性博主不进重点（仅计全板）。**全板观点模型**：state 增 `recent_views`（每博主存一"近期观点"=最新帖立场，发新帖即更新，没发帖保持不变）；共识多空统计=对全部博主近期观点计数（非增量帖统计）；7 天时效，超期博主退出统计；分歧/风险/要点基于全板。综合 prompt 改全板输入（`synthesize(board, updated, ...)`，不再输出 key_bloggers/多空数字）。**已推送**：Windows 首期建板 29 位（7多/14空/8中性），飞书 code:0，历史 `20260902_115550_午前盘中.json`；state 已提交全板（last_run=11:54，旧 state 备份 `state.bak_v1`），下档 12:45 起自动增量 |

> 说明：博主画像是一次性生成任务，**不在每次简报运行时重跑**。`run_briefing.py` 只在 `data/briefing/profiles.json` 缺失时才自动生成；之后如需更新画像（比如榜单重排后），手动重跑上面的命令并在本表登记。
