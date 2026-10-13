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
# 창이 아예 없어야 한다: Windows Terminal 이 기본 터미널이면 `pwsh -WindowStyle Hidden` 도 터미널 창이 하나 뜨고(닫으면 감시 실행기가
# 0xC000013A 로 죽어 Worker Health 경보), 그래서 conhost --headless 로 띄운다(콘솔 창 없음 · 터미널 위임 없음).
$conhost = Join-Path $env:WINDIR 'System32\conhost.exe'
$action = New-ScheduledTaskAction -Execute $conhost -WorkingDirectory $repo `
    -Argument "--headless `"$pwsh`" -NoProfile -ExecutionPolicy Bypass -File `"$runner`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description 'frogterminal 브릿지(웹 터미널) 감시 실행기 — 로그온 때 자동 시작' -Force | Out-Null
Start-ScheduledTask -TaskName $name
Write-Output "등록·시작: $name"
