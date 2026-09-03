# Briefing scheduled-task registration (v13: single intraday task, 30-min grid).
# 2026-09-03 v13: super-short board pushes every grid tick (09:30..15:00, every
#   30 min); swing board is a subset handled inside run_briefing.py by wall clock
#   (config.due_boards). So ONE task suffices; the script decides and no-ops on
#   off-grid pseudo ticks (12:00/12:30/15:30, StartWhenAvailable catch-up), exits 0
#   before lock/scrape.
# We bypass briefing_runner.bat and call python.exe directly with --push, so the
#   working directory and ASCII/CRLF constraints of the .bat no longer matter.
# Settings: Daily 09:30 start + Repetition every 30 min for 6h (covers through the
#   15:00 close tick; boundary pseudo ticks are filtered in code), StartWhenAvailable,
#   ExecutionTimeLimit 20 min (< 30-min grid so a run never crosses into the next tick),
#   MultipleInstances IgnoreNew, principal 24966 Interactive Limited.
# Keep this file ASCII-only (no Chinese) so Windows PowerShell 5.1 parses it
# cleanly regardless of console codepage.
#
# Usage (on the Windows host):
#   powershell -NoProfile -ExecutionPolicy Bypass -File <path>\register_tasks.ps1
$ErrorActionPreference = 'Stop'

$py = 'C:\Users\24966\AppData\Local\Programs\Python\Python311\python.exe'
$wd = 'C:\Users\24966\blogger_ana'
$taskName = 'BriefingIntraday'

# Drop the v9/v12 three-daily-task set (if any) so only the single task remains.
$oldNames = @('BriefingMorning', 'BriefingAfternoon', 'BriefingLate')
foreach ($n in $oldNames) {
    Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction SilentlyContinue
}

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction -Execute $py `
    -Argument '-m briefing.scripts.run_briefing --push' `
    -WorkingDirectory $wd

# Daily at 09:30, repeating every 30 min for 6 hours (grid: 09:30..15:30 boundary;
#   code exits 0 on the off-grid tail like 12:00/12:30/15:30).
$trigger = New-ScheduledTaskTrigger -Daily -At '09:30'
# New-ScheduledTaskTrigger -Daily has no repetition params; borrow the Repetition
#   pattern from an -Once trigger that declares them.
$rep = (New-ScheduledTaskTrigger -Once -At '09:30' `
        -RepetitionInterval (New-TimeSpan -Minutes 30) `
        -RepetitionDuration (New-TimeSpan -Hours 6)).Repetition
$trigger.Repetition = $rep

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
    -MultipleInstances IgnoreNew -Compatibility Win7
$principal = New-ScheduledTaskPrincipal -UserId '24966' `
    -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null

Write-Output ('registered ' + $taskName)

Write-Output '--- verify ---'
$t = Get-ScheduledTask -TaskName $taskName
$rep = $t.Triggers[0].Repetition
Write-Output ($taskName + ' | ' + $t.State + ' | start=' + $t.Triggers[0].StartBoundary +
             ' | repeatEvery=' + $rep.Interval + ' | duration=' + $rep.Duration +
             ' | stopAtEnd=' + $rep.StopAtDurationEnd)
$info = $t | Get-ScheduledTaskInfo
Write-Output ('next=' + $info.NextRunTime + ' | last=' + $info.LastRunTime + ' | result=' + $info.LastTaskResult)
