@echo off
rem 简报调度入口：%1 = slot key（early_morning/am/am2/pm_pre/pm/close/evening）
rem 2026-09-02：去掉 `>> briefing.log 2>&1` 重定向——cmd 手柄与 python FileHandler 双开同文件会 PermissionError（定时任务退出 1）；
rem           日志由 run_briefing 的 FileHandler 独占写入；此处仅留面包屑 echo（写完即关，时序上不与 python 冲突）。
cd /d "%~dp0..\.."
set PYTHONPATH=C:\Users\24966\blogger_ana
if not exist "C:\Users\24966\blogger_ana\briefing\data" mkdir "C:\Users\24966\blogger_ana\briefing\data"
set PYTHONIOENCODING=utf-8
echo [%date% %time%] ====== 触发 slot=%1 ====== >> "C:\Users\24966\blogger_ana\briefing\data\briefing.log"
C:\Users\24966\AppData\Local\Programs\Python\Python311\python.exe -m briefing.scripts.run_briefing --push --slot %1
