# Briefing scheduled-task registration (v9: 3 slots, lowercase arg keys).
# 2026-09-03: lowercase slot args (morning/afternoon/late) - the slot-key bug
#   that made 09:30 fail with LastTaskResult=1 was caused by registering with
#   uppercase args (Morning/...) while config.SLOTS keys are lowercase.
# Settings mirror the pre-fix tasks: Daily, StartWhenAvailable, ExecutionTimeLimit
#   60min, MultipleInstances IgnoreNew, principal 24966 Interactive.
# Keep this file ASCII-only (no Chinese) so Windows PowerShell 5.1 parses it
# cleanly regardless of console codepage.
#
# Usage (on the Windows host):
#   powershell -NoProfile -ExecutionPolicy Bypass -File <path>\register_tasks.ps1
$ErrorActionPreference = 'Stop'

$bat = 'C:\Users\24966\blogger_ana\briefing\runners\briefing_runner.bat'

$slots = @(
    @{ Name = 'BriefingMorning';   Slot = 'morning';   Time = '09:30' },
    @{ Name = 'BriefingAfternoon'; Slot = 'afternoon'; Time = '13:00' },
    @{ Name = 'BriefingLate';      Slot = 'late';      Time = '14:30' }
)

foreach ($s in $slots) {
    Unregister-ScheduledTask -TaskName $s.Name -Confirm:$false -ErrorAction SilentlyContinue
    $action = New-ScheduledTaskAction -Execute $bat -Argument $s.Slot
    $trigger = New-ScheduledTaskTrigger -Daily -At $s.Time
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 60) `
        -MultipleInstances IgnoreNew -Compatibility Win7
    $principal = New-ScheduledTaskPrincipal -UserId '24966' `
        -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $s.Name -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Force | Out-Null
    Write-Output ("registered " + $s.Name + " slot=" + $s.Slot + " at " + $s.Time)
}

Write-Output '--- verify ---'
Get-ScheduledTask -TaskName 'Briefing*' | Sort-Object TaskName | ForEach-Object {
    $info = $_ | Get-ScheduledTaskInfo
    Write-Output ($_.TaskName + ' | ' + $_.State + ' | next=' + $info.NextRunTime +
                 ' | last=' + $info.LastRunTime + ' | result=' + $info.LastTaskResult)
}
