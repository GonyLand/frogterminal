# 작업 스케줄러에 'FrogTerminal Bridge' 를 등록하고 바로 띄운다(로그온 때마다 자동 시작).
#   pwsh -File scripts\windows\install-task.ps1             등록 + 시작
#   pwsh -File scripts\windows\install-task.ps1 -Uninstall  멈추고 등록 해제
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'
$name = 'FrogTerminal Bridge'
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$runner = Join-Path $PSScriptRoot 'run-bridge.ps1'
$pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (-not $pwsh) { $pwsh = (Get-Command powershell).Source }

if ($Uninstall) {
    Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "등록 해제: $name (떠 있는 브릿지는 scripts\windows\restart.ps1 -Stop 으로 끈다)"
    return
}
$action = New-ScheduledTaskAction -Execute $pwsh -WorkingDirectory $repo `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description 'frogterminal 브릿지(웹 터미널) 감시 실행기 — 로그온 때 자동 시작' -Force | Out-Null
Start-ScheduledTask -TaskName $name
Write-Output "등록·시작: $name"
